# 設計：問答歷史查看（過往問答）

- 日期：2026-06-18
- 範圍：問答模式內新增「歷史」抽層，讓使用者查看／重現過往問答。資料現成於 `research.qa_log`。
- 不碰：檢索頁、意圖閘門、網搜整合、`hybrid_search`。

## 決策（brainstorming 定案）

1. 入口 = **問答模式內的「歷史」抽層**（覆蓋式清單，ChatGPT 側欄風）。
2. 過濾 = **排除離題拒答**（`answer = OFF_TOPIC_MESSAGE`）。
3. `[n]` = **可完整還原可點** → 需存當時完整來源清單（含編號）。

## 背景

- `research.qa_log` 每次 `/api/ask` 寫一列：`id, question, answer, cited_report_ids, filters, latency_ms, created_at, feedback`。
- 但未存「當時完整 sources（含 n 編號）」，故答案 `[n]` 無法精準連回報告。本設計補存 `sources jsonb` 解決。
- 本 app 單一共用登入，故歷史 = 全部使用者的過往問答（內部工具，合理）。
- 前端問答為單輪顯示（`paintAnswer`/`paintSources` 渲染、`[n]` 點擊由 `sources.find(n→report_id)` → `openFull`）。

## 後端

### Schema
- `ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS sources jsonb`（冪等）。
- 存「sources 事件」原樣：`[{n, report_id, file_name, market, report_date}]`（即 `[asdict(s) for s in sources]`）。

### `_log_qa`（`app/services/answer.py`）
- 簽章加 `sources: list[dict]`；INSERT 多寫 `sources`（`json.dumps`，jsonb）。
- `answer_question` 三條呼叫點：主回答傳實際 sources；離題、無脈絡傳 `[]`。

### `GET /api/history`（`web/server.py`，登入授權、唯讀）
- query：`limit`（預設 50、夾 1..200）。
- SQL：`SELECT id, question, answer, created_at, feedback, sources FROM research.qa_log WHERE answer IS DISTINCT FROM :offtopic ORDER BY created_at DESC LIMIT :limit`（`:offtopic = OFF_TOPIC_MESSAGE`）。
- 回 `[{id, question, answer, created_at(iso), feedback, sources}]`；`sources` 為 null 時回 `[]`（舊列優雅退化）。
- 新增 `HistoryItem` Pydantic 回應模型（含 from_attributes 非必要；直接組 dict）。

## 前端（`web/static/app/ask.js` / `index.html`）

### 「歷史」抽層
- 問答頁加「歷史」鈕（composer 區或 thread 頂）；點擊 `openHistory()` → `GET /api/history` → 渲染抽層清單。
- 抽層：覆蓋式面板（右側滑入或下拉），每筆一列：問題（截斷單行）+ 日期（`fmtDate`）+ 回饋徽章（讚/倒讚，無則略）。空清單顯示空狀態。
- 點抽層外、Esc、或關閉鈕 → 收起抽層。a11y：focus trap 比照既有 modal 慣例（若既有 modal 有共用工具則沿用）。

### 點選一筆 → 唯讀重現
- 關抽層，在主對話區重現：`#askQuestion`（問題泡泡）+ `paintAnswer(answer,false)`（markdown）+ `paintSources(item.sources)`（來源，`[n]` 可點開 `openFull`）。
- `sources = item.sources`（模組變數），使 `[n]` 點擊與來源卡一致運作。
- 動作列：`paintActions(item.id, item.answer, item.sources.length, 0)`；讚/倒讚依 `item.feedback` 預先高亮（沿用現有 `.on`），可改（沿用 `/api/feedback`）。外部來源不入庫 → extCount=0、無「外部參考」鈕。
- 重現屬唯讀歷史：不重打 `/api/ask`。

### Reset
- 既有 `askQuestion` 開頭的 reset 照舊；另：開新問題時若正顯示歷史重現，照常被覆寫（無特殊處理）。

## 錯誤處理
- `/api/history` 失敗 → 抽層顯示「載入失敗，請稍後再試」。
- 401 → 導向 `/login`（沿用既有 fetch 慣例）。

## 測試
- 單元（unittest）：
  - `_log_qa` 寫入含 `sources`（monkeypatch session 捕捉 INSERT 參數，斷言含 sources jsonb）。
  - `answer_question` 主回答路徑：`_log_qa` 被以非空 sources 呼叫（monkeypatch `_log_qa` 記錄參數）。
  - history 端點過濾：以假資料/或純函式拆出「組 history dict」的轉換並測（離題列被排除、sources null→[]、created_at iso）。
- 端到端（Playwright）：問一題 → 開歷史 → 該題在列；點開 → 主區重現問題/答案/來源、`[n]` 可點；離題題不在歷史。

## 不做（YAGNI）
- 不做分頁/搜尋/刪除歷史、不存外部來源、不做每使用者隔離（共用帳號）、不改 `hybrid_search`。
