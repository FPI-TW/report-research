# 硬體用量量測（選型與容量旋鈕的證據基礎）

這份文件記兩件事：**怎麼量這套服務吃多少 vCPU／RAM／磁碟**，以及**已經量到的數字**——`ASK_RERANK_CANDIDATES`、`EMBED_TORCH_THREADS`、上傳 worker 與 ClamAV 的記憶體上限都是依這裡的實測定的，改之前先讀對應段落。

所有數字都量自辦公室主機（Intel Core i7-14700、20 核、WSL2，同時是開發機）。正式環境（EC2）是 `c7i.xlarge`（4 vCPU／8 GiB，`deploy/aws/infrastructure.yaml`），沒有裝取樣器、也沒有在那台上重測；要把數字搬過去只能用可移植的量（核心秒），不能直接套延遲與峰值。

## 工具

| 工具 | 職責 | 為什麼分開 |
|---|---|---|
| `scripts/collect_resource_usage.py` | 常駐取樣，只記錄事實 → `data/metrics/resource-YYYYMMDD.jsonl` | 跑在常駐 unit 裡，必須極度不容易壞；任何判定邏輯都是它的風險來源 |
| `scripts/analyze_resource_usage.py` | 讀 JSONL，算分位數、成長率與選型建議 | 判定會反覆修改，且是離線唯讀，改壞了不影響資料 |
| `scripts/bench_load.py` | 對真實 `/api/ask` 打可控負載；另有離線 rerank 微基準 | 被動監控量不到問答的尖峰（見「效力邊界」） |

**觀測與判定分離**（與 `scripts/check_web_health.sh`／`scripts/incident_handler.sh` 同一種分工）：取樣器沒有門檻、沒有告警、沒有非零退出碼。

**取樣器與分析器都用系統的 `/usr/bin/python3`、不 import `app.*`、不需要 `.venv`**：相依 `.venv` 的量測工具會和被監控的服務一起死（`.venv` 損毀時正是最需要用量資料的時刻）。`bench_load.py` 的 HTTP 模式同樣只用標準庫；只有 `--offline-rerank` 會載入 `app.services.rerank`，要用 `uv run`。

```bash
make metrics-once                      # 一次性快照（確認取樣器看得到哪些元件；不寫檔）
make metrics-collect DURATION=3600     # 前景取樣一小時（臨時量測用）
make metrics                           # 分析全部資料
make metrics SINCE=24h                 # 只看最近 24 小時
python3 scripts/analyze_resource_usage.py --json   # 給其他工具消費
```

常駐取樣由 `report-mark-metrics.service` 負責（辦公室主機；它讀 docker 的 cgroup，EC2 不裝）。安裝照 `AGENTS.md` 改動對照表的 `deploy/systemd/` 一列：

```bash
sudo cp deploy/systemd/report-mark-metrics.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now report-mark-metrics.service
```

輸出約 **10 MB／日**（20 秒一筆），取樣器自帶 `--retention-days 30` 修剪；`data/metrics/` 不進版控。同一支收集器另外每 60 秒把主機、容器與 unit 狀態寫進監控 spool `data/ops_spool/`（管理後台用，見 `docs/production_resilience.md`「監控 spool 與匯入」）。取樣器自己也會被量到（報表中的 `metrics` 元件），這是刻意的：宣稱「開銷可忽略」要有證據。

## 量測口徑（讀數字前必須先看懂）

