# 廷豐智能研報 — 實作計畫（交付 Claude Code 執行）

> 本文件把兩份設計藍圖轉成**可執行的里程碑 backlog**：
> - 研報：`docs/REPORT_GEN_REDESIGN.md`
> - 問答：`docs/QA_REDESIGN.md`
>
> 設計細節看藍圖；本文件只管**做什麼、動哪些檔、怎麼算完成**。依相依性排序，一個里程碑一個 session。

---

## 如何使用（給操作者）

- 每次給 Claude Code **一個里程碑**，並附上對應藍圖章節連結。範例指令：
  > 「請實作 `docs/IMPLEMENTATION_PLAN.md` 的 **M0**，設計細節見 `docs/REPORT_GEN_REDESIGN.md` §3 Phase 0。完成後跑測試、更新該里程碑的驗收清單、依 repo 慣例提交。」
- 每個里程碑都設計成**可獨立上線、可回退**（feature flag 或 fail-open）。
- 先做 **M0 → M1 → M2**（共用地基與量測），之後 UX(M3) 可與後端平行推進。

---

## 全域約束（Claude Code 每個里程碑都必須遵守）

摘自 `CLAUDE.md`、`AGENTS.md`，違反會壞環境或污染他人 WIP：

1. **語言**：面向使用者的文案/回覆一律繁體中文；程式碼、識別字、路徑保留原文。**不加裝飾性 emoji**。
2. **Git**：共享工作目錄，**只 `git add <明確路徑>`，禁止 `git add -A`/`.`**；提交前 `git diff --staged --stat` 確認範圍。Conventional Commits + 繁中 scope（如 `feat(問答): ...`、`refactor(檢索): ...`）。
3. **Schema**：`db/schema.sql` 無 migration 工具，新欄位一律 `ALTER TABLE research.<t> ADD COLUMN IF NOT EXISTS ...`，保持冪等；套用用 `make schema`。
4. **`content_norm` 不變式**：DB 的 `content_norm` GENERATED 運算式必須與 `textnorm.norm_for_match()` **逐字節等價**——動到正規化就要兩邊同步。
5. **服務**：`make serve` 無 `--reload`，後端改動需重啟 `report-mark-web.service`（prod 為 systemd）；`web/static/**` 靜態資產即時生效。
6. **唯讀來源**：`研報自動匯入/` 唯讀，**禁止寫入**。
7. **`claude` CLI 必須在 PATH**（tagging/summaries/Q&A/reports 都靠它；systemd 需顯式 PATH drop-in）。
8. **config 從 env**：新增可調參數用 `os.getenv("REPORT_MARK_* / ASK_* / REPORT_*", 預設)`，集中到 `app/config.py`（M0 建立）。
9. **測試**：Python `uv run pytest -q`；前端 ES module `node --test frontend/... 或 web/static/app/*.test.mjs`。**無 linter/formatter**，風格依 `AGENTS.md`。
10. **fail-open 原則**：任何新增的 LLM/模型步驟失敗、逾時、空回應，一律退回既有行為，不得讓問答/研報產不出來。

**每個里程碑的通用完成定義（DoD）**：功能以 flag 或 fail-open 包裹 → 新增/更新對應測試且 `pytest` 全綠 → 若影響檢索/生成品質，跑 M1 eval 確認**未回歸** → 依上述 Git 慣例提交。

---

## 里程碑總覽與相依

```
M0 共用地基 ──┬── M1 Eval 地基 ──┬── M2 Rerank 接入
              │                   ├── M6 研報檢索增強(多查詢+MMR) ── M7 研報大綱→逐節
              ├── M4 範圍路由 ─────── M5 問答 agentic 迴圈
              ├── M8 忠實度查核
              └── M9 Typst 可選模板渲染（M9a 引擎+首款 → M9b registry+換皮）
M3  問答 UX 補齊（低風險，可與 M1+ 平行）
M10 雙語 / i18n（貫穿式；M10a 後端跨語言＋輸出語言 → M10b 前端 → M10c 英文模板，接 M9）
```

