import asyncio
from datetime import datetime

from bleak import BleakScanner


def on_advertisement(device, advertisement):
    """
    每次收到 BLE Advertisement 時執行。
    """

    timestamp = datetime.now().strftime(
        "%H:%M:%S.%f"
    )[:-3]

    print("\n================================")

    print(f"Time: {timestamp}")

    print(f"Device Name: {device.name}")

    print(f"Device ID: {device.address}")

    print(f"RSSI: {advertisement.rssi}")

    print(
        "Manufacturer Data:",
        advertisement.manufacturer_data
    )

    print(
        "Service Data:",
        advertisement.service_data
    )

    print(
        "Service UUIDs:",
        advertisement.service_uuids
    )


async def main():

    print("Starting BLE Scanner...")

    scanner = BleakScanner(
        detection_callback=on_advertisement
    )

    await scanner.start()

    print("Scanning... Press Ctrl+C to stop.")

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
