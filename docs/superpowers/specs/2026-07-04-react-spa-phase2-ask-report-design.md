# Phase 2 問答 + 深度研報 `/app/ask` — React SPA 遷移設計

> react-spa-rebuild 的第三階段（承 Phase 0 骨架、Phase 1 檢索頁）。以 `docs/design/廷豐智能研報.dc.html` 為唯一視覺權威；總 spec `docs/superpowers/specs/2026-07-03-react-spa-rebuild-design.md` §7 為上位設計，本 spec 為其可實作化的細化，並以實測後端契約校正。**後端契約與邏輯零改動**（僅消費既有端點；不新增/不修改 `web/server.py`、`app/services/*`）。

## 0. 決策表（binding）

| 項目 | 決策 |
|---|---|
| 範圍 | **單一 Phase 2**：問答 + 深度研報一起交付、一份 plan（使用者拍板；不拆 2a/2b）。 |
| 路由 | `/app/ask`（Phase 0 佔位頁改實作）；`?c=<conversation_id>` 深連結載入既有對話（限已登入 session）。 |
| 問答範圍 | **全語料**：不帶任何篩選欄位（沿用既有「問答＝全語料」決策；`.dc.html` 問答頁本就無篩選 UI）。送出 body 僅 `question`（+ 多輪 `conversation_id`）。 |
| 串流狀態 | live 串流用**本地 `useReducer`**（命令式 append，不適合 react-query）；對話清單與載入既有對話用 **TanStack Query**。 |
| SSE | fetch reader `readSSE`；latest-wins（遞增 `reqId` 守門）；卸載/重送 `AbortController.abort()`；401 → `redirectToLogin`。 |
| 來源呈現 | **右側「引用來源」抽屜**（`.dc.html` 權威，非 vanilla 行內展開）；以「被點的那一回合」為範圍，含研報來源＋網路來源。 |
| 來源點擊 | 研報來源 → **重用 Phase 1 `ReportDetailModal`**（打 `/api/report/{id}/full`+`/file`，原始語料表）；網路來源 → 新分頁（scheme 守門）。 |
| 研報渲染 | **不 inline 渲染研報 markdown**（token 含 ```kpi/```chart/偶發前言，僅作進度感）；完成即 `下載 PDF`（`done.download_url`＝`/api/report-doc/{id}/pdf`，scheme 守門）。 |
| 研報進度 | **階段里程碑 %**：`retrieving 20% → writing 50% → rendering 90% → done 100%`；撰寫階段（最久）填充條加動態流動效果避免看似卡住；附階段文字。 |
| 錯誤/警告 | 專屬 `Callout`（帶底色/邊框/圖示，與一般訊息明顯區隔）：`error`（紅）用於問答/研報失敗＋重試；`warning`（amber）用於離題 `notice`＋重新提問。 |
| `done` schema | **隨路徑而異**（`offer_report`/`report_title`/`qa_id` 未必存在）→ 一律用「欄位有無」判斷，不假設固定 schema。 |
| 範例膠囊 | 問答空狀態**無**範例膠囊（沿用 rebuild 決策）。 |

## 1. 後端契約（消費對象，零改動）

所有 `/api/*` 未帶有效 `tf_session` cookie → `401 {"detail":"未登入"}`（**非導向**）。前端遇 401 一律 `redirectToLogin()`。

### 1.1 `POST /api/ask`（SSE, `text/event-stream`）
- **Request body**：`{ question: string(≤2000), conversation_id?: string }`。（後端另支援單值篩選 + `k`，本頁**不送**——全語料、`k` 用預設。）
- **併發**：`_ASK_SEMAPHORE=3`（超額排隊，非 429）。
- **SSE 幀**：`event: <name>\ndata: <json>\n\n`。**終止事件＝`done` 或 `error`**（無獨立 stream-end；無心跳）。
- **事件序**（永遠先 `status{stage:"understanding"}`，之後三條互斥路徑）：

