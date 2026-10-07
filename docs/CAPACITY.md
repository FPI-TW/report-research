# 硬體用量量測（上雲選型的證據基礎）

這份文件回答一件事：**要把這套服務搬上雲，該買多大的機器**——而且答案要來自實測，
不是估算。既有的三份成本評估簡報（`AWS上雲架構與完整成本評估_正式版_2026-08-10` 等，
未入版控）算的是 **token 成本與架構**，那一側已經有數字；缺的是**硬體那一側**：
vCPU、RAM、磁碟、IOPS 到底要多少，repo 裡從來沒有任何工具在量。

在此之前唯一與「用量」沾邊的東西是 `qa_log.latency_ms` 與程式碼註解裡的點狀實測
（「rerank 50 對 ~34s」）。**延遲不等於用量**：一次 34 秒的 rerank 到底吃掉幾顆核心、
峰值多少 RSS，決定的是要買 2 vCPU 還是 8 vCPU，而那個數字過去完全不可觀測。

---

## 兩支工具，刻意不合流

| 工具 | 職責 | 為什麼分開 |
|---|---|---|
| `scripts/collect_resource_usage.py` | 常駐取樣，只記錄事實 → `data/metrics/resource-YYYYMMDD.jsonl` | 它跑在常駐 unit 裡，必須極度不容易壞；任何判定邏輯都是它的風險來源 |
| `scripts/analyze_resource_usage.py` | 讀 JSONL，算分位數、成長率與選型建議 | 判定會反覆修改，且是離線唯讀，改壞了不影響資料 |

與 `scripts/check_web_health.sh`（P4）／`scripts/incident_handler.sh`（P5）同一種分工：
**觀測與判定分離**。取樣器沒有門檻、沒有告警、沒有非零退出碼。

**兩支都刻意用系統的 `/usr/bin/python3`、不 import `app.*`、不需要 `.venv`。**
理由與 P4 完全相同：2026-08-18 那次 4 小時 50 分的中斷，根因正是 `.venv` 損毀
（uv 的 `.tmp`→rename 在 9p 上以 os error 2 失敗）。相依 `.venv` 的量測工具會和被監控
的服務一起死，而那正是最需要用量資料的時刻。

---

## 量測口徑（讀數字前必須先看懂）

### 1. CPU 一律以「核心數」計，不是百分比

`0.35` ＝ 平均吃掉 0.35 顆核心。這是為了讓數字**可以直接對到雲端機型的 vCPU**。
百分比會騙人：本機 20 核上量到的 5%，換到 2 vCPU 機型就是 50%。

### 2. 分元件用 cgroup v2，不掃 `/proc/<pid>`

批次腳本會 fork 出 `claude` CLI 與 BGE-M3 子行程，**按 PID 掃一定會漏掉它們**，
而那正是全天尖峰的來源。cgroup 天然把子孫行程算進父 unit。

容器同理，且**直接讀 `/sys/fs/cgroup/docker/<id>/` 而不是 `docker stats`**：實測後者
單次 2.1 秒，放進 20 秒的取樣迴圈等於一成的時間都在等 docker。docker CLI 只用在
「把 container id 換成名字」（TTL 300 秒）與「查 DB 容量」，掛掉就降級成短 id，
不影響取樣本身。

### 3. 非服務負載必須扣掉，而 WSL 下它不在 `user.slice`

這台機器同時是開發機。**關鍵事實：從 WSL shell 啟動的行程（互動 shell、`claude` CLI、
開發工具）落在 `init.scope`，不是 `user.slice`。** 只量 `user.slice` 會讓開發負載變成
「無人認領」，而無人認領的那一塊在報告裡看起來就像服務自己吃掉的。

取樣器因此**全掃 cgroup 樹的第一層**（`_` 前綴的聚合項），確保全機負載都有歸屬。
分析器則以「每筆樣本的服務葉節點加總」為服務用量，其餘一律歸「非服務」。

### 4. 「服務合計」是先加總再取分位數

不是「各元件的 p95 相加」。web 的尖峰與 postgres 的尖峰多半不同時發生，把兩個 p95
相加會系統性高估——而高估的結果是每個月多付一台機器的錢。

### 5. 線上路徑與批次路徑分開報

