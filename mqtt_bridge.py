"""BLE -> TLS MQTT with a bounded, durable, single-process event outbox."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from getpass import getpass
import json
import logging
import os
import re
import ssl
import threading
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from uuid import uuid4

from bleak import BleakScanner
import paho.mqtt.client as mqtt
from decrypt_xiaomi import XIAOMI_UUID, decrypt_mibeacon, parse_event
from outbox import Outbox

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    host: str
    username: str
    password: str = field(repr=False)
    port: int = 8883
    ca_cert: str | None = None
    home_id: str = 'main'
    device_id: str = 'front-door'
    outbox_path: str = '.local/outbox.sqlite3'

    @classmethod
    def from_env(cls, env):
        for name in ('MQTT_HOST', 'MQTT_USERNAME', 'MQTT_PASSWORD'):
            if not env.get(name, '').strip():
                raise ValueError(f'{name} is required')
        try:
            port = int(env.get('MQTT_PORT', '8883'))
        except ValueError:
            raise ValueError('MQTT_PORT must be an integer') from None
        if not 1 <= port <= 65535:
            raise ValueError('MQTT_PORT out of range')
        ids = [env.get('CARE_HOME_ID', 'main'), env.get('CARE_DEVICE_ID', 'front-door')]
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value) for value in ids):
            raise ValueError('Home/device IDs must be 1-100 letters, digits, underscores or hyphens')
        return cls(env['MQTT_HOST'], env['MQTT_USERNAME'], env['MQTT_PASSWORD'],
                   port, env.get('MQTT_CA_CERT') or None, *ids,
                   env.get('CARE_OUTBOX_PATH', '.local/outbox.sqlite3'))

    @property
    def topic(self):
        return f'care/v1/home/{self.home_id}/device/{self.device_id}/event'


class Publisher:
    def __init__(self, config):
        self.config = config
        self.connected = threading.Event()
        self.pending = None
        self.stopping = False
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id=f'care-bridge-{uuid4().hex}',
                                  protocol=mqtt.MQTTv311)
        self.client.username_pw_set(config.username, config.password)
        self.client.tls_set_context(ssl.create_default_context(cafile=config.ca_cert))
        self.client.max_queued_messages_set(100)
        self.client.reconnect_delay_set(1, 30)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            LOG.error('mqtt_connection_rejected')
        else:
            self.connected.set()
            LOG.info('mqtt_connected')

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        self.connected.clear()
        (LOG.info if self.stopping else LOG.warning)('mqtt_disconnected')

    def start(self, wait_for_connection=True):
        self.client.connect_async(self.config.host, self.config.port, keepalive=60)
        self.client.loop_start()
        if wait_for_connection and not self.connected.wait(15):
            self.close()
            raise TimeoutError('MQTT connection not established within 15 seconds')

    def publish(self, event):
        # One FIFO worker owns this client. Keep the SAME Paho message on timeout:
        # reconnect retransmission must not create new IDs or unbounded queue entries.
        if self.pending is None:
            if not self.connected.wait(10):
                raise TimeoutError('MQTT disconnected; event remains in outbox')
            info = self.client.publish(self.config.topic, json.dumps(event), qos=1, retain=False)
            if info.rc not in (mqtt.MQTT_ERR_SUCCESS, mqtt.MQTT_ERR_NO_CONN):
                raise RuntimeError('MQTT enqueue rejected; event remains in outbox')
            self.pending = (event['eventId'], info)
        event_id, info = self.pending
        if event_id != event['eventId']:
            raise RuntimeError('Previous event still awaits acknowledgment')
        if info.rc == mqtt.MQTT_ERR_SUCCESS:
            info.wait_for_publish(timeout=10)
        else:
            # A disconnect racing publish() may return NO_CONN for a queued QoS 1
            # message; wait_for_publish would raise immediately on that rc.
            deadline = time.monotonic() + 10
            while not info.is_published() and time.monotonic() < deadline:
                time.sleep(0.1)
        if not info.is_published():
            raise TimeoutError('MQTT acknowledgement timeout; event retained')
        self.pending = None

    def close(self):
        self.stopping = True
        self.client.disconnect()
        self.client.loop_stop()


class Bridge:
    def __init__(self, key, device_id, outbox):
        self.key = key
        self.device_id = device_id
        self.outbox = outbox
        self.failure = asyncio.Event()

    def on_advertisement(self, device, advertisement):
        payload = advertisement.service_data.get(XIAOMI_UUID)
        if not payload:
            return
        plaintext = decrypt_mibeacon(payload, self.key)
        if plaintext is None:
            return
        event_type = parse_event(plaintext)
        if event_type not in ('OPEN', 'CLOSED', 'LEFT_OPEN'):
            return
        event = dict(eventId=str(uuid4()), deviceId=self.device_id,
                     sensorType='DOOR_CONTACT', eventType=event_type,
                     occurredAt=datetime.now(timezone.utc).isoformat(),
                     source='XIAOMI_BLE', rssi=advertisement.rssi)
        try:
            stored = self.outbox.enqueue(payload, event)
        except BufferError:
            LOG.error('event_not_stored outbox_full event_type=%s', event_type)
            self.failure.set()
            return
        except Exception as exc:
            LOG.error('event_not_stored error_type=%s', type(exc).__name__)
            self.failure.set()
            return
        if stored:
            LOG.info('event_stored event_id=%s event_type=%s sequence=%s occurred_at=%s',
                     stored['eventId'], stored['eventType'], stored['sequence'], stored['occurredAt'])


async def publish_events(outbox, publisher):
    while True:
        event = outbox.next_event()
        if event is None:
            await asyncio.sleep(0.2)
            continue
        try:
            await asyncio.to_thread(publisher.publish, event)
        except Exception as exc:
            LOG.warning('delivery_pending event_id=%s event_type=%s sequence=%s error_type=%s',
                        event['eventId'], event['eventType'], event['sequence'], type(exc).__name__)
            await asyncio.sleep(1)
            continue
        # A storage failure here must stop the worker, leaving replayable pending
        # data. A crash between PUBACK and commit may duplicate the SAME eventId.
        outbox.acknowledge(event['eventId'])
        LOG.info('mqtt_ack event_id=%s event_type=%s sequence=%s occurred_at=%s',
                 event['eventId'], event['eventType'], event['sequence'], event['occurredAt'])


async def run(config, key):
    outbox = Outbox(config.outbox_path)
    publisher = None
    worker = None
    failed = None
    try:
        outbox.bind_topic(config.topic)
        publisher = Publisher(config)
        publisher.start(wait_for_connection=False)
        bridge = Bridge(key, config.device_id, outbox)
        worker = asyncio.create_task(publish_events(outbox, publisher))
        failed = asyncio.create_task(bridge.failure.wait())
        async with BleakScanner(detection_callback=bridge.on_advertisement):
            LOG.info('ble_scanning pending_events=%s', outbox.pending_count())
            await asyncio.wait((worker, failed), return_when=asyncio.FIRST_COMPLETED)
            if worker.done():
                await worker
            raise RuntimeError('Outbox could not store event; bridge stopped')
    finally:
        # Stop scanning first. Pending records survive shutdown without waiting
        # for the network; the next process replays their original envelopes.
        for task in (worker, failed):
            if task:
                task.cancel()
        await asyncio.gather(*(task for task in (worker, failed) if task), return_exceptions=True)
        if publisher:
            await asyncio.to_thread(publisher.close)
        LOG.info('bridge_shutdown pending_events=%s', outbox.pending_count())
        outbox.close()


def main():
    os.umask(0o077)
    Path('.local').mkdir(mode=0o700, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s',
                        handlers=[logging.StreamHandler(), RotatingFileHandler(
                            '.local/bridge.log', maxBytes=2_000_000, backupCount=2)])
    try:
        config = Config.from_env(os.environ)
        key_hex = os.environ.get('BLE_BINDKEY') or getpass('BLE bindkey: ')
        try:
            key = bytes.fromhex(key_hex.strip())
        except ValueError:
            raise ValueError('BLE bindkey must be 32 hexadecimal characters') from None
        if len(key) != 16:
            raise ValueError('BLE bindkey must be 32 hexadecimal characters')
        asyncio.run(run(config, key))
    except KeyboardInterrupt:
        LOG.info('bridge_stopped')
    except Exception as exc:
        # Third-party exception text can include configuration; omit it.
        LOG.error('bridge_failed error_type=%s; check configuration and broker', type(exc).__name__)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
