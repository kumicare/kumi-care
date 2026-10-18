import asyncio
from datetime import datetime
from getpass import getpass

from bleak import BleakScanner
from Crypto.Cipher import AES


XIAOMI_UUID = "0000fe95-0000-1000-8000-00805f9b34fb"

# MCCGQ02HL 的 Product ID
PRODUCT_ID = 0x098B

# 用來過濾重複封包
seen_frames = set()


def decrypt_mibeacon(data: bytes, key: bytes):

    # 基本長度檢查
    if len(data) < 18:
        return None

    # 小米設備 Product ID 使用 Little Endian
    product_id = int.from_bytes(data[2:4], "little")

    if product_id != PRODUCT_ID:
        return None

    # 從廣播中取得解密所需資料
    mac = data[5:11]

    product_and_counter = data[2:5]

    encrypted_data = data[11:]

    if len(encrypted_data) < 7:
        return None

    ciphertext = encrypted_data[:-7]

    extended_counter = encrypted_data[-7:-4]

    authentication_tag = encrypted_data[-4:]

    # MiBeacon v5 AES-CCM Nonce
    nonce = (
        mac
        + product_and_counter
        + extended_counter
    )

    try:

        cipher = AES.new(
            key,
            AES.MODE_CCM,
            nonce=nonce,
            mac_len=4
        )

        cipher.update(b"\x11")

        plaintext = cipher.decrypt_and_verify(
            ciphertext,
            authentication_tag
        )

        return plaintext

    except ValueError:
        # 金鑰錯誤或封包驗證失敗
        return None


def parse_event(plaintext: bytes):

    if len(plaintext) < 4:
        return None

    object_id = int.from_bytes(
        plaintext[0:2],
        "little"
    )

    object_length = plaintext[2]

    if len(plaintext) < 3 + object_length:
        return None

    value = plaintext[3:3 + object_length]

    # MCCGQ02HL 開關門事件
    if object_id == 0x1019 and len(value) == 1:

        if value[0] == 0:
            return "OPEN"

        if value[0] == 1:
            return "CLOSED"

        if value[0] == 2:
            return "LEFT_OPEN"

    return f"OTHER_EVENT: {plaintext.hex()}"


def main_callback(device, advertisement):

    payload = advertisement.service_data.get(
        XIAOMI_UUID
    )

    if payload is None:
        return

    if len(payload) < 18:
        return

    product_id = int.from_bytes(
        payload[2:4],
        "little"
    )

    if product_id != PRODUCT_ID:
        return

    plaintext = decrypt_mibeacon(
        payload,
        bindkey
    )

    if plaintext is None:
        return

    # 使用裝置識別、計數器及封包內容過濾重複資料
    frame_id = (
        payload[5:11],
        payload[4],
        payload[-7:-4],
        payload[-4:]
    )

    if frame_id in seen_frames:
        return

    seen_frames.add(frame_id)

    event = parse_event(plaintext)

    timestamp = datetime.now().strftime(
        "%H:%M:%S"
    )

    print("----------------------")
    print(f"Time: {timestamp}")
    print("Decryption: SUCCESS")
    print(f"Event: {event}")
    print(f"RSSI: {advertisement.rssi}")
    print("----------------------")


async def main():

    print("Scanning Xiaomi Door Sensor...")

    scanner = BleakScanner(
        detection_callback=main_callback
    )

    await scanner.start()

    try:
        while True:
            await asyncio.sleep(1)

    finally:
        await scanner.stop()


if __name__ == "__main__":

    key_hex = getpass(
        "Enter your BLE KEY: "
    ).strip()

    if len(key_hex) != 32:
        raise ValueError(
            "BLE KEY must contain 32 hexadecimal characters"
        )

    bindkey = bytes.fromhex(key_hex)

    try:
        asyncio.run(main())

    except KeyboardInterrupt:
        print("\nScanner stopped")