1. **CPU 一律以「核心數」計，不是百分比。** `0.35` ＝平均吃掉 0.35 顆核心，可以直接對到雲端機型的 vCPU；20 核上的 5% 換到 2 vCPU 就是 50%。
2. **分元件用 cgroup v2，不掃 `/proc/<pid>`。** 批次腳本會 fork 出 BGE-M3 子行程，按 PID 掃一定會漏掉它們，而那正是全天尖峰的來源。容器直接讀 `/sys/fs/cgroup/docker/<id>/` 而不是 `docker stats`（實測後者單次 2.1 秒，放進 20 秒的取樣迴圈等於一成時間在等 docker）；docker CLI 只用來把 container id 換成名字（TTL 300 秒）與查 DB 容量，掛掉就降級成短 id。
3. **非服務負載要扣掉，而 WSL 下它不在 `user.slice`。** 從 WSL shell 啟動的行程（互動 shell、開發工具）落在 `init.scope`；只量 `user.slice` 會讓開發負載變成「無人認領」，在報告裡看起來像服務自己吃掉的。取樣器因此全掃 cgroup 樹第一層（`_` 前綴的聚合項），分析器以「每筆樣本的服務葉節點加總」為服務用量，其餘歸「非服務」。
4. **「服務合計」是先加總再取分位數**，不是各元件的 p95 相加：web 與 postgres 的尖峰多半不同時發生，相加會系統性高估。
5. **線上路徑與批次路徑分開報。** 分析器另給「線上路徑合計（web＋DB＋邊緣＋ClamAV）」與「批次路徑合計（`BATCH_COMPONENTS`：sync、backup、audit、freshness、upload）」，窗期有超過 20% 的樣本正在跑批次時會明講「這不是典型的一天」。
6. **批次元件的分位數只涵蓋它執行中的樣本。** oneshot 的 cgroup 只在執行期間存在；把閒置樣本算進去 p95 會是 0，而那幾分鐘正是全天尖峰。報表會標「在場率」。
7. **主單位是核心秒（`Σ(cpu × dt)`），而且是扣掉閒置基線後的邊際值**（基線取壓測開始前 10 分鐘的中位數）。核心秒可加、可除以請求數；峰值不行。`EMBED_TORCH_THREADS=0`（`app/config.py` 的預設）讓 torch 吃滿可用核心，所以量到的峰值是**供給**不是需求——同一份工作在 4 vCPU 上會用 4 核跑久一點，不會失敗。

## 受控負載的量法

`scripts/bench_load.py` 的 HTTP 模式對**真實端點**打可控負載（每題打真實 `/api/ask`，會用掉 LLM 額度），跑完用分析器框出那段窗期：

```bash
python3 scripts/bench_load.py --dry-run --limit 4          # 先看計畫
python3 scripts/bench_load.py --limit 4 --concurrency 1    # 單條成本
python3 scripts/analyze_resource_usage.py --bench data/metrics/bench-ask-<stamp>.json
```

- 登入帳號讀 `REPORT_MARK_BENCH_USERNAME`／`REPORT_MARK_BENCH_PASSWORD`（建議開一個壓測專用的一般帳號，問答紀錄落在它名下）。
- 與 `eval/run_ragas.py` 的分工：那支評品質、刻意直接呼叫函式不經 HTTP；這支評資源成本，必須走真實端點——併發閘（`/api/ask` 上限 3）、SSE 串流與認證中介層都在 HTTP 那一層。題目取自同一份凍結題集 `eval/ragas_questions.json`。
- **避開批次**：壓測撞上每 3 小時的 `report-mark-sync` 時數字看起來正常卻是錯的。2026-08-28 實測：撞上 sync 的那一輪單條成本 654 核心秒、延遲 p50 110.8s，乾淨窗期是 354.5 核心秒、93.5s（**高估 85%**）。分析器偵測到 `BATCH_COMPONENTS` 在窗期內活躍時會把 `reliable` 設為 False 並印警語。
- 壓測期間把取樣間隔調細（取樣器的 `--interval 5`）：20 秒一筆時，一條 60 秒的問答只落在 3 筆樣本上。分析器在窗期樣本 <5 筆或基線 <3 筆時會標警語。

## 實測結果

### 閒置與資料量（2026-08-28）

| 項目 | 值 |
|---|---|
| 服務合計 CPU | **0.009 核**（幾乎是零） |
| 服務常駐 anon | 0.66–1.02 GiB（`web` 佔絕大部分） |
| `web` 的 cgroup `memory.peak` | 4.24 GiB（含暖機與頁快取） |
| `report-mark-postgres` 佔用 | 約 3.3 GiB，**幾乎全是頁快取**（`shared_buffers` 記在 file 不是 anon） |
| DB 大小 | 9.00 GiB（`report_chunk` 8.73 GiB／584k 列／15,060 篇） |

### 單條問答的成本（rerank 50 候選；2026-08-28、2026-09-02）

兩次都在乾淨窗期（批次路徑 0 核）、取樣間隔 20 秒，題目取自凍結題集。當時 `ASK_RERANK_CANDIDATES` 是 50、LLM 是本機 spawn 的 CLI（現在是 DeepSeek HTTP 呼叫、候選 16）；**改成 16 候選之後沒有重量核心秒**，下表是上限估計。