| 路徑 | 事件序 |
|---|---|
| 正常 RAG（有脈絡） | `sources[]` → `status{stage:"retrieved",count}` → `status{stage:"reading"}` →〔`status{stage:"searching_web"}` 至多一次〕→ `status{stage:"generating",thinking_ms}`（首個真文字前一次）→ `token`×N → `ext_sources[]`（串流後一次）→ `done{cited[],qa_id,conversation_id,thinking_ms,offer_report,report_title}` |
| 正常 RAG（無脈絡） | `sources[]` → `status{retrieved,count}` → `status{generating,thinking_ms}` → `token`（`NO_CONTEXT_MESSAGE`）→ `done{cited:[],qa_id,conversation_id,thinking_ms}`（**無** offer_report） |
| 總覽/枚舉 | `sources[]`（`is_latest` 恆 false）→ `status{retrieved,count}` → `status{generating,thinking_ms}` → `token`×N → `done{cited,qa_id,conversation_id,thinking_ms}`（**無** offer_report；無網搜） |
| 離題 | `sources[]`（空）→ `notice`（字串 `OFF_TOPIC_MESSAGE`）→ `done{cited:[],conversation_id,thinking_ms}`（**無** qa_id、**無** offer_report） |

- `sources[]` item：`{ n:int, report_id:str, file_name:str, market:str, report_date:str|null, is_latest:bool }`。**無 `source`(發行機構)、無獨立 title 欄**——抽屜卡片標題用 `file_name`、次資訊只用 `report_date`；完整發行機構於點卡片開 `ReportDetailModal`(`/api/report/{id}/full` 才有 `source`)時顯示，**不在抽屜逐筆 enrichment**（避免 N 次額外請求）。
- `ext_sources[]` item：`{ title:str, url:str }`（僅正常 RAG 路徑；串流結束後一次）。
- 引用：正文行內 `[1]`、`[2]`（可連寫 `[1][3]`）對應 `sources[].n`；網路引用為正文字面「（網路）」＋獨立 `ext_sources`。
- `error`：`{"detail":"問答服務發生錯誤"}`，終止。
- **深度研報建議**（`report_gate`，純規則）：塞在正常 RAG 的 `done.offer_report`(bool) 與 `done.report_title`(str|null，格式 `"{問題} 深度研報"`)；**非獨立事件**。

### 1.2 `POST /api/report`（SSE）
- **Request body**：`{ question: string, conversation_id?: string(合法UUID否則400), qa_id?: string(合法UUID否則400) }`。`qa_id` 取自 ask `done.qa_id`，用以把研報回掛該輪。
- **併發**：`_REPORT_SEMAPHORE=1`（**序列化**，全站同時一份）。
- **事件序**：`status{stage:"retrieving"}` → `sources[]` →〔無脈絡且網搜關 → `error{detail:"找不到足夠資料生成研報"}` 終止〕→ `status{stage:"writing"}` →〔`status{stage:"searching_web"}` 至多一次、`status{stage:"writing"}` 可重送〕→ `token`×N（**markdown，含 kpi/chart/前言；前端不渲染**）→ `status{stage:"rendering"}` → `done{report_id,title,download_url,thinking_ms}`。
- 與 ask 差異：**無** `generating`、**無** `ext_sources`（網路參考內嵌在 markdown 正文）；`done` **無** `qa_id/cited/conversation_id`。
- `error`：`{"detail":"研報生成發生錯誤"}`，終止。
- `done.download_url` 固定 `/api/report-doc/{report_id}/pdf`。

### 1.3 其餘端點（一般 JSON）
| 端點 | 回傳 |
|---|---|
| `GET /api/report-doc/{id}/pdf` | binary `application/pdf`（cookie 認證，`<a href>` 直接可下載；缺檔即時由 markdown 重建） |
| `GET /api/conversations?limit=50` | `[{ conversation_id, title:str|null, last_at:iso, turn_count:int }]`（只收至少一輪非離題的對話串） |
| `GET /api/conversations/{id}` | `[{ id, question, answer, created_at, feedback:'like'|'dislike'|null, sources[], ext_sources[], is_offtopic:bool, thinking_ms:int|null, reports[{report_id,title,download_url,created_at}] }]`（由舊到新；**不濾**離題輪，靠 `is_offtopic` 重播拒答樣式） |
| `DELETE /api/conversations/{id}`（別名 `POST /api/conversations/{id}/delete`） | `{ ok:bool }` |
| `POST /api/feedback` | body `{ qa_id:str, value:'like'|'dislike' }` → `{ ok:bool }` |

**命名陷阱**：`/api/report/{id}/full`、`/api/report/{id}/file`＝**原始語料**（`research_report`，Phase 1 用）；`/api/report`(POST)、`/api/report-doc/{id}/pdf`＝**生成深度研報**（`report_doc`）。兩者 id 空間不同，前端務必分清。

## 2. 架構與狀態

