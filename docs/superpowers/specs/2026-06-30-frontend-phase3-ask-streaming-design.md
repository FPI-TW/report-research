# Phase 3 — 問答串流（ask）vanilla → React 遷移 設計 spec

> 狀態：設計已核可（2026-06-30），待使用者複審 → writing-plans。
> 分支：`feat/frontend-phase3-ask-streaming`（從 main `89a0d8e` 開）。
> 前置：Phase 0/1（PR #40）、Phase 2a（#42）、Phase 2b（#43）皆已併入 main。

## 1. 目標與策略

把舊 vanilla 問答（`web/static/app/ask.js` 676 行 + `markdown.js`/`modal.js`/`confirm.js`）遷移成 React 功能模組，掛在 `/app/ask`，與舊 `/` 問答模式**平價共存（不 cutover）**。

- **策略**：先移植不重構、保 vanilla 為對照、寫 e2e 驗事件序。
- **後端零變動**：`web/server.py`、`app/**`、`db/**` 不動；不改 schema。
- **棧**：沿用 main 既有 React 棧（React 19.2.7、Vite 8、react-router 8 basename `/app`、Mantine 9、TanStack Query 5.101、Zod 4、Vitest 4、TypeScript、Playwright）。

## 2. 範圍（鎖定）

**IN（Phase 3 交付）**
- `/api/ask` SSE 串流問答：答案串流、來源、外部來源、流程步驟面板、引用 `[n]`→來源 modal。
- 變體序列：離題（notice）、無脈絡（NO_CONTEXT token）、總覽/聚合（overview，事件文法同 happy path）。
- 多輪對話：`conversation_id` 跨輪維持；歷史側欄（清單/開啟/刪除/新對話/active 標記）。
- 回饋：like/dislike/copy；`POST /api/feedback`。
- markdown 安全渲染（移植 markdown.js 為純函式 JSX）。
- 引用/來源點擊複用既有 `ReportDetailModal`（metadata-only）。

**OUT（留 Phase 4）**
- 深度研報：`maybeOfferReport`、`/api/report` 第二條 SSE 串流、報告產生/PDF 下載、modal 內 PDF 內嵌。
- `done.offer_report` 旗標：Phase 3 **不接 UI**（保留型別解析，但不渲染「建議報告」按鈕；留待 Phase 4）。

**不做**：cutover（舊 `/` 問答保留）；後端/schema 變動；非必要重構。

## 3. 權威 SSE 事件契約（來自後端 `app/services/answer.py`，唯讀對照）

`POST /api/ask`，body = `AskRequest`（前端只送 `{question}`，多輪時加 `{conversation_id}`；其餘 filter 用預設）。回應 `text/event-stream`，幀格式 `event: <name>\ndata: <json>\n\n`，幀間以 `\n\n` 分隔。

**Happy（RAG）序列**
1. `status` `{"stage":"understanding"}`
2. `sources` `[{n:int, report_id:str, file_name:str, market:str|null, report_date:str|null, is_latest:bool}, ...]`
3. `status` `{"stage":"retrieved","count":int}`
4. `status` `{"stage":"reading"}`
5. （條件）`status` `{"stage":"searching_web"}`（WebSearch 觸發時一次）
6. `status` `{"stage":"generating","thinking_ms":int}`（首 token 前一次）
7. 一個以上 `token` `"<文字片段>"`（data 是 **JSON 字串**，非物件）
8. `ext_sources` `[{title,url}, ...]`（可空）
9. `done` `{"cited":[report_id,...], "qa_id":str, "conversation_id":str, "thinking_ms":int|null, "offer_report":bool, "report_title":str|null}`

**變體終止序列**
- **離題**：`sources []` → `notice "<OFF_TOPIC_MESSAGE>"` → `done {"cited":[], "conversation_id", "thinking_ms"}`（**無 `qa_id`**）。
- **無脈絡**：`sources []` → `status generating` → `token "<NO_CONTEXT_MESSAGE>"` → `done {cited:[], qa_id, conversation_id, thinking_ms}`。
- **總覽/聚合**：事件文法同 happy（`sources`→`status retrieved`→`status generating`→`token…`→`done`）。
- **錯誤**：`error {"detail":...}`（伺服器層例外）。