建議執行序：**M0 → M1 → M2 →（M3 平行）→ M4 → M5 → M6 → M7 → M8 → M9 →（M10 雙語，跨越 M4/M5/M9）**

---

## M0 — 共用地基（重構，不改行為）

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

## M1 — Eval 地基（reference-free 基準線）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 5。**無黃金答案，用 RAGAS reference-free 指標。**

任務：
- `eval/dataset.py`：從 `research.qa_log` / 既有研報主題萃取固定評測題集（金融範圍），落地為版本化檔案。
- `eval/run_ragas.py`：離線批次，初版指標為 Faithfulness / Context Precision / Answer Relevancy；Context Recall 僅在有人工參考答案、標記證據或可審核 pseudo-reference 時啟用。評審 LLM 走現有 `claude` CLI（Haiku/Sonnet）。**不進 `web/server.py` 請求路徑。**
- 產出基準線報表（存 `eval/baselines/`）。

驗收：
- `uv run python eval/run_ragas.py` 可跑出指標並存基準線。
- 凍結問答／研報分離題集並保存逐題結果；文件化基準線與容許誤差。LLM 評審以相對基準線加人工抽樣複核作 gate，不以單次絕對分數或未實作的 Context Recall 阻擋合併。
- Commit：`feat(評測): 建立 reference-free RAG 評測與基準線`。

---

## M2 — Rerank 接入（問答單輪 + 研報共用）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 1（reranker 段）。

任務：
- `app/services/rerank.py`：封裝 **BGE-reranker-v2-m3**（CPU、延遲載入單例，比照 `embed.py` 的模型落地慣例）；輸入 `(query, candidates)` 輸出重排後 top-N。
- 插在 `retrieval_pipeline` 的 `hybrid_search` 之後、選篇之前；計時記 `rerank_ms`。
- **候選上限**：問答路徑比研報保守（顧延遲）；env 化。
- fail-open：模型載入/推論失敗 → 跳過重排、用原融合序。

驗收：
- M1 eval：Context Precision / Faithfulness **較基準線上升**、Answer Relevancy 不退步；若日後接入有依據的 Context Recall，再另列其基準線。
- 問答快速路徑延遲增幅在可接受範圍（記錄前後 `qa_timing`）。
- Commit：`feat(檢索): 接入 BGE-reranker-v2-m3 重排`。

---

## M3 — 問答 UX 補齊（低風險，可平行）

藍圖：`QA_REDESIGN.md` §3-C。前端已有 `ThinkingSteps`/`SourcesDrawer`/`AssistantMessage`/`markdown.js` 骨架。

任務（前端 `frontend/src/features/ask/` + 後端 `web/server.py`/`answer.py` 加法事件）：
- **重新生成**：`AssistantMessage` 加鈕；後端支援同 `conversation_id` 重跑覆蓋上一輪。
- **停止生成**：訊息列/Composer 加停止鈕；中止 SSE，保留已串流內容（`stream_completion` 已能 kill）。
- **編輯重問**：`UserMessage` 編輯態，以新問題開一輪、截斷其後歷史。
- **追問建議**：新 `app/services/qa_followups.py`（Haiku、**限定金融範圍**），答完隨 `done` 回 3 條；前端底部 chips。
- **步驟卡可展開**：`ThinkingSteps` 收合/展開，對應擴充後的 `status.stage`。

驗收：
- 新增事件為**加法**、舊前端忽略仍可運作（相容）。
- 前端測試（`*.test.tsx`）綠；`node --test` 綠。
- 追問建議不引導出金融範圍。
- Commit：`feat(問答): Claude-like 互動（重新生成/停止/編輯/追問）`。

---

## M4 — 範圍路由（拒答 → 分流）

藍圖：`QA_REDESIGN.md` §3-A。

