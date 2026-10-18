# Pre–Spring Boot acceptance

## Camera transport experiment — 2026-10-02

- Extractor inventory: `mxiang.camera.c500os`, region `tw`; private values are in
  ignored root `.env` (0600), preserving unrelated settings.
- Upstream go2rtc v1.9.14 release verified, then source patched to add the Taiwan
  API endpoint/UI option. Region regression tests passed. See architecture for
  patch and exact upstream commit. User completed cloud login; no login secret
  belongs in documentation. The `tw` device list remained empty, but explicitly
  configuring the extractor DID/IP successfully streamed the device.
- VPS has FFmpeg/FFprobe. RTSP is sent through a temporary loopback SSH reverse
  tunnel; no public RTSP port was opened and no Xiaomi credential sent to VPS.
- Primary 15-second decode PASS: HEVC, 848x480, advertised 20 fps; sampling at
  1 fps produced 15 frames and 15 distinct hashes. Evidence:
  `.local/camera/probes/20261002T023041233544Z/report.json`.
- After stopping/restarting go2rtc with persisted configuration, secondary
  15-second decode PASS with the same format and 15 distinct decoded frames.
  Evidence: `.local/camera/probes/20261002T023207381527Z/report.json`.
- Primary after restart: 60-second decode PASS, 60 sampled frames / 60 distinct
  hashes, decoder exit 0. Evidence:
  `.local/camera/probes/20261002T023344133913Z/report.json`.
- Visual evidence: first immediate JPEG was gray; sampling after 5 seconds
  produced a recognizable entrance image (primary) and living-room image
  (secondary/channel=2), with camera timestamps matching the session minute.
  VPS-decoded JPEGs returned to Mac are `.local/camera/vps-primary-after5s.jpg`
  and `.local/camera/vps-secondary-after5s.jpg`. They are private household images.
  A five-second warmup is now used in the probe; it is not a guaranteed maximum
  time to a valid image. Lens labels are based on observed views, not a verified
  fixed-versus-PTZ hardware mapping.
- A probe PASS establishes sampled decoding only. Visual validation is separate;
  hashes alone do not establish useful imagery or an acceptable stream delay.
- Probe upgraded (warmup option, per-frame luma non-flat check in PASS, `--snapshot`,
  30 s progress). Latest-version primary 15 s PASS: 848x480, 15/15 decoded,
  non-flat; snapshot shows the entrance. Evidence:
  `.local/camera/probes/20261002T023904860483Z/`.
- Resolution: `subtype=3` gives 2560x1440 HEVC 20 fps on both channels; 4/5 fail;
  default and `sd` are 848x480. HD 30 s PASS, 30/30 decoded, 30 distinct, 30
  non-flat: secondary `20261002T024914400144Z`, primary `20261002T025030769541Z`.
  Both HD snapshots were inspected by the agent: real living-room and entrance scenes,
  camera OSD minute matching capture time. A HD probe at 02:49 (`20261002T024903917593Z`)
  FAILED because the operator killed its SSH session; it is not a camera fault.
- Camera OSD was 1-3 s ahead of the NTP-synced VPS clock across snapshots, so it is
  not a latency measurement.
- 30-minute HD primary continuity (`20261002T025144998590Z`): **FAIL by the strict
  criterion**, but the decode was continuous: 1800/1800 sampled frames, 1800 distinct,
  decoder exit 0, no disconnect, elapsed 1818 s. 4 sampled frames (at ~110, 664, 775,
  997 s) were uniform mid-gray (luma ~128, range <24) and coincided with 10 HEVC
  "Could not find ref with POC" decoder errors, i.e. missing reference data produced
  gray frames. SSH and RTSP/TCP are lossless, so the loss is most likely between camera
  and go2rtc. This is unproven. The criterion was not relaxed; an ETL consumer must drop
  flat or corrupted frames instead of treating every decoded frame as valid.
- Mac go2rtc process restart (clean SIGTERM, same binary/config): all four streams
  reloaded from config, no re-login, listeners still loopback only. HD 15 s PASS on both
  channels with 15/15 non-flat frames and real-scene snapshots: primary
  `20261002T032334699113Z`, secondary `20261002T032501316325Z`. This covers a clean
  restart only, not a crash, a Mac reboot or a sleep/wake cycle.
