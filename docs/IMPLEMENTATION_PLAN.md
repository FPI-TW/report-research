# 廷豐智能研報 — 實作計畫（交付 Claude Code 執行）

> 本文件把兩份設計藍圖轉成**可執行的里程碑 backlog**：
> - 研報：`docs/REPORT_GEN_REDESIGN.md`
> - 問答：`docs/QA_REDESIGN.md`
>
> 設計細節看藍圖；本文件只管**做什麼、動哪些檔、怎麼算完成**。依相依性排序，一個里程碑一個 session。

> **狀態校準（2026-07-14）**：M0、M2、M3、M4 已落地；M1 的**問答**題集、評測 runner 與基準線已落地。它們不得再重做。M1b（研報專用評測）、M4a（受信任時效資料）與 M4b（共用證據帳本）是後續工作的前置契約；其餘里程碑仍待執行。

---

## 如何使用（給操作者）

- 每次給 Claude Code **一個里程碑**，並附上對應藍圖章節連結。範例指令：
  > 「請實作 `docs/IMPLEMENTATION_PLAN.md` 的 **M0**，設計細節見 `docs/REPORT_GEN_REDESIGN.md` §3 Phase 0。完成後跑測試、更新該里程碑的驗收清單、依 repo 慣例提交。」
- 每個里程碑都設計成**可獨立上線、可回退**（feature flag 或 fail-open）。
- 先讀「狀態校準」：已完成的 M0–M4 只做回歸驗證或必要修補，不得覆寫既有契約；新工作由 **M1b 與 M4b-core → M4a → M4b 完整契約 → M5/M6 → M7** 開始。

---

## 全域約束（Claude Code 每個里程碑都必須遵守）

摘自 `CLAUDE.md`、`AGENTS.md`，違反會壞環境或污染他人 WIP：

1. **語言**：面向使用者的文案/回覆一律繁體中文；程式碼、識別字、路徑保留原文。**不加裝飾性 emoji**。
2. **Git**：共享工作目錄，**只 `git add <明確路徑>`，禁止 `git add -A`/`.`**；提交前 `git diff --staged --stat` 確認範圍。Conventional Commits + 繁中 scope（如 `feat(問答): ...`、`refactor(檢索): ...`）。
3. **Schema**：`db/schema.sql` 無 migration 工具，新欄位一律 `ALTER TABLE research.<t> ADD COLUMN IF NOT EXISTS ...`，保持冪等；套用用 `make schema`。
4. **`content_norm` 不變式**：DB 的 `content_norm` GENERATED 運算式必須與 `textnorm.norm_for_match()` **逐字節等價**——動到正規化就要兩邊同步。
5. **服務**：`make serve` 無 `--reload`，後端改動需重啟 `report-mark-web.service`（prod 為 systemd）；目前問答 SPA 的來源在 `frontend/`，需 build 產出 `frontend/dist`，不可把 React 變更誤當成 `web/static/**` 的即時靜態變更。
6. **唯讀來源**：`研報自動匯入/` 唯讀，**禁止寫入**。
7. **`claude` CLI 必須在 PATH**（tagging/summaries/Q&A/reports 都靠它；systemd 需顯式 PATH drop-in）。
8. **config 從 env**：新增可調參數用 `os.getenv("REPORT_MARK_* / ASK_* / REPORT_*", 預設)`，集中到 `app/config.py`（M0 建立）。
9. **測試**：Python `uv run pytest -q`；React SPA 用 `npm --prefix frontend test`、`npm --prefix frontend run typecheck` 與 `npm --prefix frontend run build`；僅修改 legacy 原生模組時才用 `node --test web/static/app/*.test.mjs`。**無 Python linter/formatter**，風格依 `AGENTS.md`。
10. **fail-open 原則**：任何新增的 LLM/模型步驟失敗、逾時、空回應，一律退回既有行為，不得讓問答/研報產不出來。

**每個里程碑的通用完成定義（DoD）**：功能以 flag 或 fail-open 包裹 → 新增/更新對應測試且相關套件全綠 → 若影響檢索/生成品質，跑**同範圍**的凍結 eval，比較相對基準線、有效題數、錯誤率與人工抽樣；單次 LLM 分數不作絕對 gate → 依上述 Git 慣例提交。

---

## 里程碑總覽與相依

