# Phase 2 問答 + 深度研報 `/app/ask` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 vanilla 的問答（RAG Q&A）與深度研報功能逐像素遷移到 React 19 SPA 的 `/app/ask` 頁，後端契約零改動。

**Architecture:** 純函式 lib 層（`readSSE`/schemas/markdown/reducer/stage-progress）→ orchestration hook（`useAskController`，持 reqId 做 latest-wins、串接 reducer 與 SSE）→ 呈現元件（Composer/ThinkingSteps/AssistantMessage/SourcesDrawer/DeepReportPanel/Callout）→ `AskPage` 組合。串流用本地 `useReducer`，對話清單/歷史載入用 TanStack Query。

**Tech Stack:** React 19.2、TypeScript、react-router 8、@tanstack/react-query 5、Zod 4、Vitest 4 + @testing-library/react、Playwright（e2e）。零元件庫、CSS Modules + tokens.css。

## Global Constraints

- **後端契約零改動**：不修改 `web/server.py`、`app/services/*`；只消費既有端點。設計權威＝`docs/design/廷豐智能研報.dc.html`（衝突以它為準）。
- **設計 spec**：`docs/superpowers/specs/2026-07-04-react-spa-phase2-ask-report-design.md`（唯一需求來源，含逐字文案、契約、非目標）。
- **一筆 `Turn` ＝ 一輪 QA pair**（非 role-based 訊息串）；submit 只 push 一筆（question 設定、answer 空），render 時同筆輸出 `UserMessage`+`AssistantMessage`。
- **來源入口＝單一 `資料來源 {N}`**，N＝研報來源數＋網路來源數（對齊 `.dc.html` `m.refCount`/`drawerSrcCount = sources.length + externals.length`）；**無**獨立「外部參考」鈕。抽屜子標「資料來源 · {N}」亦為總數。
- **latest-wins 在串流消費層**（`useAskController` 持 `reqId` ref，dispatch 前丟棄舊 stamp 事件）；`askReducer` 為純函式、不含 stamp 概念。
- **問答走全語料**：`POST /api/ask` body 僅 `{question, conversation_id?}`，不送篩選、不送 k。
- **研報 token 不 inline 渲染**（含 kpi/chart/前言）：只用來驅動進度感；完成即 `下載 PDF`（`done.download_url`＝`/api/report-doc/{id}/pdf`），下載連結 scheme 守門（僅同源/相對路徑）。
- **研報進度里程碑 %**：`retrieving→20`、`writing→50`、`rendering→90`、`done→100`；撰寫階段填充條用 `tf-indet` 動態流動。
- **`done` schema 隨路徑而異**：`offer_report`/`report_title`/`qa_id` 未必存在 → 一律用「欄位有無」判斷。
- **抽屜研報來源卡**：標題用 `file_name`、次資訊只用 `report_date`（ask `sources[]` 無 `source`/發行機構欄）；點卡片開 `ReportDetailModal`（`/api/report/{id}/full`）才顯完整發行機構，**不逐筆 enrichment**。
- **錯誤/警告用 `Callout`**（與一般訊息明顯區隔）：`error`（紅，`--tf-error-*`）用於問答/研報失敗＋重試；`warning`（amber，`--tf-warn` + 新增 `--tf-warn-bg/-border/-text`）用於離題 `notice`＋重新提問。
- **刪除對話＝【批准延伸】**：`.dc.html` 未畫，依功能對等保留 vanilla 能力（confirm→`DELETE`→fallback `POST /delete`→刪目前對話轉新對話）。
- **XSS**：markdown 不用 `dangerouslySetInnerHTML`；外部連結僅 `http(s):`＋`rel="noopener noreferrer"`。
- **IME 守衛**：composer Enter 送出須讀 `e.nativeEvent.isComposing`（React 19 `e.isComposing` 恆 undefined）。
- **不 cutover**：`/app/ask` 與 vanilla 共存，`/` 仍導 vanilla。
- **逐字文案**：所有中文 UI 字串以 spec §9 為準，逐字複製。
- **測試指令用直呼 binary**（npm script 會遮 exit code；RTK 亦可能遮 vitest exit）：
  - 單元/元件：`./node_modules/.bin/vitest run <files>`
  - 型別：`./node_modules/.bin/tsc --noEmit`
  - lint：`./node_modules/.bin/eslint .`
  - build：`./node_modules/.bin/vite build`
- **提交**：Conventional Commits + 繁中 scope（如 `feat(問答): ...`）；只 stage 明確檔（`git add <path>`，禁 `-A`/`.`）；訊息結尾附 `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。分支 `feat/react-spa-rebuild`（現行）。

---

## 檔案結構（新增/修改）

**lib（純函式優先，先做）**
- `src/lib/readSSE.ts` — fetch reader，`\n\n` 分幀、`event:`/`data:` 解析、壞幀丟棄、401→redirect。回 `AsyncGenerator<RawSSEEvent>`。
- `src/lib/askSchemas.ts` — Zod schema（source/ext/status/ask-done/report-done/conversation-turn）＋型別＋`parseAskEvent`/`parseReportEvent`。
- `src/lib/askApi.ts` — `streamAsk`/`streamReport`（薄包 readSSE）、`getConversation`、`deleteConversation`、`sendFeedback`。
- `src/lib/thinkingStages.ts` — `stagesToSteps(stages, webUsed)`：五步三態純函式。
- `src/lib/reportProgress.ts` — `reportProgress(stage)`：里程碑 %＋文案。
- `src/lib/askMarkdown.tsx` — `renderAnswer(md, sourceCount, onCite)`：markdown→React 節點、`[n]` 膠囊、XSS 安全。
- `src/lib/askReducer.ts` — `Turn`/`AskState`/`askReducer`＋`turnFromHistory`。
- `src/lib/useAskController.ts` — orchestration hook（reducer + 串流 + latest-wins + abort）。
- `src/lib/useDeleteConversation.ts` — react-query mutation（DELETE + fallback）。

**元件（呈現）**
- `src/components/primitives/Callout.tsx`(+`.module.css`) — error/warning 警示卡。
- `src/components/primitives/ConfirmDialog.tsx` — 用 `Modal` 包的確認對話框。
- `src/features/ask/Composer.tsx`(+css)、`ThinkingSteps.tsx`(+css)、`UserMessage.tsx`(+css)、`AssistantMessage.tsx`(+css)、`SourcesDrawer.tsx`(+css)、`DeepReportPanel.tsx`(+css)、`AskEmptyState.tsx`(+css)。
- `src/features/ask/AskPage.tsx` — 由佔位改實作。

**修改既有**
- `src/components/primitives/Icon.tsx` — 新增 `alertCircle`/`alertTriangle`/`send`/`fileText`/`trash`/`thumbUp`/`thumbDown`/`copy`。
- `src/styles/tokens.css` — 新增 `--tf-warn-bg/-border/-text`。
- `src/components/shell/ConversationList.tsx`(+css) — 加 active 標示 + 刪除鈕。

**e2e**
- `e2e/ask.spec.ts`（Playwright，:8098 live）。

---

### Task 1: Callout 警示卡 + warning tokens + 圖示

**Files:**
- Create: `src/components/primitives/Callout.tsx`, `src/components/primitives/Callout.module.css`, `src/components/primitives/Callout.test.tsx`
- Modify: `src/styles/tokens.css`（狀態色區加 amber 三色）、`src/components/primitives/Icon.tsx`（加圖示）

**Interfaces:**
- Produces: `Callout({ variant, children, action? }: { variant: 'error'|'warning'; children: ReactNode; action?: { label: string; onClick: () => void } })`。`Icon` 新增 `IconName`：`'alertCircle'|'alertTriangle'|'send'|'fileText'|'trash'|'thumbUp'|'thumbDown'|'copy'`。

- [ ] **Step 1: 加 tokens** — 在 `src/styles/tokens.css` 的「狀態色」區塊（現有 `--tf-warn: #ff9f0a;` 該行後）補三行：

```css
  --tf-warn-bg: #fffaeb;
  --tf-warn-border: #fedf89;
  --tf-warn-text: #b25e00;
```

- [ ] **Step 2: 加圖示** — 在 `src/components/primitives/Icon.tsx` 的 `IconName` union 末端補 `| 'alertCircle' | 'alertTriangle' | 'send' | 'fileText' | 'trash' | 'thumbUp' | 'thumbDown' | 'copy'`，並在 `PATHS` 物件補：

```tsx
  alertCircle: (<><circle cx="12" cy="12" r="9" /><path d="M12 8v4M12 16h.01" /></>),
  alertTriangle: (<><path d="M12 3l9 16H3z" /><path d="M12 10v4M12 17h.01" /></>),
  send: (<><path d="M12 20V5" /><path d="M6 11l6 -6l6 6" /></>),
  fileText: (<><path d="M14 3H7a2 2 0 0 0 -2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2 -2V8z" /><path d="M14 3v5h5M9 13h6M9 17h6" /></>),
  trash: (<><path d="M4 7h16M9 7V5a1 1 0 0 1 1 -1h4a1 1 0 0 1 1 1v2M6 7l1 12a1 1 0 0 0 1 1h8a1 1 0 0 0 1 -1l1 -12" /></>),
  thumbUp: (<><path d="M7 11v9" /><path d="M11 11l1.4 -4.2a1.5 1.5 0 0 1 3 .5v3.7h3.6a1.6 1.6 0 0 1 1.6 1.9l-1.2 5.5a1.6 1.6 0 0 1 -1.6 1.2H7v-9z" /></>),
  thumbDown: (<><path d="M17 13v-9" /><path d="M13 13l-1.4 4.2a1.5 1.5 0 0 1 -3 -.5v-3.7H5a1.6 1.6 0 0 1 -1.6 -1.9l1.2 -5.5a1.6 1.6 0 0 1 1.6 -1.2H17v9z" /></>),
  copy: (<><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2 -2h10" /></>),
```

- [ ] **Step 3: 寫失敗測試** — `src/components/primitives/Callout.test.tsx`：

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { Callout } from './Callout'

test('error variant 顯示內容與重試 action', () => {
  const onClick = vi.fn()
  render(<Callout variant="error" action={{ label: '重試', onClick }}>查詢逾時或失敗</Callout>)
  const box = screen.getByRole('alert')
  expect(box).toHaveTextContent('查詢逾時或失敗')
  expect(box.className).toMatch(/error/)
  fireEvent.click(screen.getByRole('button', { name: '重試' }))
  expect(onClick).toHaveBeenCalledOnce()
})

test('warning variant 無 action 時不渲染按鈕', () => {
  render(<Callout variant="warning">無法回答此問題</Callout>)
  expect(screen.getByRole('alert').className).toMatch(/warning/)
  expect(screen.queryByRole('button')).toBeNull()
})
```

- [ ] **Step 4: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/components/primitives/Callout.test.tsx`；預期 FAIL（找不到 `./Callout`）。

- [ ] **Step 5: 實作 Callout** — `src/components/primitives/Callout.tsx`：

```tsx
import type { ReactNode } from 'react'
import { Icon } from './Icon'
import styles from './Callout.module.css'

interface Props {
  variant: 'error' | 'warning'
  children: ReactNode
  action?: { label: string; onClick: () => void }
}

export function Callout({ variant, children, action }: Props) {
  return (
    <div className={`${styles.box} ${styles[variant]}`} role="alert">
      <Icon name={variant === 'error' ? 'alertCircle' : 'alertTriangle'} size={18} className={styles.icon} />
      <div className={styles.content}>
        <div className={styles.text}>{children}</div>
        {action && (
          <button type="button" className={styles.action} onClick={action.onClick}>{action.label}</button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 6: 樣式** — `src/components/primitives/Callout.module.css`：

```css
.box { display: flex; gap: 10px; align-items: flex-start; border: 1px solid; border-radius: var(--tf-radius-card); padding: 12px 14px; margin: 4px 0 12px; }
.error { background: var(--tf-error-bg); border-color: var(--tf-error-border); color: var(--tf-error-text); }
.warning { background: var(--tf-warn-bg); border-color: var(--tf-warn-border); color: var(--tf-warn-text); }
.icon { flex: none; margin-top: 1px; }
.content { min-width: 0; display: flex; flex-direction: column; gap: 8px; }
.text { font-size: 14px; line-height: 1.6; }
.action { align-self: flex-start; background: none; border: 1px solid currentColor; color: inherit; border-radius: var(--tf-radius-pill); font-size: 12.5px; font-weight: 600; padding: 4px 14px; cursor: pointer; }
.action:hover { opacity: 0.82; }
```

- [ ] **Step 7: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/components/primitives/Callout.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/components/primitives/`；預期全 PASS/OK。

- [ ] **Step 8: Commit**

```bash
git add src/components/primitives/Callout.tsx src/components/primitives/Callout.module.css src/components/primitives/Callout.test.tsx src/components/primitives/Icon.tsx src/styles/tokens.css
git commit -m "$(cat <<'EOF'
feat(問答): Callout 警示卡 + warning tokens + 問答圖示

錯誤/警告與一般訊息區隔的 Callout（error 紅/warning amber）；tokens 補
amber 三色階；Icon 補問答頁所需圖示。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: readSSE — SSE fetch reader

**Files:**
- Create: `src/lib/readSSE.ts`, `src/lib/readSSE.test.ts`

**Interfaces:**
- Produces: `type RawSSEEvent = { event: string; data: unknown }`；`async function* readSSE(path: string, body: unknown, signal: AbortSignal): AsyncGenerator<RawSSEEvent>`。逐幀吐出（`data` 已 `JSON.parse`）；壞幀（JSON 失敗或缺 data）丟棄；`resp.status===401` → `redirectToLogin()` 後 `throw new ApiError(401,'未登入')`。

- [ ] **Step 1: 寫失敗測試** — `src/lib/readSSE.test.ts`（用可控 ReadableStream 餵 SSE 位元組）：

```ts
import { afterEach, expect, test, vi } from 'vitest'
import { readSSE, type RawSSEEvent } from './readSSE'

afterEach(() => vi.unstubAllGlobals())

function sseResponse(chunks: string[], status = 200): Response {
  const enc = new TextEncoder()
  const stream = new ReadableStream<Uint8Array>({
    start(c) { for (const ch of chunks) c.enqueue(enc.encode(ch)); c.close() },
  })
  return new Response(stream, { status })
}

async function collect(path: string, body: unknown): Promise<RawSSEEvent[]> {
  const out: RawSSEEvent[] = []
  for await (const ev of readSSE(path, body, new AbortController().signal)) out.push(ev)
  return out
}

test('解析多幀、跨 chunk 邊界拼接、丟棄壞幀', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse([
    'event: status\ndata: {"stage":"understanding"}\n\n',
    'event: token\ndata: "片',              // 半幀（跨 chunk）
    '段"\n\n',
    'event: oops\ndata: {bad json}\n\n',      // 壞幀 → 丟棄
    'event: done\ndata: {"conversation_id":"c1"}\n\n',
  ])))
  const evs = await collect('/api/ask', { question: 'x' })
  expect(evs).toEqual([
    { event: 'status', data: { stage: 'understanding' } },
    { event: 'token', data: '片段' },
    { event: 'done', data: { conversation_id: 'c1' } },
  ])
})

test('401 導向登入並拋 ApiError', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { pathname: '/ask', search: '', assign })
  vi.stubGlobal('fetch', vi.fn(async () => sseResponse([], 401)))
  await expect(collect('/api/ask', {})).rejects.toMatchObject({ status: 401 })
  expect(assign).toHaveBeenCalledWith(expect.stringContaining('/login?next='))
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/lib/readSSE.test.ts`；預期 FAIL（找不到 `./readSSE`）。

- [ ] **Step 3: 實作** — `src/lib/readSSE.ts`：

```ts
import { ApiError, redirectToLogin } from './api'

export type RawSSEEvent = { event: string; data: unknown }

function parseFrame(frame: string): RawSSEEvent | null {
  let event = 'message'
  let data: string | null = null
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) data = (data === null ? '' : data + '\n') + line.slice(5).trim()
  }
  if (data === null) return null
  try { return { event, data: JSON.parse(data) } } catch { return null }
}

export async function* readSSE(path: string, body: unknown, signal: AbortSignal): AsyncGenerator<RawSSEEvent> {
  const resp = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    credentials: 'same-origin',
    signal,
  })
  if (resp.status === 401) { redirectToLogin(); throw new ApiError(401, '未登入') }
  if (!resp.ok || !resp.body) throw new ApiError(resp.status, `HTTP ${resp.status}`)
  const reader = resp.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  while (true) {
    const { value, done } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let sep: number
    while ((sep = buf.indexOf('\n\n')) !== -1) {
      const frame = buf.slice(0, sep)
      buf = buf.slice(sep + 2)
      const ev = parseFrame(frame)
      if (ev) yield ev
    }
  }
}
```

- [ ] **Step 4: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/lib/readSSE.test.ts && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/lib/readSSE.ts`；預期 PASS/OK。

