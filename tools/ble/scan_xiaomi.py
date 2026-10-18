import asyncio
from datetime import datetime

from bleak import BleakScanner


XIAOMI_SERVICE_UUID = (
    "0000fe95-0000-1000-8000-00805f9b34fb"
)

# 記錄每個裝置最後一次收到的 Payload
last_payloads = {}


def on_advertisement(device, advertisement):

    service_data = advertisement.service_data

    payload = service_data.get(
        XIAOMI_SERVICE_UUID
    )

    if payload is None:
        return

    device_id = device.address
    payload_hex = payload.hex()

    # 同一裝置、相同 Payload 不重複輸出
    if last_payloads.get(device_id) == payload_hex:
        return

    last_payloads[device_id] = payload_hex

    timestamp = datetime.now().strftime(
        "%H:%M:%S.%f"
    )[:-3]

    print("\n==============================")
    print(f"Time: {timestamp}")
    print(f"Device: {device_id}")
    print(f"RSSI: {advertisement.rssi}")
    print(f"Payload: {payload_hex}")
    print("==============================")


async def main():

    print("Starting Xiaomi BLE Scanner...")

    scanner = BleakScanner(
        detection_callback=on_advertisement
    )

    await scanner.start()

    try:
        while True:
            await asyncio.sleep(1)

    finally:
        await scanner.stop()


if __name__ == "__main__":

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nScanner stopped.")