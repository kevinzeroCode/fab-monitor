# fab-monitor SLI / SLO

這份文件定義 tool-api 對使用者的可靠度目標，以及告警如何對應這些目標。這是學習專案：**SLO 數字是設計目標，不是量測成果**。Prometheus 只保留 6 小時，也沒有累積過 30 天資料。

## SLI、SLO、SLA 的差別

| 名詞 | 是什麼 | 在這個專案 |
|---|---|---|
| SLI（指標） | 實際量到的服務品質，通常是「好事件 ÷ 全部事件」 | 非 5xx 的請求比例 |
| SLO（目標） | 團隊自己訂的 SLI 目標 | 30 天內 99% 的請求非 5xx |
| SLA（合約） | 對客戶的承諾，違反有賠償等後果 | 沒有，內部練習不對外承諾 |

三者的關係是：用 SLI 量，用 SLO 判斷好不好，SLA 則是對外的承諾。實務上 SLA 通常比內部 SLO 寬鬆，留出緩衝。

## SLI 與 SLO 定義

| SLI | 定義 | SLO |
|---|---|---|
| 可用性 | 資料端點中，非 5xx 的請求比例 | 99% |
| 延遲 | 資料端點中，300ms 內完成的請求比例 | 95% |

**資料端點**是 `route=~"/api/tools.*"`，也就是 `/api/tools` 和 `/api/tools/{id}`。SPEC 原本寫 `/api/*`，這裡收窄並排除：

| route | 排除原因 |
|---|---|
| `/api/chaos` | 控制端點，不是使用者要拿資料的請求；而且演練時必須能在故障中清除注入 |
| `/api/whoami` | 網頁每秒輪詢一次，用來展示分流。算進來會稀釋 SLI，讓資料端點的問題看起來比實際輕微 |
| `/health`、`/ready`、`/metrics`、`/` | 探針、Prometheus 抓取和靜態網頁，不是資料請求 |

404 算好事件：機台不存在是用戶端的問題，服務本身運作正常。

## Error budget

Error budget 是 SLO 允許的失敗額度，也就是 `1 - SLO`。它讓「要不要繼續上新功能」有數字可依：額度還夠就可以承擔風險，快用完就優先處理穩定性。

**30 天 99% 可用性**：

```
30 天 = 30 × 24 = 720 小時
720 小時 × (1 - 0.99) = 7.2 小時
```

7.2 小時的意思是「服務完全中斷」時可以撐多久。SLI 是用請求算的，所以部分失敗消耗得比較慢：

| 情境 | 額度用完的時間 | 算式 |
|---|---|---|
| 100% 請求失敗 | 7.2 小時 | 720 × 0.01 ÷ 1 |
| 20% 請求失敗（`make chaos-errors`） | 36 小時 | 720 × 0.01 ÷ 0.2 |

以 20 RPS 持續 30 天計算，總共約 5,184 萬筆請求，99% 的額度約是 51.8 萬筆失敗。

延遲 SLO 同理：30 天內最多 5% 的請求可以超過 300ms。

## 這個 lab 的告警為什麼用短時間窗

正式環境通常用 30 天額度的燃燒速率，搭配長短兩個時間窗來告警。這個 lab 為了能在幾分鐘內觀察到觸發和解除，改用 **5 分鐘窗**：

| 告警 | 條件 | `for` |
|---|---|---|
| `ToolApiHighErrorRate` | 5 分鐘可用性 < 99% | 1m |
| `ToolApiSlowResponses` | 5 分鐘內 300ms 達成率 < 95% | 1m |
| `ToolApiPodNotReady` | 有 tool-api Pod 不 Ready | 2m |
| `ToolDataStale` | 所有 API 副本回報的最大資料年齡 > 30 秒 | 2m |

短窗告警**不等於 30 天 SLO 被違反**。Grafana 的 budget 面板是 5 分鐘示範值，公式是 `1 - 錯誤比例 / 0.01`，所以可能變成負數，也不是正式的 30 天額度。

## HTTP 200 不代表資料新鮮

simulator 停止時，Redis 裡還有舊資料，API 照樣回 200，可用性 SLI 維持 100%。因此資料新鮮度要獨立監控。

