# 問答中產生完整 PDF 深度研報

- 日期：2026-06-26
- 範圍：問答模式（`/api/ask` → `app/services/answer.py`、`web/server.py`、`web/static/app/ask.js`）。新增一條「深度研報生成」路徑與其持久化、歷史重現。**不動**檢索頁、瀏覽模式、既有 RAG 問答行為、離題閘門與總覽路徑。

## 背景與動機

使用者希望在問答中，能像 GPT／Claude 那樣，用**互動式選項**選擇要不要把一個問題整理成一份**完整的 PDF 報告**。

現況問答只回串流聊天答案（最多引用 8~15 篇），是典型 top-k RAG，產出的是「對話式答案」而非「可交付的研報文件」。本功能在不動既有問答的前提下，額外提供「重新做一次更深的檢索 → 生成結構化深度研報 → 輸出 PDF」的能力，並讓研報進入對話歷史可隨時下載。

## 設計決策（已與使用者拍板）

1. **PDF 內容 = 重新生成深度研報**：不是把聊天答案存成 PDF，而是額外再跑一次 LLM，產出更長、結構化的研報（執行摘要／關鍵發現／各標的分析／風險展望／引用來源）。
2. **互動 = 助理主動問**：答完後在對話中主動建議「要不要我整理成一份完整 PDF 研報？」並附可點的「要／不用」chip（對話式建議，像 ChatGPT）。
3. **時機 = 只在適合時問**：用輕量**純規則**判斷（零額外 LLM）決定該題是否「值得出研報」；適合才附建議卡。
4. **資料源 = 重新深度檢索**：產研報時重跑一次、收回更多篇（k 提高到 ~30），脈絡預算放大，讓研報引用更廣更完整。
5. **PDF 產生 = 伺服器端 WeasyPrint**：markdown→HTML→PDF，前端一鍵下載真正的 `.pdf`（含廷豐智能研報品牌頁眉、金色 #AE7415）。
6. **進歷史 = 隨對話輪次保存**：研報綁定產生它的那一輪問答（`qa_id`）；重開該對話時，那一輪下方重現研報卡片＋「下載 PDF」。持久化到新表 `research.report_doc`。

## 現況架構（變更前）

- `app/services/answer.py` `answer_question()`：傳輸無關事件產生器。首輪 = `classify_intent` 與 `embed+hybrid_search` 並行；續問 = 先 `condense_and_classify`，再檢索。離題 → 拒答；否則 `build_context(scored)`（取前 N 篇、帶編號脈絡 + `Source` 清單）→ `stream_completion`（claude CLI 串流）→ 解析 `[n]` 引用 → 寫 `research.qa_log`。事件序 `("status"|"sources"|"token"|"notice"|"ext_sources"|"done", payload)`。
- `app/services/retrieval.py` `hybrid_search`：雙路召回融合，回 `scored`。
- `app/services/llm.py` `stream_completion(prompt, *, model, system, allow_web)`：claude CLI 串流；`DEFAULT_MODEL = "claude-sonnet-4-6"`。
- `web/server.py`：`/api/ask`（SSE）、`/api/conversations`、`/api/conversations/{id}`（`get_conversation`）、`/api/report/{report_id}/file`（**注意：此處 `report_id` 指既有原始研報 `research.research_report`，與本功能的「生成研報」不同物**）。`_ASK_SEMAPHORE = Semaphore(3)`。
- `web/static/app/ask.js`：fetch+手解 SSE 的聊天前端；每輪一個 TurnObj（自帶 `sources`/`qaId`），`done` 事件處理在 `askQuestion()` 內；`paintActions` 渲染答案後動作列；`get_conversation` 重載時以 `loadConversation` 重建輪次。
- `db/schema.sql` + `make schema`：冪等 schema 管理（`CREATE TABLE IF NOT EXISTS` / `ALTER ... ADD COLUMN IF NOT EXISTS`），**非 Alembic**。
- 品牌：金色 `#AE7415`、名稱「廷豐智能研報」，logo 以 base64 內嵌（見 `index.html`/`auth.py`）。