```
M0 ✅ ── M1（QA）✅ ── M2 ✅ ── M1b 研報評測契約 ── M6 研報檢索 ──┐
M0 ✅ ── M4b-core 證據帳本 ──┬── M4b 完整契約 ─────────────────────┼── M7 逐節生成 ── M8 查核 ── M9 渲染
M4 ✅ ── M4a 受信任時效資料 ─┘                    └── M5 問答 agentic ┘
M3 ✅（獨立完成）
M10 雙語 / i18n：M4b 後開始，M10c 依賴 M9
```

建議執行序：**M1b 與 M4b-core → M4a → M4b 完整契約 →（M5 與 M6 可平行）→ M7 → M8 → M9 → M10**。M0–M4 僅做回歸驗證。

---

## M0 — 共用地基（已完成；僅回歸驗證）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 0 + `QA_REDESIGN.md` §8。

任務：
- `app/services/rows.py`：定義檢索結果 `NamedTuple`（`chunk_id, report_id, file_name, market, report_date, content, distance, …`），在 `store.py` 邊界回傳；下游 `retrieval.py`/`answer.py`/`report.py` 改用具名欄位，**移除** `answer.py:132` 的 `_RID,_FNAME…=1,2,3,6,14` 位移索引。
- `app/services/retrieval_pipeline.py`：抽 `retrieve_context(question, *, k, max_reports, ...) -> (sources, context)`，供問答與研報共用（取代兩檔各寫一次的 `embed→hybrid_search→build_context`）。
- 抽 `SentinelStreamParser`（EXT_SENTINEL 逐 chunk buffering，從 `answer.py:840-885`），補跨 chunk 邊界的單元測試。
- `app/config.py`：集中 `ASK_*`/`REPORT_*`（含目前在兩檔重複定義的 `ASK_DENSE_SCAN`）為一個 dataclass，載入一次。
- 把 `build_context` 的「選篇政策」抽成純函式 `select_reports(...)`，與字串格式化分離（為 M6 MMR 鋪路）。

驗收：
- `pytest` 全綠；**行為零變化**（同輸入產同輸出，可用既有 `tests/test_answer.py`/`test_report.py` 佐證）。
- `grep` 確認無殘留 `row[_RID]` 式位移索引。
- Commit：`refactor(檢索): 型別化 row 與共用檢索 pipeline`。

---

## M1 — 問答 Eval 地基（已完成；reference-free 基準線）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 5。**無黃金答案，用 RAGAS reference-free 指標。**

任務：
- `eval/dataset.py`：從 `research.qa_log` 萃取、人工凍結問答題集（金融範圍），落地為版本化檔案。
- `eval/run_ragas.py`：離線批次，初版指標為 Faithfulness / Context Precision / Answer Relevancy；Context Recall 僅在有人工參考答案、標記證據或可審核 pseudo-reference 時啟用。評審 LLM 走現有 `claude` CLI（Haiku/Sonnet）。**不進 `web/server.py` 請求路徑。**
- 產出基準線報表（存 `eval/baselines/`）。

驗收：
- `uv run python eval/run_ragas.py` 可跑出指標並存基準線。
- 此 runner 僅評估問答路徑；`thresholds_pass` 只供觀察，CI 不得以單次絕對分數阻擋合併。每次比較須記錄有效題數、錯誤率、無脈絡數與人工抽樣結論。
- Commit：`feat(評測): 建立 reference-free RAG 評測與基準線`。

---

## M1b — 研報評測契約與基準線（待執行）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 5。**依賴 M0、M2；不得重用 M1 的問答 runner 冒充研報評測。**

任務：
- 新增版本化研報題集：每題含主題、market、預期子題／面向、允許的來源類型，以及人工審核的無資料情境；題集需與問答題集分檔、分版本。
- 新增研報 eval runner，實際收集 `generate_report` 的完整 Markdown、來源與階段輸出；同時產出檢索層的子題覆蓋率、來源／日期多樣性，以及生成層的章節覆蓋率、引用完整率與外部來源標示率。
- 將「數值主張支持率」保留給 M8 的 grounding 結果；M1b 只能量測可由結構化 evidence link 判斷的覆蓋率，不得以 LLM 猜測取代。

驗收：
- 題集、逐題結果與評分規則版本化；每個指標都定義分母、缺資料行為與最低有效題數。
- 比較使用相對基準線、容許誤差、錯誤率與人工抽樣；Context Recall 僅在題目有人工證據／pseudo-reference 時啟用。
- Commit：`feat(評測): 建立研報專用評測契約與基準線`。

