# Mosquitto 運維

目前已部署於 `kumi.kumiland.cloud:8883`，資料位於 VPS `/opt/kumi-care/mosquitto`。TLS／帳密／ACL／QoS 1／非 retained
已驗證； Caddy 與 Hermes 保持正常。完整測試結果見 [ACCEPTANCE.md](../../docs/ACCEPTANCE.md)。

## 維護檔案

| 檔案                | 用途                                                   |
| ------------------- | ------------------------------------------------------ |
| compose.yaml        | digest-pinned image、重啟策略、資源限制與持久化掛載    |
| mosquitto.conf      | TLS 8883、認證、事件大小與佇列限制                     |
| acl                 | 感測器 publish-only、後端 read-only 的初始規則         |
| install.sh          | 全新 VPS 初次安裝；遇既有資料會拒絕覆蓋                |
| prepare-observer.sh | 加入 care-observer 的 exact-topic read-only 規則與帳密 |

既有部署不需要重跑 install.sh。新環境安裝前，將本目錄檔案放到 VPS 的 `/home/che/kumi-care-deploy/`，檢查後才執行：

```sh
sudo bash /home/che/kumi-care-deploy/install.sh
sudo bash /home/che/kumi-care-deploy/prepare-observer.sh
```

安裝器使用 Ubuntu Docker／Compose 套件，解析官方 eclipse-mosquitto:2 的 digest，再以該 digest 啟動。生成專用 CA 與帳密，不輸出秘密；不授予
che Docker group 權限。部分安裝失敗需先檢查，勿刪除資料繞過覆蓋保護。

## 連線與安全邊界

- 只監聽 IPv4 TLS 8883，無 1883 listener，禁止匿名。
- door-sensor：只可寫 `care/v1/home/main/device/front-door/event`。
- care-backend：只可讀 `care/v1/home/main/device/+/event`。
- care-observer：只可讀 front-door topic，用於驗收。
- 使用 host networking，讓 UFW INPUT 規則生效。安裝器只在 UFW 已啟用時 新增 8883 規則；不啟用或重設防火牆。Hostinger 外層防火牆需另確認。
- Container 使用 UID 1883、唯讀 root filesystem、移除 capabilities，並限制 記憶體／process 數／日誌大小；只有 broker data 可寫。

`/home/che/.config/kumi-care/` 的 bridge.env、observer.env 為 0600，旁邊是 公開 ca.crt。後端設定留在 root-only
`admin/backend.env`。CA 私鑰始終只在 VPS root-only `ca/ca.key`，勿複製到 Mac 或版本控制。

## 憑證與維運

Server certificate 到期：**2027-10-01 02:33:23 UTC**。CA 有效期 10 年， leaf 365 天；目前無自動續期。到期前，以原 root-only
CA 重簽 DNS SAN `kumi.kumiland.cloud` 的 leaf，保留權限，重啟 broker，重新驗證 chain／hostname。

最初 CA 缺少 keyUsage 的問題已修復；目前 install.sh 已包含必要欄位。 一次性修復腳本已移出維護目錄，保存在本機 `.local/archive/`；不需再次執行。

外部驗證從本機 kumi-care 執行：

```sh
.venv/bin/python scripts/check_mqtt_external.py
.venv/bin/python scripts/verify_transport.py
```

第二個指令會發送明確標示的 synthetic events，勿和實體循環驗收同時執行。

停止服務／回退（保留資料與秘密）：

```sh
sudo docker compose --project-directory /opt/kumi-care/mosquitto down
# 只有此部署新增該規則時，才移除 MQTT 規則：
sudo ufw delete allow 8883/tcp
```

官方參考：[設定文件](https://mosquitto.org/man/mosquitto-conf-5.html)、
[認證方式](https://mosquitto.org/documentation/authentication-methods/)。