雲端架構本來就會把它們放在不同機器上（既有評估簡報的雙路徑正是這個切法），所以
「服務合計」的分位數是兩者尖峰疊加的產物，**對任何一邊都不是正確答案**。分析器因此
另外給「線上路徑合計（web＋DB＋邊緣＋ClamAV）」與「批次路徑合計（sync 等）」兩條，並在窗期
有超過 20% 的樣本正在跑批次時明講「這不是典型的一天」。

### 6. 批次元件的分位數只涵蓋它執行中的樣本

`report-mark-sync.service` 是 oneshot，cgroup 只在執行期間存在。把 99% 的閒置樣本算進
去的話 p95 會是 0，而那幾分鐘正是全天尖峰。報表會標出「在場率」讓讀者自己判斷這個
尖峰多常發生。

---

## 安裝與使用

```bash
# 一次性快照（確認取樣器看得到哪些元件；不寫檔）
make metrics-once

# 前景取樣一小時（臨時量測用）
make metrics-collect DURATION=3600

# 常駐取樣（生產用；真相來源在 deploy/，改完要 cp 過去）
sudo cp deploy/systemd/report-mark-metrics.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-metrics.service
systemctl status report-mark-metrics.service

# 分析
make metrics                 # 全部資料
make metrics SINCE=24h       # 只看最近 24 小時
python3 scripts/analyze_resource_usage.py --json   # 給其他工具消費
```

輸出量約 **10 MB／日**（20 秒一筆），取樣器自帶 `--retention-days 30` 修剪。
`data/metrics/` 已在 `.gitignore`——它描述的是「這台機器這段時間」，換一台機器就沒有意義。

**取樣器自己也會被量到**（它跑在 `report-mark-metrics.service` 的 cgroup 裡，
在報表中以 `metrics` 出現）。這是刻意的：一支宣稱「開銷可忽略」卻沒有證據的量測工具，
沒有理由被相信。

---

## 首次量測結果（2026-08-28）

以下是這套工具第一次跑出來的實測值，記在這裡是因為**它們是後續任何比較的基準**，
不是因為它們是結論。窗期短、負載是人造的，效力邊界見下一節。

### 閒置

| 項目 | 值 |
|---|---|
| 服務合計 CPU | **0.009 核**（幾乎是零） |
| 服務常駐 anon | 0.66–1.02 GiB（其中 `web` 佔絕大部分） |
| `report-mark-postgres` 佔用 | 約 3.3 GiB，**幾乎全是頁快取**（`shared_buffers` 記在 file 不是 anon） |
| DB 大小 | 9.00 GiB（`report_chunk` 8.73 GiB／584k 列／15,060 篇） |

### 一條問答（`/api/ask`，併發 1，三題，無批次干擾）

| 項目 | 值 |
|---|---|
| **單條成本** | **354.5 核心秒**（逐條 289／375／397） |
| 瞬時峰值 | 10.4–10.7 核（受 `EMBED_TORCH_THREADS=0` 影響，見下） |
| 延遲 | p50 93.5s／p95 118.0s／首 token p50 58.1s |
| 記憶體 | 服務 anon 峰值 1.39 GiB（閒置基線 0.66 ⇒ **單條增量 0.73 GiB**） |
| 吞吐量（利用率 0.7） | 2 vCPU 14 條／時｜4 vCPU 28｜8 vCPU 57｜16 vCPU 114 |

**CPU 幾乎全部花在 cross-encoder rerank**：`qa_timing` 顯示 rerank 27–39 秒、
retrieve 0.7–9.5 秒、embed 1 毫秒（後者是 `embed_query_cached` 的 lru_cache 命中，
冷值約 0.16–1.5 秒）。**rerank 是這套服務唯一需要獨立算力的元件**，其餘的本機 CPU
都是零頭；`app/services/llm.py` 那一段是 spawn `claude` CLI，屬 token 成本不是算力。

### 為什麼「峰值 10.4 核」不是「需要 10 顆核心」

`EMBED_TORCH_THREADS=0`（`app/config.py` 的預設）代表 torch 不綁執行緒、會吃滿可用
核心。所以在 20 核機器上量到的峰值是**供給**不是需求——同一份工作在 4 vCPU 上會用
4 核跑久一點，不會失敗。**可移植的量只有核心秒**，分析器因此以它為主單位，並在選型
段落印出這條警語。