- [ ] **Step 5: Commit**

```bash
git add src/lib/readSSE.ts src/lib/readSSE.test.ts
git commit -m "$(cat <<'EOF'
feat(問答): readSSE fetch 串流讀取器

POST + ReadableStream reader，\n\n 分幀、跨 chunk 拼接、壞幀丟棄、401
導 login；問答/研報 SSE 共用。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: askSchemas — SSE 事件 schema 與型別

**Files:**
- Create: `src/lib/askSchemas.ts`, `src/lib/askSchemas.test.ts`

**Interfaces:**
- Consumes: `RawSSEEvent`（Task 2）。
- Produces 型別：`Source = { n:number; report_id:string; file_name:string; market:string; report_date:string|null; is_latest:boolean }`、`ExtSource = { title:string; url:string }`、`AskDone`、`ReportDone`、`ConversationTurn`。
- Produces 函式：`parseAskEvent(raw: RawSSEEvent): AskEvent | null`、`parseReportEvent(raw: RawSSEEvent): ReportEvent | null`、`conversationTurnSchema`（供 askApi 用 `z.array(...)` 驗證）。
- `AskEvent` union：`{event:'status';data:{stage:AskStage;count?:number;thinking_ms?:number}}` | `{event:'sources';data:Source[]}` | `{event:'ext_sources';data:ExtSource[]}` | `{event:'token';data:string}` | `{event:'notice';data:string}` | `{event:'done';data:AskDone}`。`AskStage='understanding'|'retrieved'|'reading'|'searching_web'|'generating'`。
- `ReportEvent` union：`{event:'status';data:{stage:ReportStage}}` | `{event:'sources';data:Source[]}` | `{event:'token';data:string}` | `{event:'done';data:ReportDone}` | `{event:'error';data:{detail:string}}`。`ReportStage='retrieving'|'writing'|'searching_web'|'rendering'`。
- `AskDone = { cited?:string[]; qa_id?:string; conversation_id:string; thinking_ms?:number; offer_report?:boolean; report_title?:string|null }`。
- `ReportDone = { report_id:string; title:string; download_url:string; thinking_ms?:number }`。

- [ ] **Step 1: 寫失敗測試** — `src/lib/askSchemas.test.ts`：

```ts
import { expect, test } from 'vitest'
import { parseAskEvent, parseReportEvent, conversationTurnSchema } from './askSchemas'

test('parseAskEvent 驗證各事件、拒未知/壞形狀', () => {
  expect(parseAskEvent({ event: 'status', data: { stage: 'retrieved', count: 8 } }))
    .toEqual({ event: 'status', data: { stage: 'retrieved', count: 8 } })
  expect(parseAskEvent({ event: 'token', data: '片段' })).toEqual({ event: 'token', data: '片段' })
  expect(parseAskEvent({ event: 'sources', data: [{ n: 1, report_id: 'r', file_name: 'f', market: 'TW', report_date: null, is_latest: false }] }))
    .toMatchObject({ event: 'sources' })
  // done 路徑差異：允許缺 qa_id/offer_report
  expect(parseAskEvent({ event: 'done', data: { conversation_id: 'c1' } }))
    .toEqual({ event: 'done', data: { conversation_id: 'c1' } })
  expect(parseAskEvent({ event: 'status', data: { stage: 'bogus' } })).toBeNull()
  expect(parseAskEvent({ event: 'unknown', data: 1 })).toBeNull()
})

test('parseReportEvent 驗證 status/done/error', () => {
  expect(parseReportEvent({ event: 'status', data: { stage: 'writing' } })).toMatchObject({ event: 'status' })
  expect(parseReportEvent({ event: 'done', data: { report_id: 'r', title: 't', download_url: '/api/report-doc/r/pdf' } }))
    .toMatchObject({ event: 'done' })
  expect(parseReportEvent({ event: 'error', data: { detail: 'x' } })).toMatchObject({ event: 'error' })
  expect(parseReportEvent({ event: 'done', data: { title: 't' } })).toBeNull() // 缺 report_id
})

test('conversationTurnSchema 容錯缺欄', () => {
  const t = conversationTurnSchema.parse({
    id: 'q1', question: 'Q', answer: 'A', created_at: '2026-06-20T00:00:00Z',
    feedback: null, sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [],
  })
  expect(t.question).toBe('Q')
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/lib/askSchemas.test.ts`；預期 FAIL（找不到模組）。

- [ ] **Step 3: 實作** — `src/lib/askSchemas.ts`：

```ts
import { z } from 'zod'
import type { RawSSEEvent } from './readSSE'

export const sourceSchema = z.object({
  n: z.number().int(),
  report_id: z.string(),
  file_name: z.string(),
  market: z.string(),
  report_date: z.string().nullable(),
  is_latest: z.boolean(),
})
export type Source = z.infer<typeof sourceSchema>

export const extSourceSchema = z.object({ title: z.string(), url: z.string() })
export type ExtSource = z.infer<typeof extSourceSchema>

const askStage = z.enum(['understanding', 'retrieved', 'reading', 'searching_web', 'generating'])
export type AskStage = z.infer<typeof askStage>
const reportStage = z.enum(['retrieving', 'writing', 'searching_web', 'rendering'])
export type ReportStage = z.infer<typeof reportStage>

const askStatusData = z.object({ stage: askStage, count: z.number().int().optional(), thinking_ms: z.number().optional() })
const askDoneData = z.object({
  cited: z.array(z.string()).optional(),
  qa_id: z.string().optional(),
  conversation_id: z.string(),
  thinking_ms: z.number().optional(),
  offer_report: z.boolean().optional(),
  report_title: z.string().nullable().optional(),
})
export type AskDone = z.infer<typeof askDoneData>

const reportDoneData = z.object({
  report_id: z.string(),
  title: z.string(),
  download_url: z.string(),
  thinking_ms: z.number().optional(),
})
export type ReportDone = z.infer<typeof reportDoneData>

export type AskEvent =
  | { event: 'status'; data: z.infer<typeof askStatusData> }
  | { event: 'sources'; data: Source[] }
  | { event: 'ext_sources'; data: ExtSource[] }
  | { event: 'token'; data: string }
  | { event: 'notice'; data: string }
  | { event: 'done'; data: AskDone }

export type ReportEvent =
  | { event: 'status'; data: { stage: ReportStage } }
  | { event: 'sources'; data: Source[] }
  | { event: 'token'; data: string }
  | { event: 'done'; data: ReportDone }
  | { event: 'error'; data: { detail: string } }

export function parseAskEvent(raw: RawSSEEvent): AskEvent | null {
  switch (raw.event) {
    case 'status': { const r = askStatusData.safeParse(raw.data); return r.success ? { event: 'status', data: r.data } : null }
    case 'sources': { const r = z.array(sourceSchema).safeParse(raw.data); return r.success ? { event: 'sources', data: r.data } : null }
    case 'ext_sources': { const r = z.array(extSourceSchema).safeParse(raw.data); return r.success ? { event: 'ext_sources', data: r.data } : null }
    case 'token': return typeof raw.data === 'string' ? { event: 'token', data: raw.data } : null
    case 'notice': return typeof raw.data === 'string' ? { event: 'notice', data: raw.data } : null
    case 'done': { const r = askDoneData.safeParse(raw.data); return r.success ? { event: 'done', data: r.data } : null }
    default: return null
  }
}

export function parseReportEvent(raw: RawSSEEvent): ReportEvent | null {
  switch (raw.event) {
    case 'status': { const r = z.object({ stage: reportStage }).safeParse(raw.data); return r.success ? { event: 'status', data: r.data } : null }
    case 'sources': { const r = z.array(sourceSchema).safeParse(raw.data); return r.success ? { event: 'sources', data: r.data } : null }
    case 'token': return typeof raw.data === 'string' ? { event: 'token', data: raw.data } : null
    case 'done': { const r = reportDoneData.safeParse(raw.data); return r.success ? { event: 'done', data: r.data } : null }
    case 'error': { const r = z.object({ detail: z.string() }).safeParse(raw.data); return r.success ? { event: 'error', data: r.data } : null }
    default: return null
  }
}

export const conversationReportSchema = z.object({
  report_id: z.string(),
  title: z.string(),
  download_url: z.string(),
  created_at: z.string().nullish(),
})
export const conversationTurnSchema = z.object({
  id: z.string(),
  question: z.string(),
  answer: z.string(),
  created_at: z.string().nullish(),
  feedback: z.enum(['like', 'dislike']).nullable().default(null),
  sources: z.array(sourceSchema).default([]),
  ext_sources: z.array(extSourceSchema).default([]),
  is_offtopic: z.boolean().default(false),
  thinking_ms: z.number().nullable().default(null),
  reports: z.array(conversationReportSchema).default([]),
})
export type ConversationTurn = z.infer<typeof conversationTurnSchema>
```

- [ ] **Step 4: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/lib/askSchemas.test.ts && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/lib/askSchemas.ts`；預期 PASS/OK。

- [ ] **Step 5: Commit**

```bash
git add src/lib/askSchemas.ts src/lib/askSchemas.test.ts
git commit -m "$(cat <<'EOF'
feat(問答): askSchemas — SSE 事件驗證與型別

Zod schema + parseAskEvent/parseReportEvent（done schema 隨路徑而異、用
欄位有無判斷）+ conversationTurnSchema（歷史重播容錯缺欄）。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: askApi — 端點包裝（串流 + 對話/回饋）

**Files:**
- Create: `src/lib/askApi.ts`, `src/lib/askApi.test.ts`

**Interfaces:**
- Consumes: `readSSE`（Task 2）、`getJSON`（`src/lib/api.ts`）、`conversationTurnSchema`/型別（Task 3）。
- Produces:
  - `streamAsk(body: { question: string; conversation_id?: string }, signal: AbortSignal): AsyncGenerator<RawSSEEvent>` = `readSSE('/api/ask', body, signal)`。
  - `streamReport(body: { question: string; conversation_id?: string; qa_id?: string }, signal): AsyncGenerator<RawSSEEvent>` = `readSSE('/api/report', body, signal)`。
  - `getConversation(id: string): Promise<ConversationTurn[]>`。
  - `deleteConversation(id: string): Promise<void>`（先 `DELETE`；若 404/405 fallback `POST /api/conversations/{id}/delete`）。
  - `sendFeedback(qaId: string, value: 'like'|'dislike'): Promise<void>`。

- [ ] **Step 1: 寫失敗測試** — `src/lib/askApi.test.ts`（只測 JSON 路徑；串流已由 readSSE 覆蓋）：

```ts
import { afterEach, expect, test, vi } from 'vitest'
import { getConversation, deleteConversation, sendFeedback } from './askApi'

afterEach(() => vi.unstubAllGlobals())

test('getConversation 解析歷史陣列', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify([
    { id: 'q1', question: 'Q', answer: 'A', created_at: '2026-06-20T00:00:00Z', feedback: null, sources: [], ext_sources: [], is_offtopic: false, thinking_ms: null, reports: [] },
  ]), { status: 200 })))
  const turns = await getConversation('c1')
  expect(turns).toHaveLength(1)
  expect(turns[0].question).toBe('Q')
})

test('deleteConversation：405 時 fallback POST /delete', async () => {
  const calls: Array<{ url: string; method?: string }> = []
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    calls.push({ url, method: init?.method })
    if (init?.method === 'DELETE') return new Response('', { status: 405 })
    return new Response(JSON.stringify({ ok: true }), { status: 200 })
  }))
  await deleteConversation('c1')
  expect(calls[0]).toMatchObject({ url: '/api/conversations/c1', method: 'DELETE' })
  expect(calls[1]).toMatchObject({ url: '/api/conversations/c1/delete', method: 'POST' })
})

test('sendFeedback POST 到 /api/feedback', async () => {
  const spy = vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 }))
  vi.stubGlobal('fetch', spy)
  await sendFeedback('qa1', 'like')
  const [, init] = spy.mock.calls[0]
  expect(JSON.parse((init as RequestInit).body as string)).toEqual({ qa_id: 'qa1', value: 'like' })
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/lib/askApi.test.ts`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/lib/askApi.ts`：

```ts
import { z } from 'zod'
import { getJSON } from './api'
import { readSSE, type RawSSEEvent } from './readSSE'
import { conversationTurnSchema, type ConversationTurn } from './askSchemas'

export function streamAsk(body: { question: string; conversation_id?: string }, signal: AbortSignal): AsyncGenerator<RawSSEEvent> {
  return readSSE('/api/ask', body, signal)
}

export function streamReport(body: { question: string; conversation_id?: string; qa_id?: string }, signal: AbortSignal): AsyncGenerator<RawSSEEvent> {
  return readSSE('/api/report', body, signal)
}

export function getConversation(id: string): Promise<ConversationTurn[]> {
  return getJSON(`/api/conversations/${encodeURIComponent(id)}`, z.array(conversationTurnSchema), { cache: 'no-store' })
}

export async function deleteConversation(id: string): Promise<void> {
  const path = `/api/conversations/${encodeURIComponent(id)}`
  const resp = await fetch(path, { method: 'DELETE', credentials: 'same-origin' })
  if (resp.status === 404 || resp.status === 405) {
    await fetch(`${path}/delete`, { method: 'POST', credentials: 'same-origin' })
  }
}

export async function sendFeedback(qaId: string, value: 'like' | 'dislike'): Promise<void> {
  await fetch('/api/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ qa_id: qaId, value }),
    credentials: 'same-origin',
  })
}
```

- [ ] **Step 4: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/lib/askApi.test.ts && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/lib/askApi.ts`；預期 PASS/OK。

- [ ] **Step 5: Commit**

```bash
git add src/lib/askApi.ts src/lib/askApi.test.ts
git commit -m "$(cat <<'EOF'
feat(問答): askApi — 串流/對話/回饋端點包裝

streamAsk/streamReport（薄包 readSSE）、getConversation、deleteConversation
（DELETE→fallback POST /delete）、sendFeedback。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: thinkingStages — 階段→思考步驟

**Files:**
- Create: `src/lib/thinkingStages.ts`, `src/lib/thinkingStages.test.ts`

**Interfaces:**
- Consumes: `AskStage`（Task 3）。
- Produces: `type StepState = 'done'|'active'|'pending'`；`type ThinkStep = { key: AskStage; name: string; state: StepState }`；`stagesToSteps(reached: AskStage[], webUsed: boolean): ThinkStep[]`。步驟固定五格（`webUsed=false` 時隱藏「網路補充」→ 回傳四格）。`reached` 為已抵達 stage 集合（依 SSE 到達順序）；最後抵達者 `active`、之前 `done`、之後 `pending`。文案：`理解問題`/`檢索研報`/`閱讀整理`/`網路補充`/`生成回答`（對應 `understanding/retrieved/reading/searching_web/generating`）。

- [ ] **Step 1: 寫失敗測試** — `src/lib/thinkingStages.test.ts`：

```ts
import { expect, test } from 'vitest'
import { stagesToSteps } from './thinkingStages'

test('無網路時四格、最後抵達為 active', () => {
  const steps = stagesToSteps(['understanding', 'retrieved'], false)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '檢索研報', '閱讀整理', '生成回答'])
  expect(steps.map(s => s.state)).toEqual(['done', 'active', 'pending', 'pending'])
})

test('出現 searching_web 才插入網路補充格', () => {
  const steps = stagesToSteps(['understanding', 'retrieved', 'reading', 'searching_web'], true)
  expect(steps.map(s => s.name)).toEqual(['理解問題', '檢索研報', '閱讀整理', '網路補充', '生成回答'])
  expect(steps.find(s => s.name === '網路補充')?.state).toBe('active')
})

test('generating 抵達時全部之前為 done、生成回答 active', () => {
  const steps = stagesToSteps(['understanding', 'retrieved', 'reading', 'generating'], false)
  expect(steps.map(s => s.state)).toEqual(['done', 'done', 'done', 'active'])
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/lib/thinkingStages.test.ts`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/lib/thinkingStages.ts`：

```ts
import type { AskStage } from './askSchemas'

export type StepState = 'done' | 'active' | 'pending'
export interface ThinkStep { key: AskStage; name: string; state: StepState }

