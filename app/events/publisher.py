"""Publishing DecisionMade. Routing key = event name on the shared topic exchange."""
import asyncio
import json
import logging
import threading
import uuid
from datetime import UTC, datetime
from typing import Protocol

import aio_pika

from app.domain.models import Decision

log = logging.getLogger(__name__)

DECISION_MADE = "DecisionMade"


def decision_made_envelope(decision: Decision) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "event_type": DECISION_MADE,
        "occurred_at": datetime.now(UTC).isoformat(),
        "application_id": str(decision.application_id),
        "payload": {
            "decision_id": str(decision.decision_id),
            "outcome": decision.outcome.value,
            "decided_by": decision.decided_by,
            "reason_codes": [r.code for r in decision.reasons],
            "max_approvable_amount": decision.max_approvable_amount,
            "rule_set_version": decision.rule_set_version,
            "model_version": decision.model_version,
        },
    }


class EventPublisher(Protocol):
    def publish_decision_made(self, decision: Decision) -> None: ...


class InMemoryPublisher:
    """Dev / demo / tests: no broker needed."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def publish_decision_made(self, decision: Decision) -> None:
        self.events.append(decision_made_envelope(decision))


class RabbitPublisher:
    """Publishes to RabbitMQ from sync code via a small background event loop."""

    def __init__(self, url: str, exchange: str):
        self._url, self._exchange_name = url, exchange
        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True, name="rabbit-publisher").start()
        self._connection: aio_pika.abc.AbstractRobustConnection | None = None
        self._exchange: aio_pika.abc.AbstractExchange | None = None

    async def _ensure(self) -> aio_pika.abc.AbstractExchange:
        if self._exchange is None:
            self._connection = await aio_pika.connect_robust(self._url)
            channel = await self._connection.channel(publisher_confirms=True)
            self._exchange = await channel.declare_exchange(
                self._exchange_name, aio_pika.ExchangeType.TOPIC, durable=True
            )
        return self._exchange

    async def _publish(self, body: dict) -> None:
        exchange = await self._ensure()
        await exchange.publish(
            aio_pika.Message(
                json.dumps(body).encode(),
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=body["event_id"],
            ),
            routing_key=body["event_type"],
        )

    def publish_decision_made(self, decision: Decision) -> None:
        body = decision_made_envelope(decision)
        # Blocks until the broker confirms; raises if it can't be reached.
        asyncio.run_coroutine_threadsafe(self._publish(body), self._loop).result(timeout=10)