---

## M2 — Rerank 接入（已完成；問答單輪 + 研報共用）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 1（reranker 段）。

任務：
- `app/services/rerank.py`：封裝 **BGE-reranker-v2-m3**（CPU、延遲載入單例，比照 `embed.py` 的模型落地慣例）；輸入 `(query, candidates)` 輸出重排後 top-N。
- 插在 `retrieval_pipeline` 的 `hybrid_search` 之後、選篇之前；計時記 `rerank_ms`。
- **候選上限**：問答路徑比研報保守（顧延遲）；env 化。
- fail-open：模型載入/推論失敗 → 跳過重排、用原融合序。

驗收：
- M1 問答 eval 在容許誤差內不退步，並記錄有效題數／錯誤率與人工抽樣；不要求單次 LLM judge 的每一項分數必然上升。
- 問答快速路徑延遲增幅在可接受範圍（記錄前後 `qa_timing`）。
- Commit：`feat(檢索): 接入 BGE-reranker-v2-m3 重排`。

---

## M3 — 問答 UX 補齊（已完成；低風險）

藍圖：`QA_REDESIGN.md` §3-C。前端已有 `ThinkingSteps`/`SourcesDrawer`/`AssistantMessage`/`UserMessage`/`Composer` 骨架。

任務（前端 `frontend/src/features/ask/` + 後端 `web/server.py`/`answer.py` 加法事件）：
- **重新生成**：`AssistantMessage` 加鈕；後端支援同 `conversation_id` 重跑覆蓋上一輪。
- **停止生成**：訊息列/Composer 加停止鈕；中止 SSE，保留已串流內容（`stream_completion` 已能 kill）。
- **編輯重問**：`UserMessage` 編輯態，以新問題開一輪、截斷其後歷史。
- **追問建議**：`app/services/followups.py`（Haiku、**限定金融範圍**）；主答完成後以加法 `followups` 事件回傳，前端顯示底部 chips。
- **步驟卡可展開**：`ThinkingSteps` 收合/展開，對應擴充後的 `status.stage`。

驗收：
- 新增事件為**加法**、舊前端忽略仍可運作（相容）。
- React 前端測試、typecheck 與 build 綠。
- 追問建議不引導出金融範圍。
- Commit：`feat(問答): Claude-like 互動（重新生成/停止/編輯/追問）`。

---

## M4 — 範圍路由（已完成；拒答 → 分流）

藍圖：`QA_REDESIGN.md` §3-A。

任務：
- `scope_router.py` 回傳問題類型 + `tool_policy`：`off_topic | overview | corpus_qa | time_sensitive | advice_risk`；金融知識問題維持 fail-open，但時效與個人化建議不可因 fail-open 失去保護。
- 首輪 `classify_non_overview` 與續問 `condense_and_route` 共用確定性 overview／安全前檢規則。**在 M4a 前，`time_sensitive` 必須安全婉拒，不可假設一般網搜等同受信任資料。**
- 完全離題改為更自然的婉拒文案（Claude-like），非僵硬拒絕。

驗收：
- 既有 `tests/test_answer.py` 相容；新增路由分類測試（含曾漂移的「收盤價」類案例、無外部資料時的安全退化、個人化買賣指令）。
- 枚舉題仍正確走 `overview.py`。
- Commit：`refactor(問答): 離題閘升級為問題類型與工具政策路由`。

---

## M4a — 受信任時效資料契約（待執行）

藍圖：`QA_REDESIGN.md` §3-A、§3-B。**依賴 M4；完成前維持 M4 的安全婉拒。**

任務：
- 定義 `trusted_market_data` adapter 介面與每種時效題的核准來源：報價、公告／財報、利率／政策各自的 provider、允許網域、欄位、交易所時區與最大資料年齡。
- Python adapter 回傳結構化 `{value, unit, as_of, published_at, url, source_type, profile_id, snapshot_ref, content_hash}`；`content_hash` 必須是 canonical payload 的雜湊，`snapshot_ref` 指向不可變原始內容快照。只有此結構能進入時效答案與 evidence ledger。provider 逾時、欄位缺漏、資料過舊、快照寫入失敗或無來源時一律回安全婉拒。
- 將來源驗證、快取 TTL、速率限制、取消傳播、快照保留期與「不得把研報當即時資料」寫成測試；Claude 只能決定是否需要時效資料，不能自由挑選網頁或繞過 allowlist。

