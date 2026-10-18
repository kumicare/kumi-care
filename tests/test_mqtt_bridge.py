import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
import tempfile
import subprocess
import sys
from pathlib import Path
import threading
import unittest
from unittest.mock import Mock
from uuid import UUID

from Crypto.Cipher import AES
import paho.mqtt.client as mqtt
from mqtt_bridge import Bridge, Config, Publisher, publish_events
from outbox import Outbox
from decrypt_xiaomi import XIAOMI_UUID

KEY = bytes(range(16))  # Synthetic test key, never used by a device.


def advertisement(state=0, counter=1):
    header = bytes.fromhex('58588b09') + bytes([counter]) + bytes.fromhex('010203040506')
    extended = b'\x00\x00\x00'
    cipher = AES.new(KEY, AES.MODE_CCM, nonce=header[5:11] + header[2:5] + extended, mac_len=4)
    cipher.update(b'\x11')
    encrypted, tag = cipher.encrypt_and_digest(b'\x19\x10\x01' + bytes([state]))
    return SimpleNamespace(service_data={XIAOMI_UUID: header + encrypted + extended + tag}, rssi=-73)


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Outbox(Path(self.temp.name) / 'events.sqlite3', capacity=2)
        self.addCleanup(self.queue.close)
        self.bridge = Bridge(KEY, 'front-door', self.queue)

    def take(self):
        event = self.queue.next_event()
        self.queue.acknowledge(event['eventId'])
        return event

    def test_authenticated_transitions_and_duplicates(self):
        for state in (0, 1):
            adv = advertisement(state, state + 1)
            self.bridge.on_advertisement(None, adv)
            self.bridge.on_advertisement(None, adv)
        events = [self.take(), self.take()]
        self.assertTrue(self.queue.pending_count() == 0)
        self.assertEqual([e['eventType'] for e in events], ['OPEN', 'CLOSED'])
        self.assertEqual([e['sequence'] for e in events], [1, 2])
        self.assertNotEqual(events[0]['eventId'], events[1]['eventId'])
        for event in events:
            UUID(event['eventId'])
            self.assertIsNotNone(datetime.fromisoformat(event['occurredAt']).tzinfo)
            self.assertEqual(event['deviceId'], 'front-door')
            self.assertEqual(event['rssi'], -73)
            self.assertEqual(event['sensorType'], 'DOOR_CONTACT')

    def test_reject_wrong_key_malformed_unknown(self):
        bad = advertisement()
        bad.service_data[XIAOMI_UUID] = bad.service_data[XIAOMI_UUID][:-1] + b'\x00'
        for adv in (bad, advertisement(9), SimpleNamespace(service_data={XIAOMI_UUID: b'\x00'}, rssi=0)):
            self.bridge.on_advertisement(None, adv)
        wrong = Bridge(bytes(16), 'front-door', self.queue)
        wrong.on_advertisement(None, advertisement())
        self.assertTrue(self.queue.pending_count() == 0)

    def test_left_open(self):
        self.bridge.on_advertisement(None, advertisement(2))
        self.assertEqual(self.take()['eventType'], 'LEFT_OPEN')

    def test_queue_overflow_visible_and_can_readmit(self):
        for counter in (1, 2):
            self.bridge.on_advertisement(None, advertisement(counter=counter))
        with self.assertLogs('mqtt_bridge', level='ERROR'):
            self.bridge.on_advertisement(None, advertisement(counter=3))
        self.take()
        self.bridge.on_advertisement(None, advertisement(counter=3))
        self.assertEqual(self.queue.pending_count(), 2)


class ConfigTests(unittest.TestCase):
    def test_config_defaults_and_secret_repr(self):
        config = Config.from_env(dict(MQTT_HOST='localhost', MQTT_USERNAME='sensor', MQTT_PASSWORD='secret'))
        self.assertEqual(config.port, 8883)
        self.assertEqual(config.topic, 'care/v1/home/main/device/front-door/event')
        self.assertNotIn('secret', repr(config))

    def test_invalid_config(self):
        base = dict(MQTT_HOST='localhost', MQTT_USERNAME='sensor', MQTT_PASSWORD='secret')
        for update in ({'MQTT_PASSWORD': ''}, {'MQTT_PORT': '0'}, {'MQTT_PORT': 'invalid'}, {'CARE_HOME_ID': '+'}, {'CARE_DEVICE_ID': 'a/b'}):
            with self.assertRaises(ValueError):
                Config.from_env(base | update)