**客戶端契約要點**
- `token` payload 是 JSON 字串；其餘是物件/陣列。終止永遠是 `done` 或 `error`。
- `conversation_id` 每個 `done` 都有 → 客戶端據此學習/維持 id。
- `EXT_SENTINEL`/外部來源在後端已剝離，客戶端不會看到 sentinel。
- **fallback 風險**：CLI 帶 WebSearch 時可能不吐增量 delta，完整答案以**單一大 `token`**到達——渲染**不可假設增量**。
- `done.cited` 僅供 offer-report gating，渲染不需要；`[n]`→來源對映由客戶端自行於 markdown 完成（`maxCite = 該輪 sources 長度`）。

**其他端點（皆唯讀對照，`web/server.py`）**
- `GET /api/conversations?limit=50` → `[{conversation_id, title, last_at, turn_count}]`（依 `COALESCE(conversation_id,id)` 分組、僅含非離題輪、新到舊）。
- `GET /api/conversations/{id}` → 由舊到新的 `history_item`：`{id, question, answer, created_at, feedback, sources[], ext_sources[], is_offtopic, thinking_ms, reports[]}`。
- `DELETE /api/conversations/{id}`，**POST alias `/api/conversations/{id}/delete`**（404/405 退回）→ `{"ok":bool}`。
- `POST /api/feedback` body `{qa_id, value}`（value ∈ like/dislike）→ `{"ok":bool}`。

## 4. 檔案結構（`frontend/src/features/ask/`）

**純邏輯 `lib/`（每檔同層 `.test.ts`）**
- `lib/sseEvents.ts` — SSE 事件判別聯合型別（discriminated union on `event`）；對齊 §3。
- `lib/sse.ts` — `parseFrame(frame): AskEvent | null`（移植 ask.js:20-28，`event:`/`data:` 解析、`token` 的 `JSON.parse`）；`async function* streamAsk(body, signal): AsyncGenerator<AskEvent>`（POST `/api/ask`、`resp.body.getReader()` + `TextDecoder`、緩衝依 `\n\n` 切幀、yield 型別化事件）。可用 mock `ReadableStream` 餵幀測試。
- `lib/markdown.tsx` — 移植 markdown.js：`renderMarkdown(text: string, maxCite: number, onCite?: (n: number) => void): ReactNode[]`，含 `inline()`（code span、`**bold**`、`*italic*`、http-only link、範圍內 `[n]` chip 為 `<a className="cite" role="button" tabIndex={0} onClick={() => onCite(n)} onKeyDown={Enter/Space}>` 節點——onClick 由回呼直接掛在 chip 上，不靠容器事件委派）、`normalize()`（黏行 ATX 標題，code fence 外、`C#`/`#1` 防呆）、區塊（fenced code 含 chart/kpi 佔位、h1-h6 clamp h4、blockquote、table、ul/ol、hr、段落）。**escape-then-inject 以 JSX 文字節點達成，無 `dangerouslySetInnerHTML`**。移植 `markdown.test.mjs` 全案（純渲染斷言用 `renderToStaticMarkup` 或 React Testing Library）。
- `lib/conversation.ts` — `TurnObj` 型別與工具：`buildAskBody(question, conversationId?)`；latest-wins 請求序號型別/helper（或內聚於 hook，見 §6）。

**資料存取**
- `api.ts` — `getJSON` 基礎呼叫：`getConversations()`、`getConversation(id)`、`deleteConversation(id)`（DELETE→404/405 退回 POST alias）、`sendFeedback(qaId, value)`。
- `schemas.ts` — Zod：`conversationSummarySchema`、`historyItemSchema`（含 `sources[]`/`ext_sources[]`/`reports[]`，選用欄一律 `.nullish()`）、`feedbackResponseSchema`。

