from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from event_contract import validate_event
from scripts.acceptance_report import report

TOPIC = 'care/v1/home/main/device/front-door/event'


def event(sequence=1, event_type='OPEN'):
    return dict(eventId=str(uuid4()), deviceId='front-door', sensorType='DOOR_CONTACT',
                eventType=event_type, occurredAt=datetime.now(timezone.utc).isoformat(),
                source='XIAOMI_BLE', sequence=sequence, rssi=-73)


class AcceptanceTests(unittest.TestCase):
    def test_contract_rejects_bad_identity_timestamp_and_boolean_sequence(self):
        valid = event()
        self.assertEqual(validate_event(valid, TOPIC), valid)
        for changes in ({'deviceId': 'wrong'}, {'occurredAt': '2026-01-01T00:00:00'},
                        {'sequence': True}, {'eventId': 'invalid'}, {'eventType': 'UNKNOWN'},
                        {'source': 'random'}, {'rssi': 'strong'}):
            with self.assertRaises(ValueError):
                validate_event(valid | changes, TOPIC)

    def result(self, rows, acked=None):
        with tempfile.TemporaryDirectory() as directory:
            capture, log = Path(directory) / 'events.jsonl', Path(directory) / 'bridge.log'
            capture.write_text(''.join(json.dumps(r) + '\n' for r in rows))
            if acked is None:
                acked = [r['event']['eventId'] for r in rows if r['kind'] == 'event']
            by_id = {r['event']['eventId']: r['event'] for r in rows if r['kind'] == 'event'}
            log.write_text(''.join('mqtt_ack event_id={} event_type={} sequence={} occurred_at={}\n'.format(
                event_id, by_id[event_id]['eventType'], by_id[event_id]['sequence'], by_id[event_id]['occurredAt'])
                for event_id in acked))
            return report(capture, log, cycles=2)

    def rows(self):
        return [dict(kind='event', event=event(i+1, state), retained=False, qos=1)
                for i, state in enumerate(['OPEN', 'CLOSED'] * 2)]

    def test_exact_cycles_and_qos1_duplicate_handling(self):
        rows = self.rows()
        rows.insert(1, deepcopy(rows[0]))
        result = self.result(rows)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['qos1DuplicateDeliveries'], 1)

    def test_missing_closed_extra_state_gap_and_missing_ack_fail(self):
        rows = self.rows()
        self.assertEqual(self.result(rows[:-1])['status'], 'FAIL')
        self.assertEqual(self.result(rows + [dict(rows[-1], event=event(5))])['status'], 'FAIL')
        rows[-1]['event']['sequence'] = 99
        self.assertEqual(self.result(rows)['status'], 'FAIL')
        self.assertEqual(self.result(self.rows(), acked=[])['status'], 'FAIL')

    def test_synthetic_cannot_pass_physical_test(self):
        rows = self.rows()
        for row in rows:
            row['event']['source'] = 'SYNTHETIC_MQTT_ACCEPTANCE'
        self.assertEqual(self.result(rows)['status'], 'FAIL')

    def test_conflicting_duplicate_retained_and_disconnect_fail(self):
        rows = self.rows()
        conflict = deepcopy(rows[0]); conflict['event']['rssi'] = -60
        self.assertEqual(self.result(rows + [conflict])['status'], 'FAIL')
        rows[0]['retained'] = True
        self.assertEqual(self.result(rows)['status'], 'FAIL')
        self.assertEqual(self.result(self.rows() + [dict(kind='disconnected')])['status'], 'FAIL')

    def test_single_initial_closed_is_a_baseline(self):
        rows = self.rows()
        rows.insert(0, dict(kind='event', event=event(1, 'CLOSED'), retained=False, qos=1))
        for index, row in enumerate(rows):
            row['event']['sequence'] = index + 1
        result = self.result(rows)
        self.assertEqual(result['status'], 'PASS')
        self.assertTrue(result['initialClosedBaselineIgnored'])