- `AskPage`（default export）：讀 `?c`，以 `useReducer(askReducer, ...)` 管 `turns[]`＋串流；以 react-query 管 `conversations` 清單與載入既有對話。
- **turn 模型**（reducer state；一筆 `Turn` ＝ **一輪 QA pair**，非 role-based 訊息串——render 時同一筆輸出 `UserMessage`+`AssistantMessage`；歷史重播與研報回掛皆以此 QA pair 為單位）：
  ```ts
  type Stage = 'understanding'|'retrieved'|'reading'|'searching_web'|'generating'
  interface Turn {
    id: string                 // client 端 uuid（render key）
    question: string
    // 串流/狀態
    phase: 'thinking'|'streaming'|'done'|'notice'|'error'
    stages: Stage[]            // 已抵達的階段（驅動 ThinkingSteps）
    webUsed: boolean           // 是否出現過 searching_web（決定「網路補充」步驟是否顯示）
    answer: string             // 累積 token
    thinkingMs: number | null  // done/status 的 thinking_ms；缺則用 client 計時
    startedAt: number          // client 計時起點
    // 結果
    sources: Source[]          // {n,report_id,file_name,market,report_date,is_latest}
    extSources: ExtSource[]    // {title,url}（已 scheme 過濾）
    cited: string[]            // done.cited（report_id）
    qaId: string | null        // 離題分支為 null → 不可 feedback
    isOfftopic: boolean
    noticeText: string | null
    offerReport: boolean       // done.offer_report（僅正常 RAG）
    reportTitle: string | null
    feedback: 'like'|'dislike'|null
    // 研報
    report: ReportState        // idle|offered|generating|done|error（見 §4.2）
    errorText: string | null   // 問答層失敗
  }
  ```
- `conversationId`（AskPage state，非 reducer）：首個 `done.conversation_id` 設定後續用；同步 URL `?c=`。
- latest-wins：**串流消費層**（AskPage 或 `useAskStream` hook）持 `reqId` ref，每次送出 `++reqId`；讀到 readSSE 事件時比對「此串流的 stamp === 當前 reqId」，**不符則在 dispatch 前丟棄**（故 `askReducer` 永遠只收到當前串流事件、保持純粹，stamp 不進 reducer state/action）；同時 `AbortController.abort()` 舊串流。

## 3. 元件（每個一責任、可獨立測試）

| 元件 | 責任 | 主要 props |
|---|---|---|
| `AskPage`（feature 容器） | 讀 `?c`、reducer、SSE、貼底捲動、組合 | — |
| `Composer` | textarea 自動長高、送出鈕；Enter 送出 / Shift+Enter 換行 / **IME 守衛**（`e.nativeEvent.isComposing`）；`disabled`（串流中）；空狀態置中版 + 底部固定版由 `variant` 切 | `value,onChange,onSubmit,disabled,variant` |
| `AskEmptyState` | 置中 廷 logo + 「向廷豐智能體提問」+ 副標 + 置中 `Composer`；**無範例膠囊** | `onSubmit` |
| `UserMessage` | 右側金色實心泡泡 | `text` |
| `AssistantMessage` | 「已思考 N 秒」可收合 + 襯線 markdown 區塊 + 行內 `[n]` 金膠囊 + 動作列（讚/倒讚/複製 + 分隔 + **單一** `資料來源 {N}`，N＝研報來源數＋網路來源數，對齊 `.dc.html` `m.refCount`；點擊開抽屜。無獨立「外部參考」鈕） | `turn,onCite,onToggleSources,onFeedback,onCopy` |
| `ThinkingSteps` | 思考卡：理解問題→檢索研報→閱讀整理→(網路補充)→生成回答；由 `turn.stages` 映射三態 ✓/spinner/dot；收尾凍結「已思考 N 秒」 | `turn` |
| `DeepReportPanel` | offer(要/不用)→生成中(里程碑 %+撰寫動態+階段文字)→完成(暖金卡＋下載 PDF)→**失敗** `Callout error`＋重試 | `turn.report,onGenerate,onDecline,onRetry` |
| `SourcesDrawer` | 右側「引用來源」抽屜（**單一入口、面板內含兩類內容**）：標頭「引用來源」+關閉；子標「資料來源 · {N}」（N＝研報＋網路**總數**，對齊 `.dc.html` `drawerSrcCount = sources.length + externals.length`）；研報來源卡（金 `n`+市場徽章+`file_name`(標題)+`report_date`(次資訊)，點→ReportDetailModal）；其後緊接網路來源卡（橘框 `n`+標題+「網路 · 站台」，→新分頁）；ESC/scrim 關 | `open,turn,onClose,onOpenReport` |
| `Callout` | 警示卡原語：`variant:'error'|'warning'`，圖示+底色+邊框+文字+選用 action | `variant,children,action?` |
| 重用 | `ReportDetailModal`(Phase 1)、`ConversationList`(Phase 0 側欄，導 `/ask?c=`)、`AppShell`、`Modal`/`Icon` 原語 | — |
| lib | `readSSE.ts`、`askApi.ts`（Zod schema + POST fetch + conversations getJSON）、`askMarkdown.tsx`（`[n]`→膠囊、XSS 安全 React 節點）、`thinkingStages.ts`（stage→步驟清單 + 三態）、`reportProgress.ts`（stage→里程碑 %）、`askReducer.ts`（`(state,event)→state` 純函式） | — |

