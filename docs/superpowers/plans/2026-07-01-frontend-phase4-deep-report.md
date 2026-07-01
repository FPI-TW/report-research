# Phase 4 深度研報 report — 實作計畫

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 vanilla 深度研報（offer → `/api/report` SSE 生成 → 即時預覽 → PDF 下載 → 歷史重播）遷移為 React，並擴充「app 內查看全文」與「前端 KPI 卡/圖表」。

**Architecture:** 擴充 `frontend/src/features/ask/`（非新頁）。重用 Phase 3 `parseFrame`/`renderMarkdown`/latest-wins。抽 `readSSE` 共用底層供 ask/report 兩串流。研報狀態 per-turn、單一進行中。kpi/chart 以 Zod 防禦解析後渲染成 React（圖表用 @mantine/charts）。

**Tech Stack:** React 19、Mantine 9、@mantine/charts（新增）、TanStack Query 5、Zod 4、Vitest 4、TypeScript strict、Playwright。

## Global Constraints

- 後端 `web/server.py`/`app/**`/`db/**` **零變動**；無 schema 變更。
- 加法式、**不 cutover**；研報在既有 `/app/ask` 每輪內，無新路由。
- TypeScript strict（`tsc --noEmit` 過）；**無 `dangerouslySetInnerHTML`**；不引入 Zustand/Redux。
- 圖表庫 = **@mantine/charts**（A5 定案）；前端圖表 ≠ PDF 圖表（刻意）。
- kpi/chart 壞 JSON → 該塊降級為原樣文字、**絕不整份炸**（移植 pdf.py 防禦）。
- 單一進行中研報（vanilla parity）；切對話/新研報即 abort、單調序號 latest-wins。
- report `sources` 事件被前端安全忽略（parity）。
- Conventional Commits 繁中 scope + `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer。每檔僅 `git add` 明確路徑，禁 `-A`/`.`。
- 測試指令 `cd frontend && npx vitest run <path>`；全套驗證用 `rtk proxy npx vitest run src`（RTK 遮非零 exit 陷阱）。
- SSE 事件文法（後端既有，不變）：
  - report：`status{stage: retrieving|searching_web|writing|rendering}` → `sources`(忽略) → `token`("<JSON字串>") → `done{report_id,title,download_url,thinking_ms}` | `error{detail}`。
  - `token` data 是 JSON 字串；`done`/`status`/`error` 是物件。終止 = done|error。全文可能一次一大 token。
- kpi 塊：`{"items":[{"label","value","change","dir":"up"|"down","source"}]}`。
- chart 塊：`{"type":"bar"|"line"|"pie","title","x":[...],"series":[{"name","values":[數字]}],"unit","source"}`。

---

## 檔案結構

新增（features/ask 下）：`lib/reportEvents.ts`、`lib/reportBlocks.ts`、`lib/reportMarkdown.tsx`、`components/KpiCards.tsx`、`components/ReportChart.tsx`、`components/ReportPanel.tsx`、`components/ReportFullModal.tsx`、`hooks/useReportStream.ts`（+ 各測試）。
修改：`lib/sse.ts`（抽 readSSE + streamReport）、`schemas.ts`（kpi/chart/reportDone/reportFull/historyItem.reports）、`api.ts`（getReportFull）、`components/Turn.tsx`（掛 ReportPanel）、`AskPage.tsx`（串接、切對話中止、全文 modal）、`package.json`（+@mantine/charts）。

模型：純邏輯/schema/型別（附完整程式碼＝轉錄）用 **haiku**（Tasks 1-4）；元件/hook/整合/e2e 用 **sonnet**（Tasks 5-12）；最終整支審查 **opus**。

---

### Task 1: lib/sse.ts 抽出 readSSE 共用底層（重構，行為不變）

**Files:** Modify `frontend/src/features/ask/lib/sse.ts`；既有 `frontend/src/features/ask/lib/sse.test.ts` 為安全網。

**Interfaces:**
- Produces: `export async function* readSSE(url: string, body: object, signal: AbortSignal): AsyncGenerator<AskEvent>`（yield 原始 `{event,data}`，型別沿用 `AskEvent` 聯集的底層 `parseFrame` 產物）。
- `streamAsk(body, signal)` 改為 `yield* readSSE('/api/ask', body, signal)`，對外簽名與行為不變。

- [ ] **Step 1: 先跑既有 sse 測試確認綠（回歸基準）**
Run: `cd frontend && npx vitest run src/features/ask/lib/sse.test.ts` — Expected: PASS（Phase 3 既有）。

- [ ] **Step 2: 重構 sse.ts**

把現有 `streamAsk` 內的 fetch-reader 迴圈抽成 `readSSE(url, body, signal)`，`streamAsk` 委派之。完整程式碼：

```ts
import type { AskEvent } from './sseEvents'
import { ApiError, redirectToLogin } from '../../../lib/api'