class PublisherTests(unittest.TestCase):
    def publisher(self):
        publisher = Publisher.__new__(Publisher)
        publisher.config = Config('localhost', 'sensor', 'secret')
        publisher.client = Mock()
        publisher.connected = threading.Event()
        publisher.connected.set()
        publisher.pending = None
        return publisher

    def test_qos_and_ack(self):
        publisher = self.publisher()
        info = publisher.client.publish.return_value
        info.rc = mqtt.MQTT_ERR_SUCCESS
        info.is_published.return_value = True
        event = {'eventId': 'stable-test-id'}
        publisher.publish(event)
        args, kwargs = publisher.client.publish.call_args
        self.assertEqual(args[0], publisher.config.topic)
        self.assertEqual(json.loads(args[1]), event)
        self.assertEqual(kwargs, dict(qos=1, retain=False))
        info.wait_for_publish.assert_called_once_with(timeout=10)

    def test_timeout_and_rejected_enqueue(self):
        publisher = self.publisher()
        info = publisher.client.publish.return_value
        info.rc = mqtt.MQTT_ERR_SUCCESS
        info.is_published.return_value = False
        with self.assertRaises(TimeoutError):
            publisher.publish({'eventId': 'test'})
        publisher.pending = None
        info.rc = mqtt.MQTT_ERR_QUEUE_SIZE
        with self.assertRaises(RuntimeError):
            publisher.publish({'eventId': 'test'})

    def test_timeout_retry_reuses_paho_message(self):
        publisher = self.publisher()
        info = publisher.client.publish.return_value
        info.rc = mqtt.MQTT_ERR_SUCCESS
        info.is_published.return_value = False
        event = {'eventId': 'stable'}
        with self.assertRaises(TimeoutError):
            publisher.publish(event)
        info.is_published.return_value = True
        publisher.publish(event)
        self.assertEqual(publisher.client.publish.call_count, 1)
        self.assertIsNone(publisher.pending)


class WorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_keeps_event_id_and_does_not_log_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / 'events.sqlite3')
            event = box.enqueue(b'frame', dict(eventId='one', eventType='OPEN', occurredAt='test'))
            publisher = Mock()
            publisher.publish.side_effect = [RuntimeError('sensitive-password'), None]
            with self.assertLogs('mqtt_bridge', level='INFO') as logs:
                task = asyncio.create_task(publish_events(box, publisher))
                try:
                    for _ in range(40):
                        if not box.pending_count():
                            break
                        await asyncio.sleep(0.05)
                    self.assertEqual(box.pending_count(), 0)
                finally:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    box.close()
            self.assertEqual(publisher.publish.call_count, 2)
            self.assertEqual(publisher.publish.call_args_list[0], publisher.publish.call_args_list[1])
            self.assertNotIn('sensitive-password', '\n'.join(logs.output))
            self.assertIn('mqtt_ack event_id=one event_type=OPEN sequence=1', '\n'.join(logs.output))


class OutboxTests(unittest.TestCase):
    def test_replay_restart_dedup_and_monotonic_sequence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.sqlite3'
            box = Outbox(path, history=1)
            first = box.enqueue(b'a', {'eventId': 'one'})
            box.close()
            box = Outbox(path, history=1)
            try:
                self.assertEqual(box.next_event(), first)
                self.assertIsNone(box.enqueue(b'a', {'eventId': 'different'}))
                box.acknowledge('one')
                second = box.enqueue(b'b', {'eventId': 'two'})
                box.acknowledge('two')
                third = box.enqueue(b'c', {'eventId': 'three'})
                self.assertEqual([first['sequence'], second['sequence'], third['sequence']], [1, 2, 3])
            finally:
                box.close()

    def test_single_process_lock_and_topic_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'events.sqlite3'
            box = Outbox(path)
            try:
                with self.assertRaises(RuntimeError):
                    Outbox(path)
                box.bind_topic('a')
                box.bind_topic('a')
                with self.assertRaises(ValueError):
                    box.bind_topic('b')
            finally:
                box.close()

    def test_disk_write_failure_does_not_ack_or_drop(self):
        with tempfile.TemporaryDirectory() as directory:
            box = Outbox(Path(directory) / 'events.sqlite3')
            try:
                event = box.enqueue(b'frame', {'eventId': 'one'})
                box.db.execute('PRAGMA query_only=ON')
                with self.assertRaises(Exception):
                    box.acknowledge('one')
                self.assertEqual(box.next_event(), event)
            finally:
                box.close()

    def test_abrupt_process_exit_preserves_committed_event(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'crash.sqlite3'
            code = "from outbox import Outbox; import os,sys; b=Outbox(sys.argv[1]); b.enqueue(b'frame', {'eventId':'crash-id'}); os._exit(17)"
            process = subprocess.run([sys.executable, '-c', code, str(path)], check=False)
            self.assertEqual(process.returncode, 17)
            box = Outbox(path)
            try:
                self.assertEqual(box.next_event(), {'eventId': 'crash-id', 'sequence': 1})
            finally:
                box.close()