| | 併發 1（4 條） | 併發 3（8 條） |
|---|---|---|
| 成功／排隊 | 4/4／0 | 8/8／0 |
| 延遲 p50／p95 | 86.0s／105.8s | 143.5s／191.7s |
| 首 token p50 | 44.1s | 118.6s |
| **單條邊際成本（核心秒／請求）** | **334.6** | **568.3** |
| 其中 `web` | 266.8 | 453.0 |
| 窗期服務尖峰（核） | 10.45 | **19.83（20 核機器打滿）** |
| PSI cpu.some p90 | — | 35.8% |
| 服務 anon 峰值（增量） | 1.93 GiB（+0.51） | 2.92 GiB（+1.31） |
| 每小時可服務條數（利用率 0.7，8 vCPU） | 60 | 35 |

- **單條成本穩定**：2026-08-28 的乾淨窗期是 354.5 核心秒（逐條 289／375／397），與上表併發 1 的 334.6 差 6%，在取樣噪音內。以 354.5 核心秒、利用率 0.7 換算：2 vCPU 14 條／時、4 vCPU 28、8 vCPU 57、16 vCPU 114。
- **CPU 幾乎全部花在 cross-encoder rerank**：`qa_timing` 顯示 rerank 27–39 秒、retrieve 0.7–9.5 秒、embed 1 毫秒（`embed_query_cached` 的 lru_cache 命中，冷值約 0.16–1.5 秒）。rerank 是這套服務唯一需要獨立算力的元件。
- **併發 3 是超賣**：單條成本推高 70%、延遲拉長 67%、尖峰把 20 核打滿。這不是三條各吃 10 核的線性加總——`EMBED_TORCH_THREADS=0` 讓每條問答的 torch 都想吃滿所有核心，多出來的是排隊與快取競爭。核心少於 20 的機器上併發 3 只會更差；要把延遲壓回併發 1 的水準，得把 `EMBED_TORCH_THREADS` 綁到「核心數 ÷ 併發上限」（這組沒量過）。
- **批次與線上問答共用一台機器時互相拖累**：撞上 sync 的那一輪，問答延遲從 70–108 秒拉長到 184 秒。這是批次 unit 設 `Nice`／idle I/O 與 `EMBED_TORCH_THREADS=4`（`deploy/systemd/report-mark-upload.service`）的依據。

### rerank 候選數：50 → 16（2026-09-29）

`ASK_RERANK_CANDIDATES` 的預設 16 是這次量出來的。做法：從辦公室主機的語料庫凍結 15,255 篇研報／616,769 個 chunk，還原到隔離的 PostgreSQL，啟動單 worker API、載入既有 DeepSeek 設定，對凍結題集前 8 題各送一次真實登入後的 `/api/ask` SSE 請求；依序量單併發、三併發，再把候選數從 50 改為 16 重跑，其餘設定、語料與主機不變（秒）：

| 指標 | 50 候選，併發 1 | 16 候選，併發 1 | 50 候選，併發 3 | 16 候選，併發 3 |
|---|---:|---:|---:|---:|
| 成功／錯誤 | 8/0 | 8/0 | 8/0 | 8/0 |
| 全請求 p50 | 45.81 | 19.65 | 71.00 | 31.06 |
| 全請求 p95 | 135.59 | 47.68 | 160.67 | 63.38 |
| 首 token p50 | 39.71 | 14.85 | 66.47 | 26.87 |

- 50 候選的首輪重排約需 36–54 秒，補查重排有兩筆耗盡 90 秒期限；三併發觀察到首輪重排逾時、退回融合排序（fail-open，等於 rerank 靜默關掉）。16 候選的 16 筆請求首輪重排全數實際套用，沒有逾時。
- 品質沒有劣化：相同快照、前 8 題、`--agentic` 與 DeepSeek judge，50 候選 F/CP/AR = 0.984/0.849/0.686、檢索加生成平均 54.9 秒；16 候選 0.968/0.852/0.713、平均 23.2 秒。`make eval-compare` 退出碼 0，三項仍過既定門檻。
- 效力邊界：這是 8 題、單次重跑的探索性對照（正式 18 題×3 次品質基準刻意關閉重排與補查，兩者不可混成一個分數）；這一輪沒有同步採樣 cgroup，**不能從延遲推算核心秒、吞吐量或雲端機型**。

