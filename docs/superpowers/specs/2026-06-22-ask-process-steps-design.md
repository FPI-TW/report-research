# 問答「處理過程」步驟面板 — 設計

- 日期：2026-06-22
- 範圍：問答（RAG）模式在回答前後，顯示系統實際處理步驟的時間軸面板
- 狀態：設計定案，待產出實作計畫

## 背景與目標

目前問答等待時只有一行 spinner（`檢索研報並思考中…`），第一個 token 進來就被答案覆寫；只有「搜尋網路」會換字。使用者看不到系統實際在做什麼，等待期（CPU 端 BGE-M3 embedding + 檢索 + LLM 串流，數秒到數十秒）只有空轉的焦慮。

目標：把單行 spinner 升級成一個**步驟清單面板**，掛在每一輪答案泡泡上方，等待時逐步點亮，答案出來後**保留**（預設展開、可手動收折），讓使用者看見處理過程。

明確排除：**不**顯示模型的 extended thinking（推理 token）。本功能呈現的是「系統處理步驟」，不是模型內心獨白。

## 方案取捨

- **方案 A（採用）：後端發 status 事件，前端渲染步驟清單。** 步驟邊界由後端真實流程決定，誠實、可控；既有已有 `("status","searching_web")` 事件可沿用擴充。
- 方案 B：純前端假時間軸（不靠後端訊號，用計時器假裝步驟推進）。會與真實進度脫節，誤導使用者，否決。
- 方案 C：把完整步驟時間軸存進 DB。需要 schema 變更與逐步時間戳，對「顯示處理中」這個目的過度設計，否決（YAGNI）。歷史回看改以既有欄位重建靜態面板（見下）。

呈現方式取捨（已與使用者確認）：採「全程保留、預設展開、可手動收折」面板，而非「完成即消失」或「完成後自動收成一行」。

## 步驟模型

前端固定渲染一個有序步驟清單，後端以 `status` 事件推進。步驟對應 `app/services/answer.py::answer_question` 的真實邊界：

| # | 顯示文字 | 觸發 | 說明 |
|---|----------|------|------|
| 1 | 理解問題 | 開頭 `status {stage:"understanding"}` | 首輪：意圖判定（與檢索並行）；續問：改寫追問脈絡 |
| 2 | 找到 N 篇相關研報 | `build_context` 後 `status {stage:"retrieved", count:N}` | N = 實際組進脈絡的來源數 |
| 3 | 閱讀重點、整理回答 | 送 LLM 前 `status {stage:"reading"}` | |
| 4 | 搜尋網路補充 | `status {stage:"searching_web"}`（條件性，僅 WebSearch 觸發） | 既有事件，payload 改為物件 |
| 5 | 生成回答 | 前端於**第一個 `token`** 自動點亮 | |

**誠實處理並行**：首輪的「意圖判定」與「檢索」實際同時跑，故面板定位為「完成事項清單」而非嚴格延遲時序；步驟 1 文字保持中性「理解問題」，不謊稱先後關係。

每步三態：未開始（灰）／進行中（沿用既有 `.spin` 旋轉指示）／完成（打勾）。**所有圖示用 inline SVG，不用 emoji**（遵循專案既有 SVG icon 慣例與無-emoji 偏好）。

## 後端改動

檔案：`app/services/answer.py`（`answer_question`）

- 函式開頭、載歷史/檢索前：`yield ("status", {"stage": "understanding"})`
- `sources, context = build_context(scored)` 之後：`yield ("status", {"stage": "retrieved", "count": len(sources)})`
- 送 `stream_completion` 之前：`yield ("status", {"stage": "reading"})`
- 既有 `yield ("status", "searching_web")` 改為 `yield ("status", {"stage": "searching_web"})`

`web/server.py::_sse` 已是泛型 `event + json data` 透傳，**不需改動**（物件 payload 由 `json.dumps` 處理）。

事件序總覽（正常路徑）：
`understanding → sources → retrieved(count) → reading → [searching_web?] → token… → ext_sources → done`

> 註：`sources` 事件（既有，帶來源清單）仍在 `retrieved` status 之前送出，前端據 `sources` 畫來源、據 `retrieved` 點亮步驟 2，兩者不衝突。

## 前端改動

檔案：`web/static/app/ask.js` 與 ask 區 CSS（index.html 內嵌 style 區）

