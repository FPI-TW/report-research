# report-mark Roadmap

從「研報語意檢索」升級為「問答 + 訊號 + 對外服務」平台的進度視圖。

> **2026-07-28 全面重寫。** 前一版停在 2026-06 的 Phase 0–3 敘事，其里程碑編號（M0–M3）與實際採用的 M0–M10 衝突（例如舊文件的「M2」是共識聚合，實際 M2 是 rerank），且把已上線的東西列為未實作、把交付檔名寫成從未存在的名字（`signals.py`／`consensus.py`／`app/api/auth.py`），相依清單也列了四個從未安裝的套件。本檔改以**實際里程碑**為準，每項都對得上 repo 內的檔案。

---

## 已上線

### 基礎與檢索

| 里程碑 | 內容 | 落點 |
|---|---|---|
| — | 語料管線：抽取 → Claude 標註 → BGE-M3 嵌入 → pgvector | `scripts/extract_all.py`、`tag_all_cli.py`、`ingest_all.py` |
| — | 混合檢索：dense（HNSW 餘弦）＋ lexical（`pg_trgm`）分層融合 | `app/services/retrieval.py`、`store.py`、`textnorm.py` |
| **M0** | 設定集中化 | `app/config.py`（frozen dataclass ＋ `os.getenv`，**非 pydantic-settings**；約 80 鍵） |
| **M1** | 檢索 eval／RAGAS harness 與基準線（`eval/baselines/` 三份 RAGAS 基準線的 `thresholds_pass` 皆 False；`answer_relevancy` 自 M0 起就沒通過過絕對門檻——均值 0.61–0.64 對門檻 0.85。2026-07-29 診斷結論是**指標設計與門檻不相容，不是答案品質差**：反推問題被要求「具體」而題集問題是廣義的，餘弦結構性落在 0.70 附近；門檻要調到多少屬政策決定故未動，量測紀錄留在 `ANSWER_RELEVANCY_MIN` 旁，反推問題與逐題餘弦現已落進結果檔） | `eval/`（`dataset`、`judge`、`ragas_metrics`、`run_ragas`） |
| **M1b** | 研報評測題集凍結 | `eval/report_questions.json`、`eval/report_metrics.py` |
| **M2** | Cross-encoder rerank | `app/services/rerank.py` |

### 問答

| 里程碑 | 內容 | 落點 |
|---|---|---|
| — | RAG 問答（SSE 串流、`[n]` 行內引用、`qa_log`） | `app/services/answer.py`、`web/routers/ask.py` |
| — | 多輪對話、歷史、版本鏈、回饋 | `web/routers/qa_history.py` |
| — | 總覽路徑（枚舉／聚合題走純 SQL 分面，繞過 top-k） | `app/services/overview.py` |
| **M3** | 問答 UX（重生版本鏈／停止落庫／編輯重送） | `frontend/src/features/ask/` |
| **M4** | 五類範圍路由（`OFF_TOPIC`／`OVERVIEW`／`CORPUS_QA`／`TIME_SENSITIVE`／`ADVICE_RISK`） | `app/services/scope_router.py`（前身 `intent.py`，已改名） |
| **M4a** | 受信任時效資料（registry 為空即安全婉拒） | `app/services/trusted_market_data.py` |
| **M4b** | 證據帳本 | `app/services/evidence.py` |
| **M5** | Agentic 多輪補查 | `app/services/agentic_qa.py`、`query_planner.py` |

### 深度研報

| 里程碑 | 內容 | 落點 |
|---|---|---|
| — | 深度研報生成（深檢索 → 長文串流 → KPI／圖表 → PDF → `report_doc`） | `app/services/report.py`、`web/routers/report.py` |
| **M6** | 研報檢索增強（多查詢 fan-out ＋ MMR） | `app/services/retrieval_pipeline.py` |
| **M7** | **逐節生成**：大綱 → 逐節檢索與草稿 → 逐節 grounding 與低分節修正一輪（M8b，`verifying` 階段）→ 單次組裝，狀態機落庫 | `app/services/report_writer.py`、`report_run`／`report_section` |
| **M8** | 忠實度查核：a 地基／b 研報逐節 grounding ＋修正一輪／c 問答抽查；查核結果**已有讀取路徑**（監控頁「忠實度查核」卡片＋`--claims <id>` 逐條下鑽），不再只寫不看 | `app/services/faithfulness.py`、`web/routers/monitor.py`（`/api/progress` 的 `evaluation`）、`frontend/src/features/monitor/FaithfulnessPanel.tsx`、`scripts/eval_faithfulness.py` |
| **M9a** | **Typst 渲染引擎**（成為主軌，WeasyPrint 降為 fail-open 回退） | `app/services/typst_render.py`、`app/templates/ib-classic.typ` |
| **M9b** | 模板 registry ＋ `report_rendition` 不可變表 ＋ 零 LLM 換皮重出 ＋ 前端入口 | `app/templates/manifest.py`、`broker-modern.typ`、`privatebank-dark.typ`、`frontend/src/features/ask/RerenderControl.tsx` |
| **M10** | 雙語（zh-Hant／en）：a 問答與研報輸出語言／b 前端切換／c PDF chrome | `app/services/locale.py` |
| — | **背景執行 ＋ 逐節進度**：`POST /api/report` 只是訂閱端，斷線／重整不中止生成；`/api/report-runs*` 提供探詢、重連（重播＋直播）與主動取消 | `web/report_runs.py`、`web/routers/report.py`、`frontend/src/lib/reportProgress.ts` |

