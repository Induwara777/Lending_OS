# decision-engine

LendOS 360 · Stage 4 microservice · Python 3.12 + FastAPI

The only service that decides **Approve, Refer or Decline**. It consumes `DataReady`, applies the client's rules and
risk scores, stores an immutable decision with a plain-language explanation, and publishes `DecisionMade`.

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest                                                   # 41 tests
uvicorn app.main:build_app --factory --reload            # http://localhost:8000/docs
```

No broker or Postgres needed locally: with the defaults it uses SQLite and keeps events in memory.
`docker compose up --build` starts the service with PostgreSQL and RabbitMQ.

Settings (env vars, prefix `DECISION_`): `CLIENT_PACK_DIR`, `DATABASE_URL`, `RABBITMQ_URL`, `MODEL_SERVICE_URL`, `LOG_LEVEL`.

Try it:

```bash
curl -X POST localhost:8000/api/v1/decisions \
  -H 'X-User-Id: svc' -H 'X-User-Role: system' -H 'content-type: application/json' \
  -d '{"application_id":"11111111-1111-1111-1111-111111111111","product":"vehicle_loan",
       "requested_amount":1500000,"crib_max_arrears_days":0,"debt_service_ratio":0.3,
       "ltv_ratio":0.5,"active_facilities":1}'
```

## How a decision is made (activity diagram 3.3)

1. Knock-out rules hit → **DECLINE**
2. Get scores; model down or a required feature missing → **REFER** (never Approve)
3. Affordability / LTV rules (limits per product from the client pack)
4. PD above decline limit → **DECLINE**
5. Any rule failed, or PD in the refer band → **REFER**
6. Otherwise **APPROVE**
7. Explanation written (cannot change the outcome), decision saved, `DecisionMade` published

## Layout

```
app/
  api/            decisions.py, referrals.py, deps.py (role checks)
  services/       decision_service.py, rules_engine.py, model_client.py, explainer.py
  events/         consumer.py (DataReady), publisher.py (DecisionMade)
  repository/     append-only decision storage, referral queue
  domain/         FeatureSnapshot, Scores, Decision, Reason, Outcome
  clientpack/     loads + validates client_packs/<client>/decision.yaml
client_packs/demo/decision.yaml   rules, bands, limits, authority matrix, scorecard, explanation templates
tests/
```

## API

| Method | Path | Who | Purpose |
| --- | --- | --- | --- |
| POST | `/api/v1/decisions` | system | Decide for one feature snapshot (idempotent) |
| GET | `/api/v1/decisions/{id}` | officers, auditor | One decision |
| GET | `/api/v1/decisions?application_id=` | officers, auditor | History for an application |
| GET | `/api/v1/referrals` | roles in authority matrix | Open referrals within my approval limit |
| GET | `/api/v1/referrals/{id}` | same | Scores, reasons, explanation for review |
| POST | `/api/v1/referrals/{id}/resolve` | same | Approve/decline with a reason |
| GET | `/health`, `/ready` | anyone | Health, active pack version |

Identity comes from the API gateway/Keycloak as `X-User-Id` and `X-User-Role` headers (PLT-01 belongs to the platform owner).

## Events

- **Consumes** `DataReady` on queue `decision-engine.data-ready`. Assumed shape:
  `{"application_id": "...", "payload": {<FeatureSnapshot fields>}}`. **Confirm with the verified-data owner.**
  Malformed messages are rejected without retry; transient failures are re-queued; redelivery is safe.
- **Publishes** `DecisionMade` `{decision_id, outcome, decided_by, reason_codes, max_approvable_amount, rule_set_version, model_version}`.

## Story coverage

| Story | Status |
| --- | --- |
| DEC-01 Knock-out rules | Done (rules from client pack) |
| DEC-02 Risk scores | Done with baseline scorecard; `HttpModelClient` ready for the client's model |
| DEC-03 Affordability + LTV | Done. Max-approvable formula is a simple proportional placeholder; replace with the signed-off spec formula |
| DEC-04 Decide and record | Done (append-only, hash, versions, one event per decision) |
| DEC-05 Explain | Template text + guarded LLM hook (`Explainer(llm=...)`); no real LLM wired yet |
| DEC-06 Review referred cases | Queue, authority limit, evidence, resolve with reason. Console screen is Dev 5; CRIB summary/documents are shown by the console from verified-data |
| DEC-07 Override | **Not started** (Phase 2): needs maker-checker |
| Related platform (PLT-03 audit log, PLT-05 log masking, PLT-02 pack validation) | Masking filter and pack validation here; audit log is shared code |

## Before going live

- Replace `create_all` with Alembic migrations.
- Replace demo pack numbers with the client's signed-off values.
- Add a dead-letter queue for rejected `DataReady` messages.