- `Icon` 新增：`alert`（error 用，圓圈驚嘆）、`alert-triangle`（warning 用）、`send`（送出箭頭，若 Phase 0 未有）、`file-text`（研報完成卡）。
- `tokens.css` 新增 amber 警告色階：`--tf-warning:#ff9f0a; --tf-warning-bg:#fffaeb; --tf-warning-border:#fedf89; --tf-warning-text:#b25e00;`（error 色階已存在）。

## 4. 資料流

### 4.1 送出問答
1. `Composer.onSubmit(q)` → reducer `SUBMIT`：push **一筆 Turn（QA pair）**（`question=q`、`answer=''`、`phase:'thinking'`, `startedAt=now`, `stages:['understanding']`）——render 時該筆同時輸出 `UserMessage`(question) + `AssistantMessage`(answer/狀態)，**不是**分開 push 兩筆 role-based 訊息；`++reqId`；abort 舊串流。
2. `POST /api/ask {question:q, conversation_id?}` → `readSSE`（帶此串流 stamp）。
3. 事件 → reducer（僅當 stamp===reqId）：
   - `status` → 累加 `stages`；`searching_web`→`webUsed=true`；`generating`→`phase:'streaming'`、記 `thinking_ms`。
   - `sources` → `turn.sources`（過濾/保序）。
   - `ext_sources` → `turn.extSources`（`safeHttp` 過濾）。
   - `token` → append `turn.answer`（→ `AssistantMessage` 以累積字串渲染 + 串流游標）。
   - `notice` → `phase:'notice'`、`noticeText`、`isOfftopic:true`（渲染 `Callout warning`）。
   - `done` → `phase:'done'`（或維持 notice）；記 `qaId?`、`conversationId`、`cited`、`offerReport?`、`reportTitle?`。首個 `done.conversation_id` → AskPage 設 `conversationId` + `setParams(?c=)`（`replace`，不灌歷史）。
   - 串流結束無 token 且非 notice → `phase:'error'`、`errorText:"查詢逾時或失敗"`。
4. `error` 事件 → `phase:'error'`、`errorText`。
5. 收尾 refresh `conversations`（react-query invalidate，撿新標題）。
6. 貼底捲動：僅當使用者在底部 90px 內才自動貼底（讀舊訊息不被拉回）。

### 4.2 深度研報
- `ReportState`：`{ status:'idle'|'offered'|'generating'|'done'|'error', pct:number, stageText:string, downloadUrl?:string, title?:string, errorText?:string }`。
- `done.offer_report===true` → `report.status='offered'`（`DeepReportPanel` 顯 offer）。
- 「要」→ `POST /api/report {question:turn.question, conversation_id, qa_id:turn.qaId}` → readSSE：
  - `status` → `reportProgress(stage)` 設 `pct`/`stageText`（`retrieving 20`「深度檢索研報中…」、`searching_web`「搜尋網路補充…」不改 pct、`writing 50`「撰寫研報中…」、`rendering 90`「排版 PDF 中…」）。
  - `token` → **忽略內容**，僅作 liveness（可選：撰寫階段填充條動態）。
  - `done` → `status='done'`, `pct=100`, `downloadUrl`, `title` → 完成卡（下載連結 scheme 守門）。
  - `error` / 串流結束無 done → `status='error'`, `errorText`（`Callout error`＋重試）。
- 「不用」→ `report.status='idle'`（清掉 offer，不持久化拒絕）。
- **持久化**：完成研報寫入該 turn；歷史重播由 `HistoryItem.reports[]` 還原完成卡（避免重播時重複 offer；`offer` 只在 live 出現）。研報串流獨立 `AbortController`；同時最多一份（後端序列化，前端不必額外鎖，但送出中的按鈕 disabled）。