**Hooks**
- `hooks/useAskStream.ts` — 即時串流核心。狀態：當前 turn 的 `answer`/`sources`/`extSources`/`status`/`phase`。動作：`send(question)`、`abort()`。內部：`AbortController` **加**單調請求序號（`reqRef`），**所有 UI 寫入皆 gate `mySeq === reqRef.current`**（雙重 latest-wins）；消費 `streamAsk`；**不假設增量**；錯誤對映（`error` event→「問答服務發生錯誤」；throw→「查詢逾時或失敗」；無 token/notice 空串流→「沒有取得回答」）；`done` 回傳 `conversation_id`/`qa_id`/`thinking_ms`。
- `hooks/useConversations.ts` — `useQuery(['conversations'])` 歷史清單；`useMutation` 刪除（含 alias 退回）；成功問答後 `invalidateQueries(['conversations'])`。

**元件**
- `AskPage.tsx` — 路由元件；gates on 會話清單載入（如 `SearchPage` gates on stats）；持有 `conversationId` + `turns[]`；組裝側欄 + thread + composer。
- `ConversationSidebar.tsx` — 歷史清單（開啟/刪除/active 標記/新對話）。
- `AskThread.tsx` + `Turn.tsx` — 每輪：問題、`ProcessSteps`、答案（markdown JSX + 串流 caret）、來源/外部來源、動作列、離題 notice 卡。
- `ProcessSteps.tsx` — understand/retrieved/reading/web/generate 步驟 + thinking-time 標籤。
- `AskComposer.tsx` — 輸入 + 送出（串流中停用）；空狀態範例提問。
- `ConfirmDialog.tsx` — 移植 confirm.js（Mantine modal + `Promise<boolean>`），供刪除確認。
- 引用/來源點擊**複用 `features/search/components/ReportDetailModal.tsx`**（metadata-only；PDF 留 Phase 4）。

**路由整合**
- `App.tsx`：加 `const AskPage = lazy(() => import('./features/ask/AskPage'))` 與 `/ask` route（鏡像 `/search` 的 lazy + Suspense）。
- 不動 `web/server.py`（catch-all 已服務 `/app/*` shell；auth 重導已涵蓋 `/app/*`）。

## 5. 資料流

- **send(question)**：bump 序號 → `abort()` 前一串流 → 建立新 turn（空）→ `streamAsk(buildAskBody(q, conversationId), signal)` → 逐事件更新當前 turn（gate 序號）→ `done` 擷取 `conversation_id`（更新）/`qa_id`/`thinking_ms` → `invalidateQueries(['conversations'])`。各 turn 自帶 `sources`，`[n]` 對映限該輪（`maxCite = turn.sources.length`）。
- **loadConversation(id)**：`abort()` → `getConversation(id)` → 設 `conversationId=id` → 由 history_item 靜態重播各輪（不串流；含來源/答案/回饋/離題卡）。
- **newConversation()**：`abort()` → `conversationId=null` → 清空 turns。

## 6. 競態與錯誤處理（忠實移植 ask.js 語意）

- **雙重 latest-wins**：`AbortController` 中止網路；單調 `reqRef` 序號 gate 所有 UI 寫入（含 finally 區）——AbortController 單獨不足以擋已在飛行的 UI 寫入。切換對話/再次提問/新對話皆 bump 序號 + abort。
- **單一大 token fallback**：渲染累積 `answer += token`，單一大 token 也正確（不假設多 delta）。
- **錯誤對映**：`error` event → 「問答服務發生錯誤」；網路 throw → 「查詢逾時或失敗」；空串流（無 token 無 notice）→ 「沒有取得回答」。
- **XSS 不變式**：markdown 全程 escape-then-inject + http-only 連結 + 範圍內 `[n]`；無 `dangerouslySetInnerHTML`。

## 7. 測試策略