任務：
- 將 `intent.py` 重構為問題類型 + `tool_policy`：`off_topic | overview | corpus_qa | time_sensitive | advice_risk`；金融知識問題維持 fail-open，但時效與個人化建議不可因 fail-open 失去保護。
- 放寬金融相鄰題（個股/總經/期貨/匯率/加密），時效型報價／公告強制使用受信任外部資料並標示截至時間；首輪與 `condense_and_classify` **共用同一判準**（防「首輪 IN、續問 OUT」漂移）。
- 完全離題改為更自然的婉拒文案（Claude-like），非僵硬拒絕。

驗收：
- 既有 `tests/test_answer.py` 相容；新增路由分類測試（含曾漂移的「收盤價」類案例、無外部資料時的安全退化、個人化買賣指令）。
- 枚舉題仍正確走 `overview.py`。
- Commit：`refactor(問答): 離題閘升級為問題類型與工具政策路由`。

---

## M5 — 問答 Agentic 迴圈（受控多輪 + 快速路徑）

藍圖：`QA_REDESIGN.md` §3-B、§3-E。**依賴 M0、M2；共用 `query_planner`。**

任務：
- `app/services/query_planner.py`（輕量版）：Haiku 依 `tool_policy` 提 1–N 子查詢與新鮮度需求；除 overview／純操作題外不得判「免檢索直接答」。
- `app/services/agentic_qa.py`：Python 編排迴圈（規劃→檢索+rerank→評估→作答），`QA_MAX_ROUNDS` 預設 2。
- **快速路徑豁免**：僅限非時效的單一語料事實；報價／公告仍須走外部資料政策並顯示截至時間，不能由 `_TRIVIAL_HINTS` 單獨判定。
- 問答與研報共用 `evidence_id`／來源帳本：外部論點保存 URL、來源類型、發布／取得時間與內容雜湊，不混入研報 `[n]`。
- 對每請求設定子查詢、候選、外部搜尋與總逾時預算；取消 ask 時取消尚未開始的背景工作。
- fail-open：規劃/評估異常 → 退回既有一次性 RAG。

驗收：
- 凍結問答題集：綜合題 Faithfulness／Context Precision 相對基準線不退步，並記錄延遲分佈；Context Recall 僅在具備可審核 reference 時啟用。
- 非時效快速路徑題延遲 ≈ 現況 + rerank；時效題必須驗證截至時間與外部來源標示。
- Commit：`feat(問答): 受控 agentic 多輪檢索與快速路徑`。

---

## M6 — 研報檢索增強（多查詢分解 + MMR）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 1。**依賴 M0、M2。**

任務：
- `query_planner`（深度版）：主題展開 6–8 面向子查詢（依 `market` 微調），各自檢索、合併去重、rerank。
- 在 `select_reports`（M0 抽出）加入 **MMR 多樣性**，與既有 tier/新近度/過舊配額並存。
- 與 `retrieval.rank_reports` 合流到同一套 tier/band 定義（消除 0.05 vs 0.10 漂移）。

驗收：
- 研報題集的子題覆蓋率與來源多樣性提升、無同質冗餘惡化；Context Recall 僅在具備可審核 reference 時另行量測。
- Commit：`feat(研報): 多角度查詢分解與 MMR 選取`。

---

## M7 — 研報生成重構（大綱 → 逐節）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 2。**依賴 M6。**

任務：
- `app/services/evidence.py`：定義穩定 `evidence_id` 與來源帳本，持久化 corpus/web 來源、報告／chunk 或 URL、發布／取得時間、內容雜湊；最後才渲染 `[n]`。
- `app/services/report_writer.py`：大綱生成（固定骨架 + 動態子節）→ 逐節針對性檢索 → 逐節串流撰寫 → 依證據帳本組裝。首個內容 token 前可回退單次生成；串流開始後保存 checkpoint，不得改跑另一份完整內容。
- `report.py::generate_report` 改為薄編排；沿用 `REPORT_TIMEOUT=600`；串流逐節 yield。
- `report_doc` 加 `outline`、`evidence_manifest`、`claim_evidence`（jsonb）欄位（`ADD COLUMN IF NOT EXISTS`）。
- fail-open：僅首個內容 token 前可退回既有「單次生成」；其後以 checkpoint／明確終止狀態避免重複輸出。