### 4.3 載入既有對話（`?c` 或點側欄）
- `GET /api/conversations/{id}`（react-query）→ 逐筆重建 turn：`question/answer/sources/ext_sources/thinking_ms/feedback`；`is_offtopic:true`→ notice 樣式（`Callout warning`，用 `OFF_TOPIC_MESSAGE`）；`reports[]` 非空 → 研報完成卡（取最後一筆 `download_url`）。重播不顯 offer、不顯串流游標；動作列（讚/倒讚/來源）依資料還原（feedback 預點亮）。
- 切換對話 / 新對話：abort 現行串流；清 turns；`new` → `conversationId=null`、`?c` 移除、顯空狀態、composer 回置中。

### 4.4 對話側欄 / 回饋
- `ConversationList`（Phase 0）：`GET /api/conversations?limit=50`（react-query）；點 → 導 `/ask?c=<id>`；`新對話` → 導 `/ask`。
- **刪除對話【批准延伸】**：`.dc.html` 與上位 rebuild spec §7 側欄僅畫「新對話＋歷史選取」、**未畫刪除 UI**；本 Phase 依「功能對等硬約束」保留 vanilla 現有刪除能力，為經使用者批准之刻意偏離設計權威。行為：歷史列每列附垃圾桶鈕 → `confirmDialog`（標題「刪除此對話？」/內文「將永久移除整個對話串，無法復原。」/確認鈕「刪除」）→ `DELETE /api/conversations/{id}`（若 404/405 fallback `POST /api/conversations/{id}/delete`）→ invalidate `conversations`；刪到目前對話則轉新對話（`newConversation`）。
- 回饋：`onFeedback(qaId,value)` → 樂觀切換 `.on`（讚/倒讚互斥）→ `POST /api/feedback`；失敗靜默不回滾；**僅非離題且有 `qaId` 的回合**顯示動作列。

## 5. SSE（`readSSE`）
- `fetch(url,{method:'POST',headers,body,signal})`；`resp.status===401`→`redirectToLogin()`；`resp.body.getReader()`+`TextDecoder`；累積 buffer，`\n\n` 分幀；每幀掃 `event:`/`data:` 行、`JSON.parse(data)`；壞幀（parse 失敗或缺 data）**丟棄**（回傳 null）。以 async generator 或 callback 逐一吐出 `{event,data}`。呼叫端傳 `signal`（AbortController）與 stamp 守門。`AbortError` 靜默。

## 6. markdown 渲染（`askMarkdown.tsx`）
- 解析：標題（h1–h6，視覺上限 h4，襯線）、粗體/斜體/行內 code、fenced code、有序/無序清單、表格、blockquote、hr。
- **行內 `[n]`**：`1≤n≤sources.length` → 金膠囊（`<span>` 節點，點擊 `onCite(n)` 開 `SourcesDrawer` 並高亮該來源）；越界 `[n]` 保留字面。
- ` ```chart `/` ```kpi ` fenced（研報用；此頁只在 assistant 答案偶現）→ 佔位提示（不畫圖）。
- **XSS**：回傳 React 節點，**不用** `dangerouslySetInnerHTML`；外部連結僅 `http(s):`、`rel="noopener noreferrer"`。串流中與重播兩路徑產出一致。
- 效能：以累積字串在 render 期渲染；`useMemo` 依 `answer` 長度快取；現行答案長度可接受（記錄 O(n²) 隱憂）。

## 7. Mockup ↔ 真實 調適（明確記錄，避免被當缺漏）
- `.dc.html` 的 5 步思考動畫為 mockup 定時器；**真實由 `status.stage` 驅動**：`understanding→理解問題`、`retrieved(count)→檢索研報`（可顯「找到 N 篇」）、`reading→閱讀整理`、`searching_web→網路補充`（僅出現時顯示該步）、`generating→生成回答`；收尾「已思考 N 秒」（`thinking_ms` 優先，缺則 client 計時）。
- `.dc.html` 研報 `%` 進度為 mockup；**真實無 %** → 里程碑 %（§4.2）＋撰寫動態填充。
- `.dc.html` 完成卡「14 頁 · 含 3 張圖表」為 mockup 文案；**後端 `done` 無頁數/圖數** → 完成卡顯 `title`＋`下載 PDF`（不寫死頁數/圖數）。
- `.dc.html` 來源抽屜為權威（取代 vanilla 行內展開）。

