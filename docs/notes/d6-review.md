# fab-monitor 進度交接（D6 面試演練完成 → 開始 D7）

交接日期：2026-10-28。以下保留使用者提供的 D6 複習與後續要求；此紀錄不是重開機後的即時叢集驗收。

## D6 交接當時的狀態（最新接續點見文件末尾）

- Phase 1（Docker）、Phase 2（Kubernetes）完成，lab 01–03 都已跑過。
- D6 做了面試演練：用問答方式複習 D2–D5 的觀念（紀錄在下方）。
- **Phase 3 尚未開始實作或安裝**；本次先更新規格與交接文件。
- 電腦重開機過，kind 叢集 `fab` 目前的狀態未確認。

## 我的學習方式（請照這個方式帶我）

- 繁體中文。我是初學者，操作做得出來，但需要理解背後原因。
- 每個設定檔都要有中文註解，說明「為什麼這樣寫」。
- 一次給我一個步驟，跑完、確認結果，再給下一步。不要一次丟很多指令。
- 每個主題最後給一段「面試時怎麼回答」。
- 我答錯時用具體情境解釋。

## D6 面試演練紀錄

| 主題 | 我的理解 | 需要補強的地方 |
|---|---|---|
| 專案介紹 | simulator 寫入 Redis，API 讀取後提供網頁和 REST API | 要能用 1 分鐘完整講出資料流、為什麼兩個 replica、能扛和不能扛的故障 |
| 兩個 API replica | 兩個 Pod 同時接流量，不是備份；一個掛了 Service 導到另一個；配合 `maxUnavailable: 0` 可以不中斷更新 | 扛不扛得住 node 故障，要看兩個 Pod 是否在不同 node |
| 無法處理的故障 | Redis 是單點故障；simulator 停掉時 API 回 200 但資料不更新；程式 bug 多開 replica 沒用 | — |
| Redis 單點故障怎麼改善 | 不能直接把 Redis Deployment 改成 2 個 replica，兩個 Redis 資料不同步 | 正確做法：主從複製 + Sentinel 自動切換、用 StatefulSet 或 Operator 部署；持久化（PV + AOF）解決的是資料遺失，不是停機 |
| Stateless vs Stateful | API 不存資料可直接擴展；Redis 要處理同步 | 判斷能否加 replica 要問兩件事：狀態是否存在自己身上、多個副本會不會重複做同一件事（simulator 開兩個會重複更新機台，所以只能一個） |
| 滾動更新時新版本 readiness 一直不過 | 新 Pod 不會被加進 Service，舊 Pod 不會被關，更新卡住，使用者不受影響 | ⚠️ **K8s 不會自動 rollback**，超過 `progressDeadlineSeconds` 只會標記失敗，要人工 `rollout undo` 或由 CI/CD 處理 |
| `maxSurge: 0`、`maxUnavailable: 1` | 新版本壞掉時只剩 1 個 v1 服務，能力減半 | ⚠️ `maxUnavailable` 關掉的是**正常的舊 Pod**，不是壞掉的 Pod |
| ConfigMap 修改後不生效 | 環境變數在容器啟動時注入 | ⚠️ **不用重新 build image**，`kubectl rollout restart` 讓 Pod 重建就好；同 tag 重新 apply 不會觸發任何變化；Kustomize `configMapGenerator` 可讓設定變更自動觸發滾動更新 |
| Redis 檢查放在 liveness 會怎樣 | 原本不確定，經解釋後理解 | Redis 掛掉 → 所有 API 被重啟 → CrashLoopBackOff，Redis 恢復後還要等重啟間隔，拖長恢復時間。原則：liveness 只放重啟能修好的問題，外部依賴放 readiness |
| 程式卡死（死結） | 靠 liveness 重啟 | readiness 也會一起失敗，兩者一起運作；啟動慢的服務要用 startupProbe |

原交接寫「標 ⚠️ 的三題請在適當時機再考我一次」；表格實際標了四列，後續均列入複習，一次問一題。

### 精確性補充（保留原紀錄，避免記成絕對保證）