const ORDER: { key: AskStage; name: string }[] = [
  { key: 'understanding', name: '理解問題' },
  { key: 'retrieved', name: '檢索研報' },
  { key: 'reading', name: '閱讀整理' },
  { key: 'searching_web', name: '網路補充' },
  { key: 'generating', name: '生成回答' },
]

export function stagesToSteps(reached: AskStage[], webUsed: boolean): ThinkStep[] {
  const visible = ORDER.filter(s => s.key !== 'searching_web' || webUsed)
  const last = reached.length ? reached[reached.length - 1] : null
  const lastIdx = last ? visible.findIndex(s => s.key === last) : -1
  return visible.map((s, i) => ({
    key: s.key,
    name: s.name,
    state: lastIdx === -1 ? 'pending' : i < lastIdx ? 'done' : i === lastIdx ? 'active' : 'pending',
  }))
}
```

- [ ] **Step 4: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/lib/thinkingStages.test.ts && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/lib/thinkingStages.ts`；預期 PASS/OK。

- [ ] **Step 5: Commit**

```bash
git add src/lib/thinkingStages.ts src/lib/thinkingStages.test.ts
git commit -m "$(cat <<'EOF'
feat(問答): thinkingStages — 階段映射思考步驟三態

由真實 status.stage 驅動五步（網路補充僅 searching_web 出現時顯示），
最後抵達 active、之前 done、之後 pending。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: reportProgress — 研報階段里程碑 %

**Files:**
- Create: `src/lib/reportProgress.ts`, `src/lib/reportProgress.test.ts`

**Interfaces:**
- Consumes: `ReportStage`（Task 3）。
- Produces: `reportProgress(stage: ReportStage): { pct: number; text: string }`。里程碑：`retrieving→{20}`、`searching_web→{40}`、`writing→{50}`、`rendering→{90}`。（`done` 由 reducer 設 100，不經此函式。）

- [ ] **Step 1: 寫失敗測試** — `src/lib/reportProgress.test.ts`：

```ts
import { expect, test } from 'vitest'
import { reportProgress } from './reportProgress'

test('各階段映射里程碑 % 與文案', () => {
  expect(reportProgress('retrieving')).toEqual({ pct: 20, text: '深度檢索研報中…' })
  expect(reportProgress('searching_web')).toEqual({ pct: 40, text: '搜尋網路補充…' })
  expect(reportProgress('writing')).toEqual({ pct: 50, text: '撰寫研報中…' })
  expect(reportProgress('rendering')).toEqual({ pct: 90, text: '排版 PDF 中…' })
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/lib/reportProgress.test.ts`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/lib/reportProgress.ts`：

```ts
import type { ReportStage } from './askSchemas'

const MAP: Record<ReportStage, { pct: number; text: string }> = {
  retrieving: { pct: 20, text: '深度檢索研報中…' },
  searching_web: { pct: 40, text: '搜尋網路補充…' },
  writing: { pct: 50, text: '撰寫研報中…' },
  rendering: { pct: 90, text: '排版 PDF 中…' },
}

export function reportProgress(stage: ReportStage): { pct: number; text: string } {
  return MAP[stage]
}
```

- [ ] **Step 4: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/lib/reportProgress.test.ts && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/lib/reportProgress.ts`；預期 PASS/OK。

- [ ] **Step 5: Commit**

```bash
git add src/lib/reportProgress.ts src/lib/reportProgress.test.ts
git commit -m "$(cat <<'EOF'
feat(研報): reportProgress — 階段里程碑進度映射

retrieving 20 / searching_web 40 / writing 50 / rendering 90；done 由
reducer 設 100。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: askMarkdown — 答案 markdown 渲染（`[n]` 膠囊、XSS 安全）

**Files:**
- Create: `src/lib/askMarkdown.tsx`, `src/lib/askMarkdown.test.tsx`

**Interfaces:**
- Produces: `renderAnswer(md: string, sourceCount: number, onCite: (n: number) => void): ReactNode`。回 React 節點（非 innerHTML）：支援 h1–h6（襯線，視覺上限 h4）、段落、`- `/`* ` 無序清單、`1. ` 有序清單、` ``` ` code fence、行內粗體 `**x**`/斜體 `*x*`/行內 code `` `x` ``/連結 `[t](url)`（僅 `http(s):`）/引用 `[n]`（`1≤n≤sourceCount` → 金膠囊 `button`，`onCite(n)`；越界保留字面）。

- [ ] **Step 1: 寫失敗測試** — `src/lib/askMarkdown.test.tsx`：

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { renderAnswer } from './askMarkdown'

test('[n] 界內渲染金膠囊並可點；界外保留字面', () => {
  const onCite = vi.fn()
  render(<div>{renderAnswer('看 [1] 與 [9] 的比較', 3, onCite)}</div>)
  const pill = screen.getByRole('button', { name: '1' })
  fireEvent.click(pill)
  expect(onCite).toHaveBeenCalledWith(1)
  expect(screen.getByText(/\[9\]/)).toBeInTheDocument() // 界外字面
})

test('標題與清單成塊', () => {
  const { container } = render(<div>{renderAnswer('# 標題\n- 甲\n- 乙', 0, () => {})}</div>)
  expect(container.querySelector('h3, h4')).toBeTruthy()
  expect(container.querySelectorAll('li')).toHaveLength(2)
})

test('XSS：原始 HTML 不被解讀為標籤', () => {
  const { container } = render(<div>{renderAnswer('<img src=x onerror=alert(1)> 純文字', 0, () => {})}</div>)
  expect(container.querySelector('img')).toBeNull()
  expect(container.textContent).toContain('<img')
})

test('連結僅接受 http(s)，javascript: 退回字面', () => {
  const { container } = render(<div>{renderAnswer('[好](https://a.com) [壞](javascript:alert(1))', 0, () => {})}</div>)
  const a = container.querySelector('a')
  expect(a?.getAttribute('href')).toBe('https://a.com')
  expect(a).toHaveAttribute('rel', 'noopener noreferrer')
  expect(container.textContent).toContain('[壞]')
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/lib/askMarkdown.test.tsx`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/lib/askMarkdown.tsx`：

```tsx
import type { ReactNode } from 'react'

const HTTP = /^https?:\/\//i

// 行內：粗體/斜體/行內 code/連結/[n] 膠囊 → React 節點（React 自動轉義文字）
function renderInline(text: string, sourceCount: number, onCite: (n: number) => void, keyBase: string): ReactNode[] {
  const out: ReactNode[] = []
  const re = /\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)|\[(\d+)\]/g
  let last = 0
  let m: RegExpExecArray | null
  let i = 0
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push(text.slice(last, m.index))
    const key = `${keyBase}-${i++}`
    if (m[1] !== undefined) out.push(<strong key={key}>{m[1]}</strong>)
    else if (m[2] !== undefined) out.push(<em key={key}>{m[2]}</em>)
    else if (m[3] !== undefined) out.push(<code key={key}>{m[3]}</code>)
    else if (m[4] !== undefined && m[5] !== undefined) {
      const href = m[5].trim()
      if (HTTP.test(href)) out.push(<a key={key} href={href} target="_blank" rel="noopener noreferrer">{m[4]}</a>)
      else out.push(m[0])
    } else if (m[6] !== undefined) {
      const n = Number(m[6])
      if (n >= 1 && n <= sourceCount) {
        out.push(<button key={key} type="button" className="tf-cite" onClick={() => onCite(n)}>{n}</button>)
      } else out.push(m[0])
    }
    last = re.lastIndex
  }
  if (last < text.length) out.push(text.slice(last))
  return out
}

export function renderAnswer(md: string, sourceCount: number, onCite: (n: number) => void): ReactNode {
  const lines = md.split('\n')
  const blocks: ReactNode[] = []
  let para: string[] = []
  let ul: string[] = []
  let ol: string[] = []
  let code: string[] | null = null
  let k = 0

  const flushPara = () => { if (para.length) { blocks.push(<p key={`p${k++}`}>{renderInline(para.join(' '), sourceCount, onCite, `p${k}`)}</p>); para = [] } }
  const flushUl = () => { if (ul.length) { const items = ul; blocks.push(<ul key={`ul${k++}`}>{items.map((t, i) => <li key={i}>{renderInline(t, sourceCount, onCite, `ul${k}-${i}`)}</li>)}</ul>); ul = [] } }
  const flushOl = () => { if (ol.length) { const items = ol; blocks.push(<ol key={`ol${k++}`}>{items.map((t, i) => <li key={i}>{renderInline(t, sourceCount, onCite, `ol${k}-${i}`)}</li>)}</ol>); ol = [] } }
  const flushAll = () => { flushPara(); flushUl(); flushOl() }

  for (const line of lines) {
    if (line.trim().startsWith('```')) {
      if (code === null) { flushAll(); code = [] } else { blocks.push(<pre key={`code${k++}`}><code>{code.join('\n')}</code></pre>); code = null }
      continue
    }
    if (code !== null) { code.push(line); continue }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) { flushAll(); const lvl = Math.min(h[1].length, 4); const Tag = (lvl <= 3 ? 'h3' : 'h4') as 'h3' | 'h4'; blocks.push(<Tag key={`h${k++}`} className="tf-md-h">{renderInline(h[2], sourceCount, onCite, `h${k}`)}</Tag>); continue }
    const uli = /^[-*]\s+(.*)$/.exec(line)
    if (uli) { flushPara(); flushOl(); ul.push(uli[1]); continue }
    const oli = /^\d+\.\s+(.*)$/.exec(line)
    if (oli) { flushPara(); flushUl(); ol.push(oli[1]); continue }
    if (line.trim() === '') { flushAll(); continue }
    flushUl(); flushOl(); para.push(line.trim())
  }
  if (code !== null) blocks.push(<pre key={`code${k++}`}><code>{code.join('\n')}</code></pre>)
  flushAll()
  return <>{blocks}</>
}
```

- [ ] **Step 4: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/lib/askMarkdown.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/lib/askMarkdown.tsx`；預期 PASS/OK。

- [ ] **Step 5: Commit**

```bash
git add src/lib/askMarkdown.tsx src/lib/askMarkdown.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): askMarkdown — 答案渲染與 [n] 引用膠囊

React 節點渲染（非 innerHTML，XSS 安全）：標題/段落/清單/code fence +
行內粗體/斜體/code/連結（僅 http(s)）/[n] 金膠囊（界內可點、界外字面）。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: askReducer — 問答狀態機（純函式）

**Files:**
- Create: `src/lib/askReducer.ts`, `src/lib/askReducer.test.ts`

**Interfaces:**
- Consumes: `AskEvent`/`ReportEvent`/`AskStage`/`Source`/`ExtSource`/`ConversationTurn`（Task 3）、`reportProgress`（Task 6）。
- Produces 型別：`ReportState`、`Turn`、`AskState = { turns: Turn[] }`、`AskAction`（見下）。
- Produces：`initialAskState: AskState`、`askReducer(state: AskState, action: AskAction): AskState`、`turnFromHistory(item: ConversationTurn): Turn`。
- `Turn` 欄位：`id, question, phase('thinking'|'streaming'|'done'|'notice'|'error'), stages(AskStage[]), webUsed, retrievedCount(number|null), answer, thinkingMs(number|null), startedAt, sources, extSources, qaId(string|null), isOfftopic, noticeText(string|null), offerReport, reportTitle(string|null), feedback('like'|'dislike'|null), report(ReportState), errorText(string|null)`。
- `ReportState`：`status('idle'|'offered'|'generating'|'done'|'error'), pct, stageText, downloadUrl(string|null), title(string|null), errorText(string|null)`。
- `AskAction`：`{type:'submit',id,question,startedAt}` | `{type:'ask-event',id,event:AskEvent,conversationId?:never}` | `{type:'ask-end',id}` | `{type:'report-start',id}` | `{type:'report-event',id,event:ReportEvent}` | `{type:'report-fail',id,errorText}` | `{type:'report-decline',id}` | `{type:'feedback',id,value}` | `{type:'load',turns:Turn[]}` | `{type:'reset'}`。
- 注意：`conversation_id` 不進 reducer（由 controller 讀 `done.conversation_id` 自行 setState）。

- [ ] **Step 1: 寫失敗測試** — `src/lib/askReducer.test.ts`：

```ts
import { expect, test } from 'vitest'
import { askReducer, initialAskState, turnFromHistory, type AskState } from './askReducer'

function submit(): AskState {
  return askReducer(initialAskState, { type: 'submit', id: 't1', question: 'Q', startedAt: 1000 })
}
const ev = (event: unknown) => ({ type: 'ask-event' as const, id: 't1', event: event as never })

test('submit 推入一筆 thinking turn', () => {
  const s = submit()
  expect(s.turns).toHaveLength(1)
  expect(s.turns[0]).toMatchObject({ id: 't1', question: 'Q', phase: 'thinking', answer: '' })
})

test('正常 RAG：sources→retrieved→reading→generating→token→done', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'sources', data: [{ n: 1, report_id: 'r1', file_name: 'f', market: 'TW', report_date: '2026-06-20', is_latest: true }] }))
  s = askReducer(s, ev({ event: 'status', data: { stage: 'retrieved', count: 8 } }))
  s = askReducer(s, ev({ event: 'status', data: { stage: 'reading' } }))
  s = askReducer(s, ev({ event: 'status', data: { stage: 'generating', thinking_ms: 4200 } }))
  s = askReducer(s, ev({ event: 'token', data: '答' }))
  s = askReducer(s, ev({ event: 'token', data: '案' }))
  s = askReducer(s, ev({ event: 'done', data: { cited: ['r1'], qa_id: 'qa1', conversation_id: 'c1', thinking_ms: 4200, offer_report: true, report_title: 'Q 深度研報' } }))
  const t = s.turns[0]
  expect(t.answer).toBe('答案')
  expect(t.phase).toBe('done')
  expect(t.qaId).toBe('qa1')
  expect(t.retrievedCount).toBe(8)
  expect(t.thinkingMs).toBe(4200)
  expect(t.offerReport).toBe(true)
  expect(t.report.status).toBe('offered')
  expect(t.sources).toHaveLength(1)
})

test('離題：notice→done 保持 notice、qaId 為 null、不 offer', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'notice', data: '無法回答此問題' }))
  s = askReducer(s, ev({ event: 'done', data: { conversation_id: 'c1' } }))
  const t = s.turns[0]
  expect(t.phase).toBe('notice')
  expect(t.isOfftopic).toBe(true)
  expect(t.noticeText).toBe('無法回答此問題')
  expect(t.qaId).toBeNull()
  expect(t.offerReport).toBe(false)
})

test('ask-end 無 token 且非 notice → error', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'sources', data: [] }))
  s = askReducer(s, { type: 'ask-end', id: 't1' })
  expect(s.turns[0].phase).toBe('error')
  expect(s.turns[0].errorText).toBe('查詢逾時或失敗')
})

test('searching_web 設 webUsed', () => {
  let s = submit()
  s = askReducer(s, ev({ event: 'status', data: { stage: 'searching_web' } }))
  expect(s.turns[0].webUsed).toBe(true)
})

test('研報：start→status→done', () => {
  let s = submit()
  s = askReducer(s, { type: 'report-start', id: 't1' })
  expect(s.turns[0].report.status).toBe('generating')
  s = askReducer(s, { type: 'report-event', id: 't1', event: { event: 'status', data: { stage: 'writing' } } })
  expect(s.turns[0].report).toMatchObject({ pct: 50, stageText: '撰寫研報中…' })
  s = askReducer(s, { type: 'report-event', id: 't1', event: { event: 'done', data: { report_id: 'rp1', title: 'T', download_url: '/api/report-doc/rp1/pdf' } } })
  expect(s.turns[0].report).toMatchObject({ status: 'done', pct: 100, downloadUrl: '/api/report-doc/rp1/pdf', title: 'T' })
})

test('研報 error 事件 → error 態', () => {
  let s = submit()
  s = askReducer(s, { type: 'report-start', id: 't1' })
  s = askReducer(s, { type: 'report-event', id: 't1', event: { event: 'error', data: { detail: '找不到足夠資料生成研報' } } })
  expect(s.turns[0].report).toMatchObject({ status: 'error', errorText: '找不到足夠資料生成研報' })
})