## 設計

### 1. 判斷時機：`app/services/report_gate.py`（新）

```python
def should_offer_report(question: str, sources: list, answer: str) -> tuple[bool, str | None]:
    """純規則判斷該題是否值得出深度研報，回 (offer, suggested_title)。"""
```

- **零額外 LLM**，在 `answer_question` 答案完成後、產 `done` 前計算。
- 建議（offer=True）的條件（保守，符合「只在適合時」）：
  - 實際引用來源篇數 ≥ `REPORT_MIN_CITED`（預設 3），**且**
  - 題目帶分析意圖：命中關鍵詞（分析／比較／展望／趨勢／影響／前景／評估／總結／整理／報告／深入…）**或** 偵測到多標的/多面向，**且**
  - 非單純即時事實題（純股價／今天收盤／報價 等 → 不建議），非離題、非無脈絡。
- `suggested_title`：由問題與主要標的組出的研報標題草稿（例如「台積電 2025 下半年展望深度研報」）；前端可顯示於建議卡與最終 PDF。
- 此函式為純函式，獨立單測（report-worthy vs not）。

`answer_question` 的最終 `done` payload 增加欄位：

```json
{ "cited": [...], "qa_id": "...", "conversation_id": "...", "thinking_ms": 1234,
  "offer_report": true, "report_title": "…" }
```

離題／無脈絡分支一律 `offer_report=false`（不動其既有 payload 邏輯，只補欄位）。

### 2. 研報生成：`app/services/report.py`（新）

```python
async def generate_report(
    question: str, *, filters: dict | None, conversation_id: str | None, qa_id: str | None,
    model: str = REPORT_MODEL,
) -> AsyncIterator[tuple[str, object]]:
    """深度檢索 → 結構化研報串流 → 渲染 PDF → 持久化。逐筆 yield 事件。"""
```

事件序：

```
("status", {"stage": "retrieving"})              # 深度檢索中
("sources", [asdict(s) ...])                      # 研報用到的來源清單（編號）
("status", {"stage": "writing"})                  # 撰寫中（第一個 token 前）
("token", "...")  × N                             # 串流預覽研報 markdown（像 Claude artifacts 邊寫邊看）
("status", {"stage": "rendering"})                # 排版 PDF 中
("done", {"report_id": "...", "title": "...",
          "download_url": "/api/report-doc/<id>/pdf", "thinking_ms": 12345})
```

流程：

1. **深度檢索**：`embed_query_cached(question)` → `hybrid_search(..., k=REPORT_DEEP_K, dense_scan=ASK_DENSE_SCAN)`。
2. **放大脈絡**：`build_context(scored, max_reports=REPORT_MAX_REPORTS, max_passages=REPORT_MAX_PASSAGES, max_chars=REPORT_MAX_CHARS)`（重用既有函式，只改參數）。
3. **研報 prompt**：`REPORT_SYSTEM_PROMPT` 要求固定結構化 markdown（# 標題／## 執行摘要／## 關鍵發現／## 各標的（或主題）分析／## 風險與展望／## 引用來源），延續既有 `[n]` 行內引用規範與「以片段為據、優先採新、不臆測」原則。
4. **串流**：`stream_completion(prompt, model=REPORT_MODEL, system=REPORT_SYSTEM_PROMPT, allow_web=...)`，累積 markdown 並逐塊 yield `token`（供前端即時預覽）。
5. **渲染 PDF**：`pdf.render_report_pdf(markdown, title=..., meta=...)` → bytes → 寫入 `REPORTS_DIR/<uuid>.pdf`。
6. **持久化**：插入 `research.report_doc`（id、qa_id、conversation_id、question、title、markdown、pdf_path、sources、thinking_ms）。
7. yield `done` 帶 `report_id` 與 `download_url`。