驗收：
- 研報專用凍結題集：引用完整率、數值主張支持率、章節覆蓋率與 Faithfulness 相對基準線不退步；延遲在數分鐘內（品質優先，已確認可接受）。
- PDF 仍可由 `markdown` 重建。
- Commit：`feat(研報): 大綱驅動逐節生成`。

---

## M8 — 忠實度查核（研報完整 + 問答迷你）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 3、`QA_REDESIGN.md` §3-D。

任務：
- `app/services/faithfulness.py`：RAGAS 式 claim 拆解 + 逐條 grounding；標記數值型主張並以 `evidence_id` 對應 corpus/web 證據。
- 研報：生成後查核，低分主張要求修正一輪；`report_doc.evaluation` 保存 citation coverage、numeric support rate、faithfulness 與原始判定，避免將分數解讀成真實性保證。
- 問答：迷你版——數值主張抽查，可非同步於寫 `qa_log` 時做；外部來源同樣可追溯。
- fail-open：查核異常不阻擋交付。

驗收：
- 注入含錯誤數字的測試樣本能被標記。
- 不顯著惡化延遲（問答查核非同步/抽樣）。
- Commit：`feat(查核): claim grounding 忠實度檢查`。

---

## M9 — Typst 可選模板渲染層（雙軌，內容穩定後）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 4 + 可選模板設計（本次擴充）。

**核心原則：內容與版型解耦**——`markdown` 為真相、```kpi/```chart 為結構化區塊，故「選模板」只換渲染層，**不動任何內容生成邏輯**。切成 M9a（引擎 + 契約 + 首款模板）與 M9b（registry + 選擇 + 換皮），可獨立上線。

### M9a — Typst 渲染引擎 + 模板契約 + 首款模板

任務：
- `app/services/typst_render.py`：`markdown`→Typst（cmarker/pandoc）→ `typst compile` → PDF。
- **定義「模板契約」**：每個模板需接收同一組固定區塊（標題／評等框／執行摘要／關鍵發現／重點分析／風險展望／引用來源／免責）＋ ```kpi/```chart 資料，並各自排版。契約以 Typst 函式簽章或傳入的 data model 固定下來，後續模板一律遵守。
- `app/templates/ib-classic.typ`：首款模板（國際投行密集雙欄，即先前 mockup 那款）；封面/頁首尾/評等框/KPI 卡/圖表。```kpi/```chart fence JSON 映射為 Typst 函式；圖表用原生套件（cetz/lilaq），評估退役 `chart.py` 的 matplotlib。
- **繁中字型**：安裝並指定思源宋體/Noto Serif CJK TC（部署機需裝字型，否則豆腐字）。
- env flag `REPORT_RENDERER=weasyprint|typst` 雙軌；`markdown` 永遠可回退 WeasyPrint 重建。
- 部署：`typst` binary / PyPI + systemd PATH（同 `claude` CLI）。

驗收：
- 同一份 `markdown` 在兩渲染器都能出 PDF；`ib-classic` 繁中正常、KPI/圖表正確對應來源。
- Commit：`feat(渲染): 導入 Typst 渲染引擎與 ib-classic 模板`。

### M9b — 可選模板 registry + `template_id` 貫穿 + 換皮重出