test('feedback / reset / load', () => {
  let s = submit()
  s = askReducer(s, { type: 'feedback', id: 't1', value: 'like' })
  expect(s.turns[0].feedback).toBe('like')
  s = askReducer(s, { type: 'reset' })
  expect(s.turns).toEqual([])
  const turn = turnFromHistory({ id: 'qa9', question: 'H', answer: 'A', created_at: '2026-06-20T00:00:00Z', feedback: 'dislike', sources: [], ext_sources: [], is_offtopic: false, thinking_ms: 1500, reports: [{ report_id: 'rp', title: 'RT', download_url: '/api/report-doc/rp/pdf', created_at: null }] })
  s = askReducer(s, { type: 'load', turns: [turn] })
  expect(s.turns[0]).toMatchObject({ id: 'qa9', phase: 'done', qaId: 'qa9', feedback: 'dislike' })
  expect(s.turns[0].report).toMatchObject({ status: 'done', downloadUrl: '/api/report-doc/rp/pdf' })
})

test('turnFromHistory 離題轉 notice、qaId null', () => {
  const t = turnFromHistory({ id: 'qaX', question: 'H', answer: '無法回答此問題', created_at: null, feedback: null, sources: [], ext_sources: [], is_offtopic: true, thinking_ms: null, reports: [] })
  expect(t.phase).toBe('notice')
  expect(t.noticeText).toBe('無法回答此問題')
  expect(t.qaId).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/lib/askReducer.test.ts`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/lib/askReducer.ts`：

```ts
import type { AskEvent, ReportEvent, AskStage, Source, ExtSource, ConversationTurn } from './askSchemas'
import { reportProgress } from './reportProgress'

const HTTP = /^https?:\/\//i

export interface ReportState {
  status: 'idle' | 'offered' | 'generating' | 'done' | 'error'
  pct: number
  stageText: string
  downloadUrl: string | null
  title: string | null
  errorText: string | null
}
const idleReport: ReportState = { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null }

export interface Turn {
  id: string
  question: string
  phase: 'thinking' | 'streaming' | 'done' | 'notice' | 'error'
  stages: AskStage[]
  webUsed: boolean
  retrievedCount: number | null
  answer: string
  thinkingMs: number | null
  startedAt: number
  sources: Source[]
  extSources: ExtSource[]
  qaId: string | null
  isOfftopic: boolean
  noticeText: string | null
  offerReport: boolean
  reportTitle: string | null
  feedback: 'like' | 'dislike' | null
  report: ReportState
  errorText: string | null
}

export interface AskState { turns: Turn[] }
export const initialAskState: AskState = { turns: [] }

export type AskAction =
  | { type: 'submit'; id: string; question: string; startedAt: number }
  | { type: 'ask-event'; id: string; event: AskEvent }
  | { type: 'ask-end'; id: string }
  | { type: 'report-start'; id: string }
  | { type: 'report-event'; id: string; event: ReportEvent }
  | { type: 'report-fail'; id: string; errorText: string }
  | { type: 'report-decline'; id: string }
  | { type: 'feedback'; id: string; value: 'like' | 'dislike' }
  | { type: 'load'; turns: Turn[] }
  | { type: 'reset' }

function mapTurn(turns: Turn[], id: string, fn: (t: Turn) => Turn): Turn[] {
  return turns.map(t => (t.id === id ? fn(t) : t))
}

function applyAsk(t: Turn, ev: AskEvent): Turn {
  switch (ev.event) {
    case 'status': {
      const stage = ev.data.stage
      const stages = t.stages.includes(stage) ? t.stages : [...t.stages, stage]
      return {
        ...t, stages,
        webUsed: t.webUsed || stage === 'searching_web',
        retrievedCount: stage === 'retrieved' && ev.data.count != null ? ev.data.count : t.retrievedCount,
        phase: stage === 'generating' && t.phase === 'thinking' ? 'streaming' : t.phase,
        thinkingMs: ev.data.thinking_ms ?? t.thinkingMs,
      }
    }
    case 'sources': return { ...t, sources: ev.data }
    case 'ext_sources': return { ...t, extSources: ev.data.filter(e => HTTP.test(e.url)) }
    case 'token': return { ...t, answer: t.answer + ev.data, phase: t.isOfftopic ? 'notice' : 'streaming' }
    case 'notice': return { ...t, phase: 'notice', isOfftopic: true, noticeText: ev.data }
    case 'done': return {
      ...t,
      phase: t.isOfftopic ? 'notice' : 'done',
      qaId: ev.data.qa_id ?? null,
      offerReport: ev.data.offer_report ?? false,
      reportTitle: ev.data.report_title ?? null,
      report: ev.data.offer_report ? { ...t.report, status: 'offered', title: ev.data.report_title ?? null } : t.report,
    }
  }
}

function applyReport(t: Turn, ev: ReportEvent): Turn {
  switch (ev.event) {
    case 'status': { const { pct, text } = reportProgress(ev.data.stage); return { ...t, report: { ...t.report, status: 'generating', pct, stageText: text } } }
    case 'sources': return t
    case 'token': return t
    case 'done': return { ...t, report: { ...t.report, status: 'done', pct: 100, downloadUrl: ev.data.download_url, title: ev.data.title, errorText: null } }
    case 'error': return { ...t, report: { ...t.report, status: 'error', errorText: ev.data.detail } }
  }
}

export function askReducer(state: AskState, action: AskAction): AskState {
  switch (action.type) {
    case 'submit': return {
      turns: [...state.turns, {
        id: action.id, question: action.question, phase: 'thinking', stages: ['understanding'],
        webUsed: false, retrievedCount: null, answer: '', thinkingMs: null, startedAt: action.startedAt,
        sources: [], extSources: [], qaId: null, isOfftopic: false, noticeText: null,
        offerReport: false, reportTitle: null, feedback: null, report: idleReport, errorText: null,
      }],
    }
    case 'ask-event': return { turns: mapTurn(state.turns, action.id, t => applyAsk(t, action.event)) }
    case 'ask-end': return {
      turns: mapTurn(state.turns, action.id, t => {
        if (t.phase === 'notice' || t.phase === 'done' || t.phase === 'error') return t
        return t.answer === '' ? { ...t, phase: 'error', errorText: '查詢逾時或失敗' } : { ...t, phase: 'done' }
      }),
    }
    case 'report-start': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: { status: 'generating', pct: 0, stageText: '準備生成研報…', downloadUrl: null, title: t.reportTitle, errorText: null } })) }
    case 'report-event': return { turns: mapTurn(state.turns, action.id, t => applyReport(t, action.event)) }
    case 'report-fail': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: { ...t.report, status: 'error', errorText: action.errorText } })) }
    case 'report-decline': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, report: idleReport, offerReport: false })) }
    case 'feedback': return { turns: mapTurn(state.turns, action.id, t => ({ ...t, feedback: action.value })) }
    case 'load': return { turns: action.turns }
    case 'reset': return { turns: [] }
  }
}

export function turnFromHistory(item: ConversationTurn): Turn {
  const last = item.reports.length ? item.reports[item.reports.length - 1] : null
  return {
    id: item.id,
    question: item.question,
    phase: item.is_offtopic ? 'notice' : 'done',
    stages: [],
    webUsed: false,
    retrievedCount: null,
    answer: item.answer,
    thinkingMs: item.thinking_ms,
    startedAt: 0,
    sources: item.sources,
    extSources: item.ext_sources.filter(e => HTTP.test(e.url)),
    qaId: item.is_offtopic ? null : item.id,
    isOfftopic: item.is_offtopic,
    noticeText: item.is_offtopic ? item.answer : null,
    offerReport: false,
    reportTitle: null,
    feedback: item.feedback,
    report: last ? { status: 'done', pct: 100, stageText: '', downloadUrl: last.download_url, title: last.title, errorText: null } : idleReport,
    errorText: null,
  }
}
```

- [ ] **Step 4: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/lib/askReducer.test.ts && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/lib/askReducer.ts`；預期 PASS/OK。

- [ ] **Step 5: Commit**

```bash
git add src/lib/askReducer.ts src/lib/askReducer.test.ts
git commit -m "$(cat <<'EOF'
feat(問答): askReducer — 問答/研報純函式狀態機

Turn=QA pair；ask/report 事件套用（done schema 隨路徑判斷、離題保持
notice 且無 qaId）、ask-end 無 token 轉 error、研報里程碑進度、歷史重播
turnFromHistory。純函式不含 stamp。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: useAskController — orchestration hook（latest-wins + abort）

**Files:**
- Create: `src/lib/useAskController.ts`, `src/lib/useAskController.test.tsx`

**Interfaces:**
- Consumes: `askReducer`/`initialAskState`/`turnFromHistory`/型別（Task 8）、`parseAskEvent`/`parseReportEvent`（Task 3）、`streamAsk`/`streamReport`/`getConversation`/`sendFeedback`（Task 4）。
- Produces：`useAskController()` 回：
  - `state: AskState`
  - `conversationId: string | null`
  - `submit(question: string): void`
  - `generateReport(turnId: string, question: string, qaId: string | null): void`
  - `declineReport(turnId: string): void`
  - `loadConversation(id: string): Promise<void>`
  - `newConversation(): void`
  - `setFeedback(turnId: string, qaId: string, value: 'like'|'dislike'): void`
- latest-wins：內部 `reqId` ref；每次 `submit`/`loadConversation`/`newConversation` 先 `abortAll()` 並 `reqId.current++`；串流迴圈每收一事件比對 `my === reqId.current`，否則丟棄並中止（**reducer 不接收 stale**）。研報有獨立 `reportCtrl`（提交新問題會一併 abort）。conversationId 由首個 `done.conversation_id` 設定，`newConversation` 清空、`loadConversation` 設為該 id。

- [ ] **Step 1: 寫失敗測試** — `src/lib/useAskController.test.tsx`（mock askApi）：

```tsx
import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import type { RawSSEEvent } from './readSSE'

const streamAsk = vi.fn()
const streamReport = vi.fn()
vi.mock('./askApi', () => ({
  streamAsk: (...a: unknown[]) => streamAsk(...a),
  streamReport: (...a: unknown[]) => streamReport(...a),
  getConversation: vi.fn(),
  sendFeedback: vi.fn(async () => {}),
}))
import { useAskController } from './useAskController'

afterEach(() => vi.clearAllMocks())

// 可控 async generator：每 yield 前等待外部 gate
function gated(events: RawSSEEvent[]) {
  const gates: Array<() => void> = []
  const gen = (async function* () {
    for (const ev of events) {
      await new Promise<void>(res => gates.push(res))
      yield ev
    }
  })()
  return { gen, release: () => gates.shift()?.() }
}

test('submit 串流：sources→token→done 寫入 state 並記 conversationId', async () => {
  const g = gated([
    { event: 'sources', data: [] },
    { event: 'token', data: 'Hi' },
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1' } },
  ])
  streamAsk.mockReturnValue(g.gen)
  const { result } = renderHook(() => useAskController())
  act(() => result.current.submit('Q'))
  expect(result.current.state.turns[0].phase).toBe('thinking')
  await act(async () => { g.release(); await Promise.resolve() })
  await act(async () => { g.release(); await Promise.resolve() })
  await act(async () => { g.release(); await Promise.resolve() })
  await waitFor(() => expect(result.current.state.turns[0].phase).toBe('done'))
  expect(result.current.state.turns[0].answer).toBe('Hi')
  expect(result.current.conversationId).toBe('c1')
})

test('latest-wins：第二次 submit 後，第一串流的後續事件被丟棄', async () => {
  const g1 = gated([{ event: 'token', data: 'A1' }, { event: 'token', data: 'A2' }])
  const g2 = gated([{ event: 'token', data: 'B1' }])
  streamAsk.mockReturnValueOnce(g1.gen).mockReturnValueOnce(g2.gen)
  const { result } = renderHook(() => useAskController())
  act(() => result.current.submit('Q1'))
  await act(async () => { g1.release(); await Promise.resolve() }) // A1 到第一輪
  act(() => result.current.submit('Q2'))                            // 遞增 reqId、abort 舊
  await act(async () => { g1.release(); await Promise.resolve() })  // A2 應被丟棄
  await act(async () => { g2.release(); await Promise.resolve() })  // B1 寫入新輪
  const turns = result.current.state.turns
  expect(turns).toHaveLength(2)
  expect(turns[0].answer).toBe('A1')  // 舊輪停在 A1，未被 A2 汙染
  expect(turns[1].answer).toBe('B1')
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/lib/useAskController.test.tsx`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/lib/useAskController.ts`：

```ts
import { useCallback, useReducer, useRef, useState } from 'react'
import { askReducer, initialAskState, turnFromHistory, type AskState } from './askReducer'
import { parseAskEvent, parseReportEvent } from './askSchemas'
import { streamAsk, streamReport, getConversation, sendFeedback } from './askApi'

let seq = 0
const newId = () => `t${Date.now()}_${seq++}`

export function useAskController() {
  const [state, dispatch] = useReducer(askReducer, initialAskState)
  const [conversationId, setConversationId] = useState<string | null>(null)
  const convRef = useRef<string | null>(null)
  const reqId = useRef(0)
  const askCtrl = useRef<AbortController | null>(null)
  const reportCtrl = useRef<AbortController | null>(null)

  const abortAll = useCallback(() => {
    askCtrl.current?.abort(); askCtrl.current = null
    reportCtrl.current?.abort(); reportCtrl.current = null
  }, [])

  const submit = useCallback((question: string) => {
    const q = question.trim()
    if (!q) return
    abortAll()
    const my = ++reqId.current
    const id = newId()
    const ctrl = new AbortController()
    askCtrl.current = ctrl
    dispatch({ type: 'submit', id, question: q, startedAt: Date.now() })
    void (async () => {
      try {
        const body = convRef.current ? { question: q, conversation_id: convRef.current } : { question: q }
        for await (const raw of streamAsk(body, ctrl.signal)) {
          if (my !== reqId.current) return
          const ev = parseAskEvent(raw)
          if (!ev) continue
          if (ev.event === 'done' && !convRef.current) { convRef.current = ev.data.conversation_id; setConversationId(ev.data.conversation_id) }
          dispatch({ type: 'ask-event', id, event: ev })
        }
        if (my === reqId.current) dispatch({ type: 'ask-end', id })
      } catch {
        if (my === reqId.current) dispatch({ type: 'ask-end', id })
      }
    })()
  }, [abortAll])

  const generateReport = useCallback((turnId: string, question: string, qaId: string | null) => {
    reportCtrl.current?.abort()
    const ctrl = new AbortController()
    reportCtrl.current = ctrl
    dispatch({ type: 'report-start', id: turnId })
    void (async () => {
      let sawTerminal = false
      try {
        const body: { question: string; conversation_id?: string; qa_id?: string } = { question }
        if (convRef.current) body.conversation_id = convRef.current
        if (qaId) body.qa_id = qaId
        for await (const raw of streamReport(body, ctrl.signal)) {
          const ev = parseReportEvent(raw)
          if (!ev) continue
          if (ev.event === 'done' || ev.event === 'error') sawTerminal = true
          dispatch({ type: 'report-event', id: turnId, event: ev })
        }
        if (!sawTerminal) dispatch({ type: 'report-fail', id: turnId, errorText: '研報生成未完成' })
      } catch {
        if (!ctrl.signal.aborted && !sawTerminal) dispatch({ type: 'report-fail', id: turnId, errorText: '研報生成失敗，請重試' })
      }
    })()
  }, [])

  const declineReport = useCallback((turnId: string) => dispatch({ type: 'report-decline', id: turnId }), [])

  const loadConversation = useCallback(async (id: string) => {
    abortAll()
    const my = ++reqId.current
    convRef.current = id
    setConversationId(id)
    try {
      const items = await getConversation(id)
      if (my === reqId.current) dispatch({ type: 'load', turns: items.map(turnFromHistory) })
    } catch { /* 載入失敗不破壞現況 */ }
  }, [abortAll])

  const newConversation = useCallback(() => {
    abortAll()
    ++reqId.current
    convRef.current = null
    setConversationId(null)
    dispatch({ type: 'reset' })
  }, [abortAll])

  const setFeedback = useCallback((turnId: string, qaId: string, value: 'like' | 'dislike') => {
    dispatch({ type: 'feedback', id: turnId, value })
    void sendFeedback(qaId, value).catch(() => { /* 回饋失敗不打擾 */ })
  }, [])

  return { state, conversationId, submit, generateReport, declineReport, loadConversation, newConversation, setFeedback }
}
```

- [ ] **Step 4: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/lib/useAskController.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/lib/useAskController.ts`；預期 PASS/OK。

- [ ] **Step 5: Commit**

```bash
git add src/lib/useAskController.ts src/lib/useAskController.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): useAskController — 串流編排與 latest-wins

reducer + SSE 串接；reqId 守門（dispatch 前丟棄 stale、reducer 不接收）、
AbortController 中止；submit/generateReport/decline/loadConversation/
newConversation/setFeedback；conversationId 由首個 done 設定。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: Composer — 輸入框（IME 守衛、Enter 送出、自動長高）

**Files:**
- Create: `src/features/ask/Composer.tsx`, `src/features/ask/Composer.module.css`, `src/features/ask/Composer.test.tsx`

**Interfaces:**
- Produces: `Composer({ value, onChange, onSubmit, disabled, variant }: { value: string; onChange: (v: string) => void; onSubmit: (q: string) => void; disabled?: boolean; variant?: 'center' | 'bottom' })`。**受控**（draft 由父層 AskPage 持有，便於離題「換個說法」回填）；Enter 送出（`e.nativeEvent.isComposing` 守衛、Shift+Enter 換行）；送出鈕（`Icon name="send"`）；清空由父層負責（`onSubmit` 後 `setDraft('')`）。`variant` 決定 max-width（center 640/bottom 760，對齊 `.dc.html:354-361`）。

- [ ] **Step 1: 寫失敗測試** — `src/features/ask/Composer.test.tsx`：

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { useState } from 'react'
import { expect, test, vi } from 'vitest'
import { Composer } from './Composer'

function Harness({ onSubmit }: { onSubmit: (q: string) => void }) {
  const [v, setV] = useState('')
  return <Composer value={v} onChange={setV} onSubmit={onSubmit} />
}
const area = () => screen.getByPlaceholderText('輸入你的問題…')

test('Enter 送出 trim 後值；Shift+Enter 不送', () => {
  const onSubmit = vi.fn()
  render(<Harness onSubmit={onSubmit} />)
  fireEvent.change(area(), { target: { value: '  台積電評價  ' } })
  fireEvent.keyDown(area(), { key: 'Enter', shiftKey: true })
  expect(onSubmit).not.toHaveBeenCalled()
  fireEvent.keyDown(area(), { key: 'Enter' })
  expect(onSubmit).toHaveBeenCalledWith('台積電評價')
})

test('IME 組字中的 Enter 不送出', () => {
  const onSubmit = vi.fn()
  render(<Harness onSubmit={onSubmit} />)
  fireEvent.change(area(), { target: { value: '注音' } })
  fireEvent.keyDown(area(), { key: 'Enter', nativeEvent: { isComposing: true } as KeyboardEvent })
  expect(onSubmit).not.toHaveBeenCalled()
})

test('disabled 時送出鈕不觸發', () => {
  const onSubmit = vi.fn()
  render(<Composer value="x" onChange={() => {}} onSubmit={onSubmit} disabled />)
  fireEvent.click(screen.getByRole('button', { name: '送出' }))
  expect(onSubmit).not.toHaveBeenCalled()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/ask/Composer.test.tsx`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/features/ask/Composer.tsx`：

```tsx
import { useEffect, useRef, type KeyboardEvent } from 'react'
import { Icon } from '../../components/primitives/Icon'
import styles from './Composer.module.css'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: (q: string) => void
  disabled?: boolean
  variant?: 'center' | 'bottom'
}

export function Composer({ value, onChange, onSubmit, disabled, variant = 'bottom' }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 140) + 'px'
  }, [value])

  function fire() {
    const q = value.trim()
    if (!q || disabled) return
    onSubmit(q)
  }
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); fire() }
  }

  return (
    <div className={`${styles.wrap} ${styles[variant]}`}>
      <div className={styles.box}>
        <textarea
          ref={ref}
          className={styles.input}
          value={value}
          rows={1}
          placeholder="輸入你的問題…"
          aria-label="輸入你的問題"
          onChange={e => onChange(e.target.value)}
          onKeyDown={onKey}
        />
        <button type="button" className={styles.send} onClick={fire} disabled={disabled} aria-label="送出" title="送出">
          <Icon name="send" size={20} />
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 樣式** — `src/features/ask/Composer.module.css`（對齊 `.dc.html:354-361`）：

```css
.wrap { margin: 0 auto; width: 100%; }
.center { max-width: 640px; }
.bottom { max-width: 760px; }
.box { background: var(--tf-surface); border: 1px solid var(--tf-border); border-radius: 14px; box-shadow: var(--tf-shadow-float); padding: 8px 8px 8px 16px; display: flex; align-items: flex-end; gap: 10px; }
.input { flex: 1; border: none; outline: none; resize: none; font-size: 14px; line-height: 1.6; color: var(--tf-text-1); max-height: 140px; min-height: 24px; padding: 8px 0; background: none; }
.send { width: 40px; height: 40px; flex: none; border-radius: var(--tf-radius-pill); border: none; background: var(--tf-gold-text); color: var(--tf-on-gold); display: flex; align-items: center; justify-content: center; cursor: pointer; }
.send:hover { background: var(--tf-gold-hover); }
.send:disabled { opacity: 0.5; cursor: default; }
```

- [ ] **Step 5: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/features/ask/Composer.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/features/ask/`；預期 PASS/OK。

- [ ] **Step 6: Commit**

```bash
git add src/features/ask/Composer.tsx src/features/ask/Composer.module.css src/features/ask/Composer.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): Composer 輸入框（IME 守衛/Enter 送出/自動長高）