- Deliberate scene-change latency, network-loss recovery, cloud-session expiry and
  ESP32-S3 feasibility remain **NOT YET VERIFIED**. No recognition or LINE rules
  were changed.
- Existing 20 bridge/acceptance unit tests pass; these do not test camera streaming.

This experiment does not alter the physical door acceptance limits below.

## Gates

| Gate                         | Evidence / current state                                                                                                    |
| ---------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| BLE reception/decrypt/OPEN   | Experimentally validated; user previously saw 5 real broker ACKs                                                            |
| TLS/auth/ACL                 | PASS: external TLS 1.3 chain/hostname, anonymous/wrong-password rejected, publisher topic ACL; no public 1883               |
| QoS 1, no retained event     | PASS: VPS publisher/subscriber and late-subscription checks                                                                 |
| Observer least privilege     | PASS: independent front-door read-only account, MQTT 5 publish denial                                                       |
| Persist before publish       | PASS: SQLite committed record survives abrupt process exit                                                                  |
| Restart ambiguity            | PASS: real broker replay reuses identical eventId/envelope after simulated exit between PUBACK and local acknowledgement    |
| Network interruption         | PASS: 12-second local TCP-proxy outage preserves pending event; Paho reconnect delivers original event to real VPS observer |
| Physical OPEN/CLOSED         | One real OPEN/CLOSED cycle PASS; user stopped; 10-cycle gate NOT COMPLETED                                                  |
| Spring Boot / care DB / LINE | Not implemented; next phase                                                                                                 |

Real transport evidence: `.local/transport/20261001T030030570321Z/report.json`. This is synthetic
evidence, never substituted for physical CLOSED validation. Reports contain private event timing and
stay under ignored `.local/`.

## One-command physical check

Stop any older bridge first. Keep the door closed initially, then run from kumi-care:

```sh
.venv/bin/python scripts/acceptance_session.py
```

The launcher loads the private .env without executing it as shell code, starts a TLS subscriber
first, then starts the bridge. Enter bindkey locally when prompted. Wait for ble_scanning before
moving the magnet/door. Perform exactly 10 cycles: OPEN, wait at least 5 seconds, CLOSED, wait at
least 5 seconds. After the last CLOSED wait another 5 seconds, then Ctrl+C once. The launcher stops
both processes and writes `.local/acceptance/<session>/report.json`; the latest path is stored in
`.local/acceptance/latest`. Limit per session is 30 minutes.

PASS requires:

- Exactly 10 OPEN and 10 CLOSED in alternating order after ID deduplication.
- One optional initial CLOSED baseline is excluded from cycle counting.
- Matching bridge ACK eventId, eventType, sequence and occurredAt for each event.
- Sequence contiguous within this test, no conflicting payloads for the same ID.
- Valid contract, QoS 1 and non-retained receipt; no invalid messages/disconnects.
- Bridge receive-to-subscriber delay between 0 and 5 seconds on this Mac.
- Both bridge and observer exit cleanly.

Duplicate deliveries with the SAME eventId and SAME payload are counted and allowed by QoS 1. A new
ID for an extra OPEN/CLOSED fails the exact cycle test. Synthetic events never count toward physical
acceptance. PASS is only a short physical test result, not a guarantee against future missed BLE
events.

If FAIL: preserve the report and event capture; do not delete outbox to hide a failure. Review
extra/missing transitions, restart conditions and subscription health before repeating. The observer
source timestamps and bridge log allow localized diagnosis. No bindkeys/passwords/raw BLE
advertisements enter reports.

## Reproducible automated checks

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_mqtt_external.py
.venv/bin/python scripts/verify_transport.py
```

The final command sends clearly synthetic envelopes, performs retry/replay tests, and never stops
the VPS broker. Avoid running synthetic probes during the physical test window. Broker
restart/power-loss tests are not claimed here.

## User-requested stopping point

One physical cycle in session 20261001T030358340291Z was observed and verified: OPEN sequence 1,
CLOSED sequence 2; both event IDs/types/sequences/timestamps match bridge ACK logs. No duplicate
delivery, max latency 0.163198 seconds. User said to stop here. progress-snapshot.json records this
limited result. The 10-cycle gate remains incomplete; do not present the one-cycle PASS as
completion of the full plan. The manually started acceptance process was not terminated by the
agent; operator can use Ctrl+C to stop it.
