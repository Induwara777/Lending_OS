"""Consuming DataReady from verified-data and turning it into a decision.

Assumed event contract (agree with the verified-data owner before integrating):
  {"event_type": "DataReady", "application_id": "...", "payload": {<FeatureSnapshot fields>}}
"""
import asyncio
import json
import logging

import aio_pika
from pydantic import ValidationError

from app.domain.models import FeatureSnapshot
from app.services.decision_service import DecisionService

log = logging.getLogger(__name__)

QUEUE = "decision-engine.data-ready"
DATA_READY = "DataReady"


class PoisonMessage(Exception):
    """Message can never be processed (bad JSON / bad shape). Not retried."""


def parse_data_ready(body: bytes) -> FeatureSnapshot:
    try:
        envelope = json.loads(body)
        payload = {"application_id": envelope.get("application_id"), **envelope["payload"]}
        return FeatureSnapshot.model_validate(payload)
    except (ValueError, KeyError, TypeError, ValidationError) as exc:
        raise PoisonMessage(str(exc)) from exc


async def handle_message(message: aio_pika.abc.AbstractIncomingMessage, service: DecisionService) -> None:
    try:
        snapshot = parse_data_ready(message.body)
    except PoisonMessage as exc:
        log.error("dropping malformed DataReady: %s", exc)
        await message.reject(requeue=False)  # goes to the dead-letter queue if one is configured
        return
    try:
        # The service is synchronous (DB + broker confirm), so keep it off the event loop.
        await asyncio.to_thread(service.decide, snapshot)
    except Exception:
        log.exception("decision failed for %s; will retry", snapshot.application_id)
        await message.nack(requeue=True)
        return
    await message.ack()  # safe to redeliver: decide() is idempotent


async def run_consumer(url: str, exchange: str, service: DecisionService) -> None:
    connection = await aio_pika.connect_robust(url)
    channel = await connection.channel()
    await channel.set_qos(prefetch_count=10)
    ex = await channel.declare_exchange(exchange, aio_pika.ExchangeType.TOPIC, durable=True)
    queue = await channel.declare_queue(QUEUE, durable=True)
    await queue.bind(ex, routing_key=DATA_READY)
    log.info("consuming %s", QUEUE)
    async with queue.iterator() as it:
        async for message in it:
            await handle_message(message, service)