備註：v1 研報以「該輪原問題」直接深度檢索；多輪 condense 改寫列為後續增強（不阻擋 v1）。

### 3. PDF 渲染：`app/services/pdf.py`（新）

```python
def render_report_pdf(markdown_text: str, *, title: str, meta: dict) -> bytes:
    """markdown → HTML → WeasyPrint PDF（品牌頁眉 + 頁碼）。回 PDF bytes。"""
```

- markdown→HTML 用 `markdown`（python-markdown）；外層套品牌 HTML 模板：
  - 頁眉：廷豐智能研報名稱／logo／生成日期；主色金 `#AE7415`。
  - 頁尾：頁碼（WeasyPrint `@page` CSS counter）。
  - 內文 CSS `font-family` 指向 **Noto Sans CJK**（部署機需安裝；缺字型中文會變空白方塊）。
- 純函式（輸入 markdown+meta，輸出 bytes），可冒煙測：輸出以 `b"%PDF"` 開頭。

### 4. 資料模型：`research.report_doc`（新，進 `db/schema.sql`）

```sql
CREATE TABLE IF NOT EXISTS research.report_doc (
    id              uuid PRIMARY KEY,
    qa_id           uuid,            -- 產生此研報的問答輪次（research.qa_log.id）
    conversation_id uuid,            -- 所屬對話串（COALESCE 對齊 qa_log 分組鍵）
    question        text NOT NULL,
    title           text,
    markdown        text NOT NULL,   -- 研報原始 markdown（真相來源；PDF 可由此重建）
    pdf_path        text,            -- 已渲染 PDF 檔位置
    sources         jsonb,           -- 當時引用來源（含編號，供卡片重現）
    thinking_ms     int,
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_report_doc_qa ON research.report_doc (qa_id);
CREATE INDEX IF NOT EXISTS idx_report_doc_conversation
    ON research.report_doc (conversation_id, created_at);
```

- **markdown 為真相來源**：`pdf_path` 檔遺失時可由 markdown 即時重建（lazy regen）。預設**不設 TTL**（研報是要保存的成果；PDF 體積小）。
- 部署：`make schema` 套用（**拉新碼前先 `make schema`**，避免 schema 漂移）。

### 5. 後端路由（`web/server.py`）

- `POST /api/report`（SSE）：body `ReportRequest{question, conversation_id?, qa_id?}`。獨立 `_REPORT_SEMAPHORE = Semaphore(REPORT_SEMAPHORE)`（研報生成比問答重，限流防區網雪崩）。包住 `generate_report`，事件轉 SSE，沿用 `Cache-Control: no-cache`、`X-Accel-Buffering: no`。
- `GET /api/report-doc/{id}/pdf`：依 id 撈 `report_doc` → 回 PDF `FileResponse`（`Content-Disposition: attachment`）。`pdf_path` 不存在時由 `markdown` 即時 `render_report_pdf` 重建並回寫。id 嚴格驗 uuid，無路徑注入。
- `get_conversation` 擴充（`app/services/answer.py`）：撈該對話 `report_doc`，依 `qa_id` 掛到對應輪次 dict（新增 `reports: [{report_id, title, download_url, created_at}]`）。其餘輪次無 reports 鍵或空陣列。

### 6. 前端（`web/static/app/ask.js` + CSS + `index.html`）

- **建議卡**：`done` 事件讀 `offer_report`/`report_title`；非離題且為 true → 在該輪渲染對話式建議卡：
  `「要不要我幫你整理成一份完整 PDF 研報？」 [要，幫我產生] [不用]`。
  點「不用」→ 收起卡片（純前端，不打 API）。
