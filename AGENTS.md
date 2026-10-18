# Kumi Care agent guide

## Reading order

1. [Project context](docs/PROJECT_CONTEXT.md): status and ownership.
2. [Architecture](docs/ARCHITECTURE.md): boundaries and decisions.
3. [Acceptance](docs/ACCEPTANCE.md): verified evidence and incomplete checks.
4. [Spring Boot handoff](docs/SPRING_BOOT_HANDOFF.md): next tasks.

## Current milestone

Current milestone: integration requested; backend implementation belongs in sibling ../kumi-center.
Preserve the remaining physical M3 acceptance limits. Reception, bindkey extraction, AES-CCM and one real
OPEN/CLOSED cycle are experimentally validated. Ten-cycle and long-duration CLOSED reliability
remain incomplete.

## Implementation and secrets

Preserve `decrypt_xiaomi.py` as the validated protocol reference; never replace measured facts with
claims that untested hardware or production transport works. Use small, locally verifiable changes,
Python explicit interfaces, environment configuration, bounded queues and logs without credentials
or raw advertisements. Never commit bindkeys, Xiaomi tokens, passwords, private keys or family data.

## Architecture and safety

Future backend is a Spring Boot modular monolith. Database migrations belong to ../kumi-brain-sql;
existing Hermes/LINE integration belongs to ../kumi-tools. Sensor -> MQTT -> deterministic backend
rules -> LINE. No direct bridge-to-LINE, no Hermes/LLM final safety decisions. This is auxiliary
care, never a replacement for human care or emergency response. Missing events do not imply safety.
Do not introduce Kafka, Kubernetes, microservices or camera AI for this milestone. Event contract
must survive replacement of Python with ESP32-S3 / ESP-IDF.

## Reliability and validation

The bridge uses a bounded durable outbox. Keep eventId/payload stable on retries; never delete
pending state to make acceptance pass. Never count synthetic events as physical CLOSED validation.
Test artifacts/credentials stay in ignored .local/. After transport changes, run:

```sh
.venv/bin/python -m unittest discover -s tests -v
```

## File organization

Early scanner tools live in tools/ble/. The local-only third-party extractor is
tools/xiaomi-token-extractor/ (ignored). Keep four core docs; update existing sections instead of
adding duplicate recap/decision/roadmap documents.