### 一個實測踩到的坑：壓測撞上批次

第一輪壓測（12:07）正好撞上每 3 小時的 `report-mark-sync`，兩者各吃約 10 核、把 20 核
機器打滿。後果**不是壓測失敗，而是數字看起來完全正常卻是錯的**：

| | 單條成本 | 延遲 p50 |
|---|---|---|
| 撞上 sync（12:07） | 654 核心秒 | 110.8s |
| 乾淨窗期（12:31） | **354.5 核心秒** | 93.5s |

**高估 85%。** 這就是 `BATCH_COMPONENTS` 污染偵測存在的理由——分析器現在會在窗期
偵測到 `sync`／`backup`／`audit`／`freshness` 活躍時把 `reliable` 設為 False 並印出
警語，指向分元件的 `web` 那一行。順帶一提，這個「撞車」本身也是有效的觀測：
**批次與線上問答共用一台機器時，問答延遲從 70–108 秒被拉長到 184 秒。**

---

## 三個已知限制（結論的效力邊界）

1. **本機是 WSL2，不是雲端 Linux。** 磁碟 I/O 走 9p／drvfs，PSI 的 `io.some` 常態就有
   數個百分點（實測 p99 達 39%）；那個數字**不能**直接外推成 EBS 需求。CPU 的量測不受
   此影響，但**記憶體會**：WSL2 會主動把閒置的 anon 頁換出去還記憶體給 Windows——實測
   2026-08-28 上午 swap 從 0.31 GiB 漲到 3.22 GiB，同期 `MemAvailable` 一直維持在
   14–17 GiB（**不是記憶體壓力**），而 `web` 的 anon 一度從 0.94 GiB 掉到 0.10 GiB。
   所以 **anon 的分位數會低估常駐工作集**，選 RAM 時要一併看 cgroup 的
   `memory.peak`（`web` 實測 4.24 GiB，含暖機與頁快取）。
2. **這台機器同時是開發機。** 分析器已經把非服務負載扣掉，但「扣掉」與「不存在」不同：
   開發負載會透過快取競爭與 I/O 排隊間接影響服務的量測值。要最乾淨的數字，
   在無人使用的時段（例如凌晨）取一段窗期來看。
3. **最重要的一條：現況幾乎沒有真實負載。** 2026-08-28 查 `qa_log`，近 21 天只有 7 次
   問答。**被動監控只能量到「閒置 ＋ 批次」的 footprint，量不到問答的尖峰。**
   要拿到那個數字只有一條路：跑受控負載（固定併發打 `/api/ask`），
   同時讓取樣器記錄，再用 `--since` 框出那段窗期分析。**沒有這一步，vCPU 的結論會嚴重低估。**

---

## 受控負載：把「單條問答吃多少硬體」量出來

`scripts/bench_load.py` 的 HTTP 模式對**真實端點**打可控負載，跑完用分析器框出那段窗期：

```bash
python3 scripts/bench_load.py --dry-run --limit 4          # 先看計畫
python3 scripts/bench_load.py --limit 4 --concurrency 1    # 單條成本
python3 scripts/analyze_resource_usage.py --bench data/metrics/bench-ask-<stamp>.json
```

**與 `eval/run_ragas.py` 的分工**：那支評品質、且刻意直接呼叫函式不經 HTTP；這支評
資源成本，必須走真實端點——併發閘（`/api/ask` 上限 3）、SSE 串流與認證中介層都在
HTTP 那一層，繞過去就量不到。題目取自同一份凍結題集 `eval/ragas_questions.json`，
不另外造題。

**主單位是核心秒（core-seconds）＝ `Σ(cpu × dt)`**，而且是**扣掉閒置基線後的邊際值**
（基線取壓測開始前 10 分鐘的中位數）。用核心秒不用「峰值核心數」的理由：核心秒可加、
可除以請求數，而峰值不行——兩條請求各自 2 核的尖峰若不重疊，加總不是 4 核。
**決定 vCPU 看峰值，決定「一台機器一小時能服務幾條」看核心秒**，兩個問題不同。

壓測期間建議把取樣間隔調細（`--interval 5`）：20 秒一筆的話，一條 60 秒的問答只會落在
3 筆樣本上，任何分位數都只是單點觀測。分析器會在窗期樣本 <5 筆或基線 <3 筆時直接標警語。

