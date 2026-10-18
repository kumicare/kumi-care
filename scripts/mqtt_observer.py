"""Read-only MQTT acceptance capture; keys/passwords never enter reports."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import ssl
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import paho.mqtt.client as mqtt
from event_contract import validate_event

ROOT = Path(__file__).resolve().parents[1]
TOPIC = 'care/v1/home/main/device/front-door/event'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', type=Path, default=ROOT / '.local/observer.env')
    parser.add_argument('--ca', type=Path, default=ROOT / '.local/mqtt-ca.crt')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ready-file', type=Path)
    parser.add_argument('--seconds', type=int, default=900)
    args = parser.parse_args()
    os.umask(0o077)
    settings = dict(line.split('=', 1) for line in args.profile.read_text().splitlines() if '=' in line)
    done, ready = threading.Event(), threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    stream = args.output.open('x')
    lock = threading.Lock()
    errors = []

    def record(kind, **data):
        with lock:
            stream.write(json.dumps(dict(kind=kind, receivedAt=datetime.now(timezone.utc).isoformat(), **data)) + '\n')
            stream.flush()

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
    client.username_pw_set(settings['MQTT_USERNAME'], settings['MQTT_PASSWORD'])
    client.tls_set_context(ssl.create_default_context(cafile=str(args.ca)))
    client.reconnect_delay_set(1, 10)

    def connected(client, userdata, flags, reason, props):
        if reason.is_failure:
            errors.append('connect_rejected')
            done.set()
        else:
            client.subscribe(TOPIC, qos=1)

    def subscribed(client, userdata, mid, reasons, props):
        if not reasons or any(reason.is_failure for reason in reasons):
            errors.append('subscribe_rejected')
            done.set()
            return
        record('ready', topic=TOPIC)
        if args.ready_file:
            args.ready_file.write_text('ready\n')
        ready.set()
        print('OBSERVER_READY: 開始接收 front-door 事件', flush=True)

    def message(client, userdata, msg):
        try:
            event = validate_event(json.loads(msg.payload), msg.topic)
            record('event', topic=msg.topic, qos=msg.qos, retained=msg.retain, event=event)
            print('received event_type={} sequence={} event_id={} source={}'.format(
                event['eventType'], event['sequence'], event['eventId'], event['source']), flush=True)
        except (ValueError, TypeError, UnicodeError) as exc:
            record('invalid', errorType=type(exc).__name__)
            print('INVALID_EVENT (details omitted)', flush=True)

    def disconnected(client, userdata, flags, reason, props):
        if not done.is_set():
            record('disconnected')
            print('OBSERVER_DISCONNECTED; waiting for reconnect', flush=True)

    client.on_connect, client.on_subscribe = connected, subscribed
    client.on_message, client.on_disconnect = message, disconnected
    try:
        client.connect_async(settings['MQTT_HOST'], int(settings.get('MQTT_PORT', '8883')), keepalive=30)
        client.loop_start()
        deadline = time.monotonic() + 20
        while not ready.is_set() and not done.is_set() and time.monotonic() < deadline:
            done.wait(0.1)
        if not ready.is_set():
            raise RuntimeError('Observer failed to become ready')
        done.wait(args.seconds)
    finally:
        done.set()
        client.disconnect()
        client.loop_stop()
        record('stopped')
        stream.close()
    return 1 if errors else 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('Observer failed: ' + type(exc).__name__, file=sys.stderr)
        raise SystemExit(1)