/** SSE frame（event:/data: 行）→ 型別化事件；無 data 或壞 JSON 回 null。 */
export function parseFrame(frame: string): AskEvent | null {
  let event = 'message'
  let data = ''
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data += line.slice(5).trim()
  }
  if (!data) return null
  try {
    return { event, data: JSON.parse(data) } as AskEvent
  } catch {
    return null
  }
}

/** POST + text/event-stream 通用 fetch-reader；依 \n\n 切幀 → parseFrame → yield。 */
export async function* readSSE(
  url: string,
  body: object,
  signal: AbortSignal,
): AsyncGenerator<AskEvent> {
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
    credentials: 'same-origin',
  })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  if (!resp.ok || !resp.body) throw new Error('bad response')
  const reader = resp.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let idx: number
    while ((idx = buf.indexOf('\n\n')) >= 0) {
      const evt = parseFrame(buf.slice(0, idx))
      buf = buf.slice(idx + 2)
      if (evt) yield evt
    }
  }
}

/** POST /api/ask（text/event-stream），yield 型別化 ask 事件。 */
export async function* streamAsk(body: object, signal: AbortSignal): AsyncGenerator<AskEvent> {
  yield* readSSE('/api/ask', body, signal)
}
```

- [ ] **Step 3: 跑既有 sse 測試 + tsc（行為不變回歸）**
Run: `cd frontend && npx vitest run src/features/ask/lib/sse.test.ts && npx tsc --noEmit` — Expected: PASS / 0 errors。

- [ ] **Step 4: Commit**
```bash
git add frontend/src/features/ask/lib/sse.ts
git commit  # refactor(ask): 抽出 readSSE 共用底層供 report 串流重用（Phase 4 Task 1）
```

---

### Task 2: lib/reportEvents.ts 型別 + streamReport

**Files:** Create `frontend/src/features/ask/lib/reportEvents.ts`；Modify `frontend/src/features/ask/lib/sse.ts`（加 `streamReport`）；Create `frontend/src/features/ask/lib/reportStream.test.ts`。

**Interfaces:**
- Produces `reportEvents.ts`：`ReportStage = 'retrieving'|'searching_web'|'writing'|'rendering'`；`ReportDone = { report_id: string; title: string; download_url: string; thinking_ms?: number }`；`ReportEvent = {event:'status';data:{stage:ReportStage}} | {event:'sources';data:unknown} | {event:'token';data:string} | {event:'done';data:ReportDone} | {event:'error';data:{detail?:string}}`。
- Produces `sse.ts`：`streamReport(body: object, signal: AbortSignal): AsyncGenerator<ReportEvent>`（薄包 `readSSE('/api/report', ...)`，`as AsyncGenerator<ReportEvent>` 窄化，理由：後端 report 事件集）。

- [ ] **Step 1: 寫失敗測試 `reportStream.test.ts`**

```ts
import { describe, expect, test, afterEach, vi } from 'vitest'
import { streamReport } from './sse'