### 實測結果（2026-09-02，乾淨窗期，取樣間隔 20s）

兩輪都在 12:00 那輪 sync 結束（12:03）之後跑，窗期內批次路徑 0 核；題目取自凍結題集
`eval/ragas_questions.json`，結果檔在 `data/metrics/bench-ask-20260902-121123.json`（併發 1）
與 `bench-ask-20260902-121930.json`（併發 3）。

| | 併發 1（4 條） | 併發 3（8 條） |
|---|---|---|
| 成功／排隊 | 4/4／0 | 8/8／0 |
| 延遲 p50／p95 | 86.0s／105.8s | 143.5s／191.7s |
| 首 token p50 | 44.1s | 118.6s |
| **單條邊際成本（核心秒／請求）** | **334.6** | **568.3** |
| 其中 `web`（BGE-M3＋rerank＋claude 父程序） | 266.8 | 453.0 |
| 窗期服務尖峰（核） | 10.45 | **19.83（20 核機器打滿）** |
| PSI cpu.some p90 | — | 35.8% |
| 服務 anon 峰值（增量） | 1.93 GiB（+0.51） | 2.92 GiB（+1.31） |
| 每小時可服務條數（利用率 0.7，8 vCPU） | 60 | 35 |

三個讀法：

1. **併發 1 的 334.6 核心秒與 08-28 乾淨窗期的 354.5 一致**（差 6%，在取樣噪音內），
   單條成本的數字可以視為穩定。
2. **併發 3 把單條成本推高 70%、延遲拉長 67%，而且尖峰把 20 核整台打滿。** 這不是
   「三條各吃 10 核」的線性加總——`EMBED_TORCH_THREADS=0` 讓每條問答的 torch 都想吃滿
   所有核心，三條互搶時多出來的是排隊與快取競爭，不是有效工作。**併發閘上限 3 在這台
   機器上已經是超賣**；上雲若給的核心少於 20，併發 3 的延遲會比這裡更差。
3. **決定 vCPU 看併發 1 的尖峰 10.45 核**（那是一條問答「能吃到多少就吃多少」的供給
   上限，不是需求下限；同一條在 4 vCPU 上會跑久一點），**決定吞吐看核心秒**。以上雲
   常見的 8 vCPU 估：每小時約 35–60 條問答，取決於是否允許併發互搶。要把併發 3 的
   延遲壓回併發 1 的水準，就得把 `EMBED_TORCH_THREADS` 綁到「核心數 ÷ 併發上限」，
   那是另一組要量的旋鈕，本輪沒量。

---

## 問答 rerank 延遲重測（2026-09-29）

從正式庫凍結 15,255 篇研報／616,769 個 chunk，還原到隔離 PostgreSQL；在本工作樹
`127.0.0.1:8099` 啟動單 worker API，載入既有 DeepSeek 設定，對同一組前 8 題各送
一次真實登入後的 `/api/ask` SSE 請求。依序量單併發、三併發，再把預設
`ASK_RERANK_CANDIDATES` 從 50 改為 16，重跑相同兩輪；其餘設定、語料和主機不變。
結果原檔在未版控的 `data/eval_frozen/metrics/`，以下秒數是這四輪的實測：

| 指標 | 50 候選，併發 1 | 16 候選，併發 1 | 50 候選，併發 3 | 16 候選，併發 3 |
|---|---:|---:|---:|---:|
| 成功／錯誤 | 8/0 | 8/0 | 8/0 | 8/0 |
| 全請求 p50 | 45.81 | 19.65 | 71.00 | 31.06 |
| 全請求 p95 | 135.59 | 47.68 | 160.67 | 63.38 |
| 首 token p50 | 39.71 | 14.85 | 66.47 | 26.87 |

50 候選的 `qa_timing` 首輪重排約需 36–54 秒，補查重排有兩筆耗盡 90 秒期限；三併發
也觀察到首輪重排逾時、退回融合排序。16 候選的 16 筆請求首輪重排全數實際套用，
沒有超時。另以相同快照、前 8 題、`--agentic` 與 DeepSeek judge 比對兩個候選上限：
50 候選 F/CP/AR = 0.984/0.849/0.686、檢索加生成平均 54.9 秒；16 候選為
0.968/0.852/0.713、平均 23.2 秒。`make eval-compare` 退出碼 0，三項仍過既定門檻。
這是 8 題、單次重跑的探索性對照；正式 18 題×3 次品質基準刻意關閉重排與補查，
兩者不可混成一個分數。本輪未同步採樣 cgroup，**不能從延遲推算核心秒、吞吐量或雲端機型**。