**單元（vitest，`src/**/*.test.{ts,tsx}`）**
- `sse`：`parseFrame`（event/data 解析、token JSON 字串、壞幀回 null）；`streamAsk`（mock `ReadableStream`：跨 chunk 切幀、happy、離題、無脈絡、單一大 token、error）。
- `markdown`：移植 markdown.test.mjs（`[n]` 範圍界、code-span 內不轉引用、黏行 normalize 含 `C#`/`#1` 防呆、http-only、`& < > " '` escape、chart/kpi 佔位、h 階 clamp、table/list）。
- `conversation`：`buildAskBody`（首輪無 id、多輪帶 id）。
- `useAskStream`：以假 generator 驗 latest-wins（舊串流被 abort + 序號 gate 後不寫 UI）、單一大 token、錯誤對映、`done` 擷取欄位。
- `useConversations`：刪除 DELETE 成功；404/405 退回 POST alias。
- `api`/`schemas`：Zod 解析 conversation/history_item 形狀（含 null 欄）。

**e2e（Playwright，`frontend/e2e/ask.spec.mjs`，對工作樹 `:8098`，非正式 `:8097`）**
- 登入 → 提問 → `waitForResponse(/\/api\/ask/)` → 串流答案 + 來源渲染 → 引用 `[n]` 點擊開 `ReportDetailModal` → 多輪續問沿用 `conversation_id` → 歷史側欄開啟既有對話 + 刪除確認 → 結束 0 `console.error`。元件補 `data-testid`（vanilla 用 id/class，React 依 search-spec 慣例改 testid）。
- **e2e 陷阱**（沿用 2a/2b）：正式 `:8097` 可能跑舊後端、`/app/*` 導向裸 `/login`；須對工作樹起的 `:8098` 跑，首次 `/api/ask` 前後端冷載 BGE-M3 需預熱、用後 kill。

**平價**：保留 vanilla 問答為對照，逐項比對事件序行為（串流、引用、多輪、離題、刪除）。

## 8. Global Constraints（writing-plans 每個任務隱含適用）

- 後端 `web/server.py`、`app/**`、`db/**` **零變動**；無 schema 變動。
- React 棧版本下限：React 19.2.7、TanStack Query 5.101、Zod 4、Mantine 9、Node 22.22+；TypeScript 嚴格、`tsc --noEmit` 須過。
- Zod 選用欄一律 `.nullish()`（後端可回 null）。
- **無 `dangerouslySetInnerHTML`**；markdown 走純節點 escape-then-inject。
- **不引入 Zustand/Redux**；UI 狀態用 React state/hook，伺服器狀態用 TanStack Query。
- SSE 串流自管連線 + `AbortController` + 單調序號 latest-wins；串流本身不進 Query 快取。
- 不 cutover；掛 `/app/ask`；舊 `/` 問答保留。
- 測試：vitest 全綠、`tsc --noEmit` 綠、eslint exit 0、e2e 對 `:8098` 綠。
- 提交走 Conventional Commits + 繁中 scope；共用工作目錄，stage 明確路徑（勿 `git add -A`/`.`）。
- 驗證指令：`cd frontend && npx vitest run src`、`npm run build`、`npm run lint`；背景跑 vitest 經 RTK 會遮非零 exit → 用 `rtk proxy npx vitest` 取真實結果。

## 9. 風險（最具載重）

1. **SSE 契約保真**：`token` 為 JSON 字串、`done` 帶 `conversation_id`/`qa_id?`、離題 `done` 無 `qa_id`、`ext_sources` 在 `done` 前、幀依 `\n\n` 切、data 行 `.trim()` 接合。
2. **雙重 latest-wins**：AbortController + 單調序號都要，且 gate 所有 UI 寫入。
3. **單一大 token fallback**：不可假設增量 delta。
4. **XSS 安全 markdown**：保 escape-then-inject + http-only + 範圍 `[n]`。
5. **各輪來源隔離**：每輪 `[n]` 綁自己的 sources（`maxCite`＝該輪來源數）——per-turn state，勿共用 store。