`tool_data_age_seconds` 只衡量**新鮮度**，也就是資料多久沒更新，不涵蓋所有資料正確性。例如數值錯誤但時間是新的，它就看不出來。

**30 秒 + 持續 2 分鐘是告警門檻，不是 SLO。** 兩者的差別：

| | 可用性 / 延遲 SLO | `ToolDataStale` 門檻 |
|---|---|---|
| 回答的問題 | 長期來看服務達標嗎？ | 現在要不要叫人處理？ |
| 時間範圍 | 30 天 | 當下，連續 2 分鐘 |
| 有沒有 error budget | 有 | 沒有 |
| 超過代表什麼 | 這段期間未達標 | 有事故要排查 |

這個專案沒有對資料新鮮度做 30 天的承諾。

## 為什麼新鮮度不放進 readiness

資料變舊是 simulator 的問題，不是 API 的問題。如果放進 readiness：

1. 資料一變舊，兩個 API Pod 同時 not Ready
2. 兩個 Pod 都被移出 Service，使用者連舊資料都看不到
3. simulator 還是沒有被修好

原本是「資料舊了但看得到」，反而變成「完全沒有服務」。原則是：**K8s 的自動處理只用在它修得好的地方，其他交給告警通知人。** 移出 Service 修不好 simulator，所以這件事交給 `ToolDataStale`。

## 為什麼告警看 API 端的 `tool_data_age_seconds`

simulator 掛掉時，它自己的 `/metrics` 也會跟著消失，Prometheus 只會看到沒資料。如果告警寫成看 `simulator_last_tick_timestamp_seconds`，條件沒有資料可算，告警就不會觸發：**最需要告警的時候，它反而沉默**。

API 端指標在每次 scrape 時從 Redis 讀 `updated_at` 計算，不管 simulator 在不在都有值，simulator 停止後這個值會持續上升。它量的也是使用者實際拿到的資料有多舊。

`simulator_last_tick_timestamp_seconds` 仍然保留，用來排查：確認是 simulator 沒有寫入，而不是 API 讀取有問題。

## NaN：沒有資料不等於健康

Prometheus 裡 NaN 跟任何數字比較都是 false，所以條件式告警**不會**因 NaN 觸發。這有兩個影響。

### 沒有流量時，SLI 是 NaN

SLI 是 `好事件速率 ÷ 全部事件速率`。沒有流量時是 `0 ÷ 0 = NaN`，`NaN < 0.99` 為 false，告警不會觸發。

因此：

- 演練時要開 `make load`，否則 chaos 注入了也沒有請求會失敗
- **壓測若在告警解除前結束，SLI 會變成 NaN，告警也會消失，但那不是真的恢復。** 判斷恢復要在有流量的情況下看

### Redis 讀不到時，資料年齡是 NaN

`tool_data_age_seconds` 在 Redis 讀取失敗時輸出 NaN，不會假裝是 0 秒，也不會沿用上次的值。`ToolDataStale` 因此不觸發，這種情況由 `ToolApiPodNotReady` 負責：`/ready` 失敗後 Pod 變成 not Ready。兩條告警分工不重疊。

`max()` 會略過 NaN：只有一個副本讀不到時，仍會用另一個副本的值；全部都讀不到時，結果才是 NaN。

| 狀況 | 資料年齡 | `/ready` | 哪條告警負責 |
|---|---|---|---|
| simulator 停止，Redis 正常 | 持續上升 | 200 | `ToolDataStale` |
| Redis 無法連線 | NaN | 503 | `ToolApiPodNotReady` |
| Redis 正常但沒有機台資料 | NaN | 200 | **沒有（已知缺口）** |

### 已知缺口

Redis 連得上但 `tool:*` 是空的時（例如 Redis 重啟且 simulator 同時停止；Redis 沒有持久化），資料年齡是 NaN、`/ready` 回 200，`ToolDataStale` 和 `ToolApiPodNotReady` 都不會觸發，而 `/api/tools` 回 200 和空陣列。

這個專案不新增第五條告警，只記錄這個缺口。正式環境可以偵測 NaN（`tool_data_age_seconds != tool_data_age_seconds`）或對機台數量告警來補上。這裡不能用 `absent()`，因為 NaN 仍是一筆存在的資料。
