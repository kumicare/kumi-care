"""Vendor-independent care/v1 event validation shared by acceptance tools."""
from datetime import datetime
import re
from uuid import UUID

EVENT_TYPES = {'OPEN', 'CLOSED', 'LEFT_OPEN'}
SOURCES = {'XIAOMI_BLE', 'SYNTHETIC_MQTT_ACCEPTANCE'}


def validate_event(event, topic):
    if not isinstance(event, dict):
        raise ValueError('Expected JSON object')
    match = re.fullmatch(r'care/v1/home/([A-Za-z0-9_-]{1,100})/device/([A-Za-z0-9_-]{1,100})/event', topic)
    if not match or event.get('deviceId') != match[2]:
        raise ValueError('Topic/device mismatch')
    try:
        UUID(event['eventId'])
        timestamp = datetime.fromisoformat(event['occurredAt'].replace('Z', '+00:00'))
    except (KeyError, ValueError, TypeError, AttributeError):
        raise ValueError('Invalid eventId or occurredAt') from None
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError('Timestamp requires timezone')
    if event.get('sensorType') != 'DOOR_CONTACT' or event.get('eventType') not in EVENT_TYPES:
        raise ValueError('Unsupported sensor/event type')
    if event.get('source') not in SOURCES:
        raise ValueError('Unsupported source')
    if type(event.get('sequence')) is not int or event['sequence'] < 1:
        raise ValueError('Sequence must be a positive integer')
    if type(event.get('rssi')) is not int:
        raise ValueError('RSSI must be an integer')
    return event