function sseResponse(frames: string[]) {
  const body = frames.join('')
  const enc = new TextEncoder()
  return {
    status: 200,
    ok: true,
    body: {
      getReader() {
        let sent = false
        return {
          read: async () =>
            sent ? { done: true, value: undefined } : ((sent = true), { done: false, value: enc.encode(body) }),
        }
      },
    },
  } as unknown as Response
}

afterEach(() => vi.restoreAllMocks())

test('streamReport 解析 status→token→done 事件序', async () => {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    sseResponse([
      'event: status\ndata: {"stage": "writing"}\n\n',
      'event: token\ndata: "## 標題\\n"\n\n',
      'event: done\ndata: {"report_id":"r1","title":"T","download_url":"/api/report-doc/r1/pdf"}\n\n',
    ]),
  )
  const evts: unknown[] = []
  for await (const e of streamReport({ question: 'q' }, new AbortController().signal)) evts.push(e)
  expect(evts).toEqual([
    { event: 'status', data: { stage: 'writing' } },
    { event: 'token', data: '## 標題\n' },
    { event: 'done', data: { report_id: 'r1', title: 'T', download_url: '/api/report-doc/r1/pdf' } },
  ])
})
```

- [ ] **Step 2: 跑測試確認 RED**（`streamReport` 未匯出）。
- [ ] **Step 3: 實作** `reportEvents.ts` 型別 + `sse.ts` 加：
```ts
import type { ReportEvent } from './reportEvents'
export async function* streamReport(body: object, signal: AbortSignal): AsyncGenerator<ReportEvent> {
  yield* readSSE('/api/report', body, signal) as AsyncGenerator<ReportEvent>
}
```
- [ ] **Step 4: 跑測試 GREEN + tsc + eslint**。
- [ ] **Step 5: Commit** `feat(ask): report SSE 事件型別與 streamReport（Phase 4 Task 2）`（stage reportEvents.ts、sse.ts、reportStream.test.ts）。

---

### Task 3: schemas.ts 擴充（kpi/chart/reportDone/reportFull/historyItem.reports）+ api.getReportFull

**Files:** Modify `frontend/src/features/ask/schemas.ts`、`frontend/src/features/ask/api.ts`；Create/extend `frontend/src/features/ask/schemas.test.ts`（若存在則加案）。

**Interfaces（Produces）:**
```ts
// schemas.ts
export const kpiItemSchema = z.object({
  label: z.string().default(''),
  value: z.string().default(''),
  change: z.string().nullish(),
  dir: z.enum(['up', 'down']).nullish(),
  source: z.string().nullish(),
})
export const kpiBlockSchema = z.object({ items: z.array(kpiItemSchema).default([]) })
export type KpiBlock = z.infer<typeof kpiBlockSchema>

export const chartSeriesSchema = z.object({ name: z.string().default(''), values: z.array(z.number()) })
export const chartBlockSchema = z.object({
  type: z.enum(['bar', 'line', 'pie']),
  title: z.string().nullish(),
  x: z.array(z.union([z.string(), z.number()])).default([]),
  series: z.array(chartSeriesSchema).default([]),
  unit: z.string().nullish(),
  source: z.string().nullish(),
})
export type ChartBlock = z.infer<typeof chartBlockSchema>

export const reportSummarySchema = z.object({
  report_id: z.string(),
  title: z.string().nullish(),
  download_url: z.string(),
  created_at: z.string().nullish(),
})
export type ReportSummary = z.infer<typeof reportSummarySchema>