驗收：
- 每類時效題都有 adapter 成功、過期、provider 失敗、不在 allowlist、canonical payload 雜湊不符與快照寫入失敗的測試；答案顯示資料時間與來源性質。
- 對外網頁文字不可直接當成受信任時效數值；M4 既有婉拒在 adapter 不可用時仍成立。
- Commit：`feat(問答): 建立受信任時效資料來源契約`。

---

## M4b — 共用證據帳本與來源政策（待執行）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 0.5／Phase 2、`QA_REDESIGN.md` §3-A／§3-B。**core 可在 M0 後開始；包含時效 profile 的完整契約依賴 M4a；M5、M7、M8 都依賴完整 M4b。**

任務：
- **M4b-core：**新增 `app/services/evidence.py`，定義不可變 `evidence_id`、來源類型、corpus 的 report/chunk ID 與內容版本；提供序列化、去重、`[n]` 最終渲染與 schema 驗證。
- **完整來源契約：**定義唯一 source profile registry。外部 evidence 必含 `profile_id`、URL、來源類型、發布／取得時間、`snapshot_ref` 與 canonical payload hash；`trusted_market_data` 只供時效資料，`controlled_research_web` 只供深度研報的非時效補充，一般 WebSearch 不是 evidence producer。
- `qa_log` 與 `report_doc` 新增 evidence manifest 欄位；歷史列以空 manifest 安全退化。快照以受控儲存保存，manifest 只保存參照與 hash；外部來源只能由已註冊 profile 的 Python adapter 寫入。
- 建立「每個 claim／KPI／chart 只能指向已分配 evidence」的資料契約；本階段不判斷主張真偽，真偽判斷留給 M8。

驗收：
- corpus、外部、重複來源、舊資料、snapshot 缺失／hash 不符與未註冊 profile 的序列化／反序列化測試；`[n]` 在多節重編後仍穩定。
- M5/M7 可以只依公開介面使用帳本，無需知道資料庫欄位細節。
- Commit：`feat(證據): 建立共用 evidence ledger`。

---

## M5 — 問答 Agentic 迴圈（受控多輪 + 快速路徑）

藍圖：`QA_REDESIGN.md` §3-B、§3-E。**依賴 M0、M2、M4a、M4b；共用 `query_planner`。**

任務：
- `app/services/query_planner.py`（輕量版）：Haiku 僅輸出 schema 驗證後的 1–N 子查詢與新鮮度需求；Python 正規化、去重、限制每題子查詢數／長度／總預算。除 overview／純操作題外不得判「免檢索直接答」。
- `app/services/agentic_qa.py`：Python 編排迴圈（規劃→檢索+rerank→評估→作答），`QA_MAX_ROUNDS` 預設 2。
- **快速路徑豁免**：僅限非時效的單一語料事實；報價／公告仍須走外部資料政策並顯示截至時間，不能由 `_TRIVIAL_HINTS` 單獨判定。
- 問答與研報使用 M4b 共用 `evidence_id`／來源帳本；時效外部論點只能來自 M4a adapter，不混入研報 `[n]`。
- 對每請求設定子查詢、候選、adapter 呼叫與總逾時預算；取消 ask 時取消尚未開始的背景工作。問答不啟用模型自由網搜。
- fail-open：規劃/評估異常 → 退回既有一次性 RAG。

驗收：
- 凍結問答題集：綜合題 Faithfulness／Context Precision 相對基準線不退步，並記錄延遲分佈；Context Recall 僅在具備可審核 reference 時啟用。
- 非時效快速路徑題延遲 ≈ 現況 + rerank；時效題必須驗證截至時間與外部來源標示。
- Commit：`feat(問答): 受控 agentic 多輪檢索與快速路徑`。

---

## M6 — 研報檢索增強（多查詢分解 + MMR）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 1。**依賴 M0、M1b、M2。**

