# Phase 4 — 深度研報 report（vanilla → React）設計

**日期**：2026-07-01
**分支**：`feat/frontend-phase4-deep-report`（off main 7eca29c，含已合併 Phase 3 #44）
**前置**：Phase 3 ask 串流問答（PR #44 已併 main）。本階段**擴充** Phase 3 的 ask feature，非新頁。

## 目標

把 vanilla 的深度研報功能（生成建議 → `/api/report` SSE 串流生成 → 即時預覽 → PDF 下載 → 歷史重播）遷移為 React，並依使用者拍板**擴充**兩項超出 vanilla 的能力：

1. **app 內「查看研報全文」**（除下載 PDF 外，可在 app 內開全文 modal，用既有 `/api/report/{id}/full`）。
2. **前端渲染 KPI 卡與圖表**（```kpi/```chart 區塊在即時預覽與全文檢視都以精美元件呈現，非程式碼區塊）；圖表用 **@mantine/charts（Recharts）**。

**硬約束**：後端 `web/server.py`/`app/**`/`db/**` **零變動**、無 schema 變更；加法式上線、**不 cutover**（研報是 `/app/ask` 內每輪的一部分，無新路由）；TypeScript strict；React 19 + Mantine 9 + TanStack Query 5，**不引入 Zustand/Redux**；**無 `dangerouslySetInnerHTML`**；Conventional Commits 繁中 scope + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer。

**A5 決策定案**：本階段把 REFACTOR_TODO A5 原延後至 Phase 6 的「圖表庫選型」定為 **@mantine/charts**。注意 `app/services/chart.py` 的 SVG 是 PDF 專用、與前端圖表庫無關；**前端預覽/全文圖表外觀 ≠ PDF 圖表外觀**（可接受的刻意分歧）。

## 背景：vanilla 行為（`web/static/app/ask.js`，`.ask-report-host`）

- `done` 事件帶 `offer_report`/`report_title`（Phase 3 已解析為 `TurnState.offerReport`/`reportTitle`，**刻意未渲染**，留給本階段）。
- 帶 `offer_report` → 該輪渲染對話式建議卡：「要不要我幫你整理成一份完整 PDF 研報？」+ 要／不用。
- 點「要」→ POST `/api/report`，body `{ question: turn.q, conversation_id, qa_id }`。
- **報告 SSE 事件序**：`status{stage}`（`retrieving`/`searching_web`/`writing`/`rendering`）→ `sources`（可有，**vanilla 的報告面板不渲染來源**，本階段亦忽略以維持 parity）→ `token`（**JSON 字串**，累積成 markdown）→ `done{ report_id, title, download_url, thinking_ms }` → 或 `error{ detail }`。`useReportStream` 只處理 status/token/done/error，`sources` 事件被安全忽略。
- 即時預覽：`md += token; renderMarkdown(md, 0)`（maxCite=0，預覽內 `[n]` 不可點）。
- 完成：收合為「下載 PDF」卡（`title` + `download_url`）。
- 失敗：訊息 + 重試。
- 單一全域 `currentReportCtrl`：**一次只允許一份研報**；切歷史/新對話時中止。
- 歷史重播：`getConversation` 回的每輪含 `reports: [{ report_id, title, download_url, created_at }]` → 直接渲染完成卡（不重跑生成）。

## 資料契約（後端既有，不變）

- **POST `/api/report`**（SSE）：body `{ question, conversation_id?, qa_id? }`。事件如上。
- **GET `/api/report-doc/{id}/pdf`**：PDF 位元組（`download_url` 即指向此）。
- **GET `/api/report/{id}/full`**：回 `{ report_id, title, markdown, ... }`（全文 markdown，供 app 內全文檢視同管線渲染）。
- **GET `/api/report/{id}/file`**：原始檔（本階段不需，除非全文 modal 要「原始檔」按鈕；YAGNI，暫不做）。
- **```kpi 區塊**：`{"items":[{"label","value","change","dir":"up"|"down","source"}]}`。
- **```chart 區塊**：`{"type":"bar"|"line"|"pie","title","x":[...],"series":[{"name","values":[數字]}],"unit","source"}`。
- 歷史：`getConversation(id)` 的每個 history item 需含 `reports?: [{ report_id, title, download_url, created_at }]`（後端 `get_conversation_reports` 已提供；前端 schema 補上）。