### 其他已上線

| 項目 | 內容 | 落點 |
|---|---|---|
| **觀點雷達** | 訊號擷取 → 跨券商共識聚合 → `/app/radar` | `app/services/signal_extract.py`、`app/services/radar/`、`web/routers/radar.py`、`research.report_signal` |
| **閱讀頁** | 單篇研報全文＋重點摘錄＋命中跳段，可分享網址 `/app/report/:hash` | `app/services/reading/`、`web/routers/reading.py`、`research.report_takeaway` |
| **前端 SPA** | React 19 ＋ TypeScript ＋ Vite（舊 vanilla 頁已退場） | `frontend/` |
| **CI 與分支保護** | 每個 PR 跑 pytest ＋ tsc/vitest 兩個必要檢查 | `.github/workflows/ci.yml` |
| **server.py 拆分** | 單體拆成 11 個 APIRouter ＋ `web/deps.py` 共用綁定層 | `web/routers/` |
| **生產韌性** | DB 自動重啟、免認證 `/healthz`、`OnFailure` 告警、systemd unit 收回 repo | `web/routers/health.py`、`deploy/systemd/`、`docs/production_resilience.md` |
| **定時同步** | NAS 增量匯入（3h）→ 自動補摘要 → 自動補重點摘錄 | `scripts/sync_new_reports.sh`、`report-mark-sync.timer` |

---

## 尚未實作

| 項目 | 說明 | 前置／阻礙 |
|---|---|---|
| **findb 整合** | 唯讀 Serve API 取行情與名稱，讓雷達能算「相對收盤的 upside」、時效題能引真實數字 | 不只是接線：findb 服務本身要可連（目前 `docker ps` 無 findb-app），且憑證與網路路徑屬跨專案部署問題，第一步不在本 repo |
| **每日簡報** | `brief.py` ＋ 前端頁 | 無技術前置。成本考量：會再增一條每日 `claude` CLI 批次，與既有的摘要／摘錄排程競爭同一支 CLI |
| **MCP server** | 把檢索／問答／雷達包成 agent 可消費的工具 | 選型未定：`hybrid_search` 需要**已算好的** query embedding，而 BGE-M3 是 2–4 GB 的行內 CPU 單例——stdio server 每次 spawn 都要重載模型，改走常駐 HTTP 則需先做金鑰認證 |
| **對外 REST `/api/v1/*`** | 機器可用的認證與 per-key 配額 | 全站目前只有一組共用帳密的 session cookie，無 API key 機制；昂貴端點僅靠 semaphore 擋。且尚無外部消費者的實際需求 |

---

## 關鍵原則

- **零重造檢索**：新功能一律重用 `hybrid_search` / `retrieval_pipeline`，不另建一套。
- **批次解耦且互斥**：訊號、摘要、摘錄擷取皆為可續跑的獨立批次，但**不可併發**——搶 `claude` CLI 會讓擷取被大量誤標 `rejected`（不是資料壞、也不是模型壞）。
- **邊界乾淨**：findb 只走唯讀 Serve API，不直連其 DB。
- **fail-open 優先**：派生功能（rerank、忠實度、追問、摘錄、換皮）失敗一律降級而非阻斷主流程。
- **背景 run 登錄表是行程內狀態，重啟即全滅**——`report_writer.open_run` 因此把「久無心跳（`REPORT_RUN_STALE_SECONDS`，預設 1800）的 in-flight run」也視為可重試。這兩者成對，拆一邊另一邊就是定時炸彈：一次 deploy 就會讓該冪等鍵永遠卡在「正在處理」。

## 暫不納入

掃描檔 OCR、GPU 加速 ingest、多帳號系統、通知／訂閱。