- 新增 `.ask-process` 面板節點：可點收折的標題「處理過程」（`aria-expanded`）+ 步驟 `<ol>`。掛在 turn 的答案泡泡上方。
- 以 `renderProcess(turn)` 取代現有 `thinking(turn)`：建立面板並把步驟 1 設為進行中。
- `status` 事件改走 stage 機制：
  - `understanding` → 步驟 1 進行中
  - `retrieved` → 步驟 1 完成、步驟 2 完成並填入 `找到 {count} 篇相關研報`、步驟 3 進行中
  - `reading` → 步驟 3 進行中（已由 retrieved 點亮，作冪等確認）
  - `searching_web` → 插入/點亮步驟 4「搜尋網路補充」進行中
- 第一個 `token`：步驟 4（若有）與步驟 3 標完成、點亮步驟 5「生成回答」、開始 `paintAnswer`；面板**保留**於泡泡上方，不再整段覆寫。
- 移除舊 `searchingWeb()` 的末尾臨時指示邏輯，統一進步驟面板。
- `done`：所有已點亮步驟標完成。

收折行為：面板預設展開；點標題切換 `open` class 與 `aria-expanded`。沿用既有 classList toggle 慣例（與 `資料來源` 切換一致）。

## 邊界情況

- **離題**（`in_domain=false`）：步驟 1 完成後直接渲染「無法回答」提示卡（`paintNotice`），不再有後續步驟，維持現狀體驗。
- **無脈絡**（`count=0`）：面板顯示「找到 0 篇相關研報」，答案區出 `NO_CONTEXT_MESSAGE`，步驟照常標完成。
- **錯誤**（`error` 事件）：維持現有 `fail()` 處理；面板停在當前步驟。

## 歷史回看一致性（零 DB 改動）

從側欄載入舊對話（`/api/conversations/{id}`）時沒有即時事件流，但 `qa_log` 已存 `sources` 與 `ext_sources`，足以**重建靜態面板**：

- 理解問題 ✓
- 找到 N 篇相關研報 ✓（N = `sources.length`）
- （若 `ext_sources` 非空）搜尋網路補充 ✓
- 生成回答 ✓

即時輪會動畫逐步點亮，歷史輪是一次性靜態勾選，畫面結構一致。**不需要改 schema**。

## 測試

- 後端：擴充 `tests/test_answer.py`，斷言 `answer_question` 事件序在正常路徑含 `("status", {"stage":"understanding"})` → `("status", {"stage":"retrieved", "count": N})` → `("status", {"stage":"reading"})`，並涵蓋離題（只到 understanding）與無脈絡（retrieved count=0）路徑。沿用既有 unittest + async + mock `stream_completion` 模式。
- 前端：專案無自動化前端測試框架，以 Playwright 手動驗證即時動畫、收折、以及歷史重建面板。

## 不做（YAGNI）

- 不顯示模型 extended thinking / 推理 token
- 不在 DB 存步驟時間軸或逐步時間戳
- 不顯示每步耗時毫秒數

## 部署備註

- `answer.py` 屬後端，部署後需重啟 web 服務（`report-mark-web.service`）才生效。
- 前端為靜態檔，`/static` no-cache，部署即時生效。

---

## 增補（2026-06-22）：標題改「已思考 XX 秒」計時呈現

延續上述步驟面板，調整其**呈現主體**：把可收折標題「處理過程」升級成 ChatGPT/Claude 式的耗時計時器，步驟清單收進面板內。已與使用者確認。

### 呈現（已選定）

- **預設收合**（即時輪也收合，與原本「即時輪預設展開」相反）。
- **處理中**：標題顯示 `思考中 N 秒…`，前綴 spinner，每秒跳動。
- **思考結束**（答案開始串流時）：標題凍結成 `已思考 XX 秒`，前綴打勾，可點開看 5 步驟明細。
- 面板內 5 步驟（理解問題／找到 N 篇／閱讀整理／搜尋網路／生成回答）邏輯不變；收合時隱藏，展開時顯示即時狀態。

### 秒數量測語意（已確認）

「已思考」＝**`answer_question` 開始 → 第一個 token（答案開始串流）**，於答案開始輸出時凍結。即 ChatGPT「Thought for Xs」語意：量「出字前等了多久」，**不含**逐字輸出答案的時間。