export const reportFullSchema = z.object({
  report_id: z.string(),
  title: z.string().nullish(),
  markdown: z.string().default(''),
})
export type ReportFull = z.infer<typeof reportFullSchema>
```
並在既有 `historyItemSchema` 加 `reports: z.array(reportSummarySchema).nullish()`。

```ts
// api.ts
export async function getReportFull(id: string): Promise<ReportFull> {
  return getJSON(`/api/report/${encodeURIComponent(id)}/full`, reportFullSchema)
}
```

- [ ] **Step 1: 失敗測試**：`kpiBlockSchema.safeParse` 對合法/缺欄/壞型；`chartBlockSchema` 對 bar/line/pie/未知 type（未知→safeParse fail）；`reportFullSchema` markdown 預設 `''`；`historyItemSchema` 帶/不帶 reports 皆過。
- [ ] **Step 2: RED**。
- [ ] **Step 3: 實作**（上方完整程式碼；`getReportFull` 用既有 `getJSON`/401 守門）。
- [ ] **Step 4: GREEN + tsc + eslint**。
- [ ] **Step 5: Commit** `feat(ask): 研報 kpi/chart/full schema 與 getReportFull（Phase 4 Task 3）`。

---

### Task 4: lib/reportBlocks.ts 純函式 parseReportSegments（防禦解析）

**Files:** Create `frontend/src/features/ask/lib/reportBlocks.ts` + `reportBlocks.test.ts`。

**Interfaces（Produces）:**
```ts
import { type KpiBlock, type ChartBlock } from '../schemas'
export type ReportSegment =
  | { kind: 'md'; text: string }
  | { kind: 'kpi'; block: KpiBlock }
  | { kind: 'chart'; block: ChartBlock }
export function parseReportSegments(markdown: string): ReportSegment[]
```
語意：掃描 markdown，找 ```kpi / ```chart fenced 區塊；區塊間文字為 `md` 段；區塊 JSON 以對應 Zod schema `safeParse`，**成功→kpi/chart 段；失敗→整段（含圍欄）當 `md` 文字段**（降級不炸）。相鄰 md 合併。空輸入→`[]`。

- [ ] **Step 1: 失敗測試（多案）**
```ts
import { expect, test } from 'vitest'
import { parseReportSegments } from './reportBlocks'

