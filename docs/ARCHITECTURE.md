# Architecture

## Data flow

MVP target: MCCGQ02HL -> BLE -> Mac Python bridge -> TLS MQTT / VPS Mosquitto -> Spring Boot
ingestion -> Supabase PostgreSQL -> deterministic rules -> LINE. Hermes reads history for summaries
outside the critical alert path. Later replace Mac with ESP32-S3 without changing the event
contract. Camera identity/rules remain deferred; a user-authorized camera transport
experiment is described below.

## Camera transport experiment (2026-10-02)

Scope: validate C500 Dual -> Mac -> VPS image extraction and decoding before any
recognition or LINE rule changes. Existing extractor output identifies the Taiwan
device as `mxiang.camera.c500os`, cloud region `tw`. Private inventory is stored in
the ignored root `.env` as `XIAOMI_CAMERA_MODEL`, `XIAOMI_CAMERA_DID`,
`XIAOMI_CAMERA_IP`, `XIAOMI_CAMERA_TOKEN`, `XIAOMI_CAMERA_MAC`,
`XIAOMI_CAMERA_NAME`, and `XIAOMI_CAMERA_REGION`; file permissions are 0600.
This inventory is from the existing extractor session, not a fresh camera health check.

Experimental adapter: upstream go2rtc v1.9.14, Mac arm64, downloaded with release
asset digest verification into `.local/camera/`. API binds to 127.0.0.1:11984,
RTSP to 127.0.0.1:18554; WebRTC listener disabled. Cloud credentials belong only in
the ignored 0600 go2rtc configuration. Device token is not proof of stream access.
The experimental build adds `tw` to the regional cloud endpoint mapping and Taiwan
to the UI; reproducible patch: `tools/camera/go2rtc-v1.9.14-tw.patch`, applied to
upstream commit `b5948cfb25404cc5cb37b166ecaa2dca20b11d4b`. The region routing
regression test is `tools/camera/go2rtc-taiwan_test.go` (copy into upstream
`internal/xiaomi/` before `go test ./internal/xiaomi`). Binary is
`.local/camera/go2rtc-src/go2rtc-tw`. The logged-in cloud session is retained in the
private config. Device listing still returned no sources for Taiwan, but an
explicit URL using extractor inventory successfully streamed; the empty inventory
is not evidence that streaming is unsupported. Avoid `streams: {}` when editing
through this version's API: configuration persistence failed even though the
in-memory stream was added; explicit block-style stream entries were saved instead.
Upstream documents a cloud key exchange and local camera connection; short real-device probes now decode two different views successfully. Authentication
lifetime, network reconnection and sustained reliability remain unverified.

`scripts/camera_vps_probe.py --stream <configured-stream> --seconds 15` opens a
temporary SSH reverse forward to the VPS loopback and uses its installed FFmpeg to
decode video. It persists restricted metadata/frame-hash evidence on the VPS under
`~/.local/state/kumi-camera-probes/` and a local report in `.local/camera/probes/`.
It sends no Xiaomi credentials to the VPS and opens no public camera listener.
Options: `--warmup N` (default 5 s skipped before sampling; the first frame after
connect can be gray) and `--snapshot` (one post-warmup JPEG returned over SSH stdout
to `.local/camera/probes/<run>/snapshot.jpg`, 0600; never stored on the VPS). Each
sampled frame also gets ffmpeg `signalstats` luma; a frame whose luma range is below
24 counts as flat. PASS requires decoder exit 0 and at least `seconds-1` sampled
frames that are both decoded and non-flat. Remote progress is printed to stderr
every 30 s. Probe PASS means decoded, non-flat samples were obtained; it does not
establish correct scene content (human snapshot check, recorded as
`visual_validation: NOT_CHECKED`), low latency, restart recovery or safety.

