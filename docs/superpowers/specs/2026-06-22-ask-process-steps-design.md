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