新 `qa_timing` 欄位把 rerank semaphore 排隊（`rerank_queue_ms`）與交給執行緒後的耗時
（`rerank_compute_ms`）分開，另記 `rerank_applied` 與 `rerank_timed_out`。
`compute_ms` 含 `asyncio.to_thread` 的排程等待；若已逾時，背景推論可能在 log 後才結束，
該欄位可能為空。這些欄位只涵蓋首輪檢索；agentic 補查沒有累計進來。

唯一的推論改動是 CPU cross-encoder 由 `torch.no_grad()` 改用
`torch.inference_mode()`；tokenizer、batch 大小、候選順序、分數計算與排序規則不變。
可用已快取的模型完全離線重測（沒有 DB、語料、HTTP 或 LLM 呼叫）：

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run python scripts/bench_load.py \
  --offline-rerank --limit 8 --repeat 5 --torch-threads 4
```

微基準固定 8 個合成片段與問題，模型先暖機一次，再交錯跑舊路徑與現行路徑；結果 JSON
含每輪秒數、各自中位數、逐筆最大分數差與名次是否完全一致。這是單一模型、單一 CPU
執行緒配置的推論測試；排序一致只對該合成樣本成立，不能代替 `eval/run_ragas.py` 的
真實語料品質評測。也不能從微基準推算端到端問答速度。

本機 Intel Core i7-14700、PyTorch 4 執行緒、快取的 `BAAI/bge-reranker-v2-m3`：

| 指標 | 舊 `no_grad` | 現行 `inference_mode` |
|---|---:|---:|
| 5 輪秒數 | 6.662、6.503、5.535、5.098、4.950 | 6.230、6.196、5.196、4.730、4.861 |
| 中位數 | 5.535s | 5.196s |

微基準中位數差為 0.339s（6.1%）；五組配對測量皆為現行路徑較快。8 筆逐筆分數的最大
絕對差為 **0**，完整名次一致。CPU 背景負載與模型暖機後狀態仍使各輪時間波動，這個
百分比只描述本機這組合成輸入與配置。

## 常駐元件：ClamAV

上傳管線（Admin v1.5）用常駐的 clamd 容器 `report-mark-clamav`（`deploy/clamav/docker-compose.yml`，
`make up-clamav`）掃描上傳的 PDF。**選型結論：常駐約 1.2–1.6 GB，不隨上傳量變化**——clamd 把整份病毒碼
（數百萬筆簽章）載進記憶體，閒置與掃描中的差別只是單一檔案的工作區。這一塊是線上路徑的固定開銷：
流量變少它不會變小，也不能攤進「每條請求的邊際成本」。

| 項目 | 值 |
|---|---|
| 預估常駐 | 約 1.2–1.6 GB（設計估計，**未實測**） |
| 容器上限 | `mem_limit: 2g` |
| 實測 RSS（p50／p95／max） | **待補**：上線後量一週再填，量到之前不寫數字 |
| 一週內 OOM 次數 | **待補** |

三個設定直接決定這個數字，改之前先看這裡：

- `ConcurrentDatabaseReload no`（`deploy/clamav/conf/clamd.conf`）：預設的不中斷重載會先把新病毒碼整份載進來
  再換掉舊的，期間約翻倍到 ~3 GB，必然撞上 2g 的上限。關掉的代價是重載期間 30–60 秒不能掃（上傳只是晚一點掃）。
- `TestDatabases yes`（`deploy/clamav/conf/freshclam.conf`）：freshclam 換上新病毒碼前會先在自己的行程裡試載一次，
  與 clamd 同在一個 cgroup。若一週內看到 OOM，第一個要評估的就是改成 `no`。
- `FRESHCLAM_CHECKS=4`（compose 的 environment）：一天最多 4 次檢查，只有真的有新病毒碼才重載。

量測方式不必另寫：取樣器（`scripts/collect_resource_usage.py`）會自動把容器的 cgroup 以容器名
`report-mark-clamav` 記成一個元件；分析器把它列在 `ONLINE_FIXED_COMPONENTS`（線上路徑、固定開銷），
報表的記憶體段會多一列「線上固定開銷 anon」。上線一週後補上面的表：

```bash
make metrics SINCE=7d                                            # 看 report-mark-clamav anon 與 cgroup 歷史峰值
docker stats --no-stream report-mark-clamav                      # 當下的用量與上限
docker inspect -f '{{.State.OOMKilled}} {{.State.Health.Status}} {{.RestartCount}}' report-mark-clamav
```

**改成按需的條件**（設計決策 1）：主機可用記憶體常態低於 3 GiB 時，改成按需執行（apt 的 clamscan 加
freshclam timer，執行時才暫時多約 1 GB、每輪多 20–40 秒載入病毒碼）。設計時量到的背景：主機總記憶體
19 GiB、可用約 6 GiB、swap 已用 7/8 GiB（這台同時是開發機）。

## 批次元件：上傳 worker

`report-mark-upload.service`（Admin v1.5，`scripts/process_uploads.sh`，timer 每 5 分鐘；**尚未部署**）。取樣器依 unit
名自動把它記成元件 `upload`，分析器列在 `BATCH_COMPONENTS`（批次，不算線上路徑）。每輪大多只是幾個空查詢；有乾淨檔
要入庫時才延遲載入 BGE-M3，入庫段取 claude 鎖、與 sync 互斥，主機尖峰維持「web 一份＋一支批次一份」。
backfill（`scripts/backfill_extraction.py`，也載 BGE-M3、不取 claude 鎖）正在跑時只掃描、不入庫。

| 項目 | 值 |
|---|---|
| unit 限制 | `MemoryMax=4G`、`Nice=19`、`IOSchedulingClass=idle`、`EMBED_TORCH_THREADS=4` |
| 入庫前檢查子行程 | `RLIMIT_AS`＝`UPLOAD_PREFLIGHT_MEMORY_MB`（預設 2048）、逾時 300 秒、頁數上限 300 |
| 合成 PDF 的 pdfplumber 試抽（2026-10-07，每頁約 2,700 字） | 50 頁 RSS 0.34 GB、100 頁 0.61 GB、300 頁 1.68 GB（每頁約 5.5 MB；1536 MB 上限下 300 頁 ENOMEM）；pypdf 300 頁約 60 MB |
| 實測 RSS（idle 一輪／入庫一篇的 p50／p95／max） | **待補**：上線後量一週再填，量到之前不寫數字 |
| 一週內 cgroup OOM 次數 | **待補** |

**已知風險**：入庫核心（`scripts/_ingest_core.py` 的 `ingest_one`）會在 worker 主行程再抽一次字，已載入 BGE-M3
（約 2–3 GB）時再抽一份 300 頁的密集文字 PDF（約 1.7 GB）會逼近 4G。撞到時 cgroup OOM 砍掉這一輪，殘留回收把那一筆
退回 `clean`，同一份檔被中止 3 次就轉 `failed`（不會無限重試）。上線一週後用下面的指令補表；若真的撞到，先評估
調降 `UPLOAD_PREFLIGHT_MEMORY_MB`（讓過大的檔在子行程就被擋下）或頁數上限，而不是放寬 `MemoryMax`。

```bash
make metrics SINCE=7d                                    # 看 upload 元件的 anon 與 cgroup 歷史峰值
systemctl show report-mark-upload -p MemoryPeak -p Result # 單輪峰值（需 systemd ≥ 255）與是否 oom-kill
journalctl -u report-mark-upload -g "oom" --since -7d
```

## 不在量測範圍內的成本

現行問答 LLM 是遠端 DeepSeek API，不是本機推論；上表 2026-09-02 的舊量測則含當時的
Claude CLI 父程序。**問答的模型成本不會出現在任何 CPU 數字裡**。
那一側屬於 token／API 成本，是既有評估簡報涵蓋的範圍，
兩者不可互相取代：把本機量到的 CPU 拿去推論「模型很便宜」是錯的，反過來也是。

同理，BGE-M3 嵌入與 cross-encoder rerank **確實**是本機 CPU（`torch` CPU-only），
它們是這套服務唯一真正吃 CPU 的東西，也是選機型時真正的變數。