- `maxUnavailable: 0` 能在新 Pod 一直未就緒時保留原本可用副本，但無法保證原副本或節點不會另行故障，也不保證請求完全不中斷。
- `maxUnavailable: 1` 是允許更新時少一個可用副本，不是專門挑「壞掉的 Pod」刪除；兩副本情境可能先減少一個健康舊副本。剩一個副本不代表實際吞吐量在所有情況都精確減半。
- 同 image tag 本身不決定是否更新；Deployment 的 Pod template 有變才觸發 rollout。只改透過環境變數引用的既有 ConfigMap，Pod template 沒變時，不會自動重建；不用重新 build。configMapGenerator 搭配雜湊名稱與 Kustomize 引用，才會把設定變更反映到 Pod template。
- 將外部依賴放在 liveness，會因依賴故障造成反覆重啟；是否出現 `CrashLoopBackOff` 及退避時間取決於重啟時序與設定，不是每次 liveness 失敗都立刻發生。參考 [Kubernetes Pod lifecycle](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/)。
- Redis 複製、Sentinel 與 client 的主節點探索須共同設計；StatefulSet 只提供身分等管理能力，不能單獨讓 Redis 自動同步或切換。參考 [Redis Sentinel](https://redis.io/docs/latest/operate/oss_and_stack/management/sentinel/)。

## 請你接著做的事

### 1. 先確認環境

一步一步確認 kind 叢集 `fab`、namespace `fab` 裡的 Pod，以及 `http://localhost:30080/ready`。如果叢集不見了，用 Makefile 重建，並確認回到 v1、API 2 個 Pod。先檢查，不直接刪除現有叢集。

### 2. README 補一節「已知限制與改進方向」

記錄 Redis 單點故障與改善方式、simulator 只能單一實例的原因、readiness 只檢查 Redis 連線不檢查資料新鮮度。**只寫文件，不要實作改善。**

### 3. 開始 Phase 3，拆成兩個階段，中間停下來等我確認

- **Phase 3a（D7）**：安裝 kube-prometheus-stack、ServiceMonitor、loadgen、Grafana dashboard，以及新增指標與對應 pytest。
- **Phase 3b（D8）**：`docs/slo.md`、PrometheusRule、告警、`docs/runbook.md`、chaos 觸發告警的驗收。
- 即使同意繼續 D7、D8，仍保留 D7 驗收後的明確確認點。

### 4. SPEC 修訂：Phase 3 加入「資料新鮮度」監控

D5 發現 simulator 停掉時 API 仍回 200，但資料已經不更新，因此使用者明確核准加入原 SPEC 沒有的範圍：

- **指標**：tool-api 在 `/metrics` 提供 `tool_data_age_seconds`（所有機台中 `updated_at` 距今最久的秒數）；simulator 提供 `simulator_last_tick_timestamp_seconds`。
- **告警**：`ToolDataStale`，資料超過 30 秒沒更新、持續 2 分鐘觸發，附 runbook 段落。
- **Grafana**：dashboard 加一個資料新鮮度面板。
- **Lab 步驟（D8）**：simulator scale 到 0 → 確認 API 仍回 200 → 觀察告警觸發 → scale 回 1 → 告警解除。
- **`docs/slo.md`（D8）**：說明為什麼「HTTP 200」和「資料正確」要分開監控；本次新指標具體衡量的是新鮮度，不涵蓋所有資料正確性。
- 補上對應 pytest，並同步更新 `SPEC.md` 的 Phase 3 段落。

## 本次接續紀錄

- 已完整閱讀 SPEC、README、Lab 01–03，再保存本交接。
- 已在 README 補上已知限制；SPEC 已記錄 D7／D8 停止點、新鮮度定義、告警、面板、測試與驗收要求。
- 下一個使用者操作：在 WSL 專案根目錄執行 `kind get clusters`，先確認叢集是否仍存在；之後逐步核對節點、Pod、版本與 readiness。
- D7 實作與監控安裝待環境確認，不能把規格更新視為功能已完成。

### D7 接續（2026-10-28）

- 使用者已回報 fab 與 kuberay-dev 均存在；本次只操作 kind-fab，不刪除其他叢集。
- fab 三節點 Ready／v1.34.11；fab 的 Redis／simulator 各 1、API 兩個 Ready Pod 分散於 worker；兩個 API image 與 APP_VERSION 均 v1，NodePort ready 回 200。
- Helm v3.22.0；WSL total 15 GiB、available 9.1 GiB，允許繼續準備省資源監控。
- D7 新鮮度程式、測試、壓測工具、固定 chart values、ServiceMonitor、十面板 dashboard 與 Lab 04 已準備；仍未在使用者叢集安裝或驗收。
- D7 用新的 v3 tag 和獨立 overlay；D5 原本 v1 base 保留。make load 指令有歷史同名，明確傳 S 才壓測，否則匯入 image；內部 rollout 改用 load-images。
- WSL 專用 .cache/venv-wsl 已建立、固定依賴安裝完成；使用者提供 Python 3.12.3／pytest 8.3.5 的完整輸出，54 passed in 10.45s。
- 下一步：make build VERSION=v3，再逐步 load／deploy v3、渲染 Helm、安裝與看圖。每次只給使用者一個步驟；測試通過尚不代表 image 建置或監控安裝成功。
- D8 尚未開始，保留 D7 實際驗收後的確認點。

### D7 部署與安裝排錯

- 使用者已完成 v3 build、load-images 與 deploy-observability；API／simulator rollout 成功，whoami 回 v3，新鮮度指標約 1.37 秒。
- Helm 安裝逾時。Operator ContainerCreating 的 Events 為 FailedMount，fab-monitor-admission Secret 不存在；已關閉 webhook／憑證 Job 卻漏關 Operator TLS。values 已補 tls.enabled=false，靜態回歸由失敗轉通過，尚待重新渲染與套用確認。
- Grafana 1/2 CrashLoopBackOff、kube-state-metrics 1/1。下一個使用者步驟是讀 grafana 容器的 previous logs，再依證據修復，不直接重跑十分鐘安裝。
- 監控安裝、自訂 ServiceMonitor／dashboard、targets、Grafana 與壓測尚未驗收；D8 不開始。

- Grafana describe 後確認：主容器 Ready、Restart Count=0，grafana-sc-dashboard 的 Last State=OOMKilled／137，limit=96Mi。已將 sidecar request 改 64Mi、limit 改 256Mi；合計監控 requests=560Mi、limits=1632Mi。尚未套用，不能宣稱已修復 runtime。
- 接下來先重新 Helm template，驗證 Operator 移除憑證掛載、Grafana sidecar 的資源已變；再用 make monitoring 更新既有 release，逐步確認 Ready／重啟次數是否穩定。
- 修正後的 Helm render 產物已實際檢查通過：Operator 不再掛載 tls-secret 或使用憑證參數，probes 使用 HTTP；dashboard sidecar request=64Mi、limit=256Mi。下一步是 make monitoring 套用，runtime 恢復仍待驗證。
- 使用者已重新 make monitoring 成功：CRD Established、dashboard ConfigMap 與兩個 ServiceMonitor created、Prometheus StatefulSet ready。get pods 顯示四個監控 Pod 全部 Ready／Running／RESTARTS=0，新 Pod 已約 10–11 分鐘，兩項啟動問題目前解除。
- 下一步 make prometheus 保持 port-forward，開 localhost:9090 確認兩個 API 與一個 simulator 的 targets；之後逐條 PromQL、Grafana 與固定 RPS 驗收。D7 未完成，不開始 D8。

### D7 查詢與 Grafana 主容器 OOM

- 使用者 targets 截圖確認 API 2/2 UP、simulator 1/1 UP；PromQL 資料年齡約 0.911857 秒，距最後成功 tick 約 6.14 秒。
- 使用者已成功登入 Grafana，尚未完成 dashboard 與壓測驗收。
- 22:15:54 port-forward 連 Pod 內 3000 被拒絕並中斷。describe 確認 grafana 主容器 22:15:53 OOMKilled／137、22:15:55 重啟、Restart Count=1，limit=256Mi；sidecar Running／Ready、Restart Count=0。
- 已只調整主容器 memory request 128→256Mi、limit 256→512Mi；監控宣告總量改為 requests 688Mi、limits 1888Mi，靜態整合檢查通過。尚未套用，不宣稱 runtime 修復。
- 下一步讓使用者 make monitoring 套用，成功後重新 make grafana（舊 port-forward 已中斷），操作 dashboard 並觀察新 Pod 的重啟數與資源。D7 尚未完成，D8 不開始。

### D7 首輪壓測

- 512Mi 修正已由使用者 make monitoring 成功套用，Helm release revision 3／deployed；使用者再次登入 Grafana 並提供 dashboard 截圖。操作後的 Grafana 重啟數尚待核對。
- 首輪 20 RPS／60 秒：scheduled 1200、sent 1199、dropped 1、achieved_send_rps 19.983、HTTP 200 共 1199、network_errors 0、平均延遲 14.93ms、最大 185.22ms。make exit 1 是 loadgen 對任何 dropped 的嚴格規則，不是 API 請求出錯。
- 圖表顯示 5 分鐘平均約 3–4 RPS，兩個 API Pod 有分流，5xx 比例零、histogram p95 約 20ms。新鮮度與機台狀態曲線可見；SLI／budget 流量後結果尚待核對。
- 尚不能標記精確發送驗收通過；先相同條件重跑一次確認單筆 dropped 是否重現。若重現再依排程證據診斷，不猜 API 容量、不刪失敗紀錄、不直接放寬退出規則。D7 未結束，不進 D8。

### D7 第二輪與發送診斷（最新接續點）

- 第二輪相同 20 RPS／60 秒再次 sent=1199、dropped=1、全 200、network_errors=0，mean_latency_ms=14.511、max_latency_ms=135.494、elapsed_seconds=60.002。兩輪 2398 個實際回應全成功，但發送完整度仍未通過。
- tools/loadgen.py 已新增 drop_reasons（三類略過原因）及 first_drop（第一個時槽、排定時間、遲到毫秒、在途數），沒有放寬速率／並發／退出條件。新增兩個排程情境並擴充既有測試；現有代理套件環境 12 項 loadgen 測試通過、Python 3.12 編譯通過，固定環境完整 pytest 待收尾再執行。
- 本機 loopback 三秒／20 RPS 為 60/60、零 dropped；未重現使用者情況，不能先斷言冷啟動或 WSL 原因。
- 下一個使用者步驟：原參數跑帶診斷的新工具，貼出 drop_reasons 與 first_drop；依結果定位再修復，不第三次盲猜或改 API 副本。之後仍須核對 Grafana 操作後重啟數、SLI／budget，完成 D7 才等使用者同意 D8。

### D7 第三輪診斷與初始化計時修正（最新接續點）

- 診斷回報 schedule_late=1；第 2 筆排定在 50ms，遲到 65.749ms，在途 1 筆；並發達上限及期限結束都是 0。實際 1199 筆全 200、network_errors=0、mean_latency_ms=15.135、max_latency_ms=132.593、elapsed_seconds=60。
- 問題定位在初期第二時槽；核對官方固定版本程式確有 HTTP backend 懶初始化。已在測試模擬首次準備 120ms，原版 3/4 送出，改先準備再計時後為 4/4；這不是對使用者現場初始化耗時的直接量測。
- tools/loadgen.py 已以公開 anyio.sleep(0) 先準備 async backend，再開始 clock 計時，新增 preparation_seconds；沒有額外 HTTP 請求，也保留三種 dropped 原因、並發限制與嚴格退出。requirements-dev.in 明列已安裝／已鎖定的 anyio 4.9.0，不需升級套件或重建 image。
- Windows Python 3.12.13 配合使用者 WSL venv 的純 Python 固定工具依賴實跑 21 項 tools 測試通過，獨立唯讀審查沒有具體問題。Windows loopback 新舊都未漏發，仍需 WSL 原條件驗證；目前不能稱現場根因完全確定或問題已解除。
- 下一步：使用者原命令 make load S=60 RPS=20 PYTHON=.cache/venv-wsl/bin/python，觀察 preparation_seconds、sent、dropped、first_drop。若零 dropped，再驗證完整 pytest（預期 57）及 Grafana 重啟數／SLI／budget，完成 D7 後等確認才進 D8。

### D7 壓測回歸成功（最新接續點）

- 使用者以 WSL 原參數回報：preparation_seconds=0.1、elapsed_seconds=60.001、scheduled=1200、sent=1200、dropped=0、三類略過均零、first_drop=null、achieved_send_rps=20.0、HTTP 200 共 1200、network_errors=0、mean_latency_ms=15.155、max_latency_ms=61.215，make 沒有再報 Error。
- 固定速率壓測驗收通過；準備耗時也支持初期初始化占用排程時槽的分析。保留前幾輪紀錄，不需再次重跑已通過的壓測。
- 下一步只給 make test PYTHON=.cache/venv-wsl/bin/python，完整新測試預期 57（原 54 加兩個 drop 診斷案例及一個初始化回歸）。之後確認 Grafana 新 Pod 操作後仍 Ready／重啟數不增，及有流量後 SLI／budget 面板的實際值，再整理 D7 面試說法並停下等 D8 確認。

### D7 驗收完成／等待 D8 確認（最新接續點）

- 使用者在 WSL Python 3.12.3／pytest 8.3.5 執行完整測試，57 passed in 12.48s。
- 壓測固定 20 RPS／60 秒，1200/1200 全 200、零略過、零網路錯誤；平均 15.155ms、最大 61.215ms，準備耗時 0.1 秒另計。
- Grafana Pod fab-grafana-8688786d4-gfs7m 經開圖表與壓測後，AGE=89m、READY=2/2、STATUS=Running、RESTARTS=0；先前 main／sidecar 記憶體修正通過目前操作驗證。
- Targets：兩個 API、一個 simulator 均 UP。機台數與資料新鮮度曲線已看過；RPS 四個波峰是數次壓測的歷史，兩個 API 都有接流量。
- 最近 15 分鐘已無流量時，SLI 的 99%／95% 線是目標，不能直接當實際值；改看三小時找到事件時段。代理透過已開的 localhost:9090 Prometheus HTTP API 唯讀核對 dashboard 同一組查詢：各 578 個時點、91 個有限值，三個面板在有效樣本時都是 1.0（100%）；其他時點為未定義值，不當成成功。這是五分鐘示範，不是 30 天 SLO 成果。
- D7／Phase 3a 已完成。README、Lab 04、規格與本交接已同步；保留失敗與修復歷史。**此處停下，等使用者明確確認才進 D8／Phase 3b。**
- D8 範圍：docs/slo.md、recording rules、四個告警含 ToolDataStale、docs/runbook.md、所有 API Pod 的 chaos 控制與故障觸發／恢復驗收；目前均未實作。使用者若尚未確認，可繼續 D7 口頭複習，不自動進 D8。