- **生成流程**：點「要」→ 在該輪下方開生成面板（重用既有 process-panel 樣式），`POST /api/report`（fetch+手解 SSE，比照 `askQuestion`），即時預覽撰寫中的 markdown（`renderMarkdown`），完成顯示研報結果卡：標題＋「下載 PDF」按鈕（連 `download_url`）。失敗 → 顯示重試。
- **歷史重現**：`loadConversation` 重建輪次時，若 `it.reports?.length` → 渲染研報結果卡（標題＋下載鍵），不重新生成、不顯示建議卡。
- 與既有 latest-wins / `cancelActiveAsk` 取消機制相容：研報生成請求獨立 AbortController，切歷史/新對話時一併取消。
- 圖示沿用 inline SVG（**不用 emoji**，符合既有偏好）。

### 7. 設定（env，集中於 answer/report 模組）

| 變數 | 預設 | 說明 |
|------|------|------|
| `REPORT_MODEL` | `claude-sonnet-4-6` | 研報生成模型（較長輸出用 Sonnet） |
| `REPORT_DEEP_K` | 30 | 深度檢索 k |
| `REPORT_MAX_REPORTS` | 25 | 研報脈絡最多篇數 |
| `REPORT_MAX_PASSAGES` | 6 | 每篇最多段數 |
| `REPORT_MAX_CONTEXT_CHARS` | 40000 | 脈絡總字數上限 |
| `REPORT_MIN_CITED` | 3 | 建議出研報的最低引用篇數 |
| `REPORT_SEMAPHORE` | 1 | 同時生成數（重任務，預設序列化） |
| `REPORTS_DIR` | `data/reports` | PDF 落地目錄 |

### 8. 相依與部署

- 新增 Python 相依（`uv add`）：`weasyprint`、`markdown`。
- **部署機需安裝**：WeasyPrint 原生庫（`libpango`、`libcairo`、`libgdk-pixbuf` 等）＋ **Noto Sans CJK** 字型（缺字型中文 PDF 會變空白方塊）。寫入部署筆記（`docs/` 下）。
- `make schema` 套用 `report_doc` 表。

### 9. 錯誤處理

- 生成中任一步失敗 → SSE `error` 事件 → 前端顯示「研報生成失敗，請重試」＋重試鍵；後端 `logger.exception`。
- WeasyPrint／字型缺失 → 渲染拋例外 → `error` 事件＋明確訊息＋log（部署檢查項）。
- `/api/report-doc/{id}/pdf` 查無 → 404；id 非 uuid → 400/404。
- 已 yield 過 `sources`/`token` 後才失敗：直接上拋 `error`（不嘗試重發汙染 SSE，比照既有總覽路徑慣例）。
- 限流：`_REPORT_SEMAPHORE` 滿時排隊（前端面板顯示「排隊中」狀態，沿用 status 機制）。

### 10. 測試

- `tests/test_report_gate.py`：`should_offer_report` 純規則（多標的分析題→建議、純股價題→不建議、引用不足→不建議、離題→不建議）。
- `tests/test_pdf.py`：`render_report_pdf` 冒煙（輸出 `b"%PDF"` 開頭、含標題文字）。需 WeasyPrint 可用；環境缺庫則 `pytest.importorskip`。
- `tests/test_report.py`：`generate_report` 事件序（mock `hybrid_search` 與 `stream_completion`，比照 `test_answer.py`），驗事件順序與 `report_doc` 寫入（用測試 DB / mock）。
- 前端：`ask.js` 的建議卡與 reports 重現走既有零工具鏈 `.test.mjs` 風格（解析 done payload → 是否渲染建議卡）。

## 範圍與非目標

- **非目標（v1 不做）**：獨立「我的研報」側欄清單（已選「隨對話輪次保存」）；研報多輪 condense 改寫；研報內嵌圖表；PDF TTL 自動清理；研報的讚／倒讚回饋。
- **不動**：檢索頁、瀏覽模式、既有 RAG 問答與其 `qa_log`、離題閘門、總覽路徑、`/api/report/{id}/file`（原始研報檔）。