任務：
- `query_planner`（深度版）：以嚴格 JSON schema 輸出最多 8 個面向子查詢；Python 正規化／去重、限制總候選數與全域檢索併發，再合併、去重、rerank。
- 在 `select_reports` 加入 **MMR 多樣性**：明訂冗餘度使用已持久化的 chunk embedding（不可重新 embed 全文），以及子題、`source`、報告日期的可設定上限與不足時的退化規則。
- 僅抽出問答／研報共用的 context-selection tier 定義；`retrieval.rank_reports` 是搜尋頁可分頁排名，保留其獨立 band 契約，禁止為消除數字差異而硬合流。

驗收：
- 研報題集的子題覆蓋率與來源多樣性提升、無同質冗餘惡化；Context Recall 僅在具備可審核 reference 時另行量測。
- Commit：`feat(研報): 多角度查詢分解與 MMR 選取`。

---

## M7 — 研報生成重構（大綱 → 逐節）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 2。**依賴 M4b、M6。**

任務：
- 先新增 `report_run` 與 `report_section`：run 保存冪等 request key、狀態、輸入設定、evidence manifest hash、outline、checkpoint、錯誤與目前 revision；section 保存 outline 順序、草稿／最終內容與 evidence link。狀態固定為 `queued → retrieving → outlining → drafting → verifying → rendering → completed | failed | cancelled`，每次轉換原子持久化。
- `app/services/report_writer.py`：大綱生成（固定骨架 + 動態子節）→ 逐節針對性檢索 → 逐節草稿 → 依 M4b 證據帳本組裝。可並行準備檢索／草稿，但必須依 outline 順序 commit，且不得並行寫同一帳本。
- `report.py::generate_report` 改為薄編排；沿用 `REPORT_TIMEOUT=600`。新增 SSE `section_draft`（可變草稿）與 `document_revision`（已查核最終版本，帶 revision id、markdown hash 與完整內容或驗證 URL）；舊前端可忽略這兩個加法事件。PDF、下載與歷史只能指向最終 revision。
- `report_doc` 加 `outline`、`evidence_manifest`、`claim_evidence`、目前 revision 指標（jsonb／欄位皆以 `ADD COLUMN IF NOT EXISTS`）；`report_run`／`report_section` 以冪等 `CREATE TABLE IF NOT EXISTS` 建立。
- fail-open：僅首個內容 token 前可退回既有「單次生成」；其後只從最後一致 checkpoint 重試，或標示 `failed`／`cancelled`，不得改跑另一份完整內容。

驗收：
- 研報專用凍結題集：引用完整率、證據連結覆蓋率、章節覆蓋率與 Faithfulness 相對基準線不退步；數值主張支持率在 M8 才驗收。測試草稿、最終 revision、PDF 與歷史重播皆指向相同 revision；全文潤飾只能改銜接語，若更動任何事實主張，必須重新建立 evidence link 並交 M8 查核。延遲在數分鐘內（品質優先，已確認可接受）。
- PDF 仍可由 `markdown` 重建。
- Commit：`feat(研報): 大綱驅動逐節生成`。

---

## M8 — 忠實度查核（研報完整 + 問答迷你）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 3、`QA_REDESIGN.md` §3-D。

任務：
- `app/services/faithfulness.py`：RAGAS 式 claim 拆解 + 逐條 grounding；標記數值型主張並以 `evidence_id` 對應 corpus/web 證據。
- 研報：生成草稿後查核，低分主張要求修正一輪；修正完成才建立 `document_revision`。`report_doc.evaluation` 保存 citation coverage、numeric support rate、faithfulness 與原始判定，避免將分數解讀成真實性保證。
- 問答：迷你版——數值主張抽查，可非同步於寫 `qa_log` 時做；外部來源同樣可追溯。
- fail-open：查核異常不阻擋交付。

驗收：
- 注入含錯誤數字的測試樣本能被標記。
- 不顯著惡化延遲（問答查核非同步/抽樣）。
- Commit：`feat(查核): claim grounding 忠實度檢查`。

---

## M9 — Typst 可選模板渲染層（雙軌，內容穩定後）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 4 + 可選模板設計（本次擴充）。

> **決策（2026-07-15）**：渲染路線拍板**直接走 Typst**，WeasyPrint 視覺化改版計畫（`docs/superpowers/plans/2026-07-08-report-claude-design-backend.md`）不執行。M9a 前置 spike 已完成——converter 選定 pandoc（`gfm-tex_math_dollars`，經 `pypandoc-binary`）、編譯走 typst-py、圖表首版重用 `chart.py` SVG、生產主機字型已具備；實證結論與 fixtures 見 `docs/typst_spike_m9a.md` 與 `docs/typst_spike/`。