test('純文字→單一 md 段', () => {
  expect(parseReportSegments('## 標題\n內文')).toEqual([{ kind: 'md', text: '## 標題\n內文' }])
})
test('kpi 區塊→kpi 段', () => {
  const md = '前\n```kpi\n{"items":[{"label":"營收","value":"100","dir":"up"}]}\n```\n後'
  const segs = parseReportSegments(md)
  expect(segs[0]).toEqual({ kind: 'md', text: '前' })
  expect(segs[1].kind).toBe('kpi')
  expect(segs[2]).toEqual({ kind: 'md', text: '後' })
})
test('chart 區塊→chart 段', () => {
  const md = '```chart\n{"type":"bar","x":["a"],"series":[{"name":"s","values":[1]}]}\n```'
  const segs = parseReportSegments(md)
  expect(segs[0].kind).toBe('chart')
})
test('壞 kpi JSON→降級為 md 文字段（不拋）', () => {
  const md = '```kpi\n{壞的\n```'
  const segs = parseReportSegments(md)
  expect(segs).toHaveLength(1)
  expect(segs[0].kind).toBe('md')
  expect((segs[0] as { text: string }).text).toContain('```kpi')
})
test('未知 chart type→降級 md', () => {
  const md = '```chart\n{"type":"foo","series":[]}\n```'
  expect(parseReportSegments(md)[0].kind).toBe('md')
})
test('空輸入→[]', () => {
  expect(parseReportSegments('')).toEqual([])
})
```

- [ ] **Step 2: RED**。
- [ ] **Step 3: 實作**（fenced 掃描：regex `/```(kpi|chart)\n([\s\S]*?)\n```/g` 或逐行狀態機；對每塊 JSON.parse→schema.safeParse；失敗把原始 fenced 文字併回 md）。相鄰 md 合併、trim 空段。
- [ ] **Step 4: GREEN + tsc + eslint**。
- [ ] **Step 5: Commit** `feat(ask): reportBlocks 防禦解析 kpi/chart 區塊（Phase 4 Task 4）`。

---

### Task 5: components/KpiCards.tsx（sonnet）

**Files:** Create `frontend/src/features/ask/components/KpiCards.tsx` + `KpiCards.test.tsx`。

**Interfaces（Consumes/Produces）:** `export function KpiCards({ block }: { block: KpiBlock }): JSX.Element`。渲染 `block.items` 為卡列：value（大）、label（小）、change（`dir==='up'`→綠、`'down'`→紅、否則中性）、source 徽章（有才顯）。testid `report-kpi`（容器）、`report-kpi-item`（每張）。無 items → 不渲染（回 `null` 或空）。Mantine（Group/Paper/Text）。**無 dangerouslySetInnerHTML**。對齊 `app/services/pdf.py` inject_kpi 語意。

- [ ] Step 1-2: 失敗測試（up 綠 class/顏色、down 紅、無 dir 中性、source 徽章有無、空 items 不渲染 `report-kpi-item`）→ RED。
- [ ] Step 3: 實作（顏色用 Mantine `c="teal.7"`/`c="red.7"` 或 style；以 `data-dir` 標記便於測試）。
- [ ] Step 4: GREEN + tsc + eslint。
- [ ] Step 5: Commit `feat(ask): KPI 卡片元件（Phase 4 Task 5）`。

---

### Task 6: 加 @mantine/charts + components/ReportChart.tsx（sonnet）

**Files:** Modify `frontend/package.json`（+`@mantine/charts`、`recharts`）；Create `frontend/src/features/ask/components/ReportChart.tsx` + `ReportChart.test.tsx`。

**Interfaces:** `export function ReportChart({ block }: { block: ChartBlock }): JSX.Element | null`。對映：`bar`→`BarChart`、`line`→`LineChart`、`pie`→`DonutChart`（或 `PieChart`）。把 `{x, series}` 轉 Mantine data：資料點 `x[i]` 為橫軸 key，各 series 為一數列（`data=[{x:x[i], [series.name]: values[i]}, ...]`，`series=[{name, color}]`）。title/unit/source 以文字標註。**pie 遇任一負值→拒繪（回 null 或訊息）**（對齊 chart.py）。壞/空 spec → null。testid `report-chart`、`data-charttype`。

- [ ] Step 1: 先裝相依：`cd frontend && npm install @mantine/charts recharts`（記錄版本；@mantine/charts 需 recharts peer）。確認 `@mantine/charts/styles.css` 是否需匯入（若需，於 `main.tsx`/`theme` 匯入，或在元件層——查 Mantine 9 慣例，於 App 進入點匯入一次）。
- [ ] Step 2-3: 失敗測試（bar/line/pie 各渲染出對應圖容器 + `data-charttype`；pie 負值回 null；空 series null）→ RED。注意 jsdom 下 Recharts 需 `ResizeObserver`（`src/test/setup.ts` 已 polyfill，Phase 2 補過）與可能需固定寬高（用 `<BarChart h={200} ... >`，避免 responsive 0 尺寸）。
- [ ] Step 4: 實作對映。
- [ ] Step 5: GREEN + tsc + eslint。
- [ ] Step 6: Commit `feat(ask): 研報圖表元件（@mantine/charts）（Phase 4 Task 6）`（stage package.json、package-lock.json、ReportChart.tsx、測試、及任何 styles 匯入點）。

---

### Task 7: lib/reportMarkdown.tsx renderReport 交錯渲染（sonnet）

**Files:** Create `frontend/src/features/ask/lib/reportMarkdown.tsx` + `reportMarkdown.test.tsx`。

**Interfaces:** `export function renderReport(markdown: string): ReactNode[]`。對 `parseReportSegments(markdown)` 每段：`md`→`renderMarkdown(seg.text, 0)`（Phase 3，maxCite=0 無可點引用）；`kpi`→`<KpiCards block={seg.block} />`；`chart`→`<ReportChart block={seg.block} />`。每段給穩定 key。**無 dangerouslySetInnerHTML**。

- [ ] Step 1-2: 失敗測試（混合 md+kpi+chart → 輸出含文字 + `report-kpi` + `report-chart`；純 md → 只有文字）→ RED。
- [ ] Step 3: 實作（map segments → ReactNode[]，key by index+kind）。
- [ ] Step 4: GREEN + tsc + eslint。
- [ ] Step 5: Commit `feat(ask): renderReport 交錯 markdown/KPI/圖表（Phase 4 Task 7）`。

---

### Task 8: hooks/useReportStream.ts 單一進行中研報 + latest-wins（sonnet）

**Files:** Create `frontend/src/features/ask/hooks/useReportStream.ts` + `useReportStream.test.tsx`。

**Interfaces（Produces）:**
```ts
export interface ReportState {
  turnId: string | null
  phase: 'idle' | 'generating' | 'done' | 'error'
  stage: ReportStage | null
  markdown: string
  done: ReportDone | null
  error: string | null
}
export interface UseReportStream {
  report: ReportState              // 單一進行中/最近一份
  start: (turnId: string, body: { question: string; conversation_id: string | null; qa_id: string | null }) => void
  cancel: () => void
}
export function useReportStream(): UseReportStream
```
語意（**移植 Phase 3 useAskStream latest-wins**）：`start` 先 `cancel`（bump `seqRef` + abort）→ 設 `{turnId, phase:'generating', markdown:'', ...}` → for-await `streamReport`：`status`→更新 stage；`token`→累積 markdown；`done`→`phase:'done', done`；`error`→`phase:'error', error`。所有 setState gate `mySeq===seqRef.current`。串流結束無 done/error→`phase:'error'`（未完成）。`sources` 忽略。切對話/新研報時由 `AskPage` 呼叫 `cancel`。

- [ ] Step 1-2: 失敗測試（mock `streamReport` via `vi.mock`/`vi.hoisted`）：start→status→token 累積→done（`phase==='done'`、markdown 正確）；latest-wins（start 第二次，第一次的後續事件不寫）；error→`phase:'error'`；cancel→abort。→ RED。
- [ ] Step 3: 實作（照 useAskStream 結構）。
- [ ] Step 4: GREEN + tsc + eslint。
- [ ] Step 5: Commit `feat(ask): useReportStream 單一研報串流 latest-wins（Phase 4 Task 8）`。

---

### Task 9: components/ReportPanel.tsx（sonnet）

**Files:** Create `frontend/src/features/ask/components/ReportPanel.tsx` + `ReportPanel.test.tsx`。

**Interfaces（Consumes）:**
```ts
export function ReportPanel(props: {
  turn: TurnState            // 需 offerReport, reportTitle, qaId, q, reports?
  report: ReportState        // 來自 useReportStream，僅當 report.turnId===turn.id 才視為本輪進行中
  onStart: () => void        // 呼叫 useReportStream.start(turn.id, {question:turn.q, conversation_id, qa_id})
  onDismiss: () => void      // 「不用」隱藏 offer（本地 state）
  onOpenFull: (reportId: string) => void  // 開全文 modal
}): JSX.Element | null
```
渲染邏輯（對齊 vanilla + 擴充）：
- 歷史重播：`turn.reports?.length` → 對每份渲染 done 卡（title + 下載 PDF + 查看全文），**優先**。
- 否則若本輪進行中（`report.turnId===turn.id`）：`generating`→狀態列（stage 中文：REPORT_STAGE 對映）+ 即時預覽 `renderReport(report.markdown)`；`done`→done 卡（下載 `report.done.download_url` + 查看全文 `report.done.report_id`）；`error`→失敗卡 + 重試（`onStart`）。
- 否則若 `turn.offerReport` 且未 dismiss 且未完成 → offer 卡（「要不要整理成 PDF 研報？」+ 要（`onStart`）/不用（`onDismiss`））。
- 否則 null。
testid：`report-offer`、`report-offer-yes`、`report-offer-no`、`report-generating`、`report-status`、`report-preview`、`report-done`、`report-download`（`<a href download>`）、`report-viewfull`、`report-failed`、`report-retry`。REPORT_STAGE 中文對映常數（retrieving 深度檢索研報中…/searching_web 搜尋網路補充…/writing 撰寫研報中…/rendering 排版 PDF 中…）。

- [ ] Step 1-2: 失敗測試（offer→要→呼叫 onStart；不用→隱藏；generating 顯示 report-preview 含預覽；done 顯示下載+查看全文；error→重試呼叫 onStart；歷史 turn.reports→done 卡）→ RED。
- [ ] Step 3: 實作。
- [ ] Step 4: GREEN + tsc + eslint。
- [ ] Step 5: Commit `feat(ask): ReportPanel 研報面板（offer/生成/完成/失敗/歷史）（Phase 4 Task 9）`。

---

### Task 10: components/ReportFullModal.tsx（sonnet）

**Files:** Create `frontend/src/features/ask/components/ReportFullModal.tsx` + `ReportFullModal.test.tsx`。

**Interfaces:** `export function ReportFullModal({ reportId, onClose }: { reportId: string | null; onClose: () => void }): JSX.Element`。`reportId` 非 null → 開 Mantine Modal，`useQuery`（key `['report-full', reportId]`）抓 `getReportFull`；載入中 spinner、成功以 `renderReport(data.markdown)` 渲染（title 為 Modal 標題）、失敗訊息。`transitionProps={{ duration: 0 }}`（jsdom 測試即時掛載，Phase 3 慣例）。testid `report-full-modal`、`report-full-body`。**無 dangerouslySetInnerHTML**。

- [ ] Step 1-2: 失敗測試（reportId=null 不開；給 id → mock getReportFull → 顯示 markdown 渲染 + title）→ RED。需 `QueryClientProvider` + `MantineProvider` wrapper。
- [ ] Step 3: 實作。
- [ ] Step 4: GREEN + tsc + eslint。
- [ ] Step 5: Commit `feat(ask): 研報全文 modal（Phase 4 Task 10）`。

---

### Task 11: 接線 Turn.tsx + AskPage.tsx（sonnet）

**Files:** Modify `frontend/src/features/ask/components/Turn.tsx`、`frontend/src/features/ask/AskPage.tsx`；更新對應測試。

**接線：**
- `AskPage`：`const rpt = useReportStream()`；`const [fullId, setFullId] = useState<string|null>(null)`。傳給每個 `Turn`：`report={rpt.report}`、`onStartReport={(turn)=>rpt.start(turn.id,{question:turn.q,conversation_id:ask.conversationId,qa_id:turn.qaId})}`、`onOpenFull={setFullId}`。`newConversation`/`openConversation` 內先 `rpt.cancel()`（切對話中止進行中研報）。頁尾掛 `<ReportFullModal reportId={fullId} onClose={()=>setFullId(null)} />`。以 `useCallback` 穩定 handlers。
- `Turn`：新增 props `report`/`onStartReport`/`onOpenFull`；在答案/來源/動作列之後、`phase!=='notice'` 時渲染 `<ReportPanel turn={turn} report={report} onStart={()=>onStartReport(turn)} onDismiss={本地 useState} onOpenFull={onOpenFull} />`。
- 型別：`TurnState` 已含 `offerReport`/`reportTitle`（Phase 3）；需確認 `reports` 欄位（歷史）——若 `historyToTurn` 未帶 `reports`，於 conversation.ts 補 `reports: item.reports ?? []`（Task 3 schema 已加 `historyItem.reports`）。**這是本任務附帶的 conversation.ts 小改**。

- [ ] Step 1-2: 失敗測試（AskPage 整合：一輪 done+offerReport → 出現 report-offer；點 report-viewfull → report-full-modal 開；切新對話呼叫 rpt.cancel）→ RED（可用 mock useReportStream 或淺整合）。
- [ ] Step 3: 實作接線 + conversation.ts historyToTurn 補 reports。
- [ ] Step 4: GREEN（跑 ask feature 全套 `npx vitest run src/features/ask`）+ tsc + eslint。
- [ ] Step 5: Commit `feat(ask): 接線 ReportPanel/全文 modal 至 Turn 與 AskPage（Phase 4 Task 11）`。

---

### Task 12: e2e + 最終驗證（sonnet）

**Files:** Create `frontend/e2e/report.spec.mjs`（或延伸 ask.spec.mjs）。

**e2e（對 :8098、控制端跑）：** 登入 → `/app/ask` 提問（會觸發 offer 的問題，如「台積電最新展望如何？」）→ 等答案完成 → 若出現 `report-offer` 點 `report-offer-yes` → 等 `report-generating` → 等 `report-done`（放寬 timeout，研報生成比問答久，建議每階段 ≥180s、`test.setTimeout(600_000)`）→ 斷言 `report-download` 有 `href` 指向 `/api/report-doc/` → 點 `report-viewfull` → `report-full-modal` 可見且 `report-full-body` 非空 → 0 console error。**offer 是否出現由 report_gate 決定、非確定性** → 用 `toPass`/條件式：offer 未出現則跳過生成步驟並 log（讓 skip 在輸出可見，勿靜默）。

**注意（來自 Phase 3 教訓）：** 無 playwright.config → 須 `test.setTimeout`；BGE-M3 須預熱；控制端起 :8098（`ss` 核對埠免誤殺正式 :8097）、用後 kill；`rtk proxy` 取真實 exit。

- [ ] Step 1: 寫 e2e spec（實作者只寫、不跑 live；控制端跑 live）。
- [ ] Step 2: 靜態驗證：`cd frontend && npx vitest run src`（或負載大時 `src/features/ask` + 全套盡力）、`npm run build`、`npx eslint src/features/ask e2e/report.spec.mjs`。全綠。
- [ ] Step 3: Commit `feat(ask): 深度研報 e2e 與最終驗證（Phase 4 Task 12）`。
- [ ] Step 4（控制端）: 起 :8098 + 預熱 + 跑 `ASK_BASE_URL=http://localhost:8098 rtk proxy npx playwright test e2e/report.spec.mjs`；綠後最終整支審查（opus）→ finishing-a-development-branch（push + PR）。

---

## Self-Review（撰畢自檢）

- **Spec 覆蓋**：offer→生成→預覽（含 KPI/圖表）→下載→全文 modal→歷史重播、單一進行中+latest-wins、防禦解析、A5 @mantine/charts、後端零變動 — 皆有對應 Task。✓
- **型別一致**：`KpiBlock`/`ChartBlock`（Task 3）被 reportBlocks（4）/KpiCards（5）/ReportChart（6）消費；`ReportEvent`/`ReportDone`（2）被 useReportStream（8）消費；`ReportState`（8）被 ReportPanel（9）/AskPage（11）消費；`getReportFull`/`ReportFull`（3）被 ReportFullModal（10）消費。✓
- **無 placeholder**：關鍵/純邏輯任務（1-4、7-8）附完整程式碼或完整介面+測試；元件任務（5-6、9-11）附完整介面、testid、測試綱要與 props 契約。
- **順序**：schema（3）先於 reportBlocks（4）與元件；readSSE（1）先於 streamReport（2）先於 useReportStream（8）；元件先於接線（11）先於 e2e（12）。✓
