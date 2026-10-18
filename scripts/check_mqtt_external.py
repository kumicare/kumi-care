"""External TLS/auth/ACL probe; credentials never printed, no sensor events sent."""
import os
from pathlib import Path
import socket
import ssl
import threading

import paho.mqtt.client as mqtt

ROOT = Path(__file__).resolve().parents[1]
config = dict(line.split('=', 1) for line in (ROOT / '.local/bridge.env').read_text().splitlines() if '=' in line)
host = config['MQTT_HOST']
port = int(config['MQTT_PORT'])
context = ssl.create_default_context(cafile=str(ROOT / '.local/mqtt-ca.crt'))
with socket.create_connection((host, port), timeout=10) as sock:
    with context.wrap_socket(sock, server_hostname=host) as tls:
        print('PASS TLS chain + hostname:', tls.version())
        print('Server certificate expires:', tls.getpeercert()['notAfter'])

try:
    with socket.create_connection((host, 1883), timeout=3):
        raise AssertionError('Unexpected public plaintext listener')
except (TimeoutError, ConnectionRefusedError):
    print('PASS TCP 1883 unavailable from this client')


def probe(name, username, password, expected, forbidden_topic=None):
    ready = threading.Event()
    ack = threading.Event()
    result = {}
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv5)
    client.tls_set_context(context)
    if username:
        client.username_pw_set(username, password)

    def connected(client, userdata, flags, reason, props):
        result['accepted'] = not reason.is_failure
        ready.set()

    def published(client, userdata, mid, reason, props):
        result['publish_denied'] = reason.value == 135
        ack.set()

    client.on_connect = connected
    client.on_publish = published
    try:
        client.connect(host, port, keepalive=20)
        client.loop_start()
        assert ready.wait(10), 'No CONNACK received'
        assert result['accepted'] == expected, 'Unexpected authentication result'
        print('PASS', name)
        if forbidden_topic:
            client.publish(forbidden_topic, b'kumi-acl-test-not-a-sensor-event', qos=1, retain=False)
            assert ack.wait(10), 'No ACL PUBACK received'
            assert result['publish_denied'], 'Expected MQTT 5 Not authorized PUBACK'
            print('PASS publisher denied outside assigned topic')
    finally:
        client.disconnect()
        client.loop_stop()


probe('anonymous rejected', None, None, False)
probe('wrong password rejected', config['MQTT_USERNAME'], 'intentionally-invalid', False)
probe('publisher authenticated', config['MQTT_USERNAME'], config['MQTT_PASSWORD'], True,
      'care/v1/home/main/device/acl-probe-denied/event')
