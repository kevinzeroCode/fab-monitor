# fab-monitor Runbook

告警響了就從這裡開始。指令都在 WSL 專案根目錄執行，並明確指定 `--context kind-fab`，避免操作到另一個叢集。PromQL 貼到 <http://localhost:9090/query>。

驗證狀態：實際演練紀錄見 [Lab 04](labs/04-observability-slo.md)。`ToolApiSlowResponses`、`ToolApiPodNotReady` 只確認過規則載入，以下步驟未經實際故障驗證。

**判斷恢復的共同原則**：告警消失不等於恢復。SLI 在沒有流量時是 NaN，告警也會消失，所以要在有流量時確認，見 [docs/slo.md](slo.md#nan沒有資料不等於健康)。

## ToolApiHighErrorRate

**意思**：最近 5 分鐘，資料端點的可用性低於 99%。

### 可能原因

| 原因 | 線索 |
|---|---|
| chaos 沒有清除 | 500 平均分布在各 Pod，`/health` 正常 |
| Redis 出問題 | 回 503，通常也會有 `ToolApiPodNotReady` |
| 新版本有 bug | 問題從部署時間開始，日誌有錯誤 |

### 怎麼查

**1. 看是哪種錯誤、哪個 route：**

```promql
sum by (code, route) (rate(http_requests_total{namespace="fab", job="tool-api", route=~"/api/tools.*"}[5m]))
```

- `500` → chaos 或程式錯誤
- `503` → Redis 無法使用

**2. 看是不是只有特定 Pod：**

```promql
sum by (pod) (rate(http_requests_total{namespace="fab", job="tool-api", route=~"/api/tools.*", code=~"5.."}[5m]))
```

- 只有一個 Pod 有錯 → 看那個 Pod（chaos 只設到一個 Pod 也會這樣）
- 全部 Pod 都有錯 → 共同依賴（Redis）或版本問題

**3. 看日誌和最近的部署：**

```bash
kubectl --context kind-fab -n fab logs -l app=tool-api --since=10m --tail=50
kubectl --context kind-fab -n fab rollout history deployment/tool-api
```

### 怎麼處理

| 原因 | 處理 |
|---|---|
| chaos 沒清除 | `make chaos-clear` |
| Redis 出問題 | 見 [ToolApiPodNotReady](#toolapipodnotready) |
| 新版本有 bug | `make rollback` |

### 確認恢復

5xx 的速率先歸零，**告警要再等約 5 分鐘才解除**，因為舊錯誤要滑出 5 分鐘窗。實測從清除到解除是 5 分 06 秒。這段期間不代表處理失敗。

## ToolApiSlowResponses

**意思**：最近 5 分鐘，資料端點 300ms 內完成的比例低於 95%。

### 可能原因

| 原因 | 線索 |
|---|---|
| chaos 延遲沒有清除 | 延遲穩定多出約 500ms |
| 流量超過容量 | RPS 明顯上升，CPU 碰到 limit（500m） |
| Redis 變慢 | 所有 Pod 同時變慢 |

### 怎麼查

**1. 看 p95 和流量：**

```promql
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{namespace="fab", job="tool-api", route=~"/api/tools.*"}[5m])))
```

```promql
sum(rate(http_requests_total{namespace="fab", job="tool-api", route=~"/api/tools.*"}[5m]))
```

**2. 看是不是只有特定 Pod：**

```promql
histogram_quantile(0.95, sum by (le, pod) (rate(http_request_duration_seconds_bucket{namespace="fab", job="tool-api", route=~"/api/tools.*"}[5m])))
```

**3. 量一下 Redis 回應時間：**

```bash
kubectl --context kind-fab -n fab exec deployment/redis -- redis-cli --latency
```

這個指令會一直跑，按 Ctrl+C 結束。

### 怎麼處理

| 原因 | 處理 |
|---|---|
| chaos 沒清除 | `make chaos-clear` |
| 流量超過容量 | `make scale N=3`，之後再評估 CPU limit |
| 單一 Pod 異常 | `kubectl --context kind-fab -n fab delete pod <pod>`，Deployment 會補一個新的 |

### 確認恢復

和錯誤率相同，要等舊的慢請求滑出 5 分鐘窗。

## ToolApiPodNotReady

**意思**：有 tool-api Pod 超過 2 分鐘不 Ready，已被移出 Service。

### 可能原因

| 原因 | 線索 |
|---|---|
| Redis 無法連線 | 全部 API Pod 同時不 Ready，`/ready` 回 503 |
| 新版本啟動失敗 | 只有新 Pod 不 Ready，rollout 卡住 |
| 節點或資源問題 | Pod Pending，或集中在同一節點 |

### 怎麼查

**1. 看 Pod 和 Redis：**

```bash
kubectl --context kind-fab -n fab get pods -o wide
```

**2. 看不 Ready 的原因：**

```bash
kubectl --context kind-fab -n fab describe pod <pod>
```

看最下面的 Events，以及 Conditions、Last State。

**3. 確認 Service 還剩幾個端點：**

```bash
kubectl --context kind-fab -n fab get endpoints tool-api
```

全部 Pod 都不 Ready 時，端點是空的，使用者會連不上，而且流量進不到 API，SLI 可能變 NaN，所以**不要因為 `ToolApiHighErrorRate` 沒響就以為沒事**。

**4. 看資料年齡：**

```promql
max(tool_data_age_seconds{namespace="fab", job="tool-api"})
```

NaN 代表所有 API 都讀不到 Redis，或 Redis 裡沒有機台資料。

### 怎麼處理

| 原因 | 處理 |
|---|---|
| Redis 被 scale 到 0 或無法運作 | `make fix-redis` |
| 新版本啟動失敗 | `make rollback`。K8s 不會自動回滾 |
| Pod Pending | `describe` 看 Events，通常是資源不足 |

### 確認恢復

```bash
kubectl --context kind-fab -n fab get pods -l app=tool-api
curl -s -o /dev/null -w '%{http_code}\n' localhost:30080/ready
```

Pod 都是 `1/1 Running`，`/ready` 回 200。Redis 重啟過的話資料會清空（沒有持久化），要確認 simulator 也還活著，機台資料才會回來。

## ToolDataStale

**意思**：所有 API 副本回報的最大資料年齡超過 30 秒，並持續 2 分鐘。**此時 API 可能仍回 200**，可用性 SLI 也可能是 100%。

沿著資料流，從源頭往下查：

```
simulator ──寫入──→ Redis ──讀取──→ tool-api ──→ /metrics ──→ 告警
```

**判斷原則：哪一站開始「不是新的」，問題就在那一站和前一站之間。**

### 第 1 站：simulator 還活著嗎？

```bash
kubectl --context kind-fab -n fab get deployment tool-simulator
kubectl --context kind-fab -n fab get pods -l app=tool-simulator
```

deployment 本身沒有 `app` label，要分兩行查，第一行才看得到期望的副本數。

| 看到 | 判斷 |
|---|---|
| `1/1 Running`，RESTARTS 沒有增加 | 正常，往第 2 站 |
| deployment 是 `0/0`，沒有 Pod | **異常**：被 scale 到 0 |
| `CrashLoopBackOff` 或 RESTARTS 持續增加 | **異常**：程式啟動後就退出 |
| `ImagePullBackOff`、`ErrImageNeverPull` | **異常**：節點上沒有這個 image |

### 第 2 站：simulator 寫得進 Redis 嗎？

```bash
kubectl --context kind-fab -n fab logs deployment/tool-simulator --since=5m
```

一定要加 `--since`。simulator 平常不寫 log，**只有失敗才寫**，不加的話可能看到幾天前的舊警告。

```promql
time() - max(simulator_last_tick_timestamp_seconds{namespace="fab", job="tool-simulator"} > 0)
```

| 看到 | 判斷 |
|---|---|
| log 沒有內容，且 PromQL 約 20 秒內 | 正常（抓取間隔 15 秒，不會剛好是 2 秒） |
| log 有 `Redis unavailable; retrying next tick` | **異常**：連不上 Redis |
| PromQL 持續上升 | **異常**：simulator 沒有成功寫入 |
| PromQL 是空的 | 沒有 target：simulator 不在，回第 1 站。空的不代表健康 |
| `logs` 回 `timed out waiting for the condition` | 沒有 Pod 可讀 log（實測 scale 到 0 時會這樣），回第 1 站，不是 log 服務壞掉 |

### 第 3 站：Redis 裡的資料新不新？

```bash
kubectl --context kind-fab -n fab exec deployment/redis -- redis-cli HGET tool:ETCH-01 updated_at
date -u +%FT%TZ
```

兩行都是 UTC，直接相減。

| 看到 | 判斷 |
|---|---|
| 差距幾秒內，再跑一次時間有前進 | 正常，資料還在更新 |
| 時間不前進 | **異常**：寫入端（第 1、2 站）有問題 |
| 空白 | **異常**：Redis 裡沒有這台機台，看 `redis-cli DBSIZE`（正常是 12） |
| `error: unable to upgrade connection` 或指令失敗 | **異常**：Redis 本身有問題 |

### 第 4 站：API 讀到的新不新？

```bash
curl -s localhost:30080/api/tools/ETCH-01 | python3 -m json.tool | grep updated_at
date -u +%FT%TZ
```

也可以看 Grafana 的「最舊機台資料年齡」面板，或查：

```promql
max(tool_data_age_seconds{namespace="fab", job="tool-api"})
```

| 看到 | 判斷 |
|---|---|
| 和第 3 站一樣新，年齡幾秒內 | 正常，告警應該即將解除 |
| 第 3 站是新的，這裡是舊的 | **異常**：問題在 API 端 |
| 回 200，但 `updated_at` 不前進 | 和第 3 站一樣舊：問題在上游，不在 API |

### 怎麼處理

| 查到的原因 | 處理 |
|---|---|
| simulator 被 scale 到 0 | `kubectl --context kind-fab -n fab scale deployment/tool-simulator --replicas=1` |
| `CrashLoopBackOff` | 先看原因（下方），再決定修設定或回滾 |
| 找不到 image | `make load-images VERSION=v3`，再 `rollout restart` |
| 連不上 Redis | 見 [ToolApiPodNotReady](#toolapipodnotready)，`make fix-redis` |
| simulator 活著但不寫入 | `kubectl --context kind-fab -n fab rollout restart deployment/tool-simulator` |
| Redis 新、API 舊 | 確認 API 的 `REDIS_URL`，再 `rollout restart deployment/tool-api` |

**`CrashLoopBackOff` 怎麼查**：

```bash
kubectl --context kind-fab -n fab logs deployment/tool-simulator --previous
kubectl --context kind-fab -n fab describe pod -l app=tool-simulator
```

`--previous` 看上一次崩潰的 log，`describe` 看 Last State：`OOMKilled` 代表記憶體超過 limit（128Mi）；`Error` 代表程式退出，例如 `TOOL_COUNT` 設定錯誤會出現 `ValueError`。修好設定後重新套用；是新版本造成的就 `kubectl --context kind-fab -n fab rollout undo deployment/tool-simulator`。

### 確認恢復

simulator 第一次成功寫入後，資料年齡會降到幾秒內，告警在下一次評估就解除。這條告警不用等 5 分鐘窗，因為它看的是當下的年齡，不是 5 分鐘平均。