> 既有 `qa_log.latency_ms` 是**總耗時**（含整段串流，於寫 log 時量測），語意不同，**不挪用**；另立獨立的 `thinking_ms`。

### 單一真實來源（即時＝歷史一致）

使用者要求「全部一致」。實作為單一真實來源：

- 後端量測 `thinking_ms`，於**第一個 token 邊界**以事件 `("status", {"stage": "generating", "thinking_ms": N})` 帶給前端 → 即時顯示用此權威值；前端的「思考中 N 秒…」僅為動畫佔位。
- 同一 `thinking_ms` 寫入 `qa_log` → 歷史回看讀同一值。
- 即時與歷史顯示同一個後端量得的數字，避免新舊輪呈現不一致。

### 後端改動（增補）

- `db/schema.sql`：`research.qa_log` 新增 `thinking_ms int`（nullable）。因 `CREATE TABLE IF NOT EXISTS` 不會修改既有表，另補一行冪等 `ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS thinking_ms int;`，使 `make schema` 對既有 DB 也加得了欄。
- `app/services/answer.py::answer_question`：
  - 主串流路徑：第一個 `("token", …)` 之前 emit `("status", {"stage": "generating", "thinking_ms": int((time.monotonic()-started)*1000)})`（以 flag 確保只發一次；token 有多個 yield 點）。
  - 無脈絡路徑：在 `NO_CONTEXT_MESSAGE` token 之前同樣 emit `generating` + `thinking_ms`。
  - 離題路徑：無 token，`thinking_ms` 量到拒答點，放進 `done` payload 與 `qa_log`。
  - `done` payload 一律帶 `thinking_ms`（離題校正與後備用）。
- `_log_qa`：新增 `thinking_ms` 參數，與既有 `latency_ms` 並存寫入 INSERT。
- `history_item` / `get_conversation`：SELECT 與回傳 dict 加入 `thinking_ms`（相容舊列：缺欄或 NULL 回 `None`）。

事件序（正常路徑，更新後）：
`understanding → sources → retrieved(count) → reading → [searching_web?] → generating(thinking_ms) → token… → ext_sources → done(thinking_ms)`

### 前端改動（增補）

檔案：`web/static/app/ask.js` 與 ask 區 CSS（index.html 內嵌 style）。

- 標題列改為 `chevron + 狀態圖示（spinner/勾）+ 計時文字`；`renderProcess` 即時輪預設 `expanded=false`。
- 送出時起 `setInterval`（id 存於 `turn.timer`），標題顯示「思考中 N 秒…」。
- 收到 `generating`：停 interval，標題凍結為「已思考 {round(thinking_ms/1000)} 秒」+ 勾、點亮「生成回答」。第一個 `token` 與 `notice` 作為後備凍結點（取先到者；無 `thinking_ms` 時用 client 計值，`done.thinking_ms` 再校正）。
- `done`：以 `thinking_ms` 校正標題權威值；標完成所有顯示中步驟。
- `finishProcess` / `clearProcess` / `fail` / `cancelActiveAsk` 都要 `clearInterval(turn.timer)`，避免計時器洩漏。
- 歷史 `staticProcess`：`createTurn`/`loadConversation` 取 `it.thinking_ms`；有值 → 標題「已思考 X 秒」；**舊列 NULL** → 退回中性標題（顯示「處理過程」、無秒數，仍可展開步驟）。

### 邊界（增補）

- **離題**：無 token，計時器於 `notice` 凍結（client 值），`done.thinking_ms` 校正。
- **無脈絡**：`NO_CONTEXT` 前 emit `generating`，照常凍結。
- **錯誤／逾時**：清 interval，面板維持現有處理。
- **舊歷史列**：`thinking_ms` 為 NULL，歷史標題退回無秒數的中性呈現（可接受的漸進退化；新問答起累積真值）。

### 測試（增補）

- 後端：擴充 `tests/test_answer.py`，斷言正常路徑出現 `("status", {"stage": "generating", "thinking_ms": <int>})`、`done` 帶 `thinking_ms`，且涵蓋離題與無脈絡路徑；沿用既有 unittest + async + mock `stream_completion`。
- 前端：無自動化框架，Playwright 手動驗即時跳動、凍結時機、收合、歷史重建（含舊列無秒數退化）。

### 部署備註（增補）

- schema 變更：部署需跑 `make schema`（冪等加欄）再重啟 `report-mark-web.service`。
- 前端靜態檔即時生效。
