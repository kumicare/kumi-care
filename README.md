# Kumi Care

小米門磁 → Python BLE bridge → TLS MQTT／Mosquitto。 已驗證一組真實 OPEN／CLOSED；10 次循環與長時間可靠度尚未完成。 Spring Boot／照護 LINE 通知由獨立的
[`kumi-center`](../kumi-center/README.md) 負責，第一版已部署，真實 OPEN/CLOSED → LINE 實收驗收通過。

## 啟動 bridge

目前 Mac 的 `.env` 與 CA 已備妥，**不要用範例覆蓋現有 `.env`**。

```sh
set -a
source .env
set +a
.venv/bin/python mqtt_bridge.py
```

`BLE_BINDKEY` 留空時會隱藏輸入提示；也可在私有 `.env` 設定。 macOS 需允許執行終端機使用 Bluetooth。事件以 QoS 1、retain=false 發布，
`mqtt_ack` 只代表 broker 確認收到，不代表後端入庫。

新環境才需要執行一次：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
# .env 不存在時才建立，再用編輯器填入設定。
test -e .env || (umask 077; cp .env.example .env)
```

## 測試

```sh
# 本機測試，不連外
.venv/bin/python -m unittest discover -s tests -v

# 真實開關門驗收（先停掉舊 bridge）
.venv/bin/python scripts/acceptance_session.py
```

驗收工具會同時啟動 bridge 與唯讀 subscriber。門先關閉，等 `ble_scanning` 後做 10 次開／關、每個狀態停至少 5 秒，最後等 5 秒再 Ctrl+C 產生報告。
具體驗收條件與網路測試見 [ACCEPTANCE.md](docs/ACCEPTANCE.md)。

## 檔案配置

| 位置                          | 用途                                         |
| ----------------------------- | -------------------------------------------- |
| mqtt_bridge.py                | BLE 接收、MQTT publisher 與啟動入口          |
| decrypt_xiaomi.py             | 原始已驗證的解密／事件解析，可獨立執行       |
| outbox.py                     | 本機持久化待送佇列與去重                     |
| event_contract.py、contracts/ | Python 驗證與跨語言 JSON Schema              |
| scripts/                      | 可重複使用的觀測、驗收、網路測試             |
| tests/                        | 本機自動測試                                 |
| deploy/mosquitto/             | 部署設定、初次安裝與 observer 設定           |
| tools/ble/                    | 早期 BLE 掃描診斷工具                        |
| tools/xiaomi-token-extractor/ | 本機第三方金鑰提取工具，Git 忽略             |
| docs/                         | 狀態、架構、驗收與 Spring Boot 交接，共 4 份 |
| .env、.local/                 | 私有設定、CA、outbox、日誌與報告，Git 忽略   |

outbox 上限 1000 筆待送事件；網路中斷、重啟會保留原 eventId 重送。 容量／磁碟錯誤會停止 bridge。Mac 休眠或程式未運行時無法接收 BLE。

## 不使用 Docker 提取 bindkey

本機已保留獨立 `.venv-extractor`；工具整理後的新路徑是：

```sh
.venv-extractor/bin/python tools/xiaomi-token-extractor/token_extractor.py
```

需要重建此獨立環境時：

```sh
python3 -m venv .venv-extractor
.venv-extractor/bin/python -m pip install -r tools/xiaomi-token-extractor/requirements.txt
```

輸出可能含秘密，勿貼出完整結果。將門磁 BLE bindkey 填入 `.env`。 BLE 診斷可執行 `.venv/bin/python
tools/ble/scan_xiaomi.py`；診斷輸出可能含 裝置識別與原始廣播，勿放入版本控制。

從 [專案狀態](docs/PROJECT_CONTEXT.md) 開始閱讀；下一階段見 [Spring Boot handoff](docs/SPRING_BOOT_HANDOFF.md)。