任務：
- `app/templates/manifest`（py 或 json）：登錄可用模板（`id`、名稱、說明、縮圖、`is_default`）。
- 再交付兩款模板，皆實作 M9a 的模板契約：`broker-modern`（現代簡潔單欄）、`privatebank-dark`（深色高階私銀）。
- **參數貫穿**：`/api/report` body 加 `template_id`（預設由 `config`）→ `generate_report(..., template_id)` → `typst_render` 依 id 取模板；**未知 id → 退回預設（fail-safe）**。內容生成邏輯零改動。
- **持久化**：`report_doc` 加 `template_id` 欄（`ALTER TABLE ... ADD COLUMN IF NOT EXISTS`）。
- **換皮重出**：新增 `POST /api/report-doc/{id}/rerender { template_id }`，用既有 `markdown` 以另一模板重出 PDF、覆蓋 `pdf_path`——**零 LLM、零重新生成**（解耦紅利）。
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
- **`locale` 貫穿**：`/api/ask`、`/api/report` 加 `locale`（`zh-Hant` | `en`），預設由 `Accept-Language` / 使用者偏好 / `config`；寫入 `qa_log`、`report_doc`（`ADD COLUMN IF NOT EXISTS`）。
- **系統提示參數化**：`SYSTEM_PROMPT`/`REPORT_SYSTEM_PROMPT`/`OVERVIEW_SYSTEM_PROMPT`/intent/condense 從硬編「一律繁體中文」改為「以 `{locale}` 作答」；英文 locale 時以英文輸出，**引用規則不變、仍引用中文來源 `[n]`**。
- **跨語言檢索**：dense（BGE-M3）+ rerank 已跨語言；對 `en` 查詢，額外把查詢翻成中文餵給**字面路徑**（補 pg_trgm 不跨語言的洞），或純靠 dense+rerank——先用 M1 eval 量測再決定。
- **來源呈現**：`sources` 帶原文標題 + 可選 `title_i18n`（Haiku 快速翻譯標題供辨識），**不動底層證據內容**。
- **文案 i18n**：`overview` 的 `render_overview_text`、離題婉拒、`NO_CONTEXT_MESSAGE` 等模板字串雙語化。
- fail-open：`locale` 解析不到 → 預設 `zh-Hant`。

驗收：
- 英文問句能檢索到相關中文研報，並以**英文帶 `[n]` 引用**作答；中文行為零回歸（M1 eval）。
- Commit：`feat(i18n): 問答與研報的輸出語言與跨語言檢索`。

### M10b — 前端 i18n + 語言切換

任務：
- 前端字串抽成 locale 資源（React SPA `frontend/` + `web/static`），加語言切換器、偏好持久化。
- 日期/數字在地化格式；來源卡顯示原文標題 + 譯名；登入頁與錯誤訊息 i18n。

驗收：
- 全 UI 可切 `zh-Hant`/`en`，切換即時、偏好保存；前端測試綠。
- Commit：`feat(i18n): 前端雙語介面與語言切換`。

### M10c — 英文研報模板（接 M9 registry）

任務：
- M9 模板契約的 chrome 標籤 i18n（Executive Summary / Key Findings / Rating / Target Price / Disclaimer…），或提供 `*-en` 模板變體；由 `locale` 決定 chrome 語言。
- 英文襯線字型（如 Source Serif / Noto Serif）與繁中字型並存。

驗收：
- 同一份內容能出繁中版與英文版 PDF，版型正確、字型正常、KPI/圖表標籤隨 locale。
- Commit：`feat(i18n): 英文研報模板 chrome`。

> **未來延伸（不在本里程碑）**：把英文/外資研報**納入語料**（bilingual corpus）——tagging 提示需支援英文，BGE-M3 多語使檢索天然可行；對應商業化清單的「雙語外資通道」。這是「英文使用者查中文語料」之外、更大的一步。

---

## 附：建議先跑的三個里程碑

**M0 + M1 + M2** 一組先行——地基 + 量測 + 最省事的精準度增益（rerank 同時惠及問答與研報），風險最低、且立刻能用 eval 數字證明價值。之後 UX(M3) 可平行，agentic(M5) 與研報深度(M6→M7) 再依序推進。