## 架構

擴充 `frontend/src/features/ask/`。研報狀態**掛在每一輪**（per-turn），與問答同層但獨立生命週期。重用 Phase 3 的 `parseFrame`、`renderMarkdown`、latest-wins 心法（單調序號 + AbortController）。

### SSE 共用（小重構，DRY）

`lib/sse.ts` 目前 `streamAsk` 內含 fetch-reader 迴圈 + `parseFrame` 切幀。抽出共用底層：

```
// lib/sse.ts
export async function* readSSE(url: string, body: object, signal: AbortSignal): AsyncGenerator<{event: string; data: unknown}>
// 內含：fetch(POST json) → 401 redirect → reader.read() → 依 \n\n 切幀 → parseFrame → yield 原始 {event, data}
```

`streamAsk` 改為薄包裝 `readSSE('/api/ask', ...)`（維持既有 `AskEvent` 型別窄化）；新增 `streamReport` 薄包裝 `readSSE('/api/report', ...)`（`ReportEvent` 型別窄化）。Phase 3 既有 sse 測試（parseFrame + streamAsk 事件序）為安全網；僅底層抽取，行為不變。

### 檔案（皆在 `frontend/src/features/ask/`）

| 檔案 | 職責 |
|---|---|
| `lib/sse.ts`（改） | 抽 `readSSE` 共用底層；`streamAsk` 薄包裝 |
| `lib/reportEvents.ts`（新） | `ReportStatus`/`ReportDone`/`ReportEvent` 型別 |
| `lib/sse.ts`（同上，新增 export） | `streamReport(body, signal)` async generator（薄包 `readSSE('/api/report', ...)`，與 `streamAsk` 同檔）。**不另立 reportStream.ts** |
| `lib/reportBlocks.ts`（新，純函式） | `parseReportSegments(md)`：把研報 markdown 切為有序 段：`{kind:'md',text}` / `{kind:'kpi',items}` / `{kind:'chart',spec}`；`kpi`/`chart` 以 Zod 防禦解析，**壞塊 → 降級為 `{kind:'md'}` 原樣文字，絕不拋**（移植 pdf.py「逐層守門、壞塊跳過」教訓） |
| `lib/reportMarkdown.tsx`（新） | `renderReport(md): ReactNode[]`：對 `parseReportSegments` 的每段，md 段走 Phase 3 `renderMarkdown`（maxCite=0），kpi 段 → `<KpiCards>`，chart 段 → `<ReportChart>`。預覽與全文 modal 共用 |
| `components/KpiCards.tsx`（新） | KPI 卡列：value（大）/label（小）/change（`dir==='up'` 綠、`'down'` 紅）/來源徽章；對齊 `pdf.py` inject_kpi 語意；Mantine |
| `components/ReportChart.tsx`（新） | chart spec → `@mantine/charts`：`bar`→BarChart、`line`→LineChart、`pie`→DonutChart；spec.series/x → Mantine data；title/unit/source 標註；**pie 遇負值拒繪**（對齊 chart.py）；壞 spec → 不渲染 |
| `components/ReportPanel.tsx`（新） | 每輪研報區：`offer`（要/不用）→ `generating`（狀態列 + 即時預覽 `renderReport`）→ `done`（下載 PDF + 查看全文）→ `failed`（訊息 + 重試）。狀態由 `useReportStream` 提供 |
| `components/ReportFullModal.tsx`（新） | 開啟時抓 `getReportFull(id)`，以 `renderReport` 渲染全文（含 KPI/圖表）；Mantine Modal |
| `hooks/useReportStream.ts`（新） | 管理**單一進行中研報**（vanilla parity）：`start(turn)`、`retry`、`cancel`、`state`（依 turnId 對映）；單調 `seqRef` + AbortController，切對話/新研報即中止、latest-wins gate |
| `schemas.ts`（擴充） | `reportDoneSchema`、`kpiBlockSchema`、`chartBlockSchema`、`reportFullSchema`、`historyItemSchema.reports`（`.nullish()`） |
| `api.ts`（擴充） | `getReportFull(id)`（401 守門、Zod 驗證） |
| `components/Turn.tsx`（改） | 掛 `<ReportPanel turn={turn} report={...} on...={...} />`（done 且 offerReport 或已有研報時） |
| `AskPage.tsx`（改） | 串接 `useReportStream`；`newConversation`/`loadConversation` 時中止進行中研報；全文 modal 狀態 |