Enter 送出、Shift+Enter 換行、e.nativeEvent.isComposing 守衛 IME 選字；
center/bottom 兩版對齊 .dc.html。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: ThinkingSteps — 思考步驟卡

**Files:**
- Create: `src/features/ask/ThinkingSteps.tsx`, `src/features/ask/ThinkingSteps.module.css`, `src/features/ask/ThinkingSteps.test.tsx`

**Interfaces:**
- Consumes: `Turn`（Task 8）、`stagesToSteps`（Task 5）。
- Produces: `ThinkingSteps({ turn }: { turn: Turn })`。可收合卡（對齊 `.dc.html:315-327`）：header 文字＝`turn.phase==='thinking'||'streaming'` 時「思考中…」，否則「已思考 {sec} 秒」（`sec=Math.round((turn.thinkingMs??0)/1000)`）；點 header 收合/展開步驟。步驟由 `stagesToSteps(turn.stages, turn.webUsed)`，三態圖示：done＝金勾（`Icon alertCircle`? 不，用 check）／active＝spinner（`tf-spin`）／pending＝空點。預設展開＝thinking 中。`turn.stages` 為空時只顯 header（不顯步驟區）。
- 需要金勾圖示：在 `Icon.tsx` `IconName` 補 `'check'`，`PATHS` 補 `check: (<path d="M5 12l5 5l9 -11" />)`。

- [ ] **Step 1: 寫失敗測試** — `src/features/ask/ThinkingSteps.test.tsx`：

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test } from 'vitest'
import { ThinkingSteps } from './ThinkingSteps'
import type { Turn } from '../../lib/askReducer'

function turn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'thinking', stages: ['understanding', 'retrieved'], webUsed: false,
    retrievedCount: null, answer: '', thinkingMs: null, startedAt: 0, sources: [], extSources: [],
    qaId: null, isOfftopic: false, noticeText: null, offerReport: false, reportTitle: null,
    feedback: null, report: { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null },
    errorText: null, ...over,
  }
}

test('thinking 顯示「思考中…」與步驟', () => {
  render(<ThinkingSteps turn={turn({ phase: 'thinking' })} />)
  expect(screen.getByText('思考中…')).toBeInTheDocument()
  expect(screen.getByText('理解問題')).toBeInTheDocument()
})

