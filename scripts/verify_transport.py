"""Real VPS acceptance with synthetic events and a local TCP fault proxy.
Never interrupts the VPS broker, LINE or other clients. Paho's test-only socket
factory is routed through the proxy; its original TLS hostname is unchanged.
"""
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import select
import socket
import ssl
import sys
import threading
import time
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paho.mqtt.client as mqtt
from event_contract import validate_event
from mqtt_bridge import Config, Publisher, publish_events
from outbox import Outbox

ROOT = Path(__file__).resolve().parents[1]


def profile(name):
    data = dict(line.split('=', 1) for line in (ROOT / '.local' / name).read_text().splitlines() if '=' in line)
    data['MQTT_CA_CERT'] = str(ROOT / '.local/mqtt-ca.crt')
    return data


class Proxy:
    def __init__(self, host, port):
        self.host, self.port = host, port
        self.online, self.stop = threading.Event(), threading.Event()
        self.online.set()
        self.sockets = []
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.port_local = self.listener.getsockname()[1]
        self.listener.listen(5)
        self.listener.settimeout(0.2)
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()

    def cut(self):
        self.online.clear()
        for sock in list(self.sockets):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def run(self):
        while not self.stop.is_set():
            sockets = []
            try:
                try:
                    client, _ = self.listener.accept()
                except socket.timeout:
                    continue
                sockets.append(client)
                if not self.online.is_set():
                    continue
                upstream = socket.create_connection((self.host, self.port), timeout=5)
                sockets.append(upstream)
                self.sockets = sockets
                while self.online.is_set() and not self.stop.is_set():
                    ready, _, _ = select.select(sockets, [], [], 0.2)
                    eof = False
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            eof = True
                            break
                        (upstream if source is client else client).sendall(data)
                    if eof:
                        break
            except OSError:
                pass
            finally:
                self.sockets = []
                for sock in sockets:
                    sock.close()
        self.listener.close()

    def close(self):
        self.stop.set()
        self.cut()
        self.thread.join(6)


async def until(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError('Transport acceptance timed out')
        await asyncio.sleep(0.1)


async def main():
    os.umask(0o077)
    config = Config.from_env(profile('bridge.env'))
    observer_config = profile('observer.env')
    directory = ROOT / '.local/transport' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    directory.mkdir(parents=True, mode=0o700)
    received, errors = [], []
    subscribed, denied = threading.Event(), threading.Event()
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
    sub.tls_set_context(ssl.create_default_context(cafile=config.ca_cert))
    sub.username_pw_set(observer_config['MQTT_USERNAME'], observer_config['MQTT_PASSWORD'])

    def connected(client, userdata, flags, reason, props):
        if reason.is_failure:
            errors.append('observer authentication failed')
        else:
            client.subscribe(config.topic, qos=1)

    def on_subscribe(client, userdata, mid, reasons, props):
        if not reasons or any(reason.is_failure for reason in reasons):
            errors.append('observer subscription failed')
        else:
            subscribed.set()

    def on_message(client, userdata, msg):
        try:
            event = validate_event(json.loads(msg.payload), msg.topic)
            if event['source'] == 'SYNTHETIC_MQTT_ACCEPTANCE':
                received.append(dict(event=event, qos=msg.qos, retained=msg.retain))
        except Exception:
            errors.append('invalid event')

    def on_publish(client, userdata, mid, reason, props):
        if reason.value == 135:
            denied.set()
        else:
            errors.append('observer publish was not explicitly denied')

    sub.on_connect, sub.on_subscribe = connected, on_subscribe
    sub.on_message, sub.on_publish = on_message, on_publish
    proxy = Proxy(config.host, config.port)
    box = Outbox(directory / 'outbox.sqlite3')
    box.bind_topic(config.topic)
    publisher, worker = None, None
    checks = []

    def make_publisher():
        result = Publisher(config)
        result.client._create_socket_connection = lambda: socket.create_connection(('127.0.0.1', proxy.port_local), timeout=3)
        return result

    def enqueue(state):
        return box.enqueue(uuid4().bytes, dict(eventId=str(uuid4()), deviceId='front-door',
            sensorType='DOOR_CONTACT', eventType=state,
            occurredAt=datetime.now(timezone.utc).isoformat(), source='SYNTHETIC_MQTT_ACCEPTANCE', rssi=-73))

    def count(event):
        return sum(r['event']['eventId'] == event['eventId'] for r in received)

    try:
        sub.connect(config.host, config.port, keepalive=30)
        sub.loop_start()
        await until(subscribed.is_set, 15)
        # Observer itself must remain read-only, even on its permitted read topic.
        sub.publish(config.topic, b'ACL_PROBE_NOT_A_SENSOR_EVENT', qos=1, retain=False)
        await until(denied.is_set, 10)
        checks.append('observer_publish_denied')
        publisher = make_publisher()
        await asyncio.to_thread(publisher.start)
        first = enqueue('OPEN')
        await asyncio.to_thread(publisher.publish, first)
        await until(lambda: count(first) == 1)
        # Simulate process exit after PUBACK but before SQLite acknowledgment.
        await asyncio.to_thread(publisher.close)
        box.close()
        box = Outbox(directory / 'outbox.sqlite3')
        assert box.next_event() == first
        publisher = make_publisher()
        await asyncio.to_thread(publisher.start)
        await asyncio.to_thread(publisher.publish, box.next_event())
        await until(lambda: count(first) == 2)
        box.acknowledge(first['eventId'])
        checks.append('restart_replays_identical_event_id_and_envelope')
        proxy.cut()
        await until(lambda: not publisher.connected.is_set(), 10)
        second = enqueue('CLOSED')
        worker = asyncio.create_task(publish_events(box, publisher))
        await asyncio.sleep(12)
        assert box.pending_count() == 1 and count(second) == 0
        checks.append('network_outage_preserves_pending_event')
        proxy.online.set()
        await until(lambda: box.pending_count() == 0, 45)
        await until(lambda: count(second) >= 1, 10)
        assert all(r['event'] == second for r in received if r['event']['eventId'] == second['eventId'])
        checks.append('automatic_reconnect_delivers_original_event')
        assert second['sequence'] == first['sequence'] + 1
        assert all(r['qos'] == 1 and not r['retained'] for r in received)
        assert not errors, errors
        checks.append('qos1_nonretained_valid_contract_and_persistent_sequence')
        result = dict(status='PASS', checks=checks, syntheticEvents=received,
                      physicalTest='PENDING', interruptionScope='local TCP proxy only, broker stayed running')
        (directory / 'report.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(status='PASS', checks=checks, report=str(directory / 'report.json')), indent=2))
    finally:
        if worker:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        if publisher:
            await asyncio.to_thread(publisher.close)
        box.close()
        proxy.close()
        sub.disconnect()
        sub.loop_stop()


if __name__ == '__main__':
    asyncio.run(main())
