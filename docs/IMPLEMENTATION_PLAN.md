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
              └── M9 Typst 渲染（內容穩定後）
M3 問答 UX 補齊（低風險，可與 M1+ 平行）
```

建議執行序：**M0 → M1 → M2 →（M3 平行）→ M4 → M5 → M6 → M7 → M8 → M9**

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
- `eval/run_ragas.py`：離線批次，指標 Faithfulness / Context Precision / Context Recall / Answer Relevancy；評審 LLM 走現有 `claude` CLI（Haiku/Sonnet）。**不進 `web/server.py` 請求路徑。**
- 產出基準線報表（存 `eval/baselines/`）。

驗收：
- `uv run python eval/run_ragas.py` 可跑出指標並存基準線。
- 文件化門檻：Faithfulness>0.9、Context Precision>0.8、Context Recall>0.8、Answer Relevancy>0.85（作為後續回歸 gate）。
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
- M1 eval：Context Precision / Faithfulness **較基準線上升**、Context Recall 不下降。
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
- 將 `intent.py` 重構為三態 `scope_router`：`off_topic | corpus_qa | overview`，維持 fail-open。
- 放寬金融相鄰題（個股/總經/期貨/匯率/加密/報價），首輪與 `condense_and_classify` **共用同一判準**（防「首輪 IN、續問 OUT」漂移）。
- 完全離題改為更自然的婉拒文案（Claude-like），非僵硬拒絕。

驗收：
- 既有 `tests/test_answer.py` 相容；新增路由分類測試（含曾漂移的「收盤價」類案例）。
- 枚舉題仍正確走 `overview.py`。
- Commit：`refactor(問答): 離題閘升級為三態範圍路由`。

---

## M5 — 問答 Agentic 迴圈（受控多輪 + 快速路徑）

藍圖：`QA_REDESIGN.md` §3-B、§3-E。**依賴 M0、M2；共用 `query_planner`。**

任務：
- `app/services/query_planner.py`（輕量版）：Haiku 提 1–N 子查詢或判「免檢索直接答」。
- `app/services/agentic_qa.py`：Python 編排迴圈（規劃→檢索+rerank→評估→作答），`QA_MAX_ROUNDS` 預設 2。
- **快速路徑豁免**：簡單事實/報價/單一標的題跳過迴圈，單輪直接答（沿用 `report_gate._TRIVIAL_HINTS` 類判斷）。
- 網路論點標「（網路）」走既有 `[EXT_SOURCES]`，不混入 `[n]`。
- fail-open：規劃/評估異常 → 退回既有一次性 RAG。

驗收：
- M1 eval：綜合題 Context Recall / Faithfulness 上升；延遲仍「聊天級」（記錄分佈）。
- 快速路徑題延遲 ≈ 現況 + rerank。
- Commit：`feat(問答): 受控 agentic 多輪檢索與快速路徑`。

---

## M6 — 研報檢索增強（多查詢分解 + MMR）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 1。**依賴 M0、M2。**

任務：
- `query_planner`（深度版）：主題展開 6–8 面向子查詢（依 `market` 微調），各自檢索、合併去重、rerank。
- 在 `select_reports`（M0 抽出）加入 **MMR 多樣性**，與既有 tier/新近度/過舊配額並存。
- 與 `retrieval.rank_reports` 合流到同一套 tier/band 定義（消除 0.05 vs 0.10 漂移）。

驗收：
- M1 eval：研報題 Context Recall 明顯上升、無同質冗餘惡化。
- Commit：`feat(研報): 多角度查詢分解與 MMR 選取`。

---

## M7 — 研報生成重構（大綱 → 逐節）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 2。**依賴 M6。**

任務：
- `app/services/report_writer.py`：大綱生成（固定骨架 + 動態子節）→ 逐節針對性檢索 → 逐節串流撰寫 → 組裝、統一重編 `[n]`。
- `report.py::generate_report` 改為薄編排；沿用 `REPORT_TIMEOUT=600`；串流逐節 yield。
- `report_doc` 加 `outline`(jsonb) 欄位（`ADD COLUMN IF NOT EXISTS`）。
- fail-open：任一步異常退回既有「單次生成」。

驗收：
- M1 eval：研報 Faithfulness / 深度指標上升；延遲在數分鐘內（品質優先，已確認可接受）。
- PDF 仍可由 `markdown` 重建。
- Commit：`feat(研報): 大綱驅動逐節生成`。

---

## M8 — 忠實度查核（研報完整 + 問答迷你）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 3、`QA_REDESIGN.md` §3-D。

任務：
- `app/services/faithfulness.py`：RAGAS 式 claim 拆解 + 逐條 grounding；標記數值型主張。
- 研報：生成後查核，低分主張要求修正一輪；`report_doc` 加 `faithfulness_score`（`ADD COLUMN IF NOT EXISTS`）。
- 問答：迷你版——數值主張抽查，可非同步於寫 `qa_log` 時做。
- fail-open：查核異常不阻擋交付。

驗收：
- 注入含錯誤數字的測試樣本能被標記。
- 不顯著惡化延遲（問答查核非同步/抽樣）。
- Commit：`feat(查核): claim grounding 忠實度檢查`。

---

## M9 — Typst 渲染層（雙軌，內容穩定後）

藍圖：`REPORT_GEN_REDESIGN.md` §3 Phase 4。

任務：
- `app/services/typst_render.py`：`markdown`→Typst（cmarker/pandoc）→ `typst compile` → PDF。
- `app/templates/report.typ`：封面/頁首尾/KPI 卡/`## 引用來源` 樣式；```kpi/```chart fence JSON 映射為 Typst 函式；圖表用原生套件（cetz/lilaq），評估退役 `chart.py` 的 matplotlib。
- **繁中字型**：安裝並指定思源宋體/Noto Serif CJK TC（部署機需裝字型，否則豆腐字）。
- env flag `REPORT_RENDERER=weasyprint|typst` 雙軌；`markdown` 永遠可回退 WeasyPrint 重建。
- 部署：`typst` binary / PyPI + systemd PATH（同 `claude` CLI）。

驗收：
- 同一份 `markdown` 在兩渲染器都能出 PDF；Typst 版繁中正常、KPI/圖表正確對應來源。
- Commit：`feat(渲染): 導入 Typst 研報渲染（雙軌可回退）`。

---

## 附：建議先跑的三個里程碑

**M0 + M1 + M2** 一組先行——地基 + 量測 + 最省事的精準度增益（rerank 同時惠及問答與研報），風險最低、且立刻能用 eval 數字證明價值。之後 UX(M3) 可平行，agentic(M5) 與研報深度(M6→M7) 再依序推進。