`qa_timing` 把 rerank semaphore 排隊（`rerank_queue_ms`）與交給執行緒後的耗時（`rerank_compute_ms`）分開，另記 `rerank_applied` 與 `rerank_timed_out`。`compute_ms` 含 `asyncio.to_thread` 的排程等待；已逾時的那一筆，背景推論可能在 log 之後才結束，欄位可能為空。這些欄位只涵蓋首輪檢索，agentic 補查沒有累計進來。

### rerank 推論：`inference_mode`（離線微基準）

cross-encoder 用 `torch.inference_mode()`（`app/services/rerank.py`），不是 `torch.no_grad()`；tokenizer、batch 大小、候選順序、分數計算與排序規則相同。可用已快取的模型完全離線重測（沒有 DB、語料、HTTP 或 LLM 呼叫）：

```bash
HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 uv run python scripts/bench_load.py \
  --offline-rerank --limit 8 --repeat 5 --torch-threads 4
```

微基準固定 8 個合成片段與問題，模型先暖機一次，再交錯跑 `no_grad` 與 `inference_mode`；結果 JSON 含每輪秒數、各自中位數、逐筆最大分數差與名次是否完全一致。辦公室主機、PyTorch 4 執行緒、快取的 `BAAI/bge-reranker-v2-m3`：

| 指標 | `no_grad` | `inference_mode` |
|---|---:|---:|
| 5 輪秒數 | 6.662、6.503、5.535、5.098、4.950 | 6.230、6.196、5.196、4.730、4.861 |
| 中位數 | 5.535s | 5.196s |

中位數差 0.339s（6.1%），五組配對測量皆為 `inference_mode` 較快；8 筆逐筆分數的最大絕對差為 **0**、名次完全一致。這只描述單一模型、單一執行緒配置與這組合成輸入：不能代替 `eval/run_ragas.py` 的真實語料品質評測，也不能推算端到端問答速度。

## 效力邊界

1. **量測主機是 WSL2，不是雲端 Linux。** PSI 的 `io.some` 常態就有數個百分點（實測 p99 達 39%），不能外推成 EBS 需求。CPU 的量測不受影響，但**記憶體會**：WSL2 會主動把閒置的 anon 頁換出去還給 Windows——2026-08-28 上午 swap 從 0.31 GiB 漲到 3.22 GiB，同期 `MemAvailable` 維持 14–17 GiB（不是記憶體壓力），`web` 的 anon 一度從 0.94 GiB 掉到 0.10 GiB。所以 **anon 的分位數會低估常駐工作集**，選 RAM 時要一併看 cgroup 的 `memory.peak`。
2. **量測主機同時是開發機。** 分析器已經把非服務負載扣掉，但開發負載仍會透過快取競爭與 I/O 排隊間接影響量測值；要最乾淨的數字，取無人使用的時段。
3. **被動監控只量得到「閒置＋批次」。** 真實問答量很低（2026-08-28 查 `qa_log`，近 21 天只有 7 次），問答的尖峰只能靠受控負載量；沒有這一步，vCPU 的結論會嚴重低估。
4. **模型成本不在任何 CPU 數字裡。** 問答與批次的 LLM 是遠端 DeepSeek API，屬 token／API 成本；本機 CPU 幾乎全是 BGE-M3 嵌入與 cross-encoder rerank（`torch` CPU-only），那才是選機型的變數。兩者不可互相推論。

## 常駐元件：ClamAV

上傳管線（Admin v1.5）用常駐的 clamd 容器 `report-mark-clamav`（`deploy/clamav/docker-compose.yml`，`make up-clamav`）掃描上傳的 PDF。**常駐約 1.2–1.6 GB，不隨上傳量變化**——clamd 把整份病毒碼載進記憶體，閒置與掃描中的差別只是單一檔案的工作區。這一塊是線上路徑的固定開銷：流量變少它不會變小，也不能攤進「每條請求的邊際成本」。

| 項目 | 值 |
|---|---|
| 預估常駐 | 約 1.2–1.6 GB（設計估計） |
| 容器上限 | `mem_limit: 2g` |
| 實測 RSS（p50／p95／max） | 尚未量；量到之前不寫數字 |
| 一週內 OOM 次數 | 尚未量 |

三個設定直接決定這個數字，改之前先看這裡：

