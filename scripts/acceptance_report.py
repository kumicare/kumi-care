"""Strict short-run physical test report, not a long-term reliability claim."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path
import re


def report(capture, bridge_log, cycles=10):
    rows = [json.loads(line) for line in Path(capture).read_text().splitlines() if line.strip()]
    records = [r for r in rows if r['kind'] == 'event' and r['event']['source'] == 'XIAOMI_BLE']
    events, seen, duplicates, conflicts = [], {}, 0, 0
    for row in records:
        event = row['event']
        event_id = event['eventId']
        if event_id in seen:
            duplicates += 1
            conflicts += seen[event_id] != event
        else:
            seen[event_id] = event
            events.append(event)
    baseline = bool(events and events[0]['eventType'] == 'CLOSED')
    baseline_event = events.pop(0) if baseline else None
    types = [e['eventType'] for e in events]
    sequences = [e['sequence'] for e in events]
    # A full equality check rejects extras/repeated states, not only missing counts.
    expected = ['OPEN', 'CLOSED'] * cycles
    logs = Path(bridge_log).read_text() if Path(bridge_log).exists() else ''
    acked = {event_id: (kind, int(sequence), occurred) for event_id, kind, sequence, occurred in
             re.findall(r'mqtt_ack event_id=([^\s]+) event_type=([^\s]+) sequence=(\d+) occurred_at=([^\s]+)', logs)}
    failures = []
    if types != expected:
        failures.append('expected_exact_alternating_OPEN_CLOSED')
    if any(b != a + 1 for a, b in zip(sequences, sequences[1:])):
        failures.append('sequence_gap_or_reordering')
    if conflicts:
        failures.append('same_event_id_different_payload')
    if any(r.get('retained') or r.get('qos') != 1 for r in records):
        failures.append('retained_or_not_qos1')
    if any(r['kind'] in ('invalid', 'disconnected') for r in rows):
        failures.append('invalid_message_or_observer_disconnected')
    missing = [e['eventId'] for e in events if acked.get(e['eventId']) !=
               (e['eventType'], e['sequence'], e['occurredAt'])]
    if missing:
        failures.append('subscriber_event_missing_or_mismatched_bridge_ack')
    delays = [(datetime.fromisoformat(r['receivedAt']) - datetime.fromisoformat(r['event']['occurredAt'])).total_seconds()
              for r in records if 'receivedAt' in r]
    if any(delay < 0 or delay > 5 for delay in delays):
        failures.append('delivery_latency_outside_0_to_5_seconds')
    return dict(initialClosedBaselineIgnored=baseline, maxDeliveryLatencySeconds=max(delays, default=None),
                status='PASS' if not failures else 'FAIL', expectedCycles=cycles,
                uniqueEvents=len(events), eventTypes=dict(Counter(types)),
                qos1DuplicateDeliveries=duplicates, conflictingDuplicateIds=conflicts,
                missingBridgeAcks=len(missing), failures=failures,
                scope='Observed physical test window only; operator must perform exactly the requested cycles.')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('capture')
    parser.add_argument('--bridge-log', default='.local/bridge.log')
    parser.add_argument('--cycles', type=int, default=10)
    args = parser.parse_args()
    result = report(args.capture, args.bridge_log, args.cycles)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