### 狀態管理

- **研報狀態 per-turn，單一進行中**：`useReportStream` 持 `{ activeTurnId, phase: 'idle'|'generating'|'done'|'error', status, markdown, done, error }`，以 turnId 對映當前研報。啟動新研報或切對話 → `cancelActive()` bump seq + abort。
- 歷史重播的研報（`turn` 帶 `reports`）為**唯讀完成卡**，不經 `useReportStream`（直接由 `ReportPanel` 依 `turn.reports` 渲染 done 卡）。
- UI 狀態（modal 開關）走 React state；**不引入狀態庫**。

### 錯誤處理／健壯性

- SSE `error` 事件或網路失敗 → `failed` 卡 + 重試（重試重呼 `start`）。
- `token` 逾時但已串部分 md → 對齊 vanilla：串流結束無 `done` → 視為未完成失敗（可重試）。
- **kpi/chart 壞 JSON → 該塊降級為原樣文字段，絕不整份炸**（前端版 pdf.py 防禦；Zod safeParse，失敗 fallback）。
- 401 → 導向登入（同 Phase 3）。

## 測試

- `lib/reportBlocks.test.ts`（純函式）：純文字、單 kpi、單 chart、混合、壞 kpi JSON→降級、壞 chart→降級、空 items、未知 chart type。
- `lib/reportMarkdown.test.tsx`：交錯段渲染出 KpiCards/ReportChart/文字；無 `dangerouslySetInnerHTML`。
- `components/KpiCards.test.tsx`：up 綠/down 紅/無 dir/來源徽章/空。
- `components/ReportChart.test.tsx`：bar/line/pie 對映、pie 負值拒繪、壞 spec 不渲染。
- `hooks/useReportStream.test.tsx`：start→status→token 累積→done；latest-wins（切換中止舊）；error→failed；retry。
- `components/ReportPanel.test.tsx`：offer→要→generating→done（下載+查看全文）；不用→收起；失敗→重試；歷史 reports→done 卡。
- `e2e`（延伸 `ask.spec.mjs` 或新 `report.spec.mjs`）：問答後 offer → 生成 → 預覽含 KPI/圖表 → 下載連結存在 → 查看全文 modal。控制端對 :8098 跑、預熱 BGE-M3、放寬 test timeout（研報生成比問答更久）。

## 相依與部署

- **新增相依**：`@mantine/charts` + `recharts`（peer）。`frontend/package.json` + `npm install`。
- 部署：`make spa-build` 重建 `frontend/dist`；後端免重啟（`_NoCacheStatic`）；無 schema。研報功能出現在既有 `/app/ask` 每輪（問答建議後），無新導覽入口。

## 範圍界線（YAGNI）

- **不做**：`/api/report/{id}/file` 原始檔按鈕（全文 modal 已足）；報告的獨立列表頁；PDF 前端渲染（下載即 server PDF）；kpi/chart 的編輯/互動超出 Recharts 內建 tooltip。
- **刻意分歧**：前端圖表（Mantine/Recharts）≠ PDF 圖表（chart.py SVG）。
- **小重構**：`lib/sse.ts` 抽 `readSSE`（DRY，非無謂重構；Phase 3 sse 測試護底）。