- `ConcurrentDatabaseReload no`（`deploy/clamav/conf/clamd.conf`）：預設的不中斷重載會先把新病毒碼整份載進來再換掉舊的，期間約翻倍到 ~3 GB，必然撞上 2g 的上限。關掉的代價是重載期間 30–60 秒不能掃（上傳只是晚一點掃）。
- `TestDatabases yes`（`deploy/clamav/conf/freshclam.conf`）：freshclam 換上新病毒碼前會先在自己的行程裡試載一次，與 clamd 同在一個 cgroup。若看到 OOM，第一個要評估的就是改成 `no`。
- `FRESHCLAM_CHECKS=4`（compose 的 environment）：一天最多 4 次檢查，只有真的有新病毒碼才重載。

量法：取樣器會自動把容器的 cgroup 以容器名 `report-mark-clamav` 記成一個元件，分析器把它列在 `ONLINE_FIXED_COMPONENTS`（線上路徑、固定開銷），報表的記憶體段多一列「線上固定開銷 anon」。

```bash
make metrics SINCE=7d                                            # 看 report-mark-clamav anon 與 cgroup 歷史峰值
docker stats --no-stream report-mark-clamav                      # 當下的用量與上限
docker inspect -f '{{.State.OOMKilled}} {{.State.Health.Status}} {{.RestartCount}}' report-mark-clamav
```

**改成按需的條件**：主機可用記憶體常態低於 3 GiB 時，改成按需執行（apt 的 clamscan 加 freshclam timer，執行時才暫時多約 1 GB、每輪多 20–40 秒載入病毒碼）。設計時量到的背景：辦公室主機總記憶體 19 GiB、可用約 6 GiB、swap 已用 7/8 GiB。

## 批次元件：上傳 worker

`report-mark-upload.service`（Admin v1.5，`scripts/process_uploads.sh`，timer 每 5 分鐘；各主機是否安裝見 `docs/DEPLOYMENT_STATUS.md`）。取樣器依 unit 名自動把它記成元件 `upload`，分析器列在 `BATCH_COMPONENTS`（批次，不算線上路徑）。每輪大多只是幾個空查詢；有乾淨檔要入庫時才延遲載入 BGE-M3，入庫段取 claude 鎖、與 sync 互斥，主機尖峰維持「web 一份＋一支批次一份」。backfill（`scripts/backfill_extraction.py`，也載 BGE-M3、不取 claude 鎖）正在跑時只掃描、不入庫。

| 項目 | 值 |
|---|---|
| unit 限制 | `MemoryMax=4G`、`Nice=19`、`IOSchedulingClass=idle`、`EMBED_TORCH_THREADS=4` |
| 入庫前檢查子行程 | `RLIMIT_AS`＝`UPLOAD_PREFLIGHT_MEMORY_MB`（預設 2048）、逾時 300 秒、頁數上限 300 |
| 合成 PDF 的 pdfplumber 試抽（2026-10-07，每頁約 2,700 字） | 50 頁 RSS 0.34 GB、100 頁 0.61 GB、300 頁 1.68 GB（每頁約 5.5 MB；1536 MB 上限下 300 頁 ENOMEM）；pypdf 300 頁約 60 MB |
| 實測 RSS（idle 一輪／入庫一篇的 p50／p95／max） | 尚未量；量到之前不寫數字 |
| 一週內 cgroup OOM 次數 | 尚未量 |

**已知風險**：入庫核心（`scripts/_ingest_core.py` 的 `ingest_one`）會在 worker 主行程再抽一次字，已載入 BGE-M3（約 2–3 GB）時再抽一份 300 頁的密集文字 PDF（約 1.7 GB）會逼近 4G。撞到時 cgroup OOM 砍掉這一輪，殘留回收把那一筆退回 `clean`，同一份檔被中止 3 次就轉 `failed`（不會無限重試）。真的撞到時，先評估調降 `UPLOAD_PREFLIGHT_MEMORY_MB`（讓過大的檔在子行程就被擋下）或頁數上限，而不是放寬 `MemoryMax`。

```bash
make metrics SINCE=7d                                    # 看 upload 元件的 anon 與 cgroup 歷史峰值
systemctl show report-mark-upload -p MemoryPeak -p Result # 單輪峰值（需 systemd ≥ 255）與是否 oom-kill
journalctl -u report-mark-upload -g "oom" --since -7d
```