## 8. 錯誤 / 警告 `Callout`（與一般訊息區隔）
- `Callout` 呈現：淺色底 + 1px 邊框 + 左側圖示 + 彩色文字 + 選用 action 鈕；**不套** assistant 訊息樣式（無「已思考」、無動作列）。
- `error`（紅）：`--tf-error-bg/-border/-text` + `alert` 圖示。用於：問答失敗（`error` 事件或串流結束無 token）「查詢逾時或失敗」+ `重試`；研報失敗「研報生成失敗，請重試」+ `重試`。
- `warning`（amber）：`--tf-warning-bg/-border/-text` + `alert-triangle` 圖示。用於：離題 `notice`（`OFF_TOPIC_MESSAGE`）+ `換個說法重新提問`（回填問題不自動送）。
- 研報「生成中/完成」為正常態，維持 `.dc.html` 暖金樣式（非 Callout）；僅失敗切 `Callout error`。

## 9. 文案（繁中，逐字）
- 空狀態：標題「向廷豐智能體提問」；副標「以自然語言詢問研究主題，回答將附上券商研報的引用來源。」；composer placeholder「輸入你的問題…」。
- 思考步驟：`理解問題`、`檢索研報`、`閱讀整理`、`網路補充`、`生成回答`；凍結「已思考 {N} 秒」。
- 動作列：單一 `資料來源 {N}`（N＝研報來源數＋網路來源數，對齊 `.dc.html` `m.refCount`）；複製後暫態 `已複製`。
- 研報 offer：標題「要不要整理成完整 PDF 深度研報？」；副標「彙整以上引用來源，生成含圖表與重點的深度研報。」；鈕 `要` / `不用`。
- 研報生成中：標題「深度研報生成中…」；階段文字（§4.2）；完成：「深度研報已完成」+ `下載 PDF`。
- 抽屜：標題「引用來源」；子標「資料來源 · {N}」（N＝研報＋網路**總數**，對齊 `.dc.html` `drawerSrcCount`）；研報來源卡標題用 `file_name`、次資訊只顯 `report_date`（ask `sources[]` 無發行機構欄）；網路來源卡緊接其後、逐條標「網路 · {站台}」（無獨立網路子標）。
- 錯誤：問答「查詢逾時或失敗」+ `重試`；研報「研報生成失敗，請重試」+ `重試`；離題採後端 `OFF_TOPIC_MESSAGE` 字串 + `換個說法重新提問`。

## 10. 測試
- **單元（Vitest）**：`readSSE`（分幀/壞幀丟棄/多幀）、`askMarkdown`（`[n]` 膠囊界內外、XSS `<img onerror>` 惰性、外部連結 scheme）、`thinkingStages`（stage→步驟三態、`searching_web` 才顯網路步）、`reportProgress`（stage→里程碑 %）、`askReducer`（各事件→state、done schema 三分支；**reducer 為純函式、不含 stamp 概念**）。
- **元件／整合**：`Composer`（Enter 送/ Shift+Enter 換行 / IME 守衛不誤送 / disabled）、`AssistantMessage`（`[n]` 點擊 onCite、**單一 `資料來源 {N}` 開抽屜且 N＝研報＋網路**、動作列僅非離題）、`ThinkingSteps`（階段轉換）、`DeepReportPanel`（offer→generating→done→error 四態、下載連結 scheme）、`SourcesDrawer`（研報點擊 onOpenReport、網路新分頁、ESC/scrim 關）、`Callout`（兩 variant）、**`AskPage`/`useAskStream` 整合：舊 stamp 事件在 dispatch 前被丟棄、reducer 不接收 stale 事件**。
- **e2e（Playwright :8098 live）**：問答→串流答案+來源抽屜、多輪追問（第二輪帶 `conversation_id`）、離題→`Callout warning`；研報 ~5min 用 terminal-agnostic（`done` 或 `error` 皆可通過），需暖機 BGE-M3 + 放寬 per-test timeout（`--timeout` 覆寫 config 30s）。live LLM 端到端不確定性用 `toPass`。

## 11. 非目標 / 範圍外
- 不動任何後端（不修 `web/server.py`/`app/services/*`；不加端點）。
- 不 inline 渲染生成研報 markdown、不做研報全文 modal（完成即下載 PDF）。
- 不對問答加篩選（全語料）。
- 不 cutover（`/` 仍導 vanilla；本頁在 `/app/ask` 與 vanilla 共存）。
- 不處理離題回合的 feedback（後端該分支無 `qa_id`）。
- 不修既有後端契約缺口（離題無 qa_id、`done` schema 不一）——前端以防禦式消費因應。