Stream quality (measured 2026-10-02 on `mxiang.camera.c500os`): default/`hd` maps to
upstream quality `2` and yields HEVC 848x480 at 20 fps; `subtype=sd` is the same
848x480; `subtype=3` yields HEVC 2560x1440 at 20 fps on both channels; `subtype=4`
and `5` fail to start. Configured streams: `c500_primary` / `c500_secondary`
(848x480) and `c500_primary_hd` / `c500_secondary_hd` (`&subtype=3`). Channel 1 shows
the entrance and `channel=2` the living room; fixed-versus-PTZ lens mapping is not
verified. The camera OSD clock was observed 1-3 s ahead of the NTP-synced VPS, so
OSD time cannot measure transport latency; that needs a deliberate physical scene
change timed against an external clock.
ESP32-S3 replacement is a separate later feasibility evaluation, not a promised
drop-in replacement for this network/video adapter.

## Event contract

Topic: care/v1/home/{homeId}/device/{deviceId}/event, QoS 1, retain false. Envelope: eventId (UUID),
deviceId, sensorType=DOOR_CONTACT, eventType=OPEN|CLOSED|LEFT_OPEN, occurredAt (UTC ISO-8601),
source=XIAOMI_BLE, sequence (increasing integer persisted in the bridge outbox), rssi (diagnostic
only). Timestamp is bridge reception time, not sensor-clock time. Sequence survives restart of the
same outbox, but a new/reset outbox starts again; never use it as a global unique identifier.
Backend deduplicates eventId.

## Durable delivery

Bridge reuses the authenticated decryptor and persists each normalized event before publishing.
SQLite outbox (.local/outbox.sqlite3) uses synchronous=FULL, monotonic sequence and a process lock;
it binds to one topic so config changes cannot replay old household data to a new topic. No BLE keys
or raw advertisements are stored: frame dedup uses SHA-256, with 2048 acknowledged records retained.
A pending event survives process restart with the same UUID/timestamp/payload. A FIFO worker uses
QoS1, retain=false and retries only after timeout; one Paho in-flight message is reused across
reconnects. Puback -> mark acknowledged. Crash after broker receipt but before local commit can
replay the same eventId; backend idempotency is required. PUBACK is not backend persistence
confirmation.

## Limits and failure behavior

TLS uses system roots or MQTT_CA_CERT and always verifies host/certificate. Up to 1000 pending
events are stored; full/disk error is explicit and stops the bridge rather than continuing to claim
healthy capture. The event exceeding capacity is NOT saved. SQLite disk work occurs in the BLE
callback for the MVP; throughput is suitable for door transitions, not high-frequency telemetry.
Network waits run off the BLE event loop. A stopped/asleep Mac cannot receive BLE. Very old repeats
outside the retained dedup history may create a new event ID; sensor counter resets/wrap and
long-duration reliability remain empirical work.

## Broker and backend boundary

Deployed broker: authenticated 8883, no public 1883, publish-only credential for this exact topic,
separate backend read credential. Future backend: validate, deduplicate and persist before rule
evaluation; notification behind a service. No bridge Supabase/LINE credentials. Future schema
changes use kumi-brain-sql.

## Decisions retained for future work

- Keep one bindkey/logical sensor per bridge process. Preserve the working decrypt_xiaomi.py
  reference and replace the edge implementation later without changing the vendor-independent
  backend topic/contract.
- Use Paho 2.x, QoS 1 plus backend idempotency; QoS 2 is unnecessary for this MVP.
- SQLite is a local transport outbox, not the care domain database. Reuse existing managed Supabase
  Free PostgreSQL; assess retention/availability/backup in M4.
- Keep Spring Boot a modular monolith. No Kafka, Kubernetes or early microservices.
- No direct bridge-to-LINE path and no final safety decisions by Hermes/LLMs. Missing sensor events
  never establish safety; this remains auxiliary care.
- Synthetic tests are clearly marked and excluded from physical acceptance and future care alert
  rules. Never disable TLS checks to make connectivity pass.
