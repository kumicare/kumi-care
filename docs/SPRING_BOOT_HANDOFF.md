# Spring Boot integration boundary

The bridge remains responsible for BLE-to-MQTT only. The user authorized the next phase on
2026-10-01: independent [`kumi-center`](../../kumi-center/README.md) now implements ingestion,
transactional Supabase persistence and durable LINE Push delivery. VPS deployment and real OPEN/CLOSED LINE receipt passed on 2026-10-01;
see kumi-center/docs/ACCEPTANCE.md for evidence and remaining reliability limits.

## Connection and contract

| Setting               | Value                                                                        |
| --------------------- | ---------------------------------------------------------------------------- |
| Host/port             | kumi.kumiland.cloud:8883                                                     |
| Transport             | MQTT over TLS, hostname + chain verification required                        |
| Subscriber credential | care-backend, root-only `/opt/kumi-care/mosquitto/admin/backend.env` on VPS  |
| Trust anchor          | `/home/che/.config/kumi-care/ca.crt` on VPS; public CA only                  |
| Subscription          | `care/v1/home/main/device/+/event`                                           |
| QoS                   | Subscribe QoS 1; current publisher QoS 1                                     |
| Retain                | false for events                                                             |
| Contract              | `contracts/sensor-event-v1.schema.json`, `event_contract.py`                 |
| Test observer         | Separate care-observer account; exact front-door read permission, no publish |

Example (synthetic, not a real care observation):

```json
{
  "eventId": "d523493a-4031-4a85-a274-82ff6bc7dd36",
  "deviceId": "front-door",
  "sensorType": "DOOR_CONTACT",
  "eventType": "OPEN",
  "occurredAt": "2026-10-01T03:00:00+00:00",
  "source": "SYNTHETIC_MQTT_ACCEPTANCE",
  "sequence": 1,
  "rssi": -73
}
```

Production sensor events use source=XIAOMI_BLE. Reject/quarantine test source from care rules and
LINE alerts. Tests must never create real care alerts. Timestamps are bridge reception time in UTC,
not sensor clock time. receivedAt belongs to the backend. Do not infer physical identity or travel
direction.

Sequence increases across restarts of the same local outbox, but resets if a new outbox is created.
It is diagnostic, not a unique key. Retries reuse eventId and all payload fields; QoS 1 can
duplicate delivery. Bind the payload deviceId to the MQTT topic and resolve home from the authorized
topic, not from caller claims. Unknown additive JSON fields can be retained in payload JSONB for
compatibility.

## Implementation checklist (now owned by kumi-center)

1. Create a modular monolith in sibling ../kumi-center (Java/Spring Boot), with MQTT config, subscriber, typed
   event DTO/enums and application ingestion boundary.
2. Supply the dedicated subscriber secret through a protected runtime file; import public CA into a
   dedicated JVM truststore or equivalent scoped TLS configuration. Never disable
   certificate/hostname verification.
3. Validate required fields, UUID, timestamp timezone, types/enums and topic/device match; log only
   accepted event ID/type and reject malformed input safely.
4. Reuse the synthetic publisher/observer acceptance path to verify deserialization and duplicate
   delivery behavior, with no LINE integration yet.

Next commit after ingestion: sensor_event migration and tests in kumi-brain-sql, using existing
Supabase PostgreSQL; unique event_id, occurred_at/received_at and JSONB original envelope. Keep DB
connection credentials out of the Python bridge. Persist idempotently before deterministic rule
evaluation. Design MQTT subscriber session/persistence and acknowledgment policy explicitly so a
backend outage or crash does not become silent loss. Broker PUBACK only confirms broker receipt: the
bridge deletes its pending record at PUBACK, so it is not an end-to-end DB ACK.

Then deterministic rule -> durable alert -> retryable LINE notification service. Hermes must remain
outside this critical path. Existing LINE transport assets are in kumi-tools; do not modify the live
Hermes route to ingest MQTT events.

## Evidence and limits

See [ACCEPTANCE.md](ACCEPTANCE.md) for this session's exact gates/results. Mac network fault
injection is a local TCP proxy affecting only the test publisher; VPS broker and existing
LINE/Hermes stay running. This does not establish resilience to broker power loss, disk loss,
prolonged BLE loss, every sensor firmware state or a full care-service outage. A stopped/asleep Mac
cannot capture new BLE advertisements.

## Milestone boundaries and remaining physical work

### Physical reliability follow-up

- **Files:** acceptance script and evidence.
- **Acceptance:** complete the planned 10 cycles; later evaluate counter reset/wrap and a longer
  soak.
- **Test:** real sensor, observer and matching ACK report.

### M4-A — MQTT ingestion

- **Modules:** backend MQTT, domain and application.
- **Acceptance:** validate topic and JSON, log accepted IDs, exclude invalid/test input from care
  rules.
- **Test:** broker integration, poison payloads and duplicate fixtures.

### M4-B — Idempotent persistence

- **Modules:** kumi-brain-sql migrations/tests and backend persistence.
- **Acceptance:** unique event_id, timestamps, original JSONB, least-privilege access and a Supabase
  retention/capacity plan.
- **Test:** duplicate insert, transaction failure, migrations and authorization.

### M5-A — Deterministic rule

- **Modules:** backend application/rules.
- **Acceptance:** OPEN informational alert with stable identity; no person/direction inference.
- **Test:** rule fixtures and replay without duplicate alerts.

### M5-B — LINE delivery

- **Modules:** backend notification and deployment docs.
- **Acceptance:** separate retryable notification service with observable failures.
- **Test:** mock LINE, then approved real recipient; Hermes unavailable test.

### Camera ETL follow-up (experimental; not part of M4/M5)

- **Scope:** kumi-care go2rtc adapter and `scripts/camera_vps_probe.py` only. No
  recognition, identity or LINE rules until the transport limits in
  [ACCEPTANCE.md](ACCEPTANCE.md) are measured.
- **Next measurements:** timed physical scene change, network-loss and Mac-restart
  recovery, cloud-session expiry. Future care rules (cat leaving is always abnormal;
  mother leaving alone alerts; mother accompanied must name the companion; unknown
  identity is never guessed) need a separate design before any backend change.

Keep these as separate reviewable changes. Completed BLE/broker/outbox work is summarized in
[PROJECT_CONTEXT.md](PROJECT_CONTEXT.md); do not reimplement it as part of M4-A.