**核心原則：內容與版型解耦**——`markdown` 為真相、```kpi/```chart 為結構化區塊，故「選模板」只換渲染層，**不動任何內容生成邏輯**。切成 M9a（引擎 + 契約 + 首款模板）與 M9b（registry + 選擇 + 換皮），可獨立上線。

### M9a — Typst 渲染引擎 + 模板契約 + 首款模板

任務：
- `app/services/typst_render.py`：`markdown`→經選定 converter 的型別化中介資料模型→Typst→ `typst compile` → PDF。
- 先以固定的 Markdown／KPI／chart fixture 比較 converter，選定一個受維護的轉換器；轉換結果必須進入型別化中介資料模型，不可把 LLM Markdown 原文直接拼接成 Typst。
- **定義「模板契約」**：每個模板需接收同一組固定區塊（標題／評等框／執行摘要／關鍵發現／重點分析／風險展望／引用來源／免責）＋ ```kpi/```chart 資料，並各自排版。契約以 Typst 函式簽章或傳入的 data model 固定下來，後續模板一律遵守。
- `app/templates/ib-classic.typ`：首款模板（國際投行密集雙欄，即先前 mockup 那款）；封面/頁首尾/評等框/KPI 卡/圖表。```kpi/```chart fence JSON 映射為 Typst 函式；圖表用原生套件（cetz/lilaq），評估退役 `chart.py` 的 matplotlib。
- **繁中字型**：安裝並指定思源宋體/Noto Serif CJK TC（部署機需裝字型，否則豆腐字）。
- env flag `REPORT_RENDERER=weasyprint|typst` 雙軌；`markdown` 永遠可回退 WeasyPrint 重建。
- 編譯前驗證 fence JSON、跳脫文字、拒絕任意檔案路徑與未核准 URL；Typst 在限制檔案讀取、無網路的子程序／sandbox 執行，編譯錯誤 fail-open 回退 WeasyPrint。
- 部署：`typst` binary / PyPI + systemd PATH（同 `claude` CLI）。

驗收：
- 同一份 `markdown` 在兩渲染器都能出 PDF；`ib-classic` 繁中正常、KPI/圖表正確對應來源。
- Commit：`feat(渲染): 導入 Typst 渲染引擎與 ib-classic 模板`。

### M9b — 可選模板 registry + `template_id` 貫穿 + 換皮重出

任務：
- `app/templates/manifest`（py 或 json）：登錄可用模板（`id`、名稱、說明、縮圖、`is_default`）。
- 再交付兩款模板，皆實作 M9a 的模板契約：`broker-modern`（現代簡潔單欄）、`privatebank-dark`（深色高階私銀）。
- **參數貫穿**：`/api/report` body 加 `template_id`（預設由 `config`）→ `generate_report(..., template_id)` → `typst_render` 依 id 取模板；**未知 id → 退回預設（fail-safe）**。內容生成邏輯零改動。
- **持久化**：新增不可變 `report_rendition`（report ID、renderer、template ID、content hash、PDF path、建立時間、狀態），`report_doc` 僅保存目前 rendition 指標；舊 `pdf_path` 作相容讀取，不覆蓋歷史產物。
- **換皮重出**：新增 `POST /api/report-doc/{id}/rerender { template_id }`，用既有 `markdown` 以另一模板建立新 rendition，成功後原子切換目前版本——**零 LLM、零重新生成**；失敗保留上一個可下載 PDF。
- **前端**：`DeepReportPanel` 生成前加模板選擇器（縮圖卡，讀 manifest）；研報詳情頁加「換模板重新產出」。縮圖走 `web/static`（`_NoCacheStatic`）。

驗收：
- 同一份研報可用三款模板分別出 PDF，版型正確、繁中正常、KPI/圖表對應無誤。
- 未知 `template_id` 安全退回預設；`rerender` **不觸發任何 LLM**。
- 前端可選模板，新舊研報都能換皮；`template_id` 為加法，舊請求不帶則走預設（相容）。
- Commit：`feat(渲染): 可選模板 registry 與換皮重出`。

> **白牌紅利**：模板即品牌——此 registry 天然支撐白牌（每客戶一套 `.typ`，同內容多品牌輸出），見商業化延伸清單。

---