test('done 顯示已思考 N 秒、點擊可收合步驟', () => {
  render(<ThinkingSteps turn={turn({ phase: 'done', thinkingMs: 4200 })} />)
  const head = screen.getByRole('button', { name: /已思考 4 秒/ })
  expect(screen.getByText('理解問題')).toBeInTheDocument() // 預設 done 收合？此處展開檢查存在後收合
  fireEvent.click(head)
  expect(screen.queryByText('理解問題')).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/ask/ThinkingSteps.test.tsx`；預期 FAIL。（注意：實作需讓 done 時預設展開？為使測試「先存在後收合」成立，`done` 預設**展開**；若要對齊 vanilla 預設收合，改測試順序。實作以下採「thinking 展開、done 亦預設展開、可手動收合」。）

- [ ] **Step 3: 實作** — `src/features/ask/ThinkingSteps.tsx`：

```tsx
import { useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { stagesToSteps } from '../../lib/thinkingStages'
import type { Turn } from '../../lib/askReducer'
import styles from './ThinkingSteps.module.css'

export function ThinkingSteps({ turn }: { turn: Turn }) {
  const live = turn.phase === 'thinking' || turn.phase === 'streaming'
  const [open, setOpen] = useState(true)
  const steps = stagesToSteps(turn.stages, turn.webUsed)
  const sec = Math.round((turn.thinkingMs ?? 0) / 1000)
  const label = live ? '思考中…' : `已思考 ${sec} 秒`
  const hasSteps = steps.length > 0

  return (
    <div className={styles.card}>
      <button type="button" className={styles.head} onClick={() => setOpen(o => !o)}>
        <span>{label}</span>
        {hasSteps && <Icon name="chevronDown" size={15} className={open ? styles.chevOpen : styles.chev} />}
      </button>
      {hasSteps && open && (
        <div className={styles.steps}>
          {steps.map(s => (
            <div key={s.key} className={styles.step}>
              {s.state === 'done' && <Icon name="check" size={16} className={styles.done} />}
              {s.state === 'active' && <Icon name="chevronDown" size={16} className={styles.spin} />}
              {s.state === 'pending' && <span className={styles.dot} />}
              <span className={s.state === 'pending' ? styles.pendingText : undefined}>{s.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
```

（active 圖示用旋轉的環：以 `Icon name="check"` 不適；改用 CSS spinner。實作 `.spin` 套用 `animation: tf-spin .8s linear infinite`，圖示可用一個 3/4 圓弧 path。若沿用現有圖示不便，於 `Icon.tsx` 增 `spinner: (<path d="M12 3a9 9 0 1 0 9 9" />)` 並於 active 用 `<Icon name="spinner" className={styles.spin} />`。）

- [ ] **Step 4: 樣式 + 圖示** — 在 `Icon.tsx` 補 `'check'` 與 `'spinner'`；`src/features/ask/ThinkingSteps.module.css`（對齊 `.dc.html:315-327`）：

```css
.card { border: 1px solid var(--tf-border); background: var(--tf-surface); border-radius: var(--tf-radius-card); padding: 12px 16px; margin: 4px 0 16px; }
.head { display: flex; align-items: center; gap: 6px; width: 100%; background: none; border: none; padding: 0; cursor: pointer; font-size: 12.5px; color: var(--tf-text-4); }
.chev { transition: transform var(--tf-dur-2); }
.chevOpen { transform: rotate(180deg); transition: transform var(--tf-dur-2); }
.steps { margin-top: 10px; display: flex; flex-direction: column; gap: 6px; }
.step { display: flex; align-items: center; gap: 10px; font-size: 13px; color: var(--tf-text-2); }
.done { color: var(--tf-gold-text); }
.spin { color: var(--tf-gold-text); animation: tf-spin 0.8s linear infinite; }
.dot { width: 7px; height: 7px; border-radius: 999px; border: 1.5px solid var(--tf-border); display: inline-block; margin: 0 4.5px; }
.pendingText { color: var(--tf-text-4); }
```

- [ ] **Step 5: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/features/ask/ThinkingSteps.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/features/ask/ src/components/primitives/Icon.tsx`；預期 PASS/OK。

- [ ] **Step 6: Commit**

```bash
git add src/features/ask/ThinkingSteps.tsx src/features/ask/ThinkingSteps.module.css src/features/ask/ThinkingSteps.test.tsx src/components/primitives/Icon.tsx
git commit -m "$(cat <<'EOF'
feat(問答): ThinkingSteps 思考步驟卡

由真實階段驅動三態步驟；思考中/已思考 N 秒可收合；check/spinner 圖示。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: SourcesDrawer — 引用來源抽屜

**Files:**
- Create: `src/features/ask/SourcesDrawer.tsx`, `src/features/ask/SourcesDrawer.module.css`, `src/features/ask/SourcesDrawer.test.tsx`

**Interfaces:**
- Consumes: `Turn`（Task 8）、`marketColor`/`marketLabel`（`src/lib/meta.ts`）。
- Produces: `SourcesDrawer({ open, turn, onClose, onOpenReport }: { open: boolean; turn: Turn | null; onClose: () => void; onOpenReport: (reportId: string, fileName: string) => void })`。右側推入面板（對齊 `.dc.html:363-385`，**非 overlay、無 scrim**，由 AskPage 置於 flex row）：標頭「引用來源」+關閉鈕；子標「資料來源 · {N}」（N＝`turn.sources.length + turn.extSources.length`）；研報來源卡（金 `n` + 市場徽章 `marketColor/marketLabel` + `file_name` 標題 + `report_date`，點卡片 → `onOpenReport(report_id, file_name)`）；其後網路來源卡（橘框 `n` + `title` + 「網路 · {hostname}」，外連 `target="_blank" rel="noopener noreferrer"`）。ESC 關閉。`open===false` 或 `turn===null` 回 `null`。

- [ ] **Step 1: 寫失敗測試** — `src/features/ask/SourcesDrawer.test.tsx`：

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { SourcesDrawer } from './SourcesDrawer'
import type { Turn } from '../../lib/askReducer'

function turn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'done', stages: [], webUsed: false, retrievedCount: null,
    answer: '', thinkingMs: null, startedAt: 0,
    sources: [{ n: 1, report_id: 'r1', file_name: '台積電.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }],
    extSources: [{ title: '外部新聞', url: 'https://news.example.com/a' }],
    qaId: 'qa1', isOfftopic: false, noticeText: null, offerReport: false, reportTitle: null,
    feedback: null, report: { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null },
    errorText: null, ...over,
  }
}

test('子標計數為研報+網路總數；點研報卡開報告；ESC 關閉', () => {
  const onClose = vi.fn(); const onOpenReport = vi.fn()
  render(<SourcesDrawer open turn={turn({})} onClose={onClose} onOpenReport={onOpenReport} />)
  expect(screen.getByText('資料來源 · 2')).toBeInTheDocument()
  fireEvent.click(screen.getByText('台積電.pdf'))
  expect(onOpenReport).toHaveBeenCalledWith('r1', '台積電.pdf')
  const ext = screen.getByRole('link', { name: /外部新聞/ })
  expect(ext).toHaveAttribute('target', '_blank')
  expect(ext).toHaveAttribute('rel', 'noopener noreferrer')
  fireEvent.keyDown(document, { key: 'Escape' })
  expect(onClose).toHaveBeenCalled()
})

test('closed 或 null 不渲染', () => {
  const { container } = render(<SourcesDrawer open={false} turn={turn({})} onClose={() => {}} onOpenReport={() => {}} />)
  expect(container.firstChild).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/ask/SourcesDrawer.test.tsx`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/features/ask/SourcesDrawer.tsx`：

```tsx
import { useEffect } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { marketColor, marketLabel } from '../../lib/meta'
import type { Turn } from '../../lib/askReducer'
import styles from './SourcesDrawer.module.css'

function hostOf(url: string): string {
  try { return new URL(url).hostname } catch { return url }
}

interface Props {
  open: boolean
  turn: Turn | null
  onClose: () => void
  onOpenReport: (reportId: string, fileName: string) => void
}

export function SourcesDrawer({ open, turn, onClose, onOpenReport }: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open || !turn) return null
  const total = turn.sources.length + turn.extSources.length

  return (
    <aside className={styles.panel} aria-label="引用來源">
      <div className={styles.header}>
        <span className={styles.title}>引用來源</span>
        <button type="button" className={styles.close} onClick={onClose} aria-label="關閉"><Icon name="x" size={17} /></button>
      </div>
      <div className={`${styles.body} tf-scroll`}>
        <div className={styles.subhead}>資料來源 · {total}</div>
        <div className={styles.list}>
          {turn.sources.map(s => (
            <button key={`s${s.n}`} type="button" className={styles.srcCard} onClick={() => onOpenReport(s.report_id, s.file_name)}>
              <span className={styles.numGold}>{s.n}</span>
              <div className={styles.srcMain}>
                <span className={styles.mkt} style={{ background: marketColor(s.market) }}>{marketLabel(s.market)}</span>
                <div className={styles.srcTitle}>{s.file_name}</div>
                {s.report_date && <div className={styles.srcMeta}>{s.report_date}</div>}
              </div>
            </button>
          ))}
          {turn.extSources.map((e, i) => (
            <a key={`e${i}`} className={styles.extCard} href={e.url} target="_blank" rel="noopener noreferrer">
              <span className={styles.numOrange}>{turn.sources.length + i + 1}</span>
              <div>
                <div className={styles.extTitle}>{e.title}</div>
                <div className={styles.extMeta}>網路 · {hostOf(e.url)}</div>
              </div>
            </a>
          ))}
        </div>
      </div>
    </aside>
  )
}
```

- [ ] **Step 4: 樣式** — `src/features/ask/SourcesDrawer.module.css`（對齊 `.dc.html:363-385`）：

```css
.panel { width: 340px; flex: none; background: var(--tf-surface); border-left: 1px solid var(--tf-border); display: flex; flex-direction: column; height: 100%; }
.header { display: flex; align-items: center; justify-content: space-between; padding: 16px 18px; border-bottom: 1px solid var(--tf-border-weak); flex: none; }
.title { font-family: var(--tf-serif); font-weight: 700; font-size: 16px; color: var(--tf-text-1); }
.close { border: none; background: var(--tf-border-weak); border-radius: 999px; width: 30px; height: 30px; display: flex; align-items: center; justify-content: center; cursor: pointer; color: var(--tf-text-3); }
.close:hover { background: var(--tf-border); }
.body { flex: 1; overflow-y: auto; padding: 16px 18px; }
.subhead { font-size: 12px; font-weight: 600; color: var(--tf-text-4); margin-bottom: 10px; }
.list { display: flex; flex-direction: column; gap: 8px; }
.srcCard { text-align: left; border: 1px solid var(--tf-border); background: var(--tf-surface); border-radius: 10px; padding: 10px 12px; cursor: pointer; display: flex; gap: 10px; align-items: flex-start; }
.srcCard:hover { border-color: var(--tf-gold-text); }
.numGold { background: var(--tf-gold-tint); color: var(--tf-gold-text); border-radius: 999px; font-size: 11.5px; font-weight: 700; padding: 1px 8px; flex: none; margin-top: 1px; }
.srcMain { min-width: 0; }
.mkt { color: #fff; border-radius: 999px; padding: 1px 8px; font-size: 11px; font-weight: 600; }
.srcTitle { font-family: var(--tf-serif); font-weight: 700; font-size: 13.5px; color: var(--tf-text-1); line-height: 1.4; margin-top: 4px; word-break: break-all; }
.srcMeta { font-size: 12px; color: var(--tf-text-3); margin-top: 3px; }
.extCard { border: 1px solid var(--tf-border); background: var(--tf-surface); border-radius: 10px; padding: 10px 12px; display: flex; gap: 10px; align-items: flex-start; text-decoration: none; }
.numOrange { border: 1px solid var(--tf-warn); color: var(--tf-warn-text); border-radius: 999px; font-size: 11px; font-weight: 600; padding: 0 7px; flex: none; margin-top: 2px; }
.extTitle { font-size: 13.5px; color: var(--tf-text-1); font-weight: 500; }
.extMeta { font-size: 12px; color: var(--tf-text-4); margin-top: 2px; }
```

- [ ] **Step 5: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/features/ask/SourcesDrawer.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/features/ask/`；預期 PASS/OK。

- [ ] **Step 6: Commit**

```bash
git add src/features/ask/SourcesDrawer.tsx src/features/ask/SourcesDrawer.module.css src/features/ask/SourcesDrawer.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): SourcesDrawer 引用來源抽屜（單一入口、研報+網路）

子標計數為研報+網路總數；研報卡（file_name/report_date，點開報告）+
網路卡（新分頁）；ESC 關閉。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 13: DeepReportPanel — 深度研報 offer/進度/完成/失敗

**Files:**
- Create: `src/features/ask/DeepReportPanel.tsx`, `src/features/ask/DeepReportPanel.module.css`, `src/features/ask/DeepReportPanel.test.tsx`

**Interfaces:**
- Consumes: `ReportState`（Task 8）、`Callout`（Task 1）、`Icon`。
- Produces: `DeepReportPanel({ report, onGenerate, onDecline }: { report: ReportState; onGenerate: () => void; onDecline: () => void })`。依 `report.status`：`idle`→`null`；`offered`→offer 卡（標題「要不要整理成完整 PDF 深度研報？」副標「彙整以上引用來源，生成含圖表與重點的深度研報。」＋`要`(onGenerate)/`不用`(onDecline)）；`generating`→進度卡（標題「深度研報生成中…」＋進度條 width=`report.pct%`＋`report.stageText`；`pct===50` 時填充條加 `tf-indet` 流動）；`done`→暖金完成卡（「深度研報已完成」＋`下載 PDF` a[href]，**scheme 守門**：僅 `report.downloadUrl` 以 `/` 開頭或同源才渲染連結）；`error`→`Callout variant="error"`（`report.errorText || '研報生成失敗，請重試'`＋`重試`(onGenerate)）。對齊 `.dc.html:329-350`。

- [ ] **Step 1: 寫失敗測試** — `src/features/ask/DeepReportPanel.test.tsx`：

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { DeepReportPanel } from './DeepReportPanel'
import type { ReportState } from '../../lib/askReducer'

const rs = (over: Partial<ReportState>): ReportState => ({ status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null, ...over })

test('idle 不渲染', () => {
  const { container } = render(<DeepReportPanel report={rs({})} onGenerate={() => {}} onDecline={() => {}} />)
  expect(container.firstChild).toBeNull()
})

test('offered：要/不用 觸發 callback', () => {
  const onGenerate = vi.fn(); const onDecline = vi.fn()
  render(<DeepReportPanel report={rs({ status: 'offered' })} onGenerate={onGenerate} onDecline={onDecline} />)
  expect(screen.getByText('要不要整理成完整 PDF 深度研報？')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '要' })); expect(onGenerate).toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '不用' })); expect(onDecline).toHaveBeenCalled()
})

test('generating 顯示進度與階段文字', () => {
  render(<DeepReportPanel report={rs({ status: 'generating', pct: 50, stageText: '撰寫研報中…' })} onGenerate={() => {}} onDecline={() => {}} />)
  expect(screen.getByText('深度研報生成中…')).toBeInTheDocument()
  expect(screen.getByText('撰寫研報中…')).toBeInTheDocument()
})

test('done 顯示下載連結（scheme 守門相對路徑）', () => {
  render(<DeepReportPanel report={rs({ status: 'done', pct: 100, downloadUrl: '/api/report-doc/rp1/pdf', title: 'T' })} onGenerate={() => {}} onDecline={() => {}} />)
  const dl = screen.getByRole('link', { name: '下載 PDF' })
  expect(dl).toHaveAttribute('href', '/api/report-doc/rp1/pdf')
})

test('error 顯示 Callout 與重試', () => {
  const onGenerate = vi.fn()
  render(<DeepReportPanel report={rs({ status: 'error', errorText: '找不到足夠資料生成研報' })} onGenerate={onGenerate} onDecline={() => {}} />)
  expect(screen.getByRole('alert')).toHaveTextContent('找不到足夠資料生成研報')
  fireEvent.click(screen.getByRole('button', { name: '重試' })); expect(onGenerate).toHaveBeenCalled()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/ask/DeepReportPanel.test.tsx`；預期 FAIL。

- [ ] **Step 3: 實作** — `src/features/ask/DeepReportPanel.tsx`：

```tsx
import { Callout } from '../../components/primitives/Callout'
import { Icon } from '../../components/primitives/Icon'
import type { ReportState } from '../../lib/askReducer'
import styles from './DeepReportPanel.module.css'

function safeDownload(url: string | null): string | null {
  if (!url) return null
  if (url.startsWith('/')) return url
  try { if (new URL(url).origin === location.origin) return url } catch { /* ignore */ }
  return null
}

interface Props { report: ReportState; onGenerate: () => void; onDecline: () => void }

export function DeepReportPanel({ report, onGenerate, onDecline }: Props) {
  if (report.status === 'idle') return null

  if (report.status === 'offered') {
    return (
      <div className={styles.offer}>
        <div className={styles.offerMain}>
          <div className={styles.offerTitle}>要不要整理成完整 PDF 深度研報？</div>
          <div className={styles.offerSub}>彙整以上引用來源，生成含圖表與重點的深度研報。</div>
        </div>
        <div className={styles.offerBtns}>
          <button type="button" className={styles.yes} onClick={onGenerate}>要</button>
          <button type="button" className={styles.no} onClick={onDecline}>不用</button>
        </div>
      </div>
    )
  }

  if (report.status === 'generating') {
    return (
      <div className={styles.gen}>
        <div className={styles.genTitle}>深度研報生成中…</div>
        <div className={styles.track}>
          <div className={`${styles.fill} ${report.pct === 50 ? styles.indet : ''}`} style={{ width: `${report.pct}%` }} />
        </div>
        <div className={styles.genMeta}>{report.stageText}</div>
      </div>
    )
  }

  if (report.status === 'done') {
    const href = safeDownload(report.downloadUrl)
    return (
      <div className={styles.done}>
        <div className={styles.doneHead}><Icon name="check" size={18} className={styles.doneIcon} /><span className={styles.doneTitle}>深度研報已完成</span></div>
        {report.title && <div className={styles.doneMeta}>{report.title}</div>}
        {href && <a className={styles.dl} href={href} download><Icon name="fileText" size={16} /> 下載 PDF</a>}
      </div>
    )
  }

  return <Callout variant="error" action={{ label: '重試', onClick: onGenerate }}>{report.errorText || '研報生成失敗，請重試'}</Callout>
}
```

- [ ] **Step 4: 樣式** — `src/features/ask/DeepReportPanel.module.css`（對齊 `.dc.html:329-350`）：

```css
.offer { border: 1px solid var(--tf-border); background: var(--tf-surface); border-radius: var(--tf-radius-card); padding: 15px 16px; display: flex; flex-wrap: wrap; align-items: center; gap: 14px; margin-bottom: 8px; }
.offerMain { flex: 1; min-width: 220px; }
.offerTitle { font-family: var(--tf-serif); font-weight: 700; font-size: 15px; color: var(--tf-text-1); }
.offerSub { font-size: 12.5px; color: var(--tf-text-3); margin-top: 3px; }
.offerBtns { display: flex; gap: 8px; }
.yes { background: var(--tf-gold-text); color: var(--tf-on-gold); border: none; border-radius: 999px; font-size: 13px; font-weight: 600; padding: 7px 20px; cursor: pointer; }
.yes:hover { background: var(--tf-gold-hover); }
.no { background: var(--tf-surface); border: 1px solid var(--tf-border); border-radius: 999px; color: var(--tf-text-2); font-size: 13px; font-weight: 600; padding: 7px 18px; cursor: pointer; }
.gen { border: 1px solid var(--tf-border); background: var(--tf-surface); border-radius: var(--tf-radius-card); padding: 16px; margin-bottom: 8px; }
.genTitle { font-family: var(--tf-serif); font-weight: 700; font-size: 15px; color: var(--tf-text-1); margin-bottom: 10px; }
.track { height: 9px; border-radius: 999px; background: var(--tf-border-weak); overflow: hidden; position: relative; }
.fill { height: 100%; border-radius: 999px; background: var(--tf-gold-text); transition: width var(--tf-dur-4) var(--tf-ease-out); }
.indet { position: relative; }
.indet::after { content: ''; position: absolute; top: 0; bottom: 0; width: 40%; background: linear-gradient(90deg, transparent, rgba(255,255,255,.5), transparent); animation: tf-indet 1.1s linear infinite; }
.genMeta { font-size: 12.5px; color: var(--tf-text-3); margin-top: 8px; font-variant-numeric: tabular-nums; }
.done { border: 1px solid var(--tf-warn-border); background: #fbf8f0; border-radius: var(--tf-radius-card); padding: 16px; margin-bottom: 8px; }
.doneHead { display: flex; align-items: center; gap: 8px; }
.doneIcon { color: var(--tf-gold-text); }
.doneTitle { font-family: var(--tf-serif); font-weight: 700; font-size: 15px; color: var(--tf-text-1); }
.doneMeta { font-size: 12.5px; color: var(--tf-text-3); margin: 8px 0 14px; }
.dl { display: inline-flex; align-items: center; gap: 6px; background: var(--tf-gold-text); color: var(--tf-on-gold); border-radius: 999px; font-size: 13px; font-weight: 600; padding: 8px 20px; text-decoration: none; }
.dl:hover { background: var(--tf-gold-hover); }
```

- [ ] **Step 5: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/features/ask/DeepReportPanel.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/features/ask/`；預期 PASS/OK。

- [ ] **Step 6: Commit**

```bash
git add src/features/ask/DeepReportPanel.tsx src/features/ask/DeepReportPanel.module.css src/features/ask/DeepReportPanel.test.tsx
git commit -m "$(cat <<'EOF'
feat(研報): DeepReportPanel offer/進度/完成/失敗

offer(要/不用)→里程碑 % 進度(撰寫階段 tf-indet)→暖金完成卡(下載 PDF、
scheme 守門)→失敗 Callout error 重試。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 14: UserMessage / AssistantMessage / AskEmptyState

**Files:**
- Create: `src/features/ask/UserMessage.tsx`、`src/features/ask/AssistantMessage.tsx`、`src/features/ask/AskEmptyState.tsx`，各 `.module.css`，及 `AssistantMessage.test.tsx`、`AskEmptyState.test.tsx`

**Interfaces:**
- Consumes: `Turn`（Task 8）、`renderAnswer`（Task 7）、`ThinkingSteps`（Task 11）、`Callout`（Task 1）、`Composer`（Task 10）、`Icon`。
- Produces:
  - `UserMessage({ text }: { text: string })` — 右側金色實心泡泡（對齊 `.dc.html` user bubble：radius `16px 16px 4px 16px`）。
  - `AssistantMessage({ turn, onCite, onOpenSources, onFeedback, onNoticeRetry, onErrorRetry }: { turn: Turn; onCite: (n: number) => void; onOpenSources: () => void; onFeedback: (v: 'like'|'dislike') => void; onNoticeRetry: () => void; onErrorRetry: () => void })`。分支：`notice`→`Callout warning`（`turn.noticeText`）+ action「換個說法重新提問」(`onNoticeRetry`)；`error`→`Callout error`（`turn.errorText`）+「重試」(`onErrorRetry`)；否則→`ThinkingSteps`（`turn.stages` 非空或 live 時）+ 答案 `renderAnswer(turn.answer, turn.sources.length, onCite)`（`streaming` 時尾隨游標）+（`done`&&非離題）動作列：讚/倒讚（`onFeedback`，`turn.feedback` 高亮，僅 `turn.qaId` 非 null 顯示）、複製（內部 `navigator.clipboard`，暫態「已複製」）、分隔、**單一** `資料來源 {N}`（N＝`turn.sources.length + turn.extSources.length`，>0 才顯示，`onOpenSources`）。對齊 `.dc.html:290-311`。
  - `AskEmptyState({ value, onChange, onSubmit }: { value: string; onChange: (v: string) => void; onSubmit: (q: string) => void })` — 置中 廷 logo +「向廷豐智能體提問」+「以自然語言詢問研究主題，回答將附上券商研報的引用來源。」+ `Composer variant="center"`；**無範例膠囊**（對齊 `.dc.html:274-288`）。

- [ ] **Step 1: 寫失敗測試** — `src/features/ask/AssistantMessage.test.tsx`：

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import { expect, test, vi } from 'vitest'
import { AssistantMessage } from './AssistantMessage'
import type { Turn } from '../../lib/askReducer'

function turn(over: Partial<Turn>): Turn {
  return {
    id: 't', question: 'Q', phase: 'done', stages: [], webUsed: false, retrievedCount: null,
    answer: '答案 [1] 內容', thinkingMs: 3000, startedAt: 0,
    sources: [{ n: 1, report_id: 'r1', file_name: 'f.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }],
    extSources: [], qaId: 'qa1', isOfftopic: false, noticeText: null, offerReport: false, reportTitle: null,
    feedback: null, report: { status: 'idle', pct: 0, stageText: '', downloadUrl: null, title: null, errorText: null },
    errorText: null, ...over,
  }
}
const noop = () => {}

test('done：答案+單一資料來源 {N}+讚/倒讚', () => {
  const onFeedback = vi.fn(); const onOpenSources = vi.fn(); const onCite = vi.fn()
  render(<AssistantMessage turn={turn({})} onCite={onCite} onOpenSources={onOpenSources} onFeedback={onFeedback} onNoticeRetry={noop} onErrorRetry={noop} />)
  fireEvent.click(screen.getByRole('button', { name: '1' })); expect(onCite).toHaveBeenCalledWith(1)
  fireEvent.click(screen.getByRole('button', { name: '資料來源 1' })); expect(onOpenSources).toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '讚' })); expect(onFeedback).toHaveBeenCalledWith('like')
  expect(screen.queryByRole('button', { name: /外部參考/ })).toBeNull() // 無獨立外部參考鈕
})

test('notice：Callout warning + 換個說法重新提問', () => {
  const onNoticeRetry = vi.fn()
  render(<AssistantMessage turn={turn({ phase: 'notice', isOfftopic: true, noticeText: '無法回答此問題', qaId: null })} onCite={noop} onOpenSources={noop} onFeedback={noop} onNoticeRetry={onNoticeRetry} onErrorRetry={noop} />)
  expect(screen.getByRole('alert')).toHaveTextContent('無法回答此問題')
  fireEvent.click(screen.getByRole('button', { name: '換個說法重新提問' })); expect(onNoticeRetry).toHaveBeenCalled()
})

test('error：Callout error + 重試', () => {
  const onErrorRetry = vi.fn()
  render(<AssistantMessage turn={turn({ phase: 'error', answer: '', errorText: '查詢逾時或失敗' })} onCite={noop} onOpenSources={noop} onFeedback={noop} onNoticeRetry={noop} onErrorRetry={onErrorRetry} />)
  fireEvent.click(screen.getByRole('button', { name: '重試' })); expect(onErrorRetry).toHaveBeenCalled()
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/ask/AssistantMessage.test.tsx`；預期 FAIL。

- [ ] **Step 3: 實作 UserMessage** — `src/features/ask/UserMessage.tsx`：

```tsx
import styles from './UserMessage.module.css'
export function UserMessage({ text }: { text: string }) {
  return <div className={styles.row}><div className={styles.bubble}>{text}</div></div>
}
```

`src/features/ask/UserMessage.module.css`：

```css
.row { display: flex; justify-content: flex-end; margin: 18px 0 10px; }
.bubble { max-width: 78%; background: var(--tf-gold-text); color: var(--tf-on-gold); border-radius: 16px 16px 4px 16px; padding: 10px 14px; font-size: 14.5px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
```

- [ ] **Step 4: 實作 AssistantMessage** — `src/features/ask/AssistantMessage.tsx`：

```tsx
import { useState } from 'react'
import { Callout } from '../../components/primitives/Callout'
import { Icon } from '../../components/primitives/Icon'
import { ThinkingSteps } from './ThinkingSteps'
import { renderAnswer } from '../../lib/askMarkdown'
import type { Turn } from '../../lib/askReducer'
import styles from './AssistantMessage.module.css'

interface Props {
  turn: Turn
  onCite: (n: number) => void
  onOpenSources: () => void
  onFeedback: (v: 'like' | 'dislike') => void
  onNoticeRetry: () => void
  onErrorRetry: () => void
}

export function AssistantMessage({ turn, onCite, onOpenSources, onFeedback, onNoticeRetry, onErrorRetry }: Props) {
  const [copied, setCopied] = useState(false)

  if (turn.phase === 'notice') {
    return <Callout variant="warning" action={{ label: '換個說法重新提問', onClick: onNoticeRetry }}>{turn.noticeText ?? '無法回答此問題'}</Callout>
  }
  if (turn.phase === 'error') {
    return <Callout variant="error" action={{ label: '重試', onClick: onErrorRetry }}>{turn.errorText ?? '查詢逾時或失敗'}</Callout>
  }

  const refCount = turn.sources.length + turn.extSources.length
  const showActions = turn.phase === 'done' && !turn.isOfftopic

  function copy() {
    void navigator.clipboard?.writeText(turn.answer).then(() => { setCopied(true); setTimeout(() => setCopied(false), 1200) })
  }

  return (
    <div className={styles.msg}>
      {(turn.stages.length > 0 || turn.phase === 'thinking' || turn.phase === 'streaming') && <ThinkingSteps turn={turn} />}
      {turn.answer && (
        <div className={styles.body}>
          {renderAnswer(turn.answer, turn.sources.length, onCite)}
          {turn.phase === 'streaming' && <span className={styles.caret} />}
        </div>
      )}
      {showActions && (
        <div className={styles.actions}>
          {turn.qaId && (
            <>
              <button type="button" className={`${styles.act} ${turn.feedback === 'like' ? styles.on : ''}`} onClick={() => onFeedback('like')} aria-label="讚"><Icon name="thumbUp" size={15} /></button>
              <button type="button" className={`${styles.act} ${turn.feedback === 'dislike' ? styles.on : ''}`} onClick={() => onFeedback('dislike')} aria-label="倒讚"><Icon name="thumbDown" size={15} /></button>
            </>
          )}
          <button type="button" className={styles.act} onClick={copy} aria-label="複製回答" title={copied ? '已複製' : '複製'}><Icon name="copy" size={15} /></button>
          {refCount > 0 && (
            <>
              <span className={styles.divider} />
              <button type="button" className={styles.srcBtn} onClick={onOpenSources}>資料來源 {refCount}</button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
```

`src/features/ask/AssistantMessage.module.css`：

```css
.msg { margin: 2px 0 20px; }
.body { font-size: 15px; line-height: 1.7; color: var(--tf-text-2); }
.body :global(.tf-cite) { display: inline-block; background: var(--tf-gold-tint); color: var(--tf-gold-text); border: none; border-radius: 999px; font-size: 11.5px; font-weight: 700; padding: 0 6px; margin: 0 1px; cursor: pointer; vertical-align: baseline; }
.body :global(.tf-md-h) { font-family: var(--tf-serif); font-weight: 700; color: var(--tf-text-1); margin: 18px 0 8px; }
.caret { display: inline-block; width: 7px; height: 15px; background: var(--tf-gold-text); margin-left: 2px; vertical-align: text-bottom; animation: tf-pulse 1s steps(2) infinite; }
.actions { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-top: 12px; }
.act { display: inline-flex; align-items: center; justify-content: center; border: 1px solid var(--tf-border); background: var(--tf-surface); color: var(--tf-text-3); border-radius: 999px; padding: 6px; cursor: pointer; }
.act:hover { background: var(--tf-gold-tint); border-color: var(--tf-gold-text); color: var(--tf-gold-text); }
.on { background: var(--tf-gold-tint); border-color: var(--tf-gold-text); color: var(--tf-gold-text); }
.divider { width: 1px; height: 16px; background: var(--tf-border); margin: 0 2px; }
.srcBtn { display: inline-flex; align-items: center; gap: 5px; border: 1px solid var(--tf-border); background: var(--tf-surface); color: var(--tf-text-2); border-radius: 999px; font-size: 12px; padding: 5px 11px; cursor: pointer; }
.srcBtn:hover { background: var(--tf-gold-tint); border-color: var(--tf-gold-text); color: var(--tf-gold-text); }
```

- [ ] **Step 5: 實作 AskEmptyState + 測試** — `src/features/ask/AskEmptyState.tsx`：

```tsx
import { Composer } from './Composer'
import styles from './AskEmptyState.module.css'

interface Props { value: string; onChange: (v: string) => void; onSubmit: (q: string) => void }

export function AskEmptyState({ value, onChange, onSubmit }: Props) {
  return (
    <div className={styles.wrap}>
      <div className={styles.glyph}>廷</div>
      <div className={styles.title}>向廷豐智能體提問</div>
      <div className={styles.sub}>以自然語言詢問研究主題，回答將附上券商研報的引用來源。</div>
      <Composer value={value} onChange={onChange} onSubmit={onSubmit} variant="center" />
    </div>
  )
}
```

`src/features/ask/AskEmptyState.module.css`：

```css
.wrap { max-width: 640px; margin: 0 auto; display: flex; flex-direction: column; align-items: center; text-align: center; gap: 6px; padding: 24px 16px; }
.glyph { width: 56px; height: 56px; border-radius: 16px; background: var(--tf-gold-tint); color: var(--tf-gold-text); font-family: var(--tf-serif); font-weight: 700; font-size: 26px; display: flex; align-items: center; justify-content: center; margin-bottom: 6px; }
.title { font-family: var(--tf-serif); font-weight: 700; font-size: 22px; color: var(--tf-text-1); }
.sub { font-size: 13.5px; color: var(--tf-text-3); margin-bottom: 14px; max-width: 420px; }
```

`src/features/ask/AskEmptyState.test.tsx`：

```tsx
import { render, screen } from '@testing-library/react'
import { expect, test } from 'vitest'
import { AskEmptyState } from './AskEmptyState'

test('顯示標題/副標/輸入框，無範例膠囊', () => {
  render(<AskEmptyState value="" onChange={() => {}} onSubmit={() => {}} />)
  expect(screen.getByText('向廷豐智能體提問')).toBeInTheDocument()
  expect(screen.getByText(/以自然語言詢問研究主題/)).toBeInTheDocument()
  expect(screen.getByPlaceholderText('輸入你的問題…')).toBeInTheDocument()
  // 無範例膠囊：不應出現任何「範例」按鈕
  expect(screen.queryByRole('button', { name: /台積電|AI 伺服器|Fed/ })).toBeNull()
})
```

- [ ] **Step 6: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/features/ask/AssistantMessage.test.tsx src/features/ask/AskEmptyState.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/features/ask/`；預期 PASS/OK。

- [ ] **Step 7: Commit**

```bash
git add src/features/ask/UserMessage.tsx src/features/ask/UserMessage.module.css src/features/ask/AssistantMessage.tsx src/features/ask/AssistantMessage.module.css src/features/ask/AssistantMessage.test.tsx src/features/ask/AskEmptyState.tsx src/features/ask/AskEmptyState.module.css src/features/ask/AskEmptyState.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): UserMessage/AssistantMessage/AskEmptyState

金泡泡使用者訊息；助理訊息（思考步驟+markdown+單一資料來源{N}+讚/倒讚/
複製、離題/失敗走 Callout）；空狀態 logo+標題+置中輸入框無範例膠囊。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 15: AskPage — 組合、`?c` 同步、貼底捲動

**Files:**
- Modify: `src/features/ask/AskPage.tsx`（由佔位改實作）
- Create: `src/features/ask/AskPage.module.css`, `src/features/ask/AskPage.test.tsx`

**Interfaces:**
- Consumes: `useAskController`（Task 9）、`Composer`（Task 10）、`AskEmptyState`/`UserMessage`/`AssistantMessage`（Task 14）、`DeepReportPanel`（Task 13）、`SourcesDrawer`（Task 12）、`ReportDetailModal`（`src/components/ReportDetailModal`）、`useSearchParams`（react-router）。
- Produces: `export default function AskPage()`。持 `draft`（composer 值）、抽屜 `{ open, turnId }`、詳情 modal `{ reportId, fileName }`。`?c` ↔ `conversationId` 雙向同步（雙 effect + guard，見下）。貼底捲動（使用者在底部附近才自動貼底）。

- [ ] **Step 1: 寫失敗測試** — `src/features/ask/AskPage.test.tsx`：

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'
import type { RawSSEEvent } from '../../lib/readSSE'

const streamAsk = vi.fn()
vi.mock('../../lib/askApi', () => ({
  streamAsk: (...a: unknown[]) => streamAsk(...a),
  streamReport: vi.fn(),
  getConversation: vi.fn(async () => []),
  sendFeedback: vi.fn(async () => {}),
}))
import AskPage from './AskPage'

afterEach(() => vi.clearAllMocks())

function immediate(events: RawSSEEvent[]) {
  return (async function* () { for (const e of events) yield e })()
}
function wrap() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/ask']}><AskPage /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test('送出問題→串流答案顯示、資料來源鈕出現', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [{ n: 1, report_id: 'r1', file_name: 'f.pdf', market: 'TW', report_date: '2026-06-20', is_latest: false }] },
    { event: 'status', data: { stage: 'generating', thinking_ms: 3000 } },
    { event: 'token', data: '這是答案 [1]' },
    { event: 'done', data: { conversation_id: 'c1', qa_id: 'qa1', cited: ['r1'] } },
  ]))
  wrap()
  fireEvent.change(screen.getByPlaceholderText('輸入你的問題…'), { target: { value: '台積電評價' } })
  fireEvent.keyDown(screen.getByPlaceholderText('輸入你的問題…'), { key: 'Enter' })
  expect(await screen.findByText('台積電評價')).toBeInTheDocument()
  await waitFor(() => expect(screen.getByText(/這是答案/)).toBeInTheDocument())
  expect(await screen.findByRole('button', { name: '資料來源 1' })).toBeInTheDocument()
})

test('離題→Callout warning', async () => {
  streamAsk.mockReturnValue(immediate([
    { event: 'sources', data: [] },
    { event: 'notice', data: '無法回答此問題' },
    { event: 'done', data: { conversation_id: 'c1' } },
  ]))
  wrap()
  fireEvent.change(screen.getByPlaceholderText('輸入你的問題…'), { target: { value: '今天天氣' } })
  fireEvent.keyDown(screen.getByPlaceholderText('輸入你的問題…'), { key: 'Enter' })
  expect(await screen.findByRole('alert')).toHaveTextContent('無法回答此問題')
})
```

- [ ] **Step 2: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/features/ask/AskPage.test.tsx`；預期 FAIL（佔位頁）。

- [ ] **Step 3: 實作** — `src/features/ask/AskPage.tsx`：

```tsx
import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router'
import { useAskController } from '../../lib/useAskController'
import { AskEmptyState } from './AskEmptyState'
import { UserMessage } from './UserMessage'
import { AssistantMessage } from './AssistantMessage'
import { DeepReportPanel } from './DeepReportPanel'
import { SourcesDrawer } from './SourcesDrawer'
import { Composer } from './Composer'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import styles from './AskPage.module.css'

export default function AskPage() {
  const ctrl = useAskController()
  const { turns } = ctrl.state
  const [params, setParams] = useSearchParams()
  const c = params.get('c')
  const [draft, setDraft] = useState('')
  const [drawer, setDrawer] = useState<{ open: boolean; turnId: string | null }>({ open: false, turnId: null })
  const [modal, setModal] = useState<{ reportId: string | null; fileName?: string }>({ reportId: null })
  const flowRef = useRef<HTMLDivElement>(null)

  // URL ?c → 載入既有對話（或無 c → 新對話）
  useEffect(() => {
    if (c && c !== ctrl.conversationId) void ctrl.loadConversation(c)
    else if (!c && ctrl.conversationId !== null) ctrl.newConversation()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c])
  // conversationId → 同步 URL（首個 done 後）
  useEffect(() => {
    if (ctrl.conversationId && ctrl.conversationId !== c) setParams({ c: ctrl.conversationId }, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctrl.conversationId])
  // 貼底捲動：使用者在底部附近才自動貼底
  useEffect(() => {
    const el = flowRef.current
    if (!el) return
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight
  }, [turns])

  const last = turns[turns.length - 1]
  const busy = !!last && (last.phase === 'thinking' || last.phase === 'streaming')

  function handleSubmit(q: string) { ctrl.submit(q); setDraft('') }
  const drawerTurn = drawer.turnId ? turns.find(t => t.id === drawer.turnId) ?? null : null

  return (
    <div className={styles.page}>
      <div className={styles.column}>
        <div className={`${styles.flow} tf-scroll`} ref={flowRef}>
          {turns.length === 0 ? (
            <AskEmptyState value={draft} onChange={setDraft} onSubmit={handleSubmit} />
          ) : (
            turns.map(t => (
              <div key={t.id}>
                <UserMessage text={t.question} />
                <AssistantMessage
                  turn={t}
                  onCite={() => setDrawer({ open: true, turnId: t.id })}
                  onOpenSources={() => setDrawer({ open: true, turnId: t.id })}
                  onFeedback={v => t.qaId && ctrl.setFeedback(t.id, t.qaId, v)}
                  onNoticeRetry={() => setDraft(t.question)}
                  onErrorRetry={() => handleSubmit(t.question)}
                />
                <DeepReportPanel
                  report={t.report}
                  onGenerate={() => ctrl.generateReport(t.id, t.question, t.qaId)}
                  onDecline={() => ctrl.declineReport(t.id)}
                />
              </div>
            ))
          )}
        </div>
        {turns.length > 0 && (
          <div className={styles.composerBar}>
            <Composer value={draft} onChange={setDraft} onSubmit={handleSubmit} disabled={busy} />
          </div>
        )}
      </div>
      <SourcesDrawer
        open={drawer.open}
        turn={drawerTurn}
        onClose={() => setDrawer({ open: false, turnId: null })}
        onOpenReport={(reportId, fileName) => setModal({ reportId, fileName })}
      />
      <ReportDetailModal reportId={modal.reportId} fileName={modal.fileName} onClose={() => setModal({ reportId: null })} />
    </div>
  )
}
```

- [ ] **Step 4: 樣式** — `src/features/ask/AskPage.module.css`（對齊 `.dc.html` ask flow：flow max-width 760、底欄固定）：

```css
.page { display: flex; height: 100%; overflow: hidden; }
.column { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.flow { flex: 1; overflow-y: auto; padding: 20px 20px 8px; }
.flow > :first-child, .flow > div { max-width: 760px; margin-left: auto; margin-right: auto; }
.composerBar { flex: none; padding: 0 20px 18px; }
```

- [ ] **Step 5: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/features/ask/AskPage.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/features/ask/`；預期 PASS/OK。

- [ ] **Step 6: Commit**

```bash
git add src/features/ask/AskPage.tsx src/features/ask/AskPage.module.css src/features/ask/AskPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): AskPage 組合問答頁（?c 同步、貼底捲動、抽屜/詳情）

useAskController 串接；空狀態/訊息串/底部輸入框；?c↔conversationId 雙向
同步；來源抽屜 + ReportDetailModal；離題回填、失敗重試。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 16: ConversationList 刪除【批准延伸】+ 目前對話標示

**Files:**
- Create: `src/components/primitives/ConfirmDialog.tsx`, `src/components/primitives/ConfirmDialog.module.css`, `src/lib/useDeleteConversation.ts`
- Modify: `src/components/shell/ConversationList.tsx`, `src/components/shell/ConversationList.module.css`, `src/components/shell/ConversationList.test.tsx`

**Interfaces:**
- Consumes: `Modal`（primitives）、`deleteConversation`（Task 4）、`useConversations`（既有）。
- Produces: `ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel })`；`useDeleteConversation()`（react-query mutation，`onSuccess` invalidate `['conversations']`）。`ConversationList` 加：目前 `?c` 對話 `aria-current="page"` 高亮；每列垃圾桶鈕 → `ConfirmDialog`（標題「刪除此對話？」/內文「將永久移除整個對話串，無法復原。」/確認「刪除」）→ `mutate(id)`；刪到目前對話 → `navigate('/ask')`。

- [ ] **Step 1: 寫 ConfirmDialog + useDeleteConversation**

`src/components/primitives/ConfirmDialog.tsx`：

```tsx
import { Modal } from './Modal'
import styles from './ConfirmDialog.module.css'

interface Props { open: boolean; title: string; body: string; confirmLabel: string; onConfirm: () => void; onCancel: () => void }

export function ConfirmDialog({ open, title, body, confirmLabel, onConfirm, onCancel }: Props) {
  return (
    <Modal open={open} onClose={onCancel} title={title}>
      <p className={styles.body}>{body}</p>
      <div className={styles.actions}>
        <button type="button" className={styles.cancel} onClick={onCancel}>取消</button>
        <button type="button" className={styles.confirm} onClick={onConfirm}>{confirmLabel}</button>
      </div>
    </Modal>
  )
}
```

`src/components/primitives/ConfirmDialog.module.css`：

```css
.body { font-size: 14px; color: var(--tf-text-2); line-height: 1.6; margin: 0 0 16px; }
.actions { display: flex; justify-content: flex-end; gap: 8px; }
.cancel { background: var(--tf-surface); border: 1px solid var(--tf-border); border-radius: var(--tf-radius-pill); color: var(--tf-text-2); font-size: 13px; font-weight: 600; padding: 7px 18px; cursor: pointer; }
.confirm { background: var(--tf-error); border: none; border-radius: var(--tf-radius-pill); color: #fff; font-size: 13px; font-weight: 600; padding: 7px 18px; cursor: pointer; }
```

`src/lib/useDeleteConversation.ts`：

```ts
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { deleteConversation } from './askApi'

export function useDeleteConversation() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: () => { void qc.invalidateQueries({ queryKey: ['conversations'] }) },
  })
}
```

- [ ] **Step 2: 寫失敗測試** — 重寫 `src/components/shell/ConversationList.test.tsx`（保留既有清單測試 + 加刪除三分支）：

```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, fireEvent } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { afterEach, expect, test, vi } from 'vitest'

const deleteConversation = vi.fn(async () => {})
vi.mock('../../lib/askApi', () => ({ deleteConversation: (...a: unknown[]) => deleteConversation(...a) }))
import { ConversationList } from './ConversationList'

afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks() })

function wrap(entries = ['/ask']) {
  vi.stubGlobal('fetch', vi.fn(async () =>
    new Response(JSON.stringify([{ conversation_id: 'c1', title: 'AI 伺服器供應鏈' }]), { status: 200 })))
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={entries}><ConversationList /></MemoryRouter>
    </QueryClientProvider>,
  )
}

test('渲染新對話鈕與清單', async () => {
  wrap()
  expect(screen.getByRole('link', { name: '新對話' })).toHaveAttribute('href', '/ask')
  expect(await screen.findByText('AI 伺服器供應鏈')).toBeInTheDocument()
})

test('目前 ?c 對話標示 aria-current', async () => {
  wrap(['/ask?c=c1'])
  const link = await screen.findByRole('link', { name: 'AI 伺服器供應鏈' })
  expect(link).toHaveAttribute('aria-current', 'page')
})

test('刪除：確認→呼叫 deleteConversation；取消→不呼叫', async () => {
  wrap()
  await screen.findByText('AI 伺服器供應鏈')
  fireEvent.click(screen.getByRole('button', { name: '刪除對話' }))
  // 確認對話框
  expect(screen.getByText('刪除此對話？')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(deleteConversation).not.toHaveBeenCalled()
  // 再次開啟 → 確認
  fireEvent.click(screen.getByRole('button', { name: '刪除對話' }))
  fireEvent.click(screen.getByRole('button', { name: '刪除' }))
  expect(deleteConversation).toHaveBeenCalledWith('c1')
})
```

- [ ] **Step 3: 跑測試確認失敗** — `./node_modules/.bin/vitest run src/components/shell/ConversationList.test.tsx`；預期 FAIL。

- [ ] **Step 4: 改寫 ConversationList** — `src/components/shell/ConversationList.tsx`：

```tsx
import { useState } from 'react'
import { Link, useNavigate, useSearchParams } from 'react-router'
import { Icon } from '../primitives/Icon'
import { ConfirmDialog } from '../primitives/ConfirmDialog'
import { useConversations } from '../../lib/useConversations'
import { useDeleteConversation } from '../../lib/useDeleteConversation'
import styles from './ConversationList.module.css'

export function ConversationList() {
  const { data } = useConversations()
  const [params] = useSearchParams()
  const activeC = params.get('c')
  const navigate = useNavigate()
  const del = useDeleteConversation()
  const [pending, setPending] = useState<string | null>(null)

  function confirmDelete() {
    const id = pending
    if (!id) return
    setPending(null)
    del.mutate(id, { onSuccess: () => { if (id === activeC) navigate('/ask') } })
  }

  return (
    <>
      <div className={styles.newWrap}>
        <Link to="/ask" className={styles.newBtn}><Icon name="plus" size={17} /> 新對話</Link>
      </div>
      <div className={styles.heading}>歷史對話</div>
      <div className={`${styles.list} tf-scroll`}>
        {(data ?? []).map((cv) => (
          <div key={cv.conversation_id} className={styles.row}>
            <Link
              to={`/ask?c=${encodeURIComponent(cv.conversation_id)}`}
              className={`${styles.item} ${cv.conversation_id === activeC ? styles.active : ''}`}
              aria-current={cv.conversation_id === activeC ? 'page' : undefined}
              title={cv.title}
            >{cv.title}</Link>
            <button type="button" className={styles.del} aria-label="刪除對話" onClick={() => setPending(cv.conversation_id)}>
              <Icon name="trash" size={15} />
            </button>
          </div>
        ))}
      </div>
      <ConfirmDialog
        open={pending !== null}
        title="刪除此對話？"
        body="將永久移除整個對話串，無法復原。"
        confirmLabel="刪除"
        onConfirm={confirmDelete}
        onCancel={() => setPending(null)}
      />
    </>
  )
}
```

- [ ] **Step 5: 補樣式** — 在 `src/components/shell/ConversationList.module.css` 末端加（並把 `.item` 的 `display:block` 保留，改由 `.row` 佈局）：

```css
.row { display: flex; align-items: center; gap: 2px; border-radius: 8px; }
.row:hover { background: var(--tf-border-weak); }
.row .item { flex: 1; min-width: 0; }
.item.active { background: var(--tf-gold-tint); color: var(--tf-gold-text); font-weight: 600; }
.del { flex: none; border: none; background: none; color: var(--tf-text-4); border-radius: 6px; padding: 6px; cursor: pointer; opacity: 0; }
.row:hover .del, .del:focus-visible { opacity: 1; }
.del:hover { color: var(--tf-error); background: var(--tf-surface); }
```

- [ ] **Step 6: 跑測試 + 型別 + lint** — `./node_modules/.bin/vitest run src/components/shell/ConversationList.test.tsx && ./node_modules/.bin/tsc --noEmit && ./node_modules/.bin/eslint src/components/ src/lib/useDeleteConversation.ts`；預期 PASS/OK。

- [ ] **Step 7: Commit**

```bash
git add src/components/primitives/ConfirmDialog.tsx src/components/primitives/ConfirmDialog.module.css src/lib/useDeleteConversation.ts src/components/shell/ConversationList.tsx src/components/shell/ConversationList.module.css src/components/shell/ConversationList.test.tsx
git commit -m "$(cat <<'EOF'
feat(問答): 對話側欄刪除【批准延伸】+ 目前對話標示

ConfirmDialog（用 Modal）+ useDeleteConversation mutation；ConversationList
加 aria-current 高亮與垃圾桶鈕（確認→DELETE→刪目前對話轉 /ask）。
.dc.html 未畫刪除，依功能對等經批准保留。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

### Task 17: e2e — 問答 live 冒煙（Playwright :8098）

**Files:**
- Create: `e2e/ask.spec.ts`

**Interfaces:**
- Consumes: 既有 `playwright.config.ts`（`baseURL: http://127.0.0.1:8098`、`timeout: 30_000`）與 `login` 模式（`#username`/`#password`，預設 `analyst`/`test`）。

**前置（實作者需先備妥 live 後端，非測試步驟）：**
```bash
# 1) 於工作樹 build 前端（後端從磁碟 dist 服務 /app，免重啟）
cd frontend && ./node_modules/.bin/vite build
# 2) 確認 :8098 預覽後端在跑（analyst/test 帳密、report-mark-postgres 已起）；用 ss 核對埠，勿誤殺正式 :8097
ss -ltnp | grep 8098 || echo '需先啟動 :8098 預覽後端'
# 3) 首次查詢會冷啟動 BGE-M3，spec 內以長逾時吸收
```

- [ ] **Step 1: 寫 spec** — `e2e/ask.spec.ts`（研報 ~5min 用 terminal-agnostic；LLM 不確定性用寬逾時）：

```ts
import { test, expect, type Page } from '@playwright/test'

async function login(page: Page) {
  await page.goto('/login')
  await page.fill('#username', process.env.TF_USER || 'analyst')
  await page.fill('#password', process.env.TF_PW || 'test')
  await page.getByRole('button', { name: '登入' }).click()
  await page.waitForURL(u => u.pathname === '/')   // 尚未 cutover
  await page.goto('/app/ask')
}

test('問答：送出 → 串流答案 + 資料來源；多輪追問', async ({ page }) => {
  await login(page)
  const box = page.getByPlaceholderText('輸入你的問題…')
  await expect(box).toBeVisible()

  await box.fill('AI 伺服器供應鏈的受惠標的有哪些？')
  await box.press('Enter')
  // 使用者泡泡即時出現
  await expect(page.getByText('AI 伺服器供應鏈的受惠標的有哪些？')).toBeVisible()
  // 首次冷啟動 BGE-M3 + LLM：放寬逾時，等「資料來源 N」或「已思考」出現
  await expect(page.getByRole('button', { name: /資料來源 \d+/ })).toBeVisible({ timeout: 180_000 })

  // 開來源抽屜
  await page.getByRole('button', { name: /資料來源 \d+/ }).first().click()
  await expect(page.getByText('引用來源')).toBeVisible()
  await page.getByRole('button', { name: '關閉' }).click()

  // 多輪追問（等輸入框可用＝串流結束）
  await expect(box).toBeEnabled({ timeout: 180_000 })
  await box.fill('那散熱類股呢？')
  await box.press('Enter')
  await expect(page.getByText('那散熱類股呢？')).toBeVisible()
  await expect(page.getByRole('button', { name: /資料來源 \d+/ }).nth(1)).toBeVisible({ timeout: 180_000 })
})

test('離題 → 警示卡（Callout warning）', async ({ page }) => {
  await login(page)
  const box = page.getByPlaceholderText('輸入你的問題…')
  await box.fill('今天台北天氣如何？')
  await box.press('Enter')
  // 離題回覆或（保守）任一助理輸出；以警示卡為主
  await expect(page.getByRole('alert')).toBeVisible({ timeout: 180_000 })
})
```

- [ ] **Step 2: 執行 e2e（需 :8098 live）** — `./node_modules/.bin/playwright test e2e/ask.spec.ts --timeout=200000`；預期問答兩案通過（研報流程因 ~5min 不納入自動 e2e；如需驗證，手動於 :8098 按「要」後觀察 done 卡或失敗 Callout，屬 terminal-agnostic）。若冷啟動導致首案 flake，先於瀏覽器手動送一問暖機再重跑。

- [ ] **Step 3: Commit**

```bash
git add e2e/ask.spec.ts
git commit -m "$(cat <<'EOF'
test(問答): e2e live 冒煙（串流答案/來源抽屜/多輪/離題）

Playwright :8098 live；長逾時吸收 BGE-M3 冷啟動與 LLM 延遲；研報 ~5min
不納自動 e2e（terminal-agnostic 手動驗）。
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 自我審查（writing-plans self-review）

**Spec coverage（spec §→task）：**
- §1 契約（ask/report/conversations/feedback/report-doc） → Task 3/4（schema+api）、消費於 8/9/12/13/16。
- §2 架構/狀態（reducer+react-query、latest-wins 消費層） → Task 8/9。
- §3 元件（Composer/ThinkingSteps/UserMessage/AssistantMessage/SourcesDrawer/DeepReportPanel/Callout、lib、Icon/tokens、重用 ReportDetailModal/ConversationList） → Task 1/5/6/7/10/11/12/13/14/15/16。
- §4 資料流（送出/研報/載入既有/側欄/回饋） → Task 9（控制器）、15（AskPage）、16（側欄/刪除）。
- §5 SSE readSSE → Task 2。§6 markdown → Task 7。§7 mockup 調適（思考步驟由 stage、研報里程碑 %、完成卡不寫死頁數、抽屜取代行內） → Task 5/6/11/12/13。
- §8 Callout error/warning → Task 1，套用於 13/14。§9 文案 → 逐 task 逐字。§10 測試（含刪除三分支、latest-wins 整合、單一資料來源 {N}） → 各 task 測試 + Task 9/16。§11 非目標（後端零改動、不 inline 研報、全語料、不 cutover、離題不 feedback） → 全程；AskPage/askReducer 落實。

**Placeholder scan：** 無 TBD/TODO；每步含實際程式碼與指令。

**Type consistency：** `Source`/`ExtSource`/`AskStage`/`ReportStage`/`AskEvent`/`ReportEvent`/`AskDone`/`ReportDone`/`ConversationTurn` 定義於 Task 3，Task 8/9 一致引用；`Turn`/`ReportState`/`AskState`/`AskAction`/`askReducer`/`initialAskState`/`turnFromHistory` 定義於 Task 8，Task 9/11/12/13/14/15 一致引用；`Composer` 受控簽章（Task 10 修訂）與 Task 14/15 呼叫一致；`ReportDetailModal({reportId,fileName,onClose})`、`marketColor/marketLabel`、`getJSON`、`useConversations` 皆對齊既有碼。

**已知延伸/取捨（交由最終審查裁量）：** 刪除對話為【批准延伸】（.dc.html 未畫，經使用者批准）；研報自動 e2e 略過（~5min，改手動 terminal-agnostic）；markdown 串流每 token 重渲染的 O(n²) 於現長度可接受（spec 已記）。

---

## 執行交接

Plan complete and saved to `docs/superpowers/plans/2026-07-04-react-spa-phase2-ask-report.md`. Two execution options:

1. **Subagent-Driven（建議）** — 每任務派新 subagent、任務間審查、快速迭代。
2. **Inline Execution** — 於本 session 依 executing-plans 批次執行、檢查點審查。

Which approach?
