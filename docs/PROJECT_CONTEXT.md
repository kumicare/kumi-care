# 專案狀態與下一步

更新：2026-10-02。這份文件是進度與 repo 分工的單一入口；架構見 [ARCHITECTURE.md](ARCHITECTURE.md)，測試證據見
[ACCEPTANCE.md](ACCEPTANCE.md)。

## 目前成果

AI 智慧居家長照監控系統，以被動感測輔助家人照護。已打通：

`Xiaomi MCCGQ02HL → Mac BLE bridge → TLS MQTT → VPS Mosquitto → 唯讀觀測端`

- BLE 接收、bindkey、AES-CCM 解密已驗證；原始 decrypt_xiaomi.py 保留。
- 真實 OPEN／CLOSED 各一筆通過，訂閱內容與 ACK 一致，序號 1／2，無重複； 最大 bridge-to-subscriber 延遲 0.163198 秒。使用者選擇先停在這裡。
- **10 次循環及長時間可靠度尚未完成**；單次 CLOSED 成功不代表全面可靠。
- 20 項自動測試通過；真實 broker 的 TLS／ACL、QoS 1、非 retained、 同 eventId 重送及 12 秒網路中斷後重連測試通過。
- SQLite outbox、詳細事件日誌、唯讀驗收工具與事件契約已完成。
- Spring Boot、照護事件持久化與 LINE 通知第一版已在獨立的 `../kumi-center` 實作。
  自動化測試、VPS 部署與真實 OPEN/CLOSED → LINE 實收驗收已通過。

目前資料夾尚未初始化 Git；未 commit、push 或轉移 GitHub repo。建議日後統一 到 kumicare org 並維持 repo 分工，搬移仍是未執行的獨立工作。

## 攝像頭 ETL 實驗（2026-10-02）

使用者要求先驗證台灣 C500 雙攝版 → Mac → VPS，不提前做辨識或 LINE 規則。
Extractor 確認型號 `mxiang.camera.c500os`、地區 `tw`；私密設定存在本地 `.env`。
已補 go2rtc v1.9.14 的台灣路由並保留登入工作階段，兩路均能由 VPS 解碼，
暖機後取得玄關／客廳 JPEG。短測與實際截圖證據見 [ACCEPTANCE.md](ACCEPTANCE.md)。
入口程式 `.local/camera/go2rtc-src/go2rtc-tw -config .local/camera/go2rtc.yaml`；
驗收工具 `scripts/camera_vps_probe.py`。認證設定、影像和原始紀錄只放忽略目錄。
ESP32-S3 替换可行性留待後續，不承諾能直接替代 Mac 的小米網路取流能力。

2026-10-02 續作：probe 加入亮度非平面檢查、暖機與 `--snapshot`；實測 `subtype=3`
可取得兩路 2560x1440（預設僅 848x480），已新增 `c500_primary_hd`／`c500_secondary_hd`。
兩路 HD 30 秒 PASS 並經截圖確認為實景。30 分鐘 HD 連續測試：1800/1800 張都解碼成功、
連線沒有中斷，但有 4 張是解碼錯誤造成的灰畫面（缺參考幀），依嚴格標準判 FAIL；
之後的 ETL 必須丟掉灰畫面或壞畫面。
go2rtc 正常重啟後不需重新登入，兩路 HD 都能立即恢復取流（不包含當機、Mac 重開機或睡眠喚醒）。
尚未驗證：實際場景變化延遲（需人為動作計時；鏡頭 OSD 時鐘比 VPS 快 1-3 秒，
不能拿來量延遲）、網路中斷復原、雲端 session 到期。
使用者另要求：任一用量視窗剩餘額度低於 10% 時，整理可交給 next agent 的
context prompt；只記錄私密設定位置，不能附憑證或家庭影像。2026-10-02 最後查詢時
五小時剩餘 9%、每週剩餘 50%，已達門檻；交接 prompt 在本地
`.local/camera/NEXT_AGENT_PROMPT.md`，不含憑證。

## 既有環境與責任邊界

| 範圍                                        | 位置／狀態                                                   |
| ------------------------------------------- | ------------------------------------------------------------ |
| BLE bridge、MQTT 部署與驗收                 | 本專案 kumi-care                                             |
| 現有 Hostinger／Hermes／LINE 運維脈絡       | ../kumi-tutor                                             |
| Hermes plugin、既有 LINE patches            | ../kumi-tools                                                |
| Supabase PostgreSQL migrations 與 SQL tests | ../kumi-brain-sql                                            |
| 現有應用 API                                | ../kumi-app，TypeScript；不是預計的 Spring Boot care backend |
| 舊 GCP／Terraform                           | ../kumi-infra，歷史參考                                      |

現有資料庫是 Supabase Free；下一階段沿用，不另建 VPS PostgreSQL。 Caddy／Hermes 維持原有服務，MQTT 部署資料見
[部署指南](../deploy/mosquitto/README.md)。

## 維護中的文件

- 本文件：目前進度、所有權與下一步。
- [ARCHITECTURE.md](ARCHITECTURE.md)：資料流、技術決策與可靠度邊界。
- [ACCEPTANCE.md](ACCEPTANCE.md)：驗收方法、結果與尚未驗證的範圍。
- [SPRING_BOOT_HANDOFF.md](SPRING_BOOT_HANDOFF.md)：下一階段的契約、設定與任務拆分。

使用者已授權 Spring Boot integration 與 VPS 部署。後端進度見
[`kumi-center`](../../kumi-center/README.md)；上述實體可靠性限制仍保留。