## M10 — 雙語 / 國際化（i18n，英文使用者可用）

**關鍵前提**：BGE-M3 embedder 與 BGE-reranker-v2-m3 皆多語，故**英文查詢可跨語言檢索中文研報語料**（dense + rerank 開箱即用），只有 pg_trgm 字面路徑不跨語言。

**核心原則**：證據留原文（供 grounding 與可回溯）、輸出語言隨使用者 `locale`、來源標題原文 + 可選譯名。`locale` 以類似 `template_id` 的方式貫穿。建議在 M0（config）、M4/M5（prompt 參數化）、M9（模板 chrome）之後或同步進行。切三段獨立上線。

### M10a — 輸出語言 + 跨語言檢索（後端）

任務：
- **`locale` 貫穿**：`/api/ask`、`/api/report` 僅接受 `zh-Hant` | `en`；優先序固定為明確 request locale → 已持久化使用者偏好（尚無帳號偏好欄位時不宣稱支援）→ `Accept-Language` → config 預設。將最終 locale 寫入 `qa_log`、`report_doc`（`ADD COLUMN IF NOT EXISTS`）。
- **系統提示參數化**：`SYSTEM_PROMPT`/`REPORT_SYSTEM_PROMPT`/`OVERVIEW_SYSTEM_PROMPT`/intent/condense 從硬編「一律繁體中文」改為「以 `{locale}` 作答」；英文 locale 時以英文輸出，**引用規則不變、仍引用中文來源 `[n]`**。
- **跨語言檢索**：dense（BGE-M3）+ rerank 已跨語言；對 `en` 查詢，額外把查詢翻成中文餵給**字面路徑**（補 pg_trgm 不跨語言的洞），或純靠 dense+rerank——先用 M1 eval 量測再決定。
- **來源呈現**：`sources` 帶原文標題 + 可選 `title_i18n`（Haiku 快速翻譯標題供辨識），**不動底層證據內容**。
- **文案 i18n**：`overview` 的 `render_overview_text`、離題婉拒、`NO_CONTEXT_MESSAGE` 等模板字串雙語化。
- fail-open：`locale` 解析不到 → 預設 `zh-Hant`。

驗收：
- 英文問句能檢索到相關中文研報，並以**英文帶 `[n]` 引用**作答；翻譯字面查詢的 provider、快取、逾時與失敗回退必須明訂並量測，中文行為零回歸（M1 eval）。
- Commit：`feat(i18n): 問答與研報的輸出語言與跨語言檢索`。

### M10b — 前端 i18n + 語言切換

任務：
- 前端字串抽成 locale 資源（React SPA `frontend/` + `web/static`），加語言切換器、偏好持久化。
- 日期/數字在地化格式；來源卡顯示原文標題 + 譯名；登入頁與錯誤訊息 i18n。

驗收：
- 全 UI 可切 `zh-Hant`/`en`；只有在使用者偏好已有資料模型時才承諾跨裝置保存，否則限瀏覽器本地偏好；前端測試、typecheck、build 綠。
- Commit：`feat(i18n): 前端雙語介面與語言切換`。

### M10c — 英文研報模板（接 M9 registry）

任務：
- M9 模板契約的 chrome 標籤 i18n（Executive Summary / Key Findings / Rating / Target Price / Disclaimer…），或提供 `*-en` 模板變體；由 `locale` 決定 chrome 語言。
- 英文襯線字型（如 Source Serif / Noto Serif）與繁中字型並存。

驗收：
- 同一份**證據帳本**可分別產生繁中與英文 Markdown／PDF；不得把中文 Markdown 僅換模板就宣稱為英文研報。版型、字型、KPI／圖表標籤隨 locale。
- Commit：`feat(i18n): 英文研報模板 chrome`。

> **未來延伸（不在本里程碑）**：把英文/外資研報**納入語料**（bilingual corpus）——tagging 提示需支援英文，BGE-M3 多語使檢索天然可行；對應商業化清單的「雙語外資通道」。這是「英文使用者查中文語料」之外、更大的一步。

---

## 附：建議先跑的三個里程碑

**M1b + M4a + M4b** 應先行：先補齊研報評測、時效資料安全邊界與共用證據帳本，再進入 agentic 問答與逐節研報；這能避免用既有問答 eval、一般網搜或模型自編引用誤當成可交付的金融資料治理。
