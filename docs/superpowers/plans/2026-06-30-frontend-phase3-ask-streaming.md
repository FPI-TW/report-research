# Phase 3 — 問答串流（ask）vanilla → React 遷移 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 vanilla 問答（`web/static/app/ask.js` 等）遷移為 React 功能模組，掛 `/app/ask`、與舊頁平價共存（不 cutover）、後端零變動。

**Architecture:** 兩層分法（延續 search feature）：純邏輯置 `frontend/src/features/ask/lib/`（SSE 解析、markdown→JSX、conversation 模型）各帶同層單元測試；React 元件 + `useAskStream`（即時串流、AbortController + 單調序號雙重 latest-wins）+ `useConversations`（TanStack Query 歷史側欄）。SSE 為 POST + text/event-stream，以 fetch reader 手解（EventSource 僅 GET）。

**Tech Stack:** React 19.2.7、Vite 8、react-router 8（basename `/app`）、Mantine 9、TanStack Query 5.101、Zod 4、Vitest 4、TypeScript、Playwright。

## Global Constraints

每個任務隱含適用（值逐字取自 spec §8）：

- 後端 `web/server.py`、`app/**`、`db/**` **零變動**；無 schema 變動。
- 版本下限：React 19.2.7、TanStack Query 5.101、Zod 4、Mantine 9、Node 22.22+；TypeScript 嚴格、`tsc --noEmit` 須過。
- Zod 選用欄一律 `.nullish()`（後端可回 null）。
- **無 `dangerouslySetInnerHTML`**；markdown 走純節點（React 自動 escape 文字節點）。
- **不引入 Zustand/Redux**；UI 狀態用 React state/hook，伺服器狀態用 TanStack Query。
- SSE 串流自管連線 + `AbortController` + 單調序號 latest-wins；串流本身不進 Query 快取。
- 不 cutover；掛 `/app/ask`；舊 `/` 問答保留。
- 提交走 Conventional Commits + 繁中 scope，結尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`；共用工作目錄，stage **明確路徑**（勿 `git add -A`/`.`）。
- 驗證：`cd frontend && npx vitest run src`、`npm run build`、`npm run lint`；**背景跑 vitest 經 RTK 會遮非零 exit → 用 `rtk proxy npx vitest` 取真實結果**。
- 分支：`feat/frontend-phase3-ask-streaming`（已從 main `89a0d8e` 開、spec 已提交 `b4cbdd7`）。

### 權威 SSE 事件契約（唯讀對照，來自 `app/services/answer.py`）

幀格式 `event: <name>\ndata: <json>\n\n`，幀間以 `\n\n` 分隔。Happy：`status{stage:understanding}` → `sources[]` → `status{stage:retrieved,count}` → `status{stage:reading}` →（條件）`status{stage:searching_web}` → `status{stage:generating,thinking_ms}` → 多個 `token"<字串>"` → `ext_sources[]` → `done{cited,qa_id,conversation_id,thinking_ms,offer_report,report_title}`。離題：`sources[]`→`notice"<msg>"`→`done{cited:[],conversation_id,thinking_ms}`（**無 qa_id**）。無脈絡：`sources[]`→`status generating`→`token"<msg>"`→`done{...,qa_id}`。錯誤：`error{detail}`。要點：`token` 的 data 是 **JSON 字串**、其餘是物件/陣列；終止永遠是 `done`/`error`；`done` 必帶 `conversation_id`；完整答案可能以**單一大 token** 到達（不可假設增量）。

`SourceItem = {n:int, report_id:str, file_name:str, market:str|null, report_date:str|null, is_latest:bool}`。
`ExtSource = {title:str|null, url:str}`。

---

## Task 1: SSE 事件型別 + parseFrame

**Files:**
- Create: `frontend/src/features/ask/lib/sseEvents.ts`
- Create: `frontend/src/features/ask/lib/sse.ts`
- Test: `frontend/src/features/ask/lib/sse.test.ts`

**Interfaces:**
- Produces: `AskEvent`（discriminated union on `event`）、`SourceItem`、`ExtSource`、`DonePayload`、`StatusPayload`、`parseFrame(frame: string): AskEvent | null`。

- [ ] **Step 1: 寫失敗測試** `frontend/src/features/ask/lib/sse.test.ts`

```ts
import { describe, expect, test } from 'vitest'
import { parseFrame } from './sse'

describe('parseFrame', () => {
  test('解析 token 事件（data 為 JSON 字串）', () => {
    expect(parseFrame('event: token\ndata: "台積電"')).toEqual({ event: 'token', data: '台積電' })
  })
  test('解析 sources 事件（data 為陣列）', () => {
    const f = 'event: sources\ndata: [{"n":1,"report_id":"r1","file_name":"a.pdf","market":"TW","report_date":null,"is_latest":true}]'
    expect(parseFrame(f)).toEqual({
      event: 'sources',
      data: [{ n: 1, report_id: 'r1', file_name: 'a.pdf', market: 'TW', report_date: null, is_latest: true }],
    })
  })
  test('解析 done 事件物件', () => {
    const f = 'event: done\ndata: {"cited":["r1"],"qa_id":"q1","conversation_id":"c1","thinking_ms":1200,"offer_report":false}'
    expect(parseFrame(f)?.event).toBe('done')
  })
  test('多行 data 串接後再 JSON.parse', () => {
    expect(parseFrame('event: token\ndata: "ab"')).toEqual({ event: 'token', data: 'ab' })
  })
  test('無 data 行回 null', () => {
    expect(parseFrame('event: ping')).toBeNull()
  })
  test('壞 JSON 回 null', () => {
    expect(parseFrame('event: token\ndata: {不是json')).toBeNull()
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/lib/sse.test.ts`
Expected: FAIL（`parseFrame` 未定義 / 模組不存在）

- [ ] **Step 3: 寫型別 `sseEvents.ts`**

```ts
export interface SourceItem {
  n: number
  report_id: string
  file_name: string
  market: string | null
  report_date: string | null
  is_latest: boolean
}

export interface ExtSource {
  title?: string | null
  url: string
}

export interface StatusPayload {
  stage: string
  count?: number
  thinking_ms?: number
}

export interface DonePayload {
  cited?: string[]
  qa_id?: string | null
  conversation_id?: string | null
  thinking_ms?: number | null
  offer_report?: boolean
  report_title?: string | null
}

export type AskEvent =
  | { event: 'status'; data: StatusPayload }
  | { event: 'sources'; data: SourceItem[] }
  | { event: 'ext_sources'; data: ExtSource[] }
  | { event: 'token'; data: string }
  | { event: 'notice'; data: string }
  | { event: 'done'; data: DonePayload }
  | { event: 'error'; data: { detail?: string } }
```

- [ ] **Step 4: 寫 `parseFrame`（移植 ask.js:20-28）`sse.ts`**

```ts
import type { AskEvent } from './sseEvents'

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
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/lib/sse.test.ts`
Expected: PASS（6 案）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/ask/lib/sseEvents.ts frontend/src/features/ask/lib/sse.test.ts frontend/src/features/ask/lib/sse.ts
git commit -m "$(cat <<'EOF'
feat(ask): SSE 事件型別與 parseFrame（Phase 3 Task 1）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: streamAsk async generator

**Files:**
- Modify: `frontend/src/features/ask/lib/sse.ts`（append `streamAsk`）
- Test: `frontend/src/features/ask/lib/sse.test.ts`（append）

**Interfaces:**
- Consumes: `parseFrame`、`AskEvent`、`redirectToLogin`/`ApiError`（`frontend/src/lib/api.ts`）。
- Produces: `async function* streamAsk(body: object, signal: AbortSignal): AsyncGenerator<AskEvent>`。POST `/api/ask`，依 `\n\n` 切幀 yield 事件。401 → `redirectToLogin()` + throw `ApiError(401)`；`!resp.ok || !resp.body` → throw `Error('bad response')`。

- [ ] **Step 1: 寫失敗測試（append 到 sse.test.ts）**

```ts
import { streamAsk } from './sse'

/** 把字串陣列做成可控的 ReadableStream（每個 chunk 一段，模擬跨 chunk 切幀）。 */
function streamFrom(chunks: string[]): ReadableStream<Uint8Array> {
  const enc = new TextEncoder()
  let i = 0
  return new ReadableStream({
    pull(ctrl) {
      if (i < chunks.length) ctrl.enqueue(enc.encode(chunks[i++]))
      else ctrl.close()
    },
  })
}

async function collect(gen: AsyncGenerator<unknown>): Promise<unknown[]> {
  const out: unknown[] = []
  for await (const e of gen) out.push(e)
  return out
}

describe('streamAsk', () => {
  test('yield happy 事件序列，含跨 chunk 切幀', async () => {
    const body = streamFrom([
      'event: sources\ndata: []\n\n',
      'event: token\ndata: "你好"\n', // 幀被切在中間
      '\nevent: done\ndata: {"conversation_id":"c1"}\n\n',
    ])
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, body })
    const events = await collect(streamAsk({ question: 'q' }, new AbortController().signal))
    expect(events).toEqual([
      { event: 'sources', data: [] },
      { event: 'token', data: '你好' },
      { event: 'done', data: { conversation_id: 'c1' } },
    ])
  })

  test('單一大 token（無增量）也能 yield', async () => {
    const body = streamFrom(['event: token\ndata: "整段答案"\n\nevent: done\ndata: {}\n\n'])
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, body })
    const events = await collect(streamAsk({ question: 'q' }, new AbortController().signal))
    expect(events[0]).toEqual({ event: 'token', data: '整段答案' })
  })

  test('401 觸發導向登入並拋 ApiError', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, body: null })
    const assign = vi.spyOn(window.location, 'assign').mockImplementation(() => {})
    await expect(collect(streamAsk({ question: 'q' }, new AbortController().signal))).rejects.toMatchObject({ status: 401 })
    expect(assign).toHaveBeenCalled()
  })

  test('非 OK 回應拋錯', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 500, body: null })
    await expect(collect(streamAsk({ question: 'q' }, new AbortController().signal))).rejects.toThrow()
  })
})
```

（檔頭補 `import { vi } from 'vitest'`。）

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/lib/sse.test.ts`
Expected: FAIL（`streamAsk` 未定義）

- [ ] **Step 3: 寫 `streamAsk`（移植 ask.js:632-663 的 reader 迴圈為 generator）append 到 sse.ts**

```ts
import { ApiError, redirectToLogin } from '../../../lib/api'

/**
 * POST /api/ask（text/event-stream），以 fetch reader 手解 SSE，yield 型別化事件。
 * 連線/abort 由呼叫端透過 signal 管理。401 → 導向登入並拋；非 OK/無 body → 拋。
 */
export async function* streamAsk(body: object, signal: AbortSignal): AsyncGenerator<AskEvent> {
  const resp = await fetch('/api/ask', {
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
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/lib/sse.test.ts`
Expected: PASS（含 Task 1 共 10 案）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ask/lib/sse.ts frontend/src/features/ask/lib/sse.test.ts
git commit -m "$(cat <<'EOF'
feat(ask): streamAsk fetch-reader SSE generator（Phase 3 Task 2）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: markdown 行內解析 → ReactNode[]

**Files:**
- Create: `frontend/src/features/ask/lib/markdown.tsx`
- Test: `frontend/src/features/ask/lib/markdown.test.tsx`

**Interfaces:**
- Produces: `inline(text: string, maxCite: number, onCite?: (n: number) => void): ReactNode[]`（供 Task 4 區塊解析呼叫）。
- 安全模型：**不手動 esc**——React 自動 escape 文字節點；只產生已知元件（`<code>`/`<a>`/`<strong>`/`<em>` + cite chip）。連結僅 `http(s)`。`[n]` 僅在 `1..maxCite` 轉 chip。

說明：vanilla 以「序列 global replace（link→bold→italic→cite）」於 escaped 字串上運作（ask.js:16-42、markdown.js:16-42），故內層內容會被後續較低優先樣式套用。本移植以**遞迴節點化**等價達成：先以反引號切出行內碼（碼內不處理），其餘片段以「找最早出現的樣式 → 文字 + 節點 + 遞迴剩餘」處理；matched 內層內容遞迴套用較低優先樣式（link 內可含 bold/italic/cite；bold 內可含 italic/cite；italic 內可含 cite）。

- [ ] **Step 1: 寫失敗測試 `markdown.test.tsx`**

```tsx
import { describe, expect, test, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { Fragment, createElement } from 'react'
import { inline } from './markdown'

const html = (nodes: ReturnType<typeof inline>) =>
  renderToStaticMarkup(createElement(Fragment, null, ...nodes))

describe('inline', () => {
  test('純文字原樣（React 自動 escape）', () => {
    expect(html(inline('a < b & c', 0))).toBe('a &lt; b &amp; c')
  })
  test('行內碼不做樣式且內容跳脫', () => {
    expect(html(inline('用 `a<b` 比較', 0))).toBe('用 <code>a&lt;b</code> 比較')
  })
  test('粗體與斜體', () => {
    expect(html(inline('**粗** 與 *斜*', 0))).toBe('<strong>粗</strong> 與 <em>斜</em>')
  })
  test('http 連結 target/rel', () => {
    expect(html(inline('看 [連結](https://a.com/x) 吧', 0))).toBe(
      '看 <a href="https://a.com/x" target="_blank" rel="noopener">連結</a> 吧',
    )
  })
  test('[n] 在來源範圍內轉 chip，超出維持原樣', () => {
    expect(html(inline('見 [1] 與 [9]', 3))).toContain('class="cite"')
    expect(html(inline('見 [1] 與 [9]', 3))).toContain('[9]')
    expect(html(inline('見 [1] 與 [9]', 3))).not.toContain('data-n="9"')
  })
  test('maxCite=0 時 [1] 不轉 chip', () => {
    expect(html(inline('看 [1]', 0))).toBe('看 [1]')
  })
  test('cite chip 點擊呼叫 onCite(n)', () => {
    const onCite = vi.fn()
    const nodes = inline('見 [2]', 3, onCite)
    // 找出 chip 節點並觸發其 onClick
    const chip = nodes.find(
      (n): n is React.ReactElement<{ onClick?: () => void }> =>
        !!n && typeof n === 'object' && 'props' in n && (n as { props?: { className?: string } }).props?.className === 'cite',
    )
    chip?.props.onClick?.()
    expect(onCite).toHaveBeenCalledWith(2)
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/lib/markdown.test.tsx`
Expected: FAIL（`inline` 未定義）

- [ ] **Step 3: 寫 `inline` `markdown.tsx`**

```tsx
import type { ReactNode } from 'react'

const LINK_RE = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/
const BOLD_RE = /\*\*([^*]+)\*\*/
const ITALIC_RE = /\*([^*\n]+)\*/
const CITE_RE = /\[(\d{1,3})\]/

type Pat = 'link' | 'bold' | 'italic' | 'cite'
const ORDER: Pat[] = ['link', 'bold', 'italic', 'cite'] // 與 vanilla 序列 replace 同優先序
const RE: Record<Pat, RegExp> = { link: LINK_RE, bold: BOLD_RE, italic: ITALIC_RE, cite: CITE_RE }
// 每種樣式的內層可再套用「較低優先」的樣式（不含自身與更高優先者）
const INNER: Record<Pat, Pat[]> = {
  link: ['bold', 'italic', 'cite'],
  bold: ['italic', 'cite'],
  italic: ['cite'],
  cite: [],
}

let keySeq = 0
function k(): number {
  keySeq += 1
  return keySeq
}

/** 在 text 上，依 pats 優先序找「最早出現」的樣式並節點化；遞迴處理內層與剩餘。 */
function renderPart(text: string, pats: Pat[], maxCite: number, onCite?: (n: number) => void): ReactNode[] {
  let best: { pat: Pat; m: RegExpExecArray } | null = null
  for (const pat of pats) {
    const m = RE[pat].exec(text)
    if (!m) continue
    if (pat === 'cite') {
      const n = parseInt(m[1], 10)
      if (!(n >= 1 && n <= maxCite)) continue // 超範圍：不視為樣式
    }
    if (!best || m.index < best.m.index) best = { pat, m }
  }
  if (!best) return [text]
  const { pat, m } = best
  const out: ReactNode[] = []
  if (m.index > 0) out.push(text.slice(0, m.index))
  const innerPats = pats.filter((p) => INNER[pat].includes(p))
  if (pat === 'link') {
    out.push(
      <a key={k()} href={m[2]} target="_blank" rel="noopener">
        {renderPart(m[1], innerPats, maxCite, onCite)}
      </a>,
    )
  } else if (pat === 'bold') {
    out.push(<strong key={k()}>{renderPart(m[1], innerPats, maxCite, onCite)}</strong>)
  } else if (pat === 'italic') {
    out.push(<em key={k()}>{renderPart(m[1], innerPats, maxCite, onCite)}</em>)
  } else {
    const n = parseInt(m[1], 10)
    out.push(
      <a
        key={k()}
        className="cite"
        role="button"
        tabIndex={0}
        title={`查看來源 ${n}`}
        onClick={() => onCite?.(n)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onCite?.(n)
          }
        }}
      >
        [{n}]
      </a>,
    )
  }
  const rest = text.slice(m.index + m[0].length)
  if (rest) out.push(...renderPart(rest, pats, maxCite, onCite))
  return out
}

/** 行內：先以反引號切出行內碼（碼內不處理），其餘節點化。React 自動 escape 文字節點。 */
export function inline(text: string, maxCite: number, onCite?: (n: number) => void): ReactNode[] {
  const out: ReactNode[] = []
  for (const seg of text.split(/(`[^`]+`)/g)) {
    if (seg.length >= 2 && seg.startsWith('`') && seg.endsWith('`')) {
      out.push(<code key={k()}>{seg.slice(1, -1)}</code>)
    } else if (seg) {
      out.push(...renderPart(seg, ORDER, maxCite, onCite))
    }
  }
  return out
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/lib/markdown.test.tsx`
Expected: PASS（7 案）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ask/lib/markdown.tsx frontend/src/features/ask/lib/markdown.test.tsx
git commit -m "$(cat <<'EOF'
feat(ask): markdown 行內解析為純函式 JSX（Phase 3 Task 3）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: markdown 區塊渲染 renderMarkdown → ReactNode[]

**Files:**
- Modify: `frontend/src/features/ask/lib/markdown.tsx`（append `normalize` + `renderMarkdown`）
- Test: `frontend/src/features/ask/lib/markdown.test.tsx`（append）

**Interfaces:**
- Consumes: `inline`（Task 3）。
- Produces: `renderMarkdown(md: string, maxCite?: number, onCite?: (n: number) => void): ReactNode[]`。移植 markdown.js:50-172（normalize 黏行 ATX、區塊：fenced code 含 chart/kpi 佔位、h1-h6 clamp h4、blockquote、table、ul/ol、hr、段落）。

- [ ] **Step 1: 寫失敗測試（append 到 markdown.test.tsx）**

```tsx
import { renderMarkdown } from './markdown'

const md = (s: string, maxCite = 0) =>
  renderToStaticMarkup(createElement(Fragment, null, ...renderMarkdown(s, maxCite)))

describe('renderMarkdown', () => {
  test('h1~h6 夾到 h4', () => {
    expect(md('# 一')).toBe('<h1>一</h1>')
    expect(md('##### 五')).toBe('<h4>五</h4>')
  })
  test('黏行 ATX 標題前補換行（CJK 句末後）', () => {
    expect(md('收盤價。## 緯創')).toBe('<p>收盤價。</p><h2>緯創</h2>')
  })
  test('C# 與 #1 不被誤切/誤判標題', () => {
    expect(md('用 C# 開發')).toBe('<p>用 C# 開發</p>')
    expect(md('#1 名')).toBe('<p>#1 名</p>')
  })
  test('無序與有序清單', () => {
    expect(md('- a\n- b')).toBe('<ul><li>a</li><li>b</li></ul>')
    expect(md('1. a\n2. b')).toBe('<ol><li>a</li><li>b</li></ol>')
  })
  test('引用、分隔線、表格', () => {
    expect(md('> 引言')).toBe('<blockquote>引言</blockquote>')
    expect(md('---')).toBe('<hr/>')
    expect(md('| A | B |\n|---|---|\n| 1 | 2 |')).toContain('<table class="md-table">')
  })
  test('一般程式碼區塊跳脫', () => {
    expect(md('```\na<b\n```')).toBe('<pre><code>a&lt;b</code></pre>')
  })
  test('chart/kpi 圍欄顯示佔位（live 預覽）', () => {
    expect(md('```chart\n{"title":"營收"}\n```')).toBe('<p class="md-chart-ph">（圖表：營收）</p>')
    expect(md('```kpi\n{}\n```')).toBe('<p class="md-chart-ph">（重點數據）</p>')
  })
  test('段落內換行轉 <br>', () => {
    expect(md('一\n二')).toBe('<p>一<br/>二</p>')
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/lib/markdown.test.tsx`
Expected: FAIL（`renderMarkdown` 未定義）

- [ ] **Step 3: 寫 `normalize` + 區塊助手 + `renderMarkdown`（移植 markdown.js:50-172）append 到 markdown.tsx**

> 區塊產生 `ReactNode[]`；表格/清單儲存格內容走 `inline()`。`normalize`/`splitRow`/`isBlockStart` 的 regex 逐字沿用 markdown.js（含 CJK 範圍 `一-鿿`、護欄）。段落內 `\n` 以 `<br/>` 呈現：把 `inline()` 結果以換行切片、片段間插 `<br/>`。

```tsx
function normalize(mdSrc: string): string {
  return mdSrc
    .split(/(```[\s\S]*?```)/g)
    .map((seg, idx) =>
      idx % 2 === 1
        ? seg
        : seg.replace(/([一-鿿。！？：；、，）】」』.!?:;])[ \t]*(#{1,6}(?:[ \t]+|(?=[一-鿿]))\S)/g, '$1\n$2'),
    )
    .join('')
}

function splitRow(line: string): string[] {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
}

function isBlockStart(line: string): boolean {
  return (
    /^```/.test(line.trim()) ||
    /^#{1,6}(?:[ \t]+|(?=[一-鿿]))/.test(line) ||
    /^\s*[-*•]\s+/.test(line) ||
    /^\s*\d+[.)]\s+/.test(line) ||
    /^\s*>\s?/.test(line) ||
    /^\s*([-*_])\1{2,}\s*$/.test(line)
  )
}

/** 把 inline 結果依「\n」切成多段、段間插 <br/>（對齊 vanilla 段落 .replace(/\n/g,'<br>')）。 */
function withBreaks(text: string, maxCite: number, onCite?: (n: number) => void): ReactNode[] {
  const lines = text.split('\n')
  const out: ReactNode[] = []
  lines.forEach((ln, i) => {
    if (i > 0) out.push(<br key={k()} />)
    out.push(...inline(ln, maxCite, onCite))
  })
  return out
}

export function renderMarkdown(mdSrc: string, maxCite = 0, onCite?: (n: number) => void): ReactNode[] {
  const lines = normalize(String(mdSrc == null ? '' : mdSrc).replace(/\r\n?/g, '\n')).split('\n')
  const out: ReactNode[] = []
  const N = lines.length
  let i = 0
  while (i < N) {
    const line = lines[i]
    if (/^```/.test(line.trim())) {
      const lang = line.trim().slice(3).trim()
      const buf: string[] = []
      i++
      while (i < N && !/^```/.test(lines[i].trim())) buf.push(lines[i++])
      i++
      if (lang === 'chart') {
        let title = ''
        try {
          title = String((JSON.parse(buf.join('\n')) as { title?: unknown }).title || '')
        } catch {
          /* 串流中 JSON 未完 */
        }
        out.push(
          <p key={k()} className="md-chart-ph">
            {title ? `（圖表：${title}）` : '（圖表）'}
          </p>,
        )
      } else if (lang === 'kpi') {
        out.push(
          <p key={k()} className="md-chart-ph">
            （重點數據）
          </p>,
        )
      } else {
        out.push(
          <pre key={k()}>
            <code>{buf.join('\n')}</code>
          </pre>,
        )
      }
      continue
    }
    if (!line.trim()) {
      i++
      continue
    }
    if (/^\s*([-*_])\1{2,}\s*$/.test(line)) {
      out.push(<hr key={k()} />)
      i++
      continue
    }
    const h = line.match(/^(#{1,6})(?:[ \t]+|(?=[一-鿿]))(\S.*)$/)
    if (h) {
      const lvl = Math.min(h[1].length, 4)
      const Tag = (`h${lvl}` as 'h1' | 'h2' | 'h3' | 'h4')
      out.push(<Tag key={k()}>{inline(h[2].trim(), maxCite, onCite)}</Tag>)
      i++
      continue
    }
    if (/^\s*>\s?/.test(line)) {
      const buf: string[] = []
      while (i < N && /^\s*>\s?/.test(lines[i])) buf.push(lines[i++].replace(/^\s*>\s?/, ''))
      out.push(<blockquote key={k()}>{inline(buf.join(' '), maxCite, onCite)}</blockquote>)
      continue
    }
    if (
      line.includes('|') &&
      i + 1 < N &&
      lines[i + 1].includes('-') &&
      /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1])
    ) {
      const header = splitRow(line)
      i += 2
      const rows: string[][] = []
      while (i < N && lines[i].trim() && lines[i].includes('|')) rows.push(splitRow(lines[i++]))
      out.push(
        <table key={k()} className="md-table">
          <thead>
            <tr>{header.map((c, j) => <th key={j}>{inline(c, maxCite, onCite)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((r, ri) => (
              <tr key={ri}>{header.map((_, j) => <td key={j}>{inline(r[j] || '', maxCite, onCite)}</td>)}</tr>
            ))}
          </tbody>
        </table>,
      )
      continue
    }
    if (/^\s*[-*•]\s+/.test(line)) {
      const buf: string[] = []
      while (i < N && /^\s*[-*•]\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*[-*•]\s+/, ''))
      out.push(<ul key={k()}>{buf.map((it, li) => <li key={li}>{inline(it, maxCite, onCite)}</li>)}</ul>)
      continue
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      const buf: string[] = []
      while (i < N && /^\s*\d+[.)]\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ''))
      out.push(<ol key={k()}>{buf.map((it, li) => <li key={li}>{inline(it, maxCite, onCite)}</li>)}</ol>)
      continue
    }
    const buf = [line]
    i++
    while (i < N && lines[i].trim() && !isBlockStart(lines[i])) buf.push(lines[i++])
    out.push(<p key={k()}>{withBreaks(buf.join('\n'), maxCite, onCite)}</p>)
  }
  return out
}
```

> 註：`renderMarkdown` 的 heading 用 `const Tag = ('h' + lvl) as 'h1' | 'h2' | 'h3' | 'h4'` 再 `<Tag>`；若 tsc 對動態 intrinsic 標籤報錯，改寫成對 lvl 的 switch 回傳對應 `<h1>…<h4>`。`markdown.tsx` 僅需 `import type { ReactNode } from 'react'`（Task 3 已加），不需其他 react import。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/lib/markdown.test.tsx`
Expected: PASS（含 Task 3 共 15 案）

- [ ] **Step 5: typecheck + lint 本檔**

Run: `cd frontend && npx tsc --noEmit && npx eslint src/features/ask/lib/markdown.tsx`
Expected: 0 error（如報未用 import/變數，移除之）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/ask/lib/markdown.tsx frontend/src/features/ask/lib/markdown.test.tsx
git commit -m "$(cat <<'EOF'
feat(ask): markdown 區塊渲染為純函式 JSX（Phase 3 Task 4）

normalize 黏行 ATX、fenced code/chart/kpi 佔位、h1-h6 clamp h4、
blockquote/table/list/hr/段落（<br/>），全程無 dangerouslySetInnerHTML。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: schemas + api（會話清單/重播/刪除/回饋）

**Files:**
- Create: `frontend/src/features/ask/schemas.ts`
- Create: `frontend/src/features/ask/api.ts`
- Test: `frontend/src/features/ask/api.test.ts`

**Interfaces:**
- Consumes: `getJSON`/`ApiError`/`redirectToLogin`（`frontend/src/lib/api.ts`）、`SourceItem`/`ExtSource`（`lib/sseEvents.ts`）。
- Produces:
  - schemas：`conversationSummarySchema`、`historyItemSchema`、`ConversationSummary`、`HistoryItem`。
  - api：`getConversations(): Promise<ConversationSummary[]>`、`getConversation(id: string): Promise<HistoryItem[]>`、`deleteConversation(id: string): Promise<{ ok: boolean }>`（DELETE→404/405 退回 POST `/delete`）、`sendFeedback(qaId: string, value: 'like' | 'dislike'): Promise<void>`（best-effort）。

- [ ] **Step 1: 寫失敗測試 `api.test.ts`**

```ts
import { describe, expect, test, vi, afterEach } from 'vitest'
import { conversationSummarySchema, historyItemSchema } from './schemas'
import { getConversations, deleteConversation } from './api'

afterEach(() => vi.restoreAllMocks())

describe('schemas', () => {
  test('conversationSummary 解析', () => {
    expect(() =>
      conversationSummarySchema.parse({ conversation_id: 'c1', title: 'Q', last_at: '2026-01-01', turn_count: 2 }),
    ).not.toThrow()
  })
  test('historyItem 容忍 null 選用欄', () => {
    const parsed = historyItemSchema.parse({
      id: 'q1', question: 'Q', answer: null, created_at: null, feedback: null,
      sources: [], ext_sources: [], is_offtopic: null, thinking_ms: null, reports: null,
    })
    expect(parsed.sources).toEqual([])
  })
})

describe('api', () => {
  test('getConversations 打對 endpoint 並回陣列', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true, status: 200,
      json: async () => [{ conversation_id: 'c1', title: 'Q', last_at: null, turn_count: 1 }],
    })
    const r = await getConversations()
    expect(r).toHaveLength(1)
    expect((globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][0]).toContain('/api/conversations')
  })

  test('deleteConversation：DELETE 成功', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ ok: true }) })
    await expect(deleteConversation('c1')).resolves.toEqual({ ok: true })
  })

  test('deleteConversation：404 退回 POST /delete', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({ ok: false, status: 404, json: async () => ({}) })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true }) })
    globalThis.fetch = fetchMock
    await expect(deleteConversation('c1')).resolves.toEqual({ ok: true })
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(fetchMock.mock.calls[0][1].method).toBe('DELETE')
    expect(fetchMock.mock.calls[1][0]).toContain('/delete')
    expect(fetchMock.mock.calls[1][1].method).toBe('POST')
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/api.test.ts`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 寫 `schemas.ts`**

```ts
import { z } from 'zod'

export const sourceSchema = z.object({
  n: z.number().int(),
  report_id: z.string(),
  file_name: z.string(),
  market: z.string().nullish(),
  report_date: z.string().nullish(),
  is_latest: z.boolean().nullish(),
})

export const extSourceSchema = z.object({
  title: z.string().nullish(),
  url: z.string(),
})

export const conversationSummarySchema = z.object({
  conversation_id: z.string(),
  title: z.string(),
  last_at: z.string().nullish(),
  turn_count: z.number().int().nonnegative().nullish(),
})
export type ConversationSummary = z.infer<typeof conversationSummarySchema>

export const historyItemSchema = z.object({
  id: z.string(),
  question: z.string(),
  answer: z.string().nullish(),
  created_at: z.string().nullish(),
  feedback: z.string().nullish(),
  sources: z.array(sourceSchema).nullish(),
  ext_sources: z.array(extSourceSchema).nullish(),
  is_offtopic: z.boolean().nullish(),
  thinking_ms: z.number().nullish(),
  reports: z.array(z.unknown()).nullish(),
})
export type HistoryItem = z.infer<typeof historyItemSchema>

export const okSchema = z.object({ ok: z.boolean() })
```

- [ ] **Step 4: 寫 `api.ts`**

```ts
import { z } from 'zod'
import { ApiError, getJSON, redirectToLogin } from '../../lib/api'
import { conversationSummarySchema, historyItemSchema, type ConversationSummary, type HistoryItem } from './schemas'

export function getConversations(): Promise<ConversationSummary[]> {
  return getJSON('/api/conversations?limit=50', z.array(conversationSummarySchema), { cache: 'no-store' })
}

export function getConversation(id: string): Promise<HistoryItem[]> {
  return getJSON(`/api/conversations/${encodeURIComponent(id)}`, z.array(historyItemSchema))
}

/** DELETE /api/conversations/{id}；404/405 退回 POST /delete（移植 ask.js:231-236）。 */
export async function deleteConversation(id: string): Promise<{ ok: boolean }> {
  const path = `/api/conversations/${encodeURIComponent(id)}`
  let resp = await fetch(path, { method: 'DELETE', credentials: 'same-origin' })
  if (resp.status === 404 || resp.status === 405) {
    resp = await fetch(`${path}/delete`, { method: 'POST', credentials: 'same-origin' })
  }
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  const data = (await resp.json().catch(() => ({}))) as { ok?: boolean }
  if (!resp.ok || !data.ok) throw new ApiError(resp.status, '刪除失敗')
  return { ok: true }
}

/** 回饋為 best-effort，失敗不打擾使用者（移植 ask.js:556-568）。 */
export async function sendFeedback(qaId: string, value: 'like' | 'dislike'): Promise<void> {
  try {
    await fetch('/api/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'same-origin',
      body: JSON.stringify({ qa_id: qaId, value }),
    })
  } catch {
    /* 忽略 */
  }
}
```

- [ ] **Step 5: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/api.test.ts`
Expected: PASS（5 案）

- [ ] **Step 6: Commit**

```bash
git add frontend/src/features/ask/schemas.ts frontend/src/features/ask/api.ts frontend/src/features/ask/api.test.ts
git commit -m "$(cat <<'EOF'
feat(ask): 會話清單/重播/刪除/回饋 schemas 與 api（Phase 3 Task 5）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: conversation/turn 模型

**Files:**
- Create: `frontend/src/features/ask/lib/conversation.ts`
- Test: `frontend/src/features/ask/lib/conversation.test.ts`

**Interfaces:**
- Consumes: `SourceItem`/`ExtSource`（`lib/sseEvents.ts`）、`HistoryItem`（`schemas.ts`）。
- Produces:
  - `type TurnPhase = 'streaming' | 'done' | 'notice' | 'error'`
  - `interface TurnState { id; q; answer; sources; extSources; qaId; thinkingMs; feedback; phase; notice; errorMsg; offerReport; reportTitle; stage; webUsed }`
  - `buildAskBody(question: string, conversationId: string | null): { question: string; conversation_id?: string }`
  - `emptyTurn(id: string, q: string): TurnState`
  - `historyToTurn(item: HistoryItem, id: string): TurnState`

- [ ] **Step 1: 寫失敗測試 `conversation.test.ts`**

```ts
import { describe, expect, test } from 'vitest'
import { buildAskBody, emptyTurn, historyToTurn } from './conversation'

describe('buildAskBody', () => {
  test('首輪無 conversation_id', () => {
    expect(buildAskBody('hi', null)).toEqual({ question: 'hi' })
  })
  test('多輪帶 conversation_id', () => {
    expect(buildAskBody('hi', 'c1')).toEqual({ question: 'hi', conversation_id: 'c1' })
  })
})

describe('emptyTurn', () => {
  test('初始為 streaming 空狀態', () => {
    const t = emptyTurn('t1', 'Q')
    expect(t).toMatchObject({ id: 't1', q: 'Q', answer: '', phase: 'streaming', sources: [], qaId: null })
  })
})

describe('historyToTurn', () => {
  test('一般輪映射欄位且 phase=done', () => {
    const t = historyToTurn(
      { id: 'q1', question: 'Q', answer: 'A', feedback: 'like', sources: [], ext_sources: [], is_offtopic: false, thinking_ms: 900 },
      'h0',
    )
    expect(t).toMatchObject({ q: 'Q', answer: 'A', qaId: 'q1', feedback: 'like', thinkingMs: 900, phase: 'done' })
  })
  test('離題輪 phase=notice、notice 取 answer', () => {
    const t = historyToTurn({ id: 'q2', question: 'Q', answer: '無法回答', is_offtopic: true }, 'h1')
    expect(t.phase).toBe('notice')
    expect(t.notice).toBe('無法回答')
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/lib/conversation.test.ts`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 寫 `conversation.ts`**

```ts
import type { ExtSource, SourceItem } from './sseEvents'
import type { HistoryItem } from '../schemas'

export type TurnPhase = 'streaming' | 'done' | 'notice' | 'error'

export interface TurnState {
  id: string
  q: string
  answer: string
  sources: SourceItem[]
  extSources: ExtSource[]
  qaId: string | null
  thinkingMs: number | null
  feedback: 'like' | 'dislike' | null
  phase: TurnPhase
  notice: string | null
  errorMsg: string | null
  offerReport: boolean
  reportTitle: string | null
  stage: string | null
  webUsed: boolean
}

export function buildAskBody(
  question: string,
  conversationId: string | null,
): { question: string; conversation_id?: string } {
  return conversationId ? { question, conversation_id: conversationId } : { question }
}

export function emptyTurn(id: string, q: string): TurnState {
  return {
    id,
    q,
    answer: '',
    sources: [],
    extSources: [],
    qaId: null,
    thinkingMs: null,
    feedback: null,
    phase: 'streaming',
    notice: null,
    errorMsg: null,
    offerReport: false,
    reportTitle: null,
    stage: null,
    webUsed: false,
  }
}

export function historyToTurn(item: HistoryItem, id: string): TurnState {
  const offtopic = Boolean(item.is_offtopic)
  const fb = item.feedback === 'like' || item.feedback === 'dislike' ? item.feedback : null
  return {
    id,
    q: item.question,
    answer: item.answer ?? '',
    sources: (item.sources ?? []) as SourceItem[],
    extSources: (item.ext_sources ?? []) as ExtSource[],
    qaId: item.id,
    thinkingMs: typeof item.thinking_ms === 'number' ? item.thinking_ms : null,
    feedback: fb,
    phase: offtopic ? 'notice' : 'done',
    notice: offtopic ? item.answer ?? '' : null,
    errorMsg: null,
    offerReport: false,
    reportTitle: null,
    stage: null,
    webUsed: (item.ext_sources ?? []).length > 0,
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/lib/conversation.test.ts`
Expected: PASS（5 案）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ask/lib/conversation.ts frontend/src/features/ask/lib/conversation.test.ts
git commit -m "$(cat <<'EOF'
feat(ask): conversation/turn 模型與 buildAskBody（Phase 3 Task 6）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: useAskStream 串流核心 hook

**Files:**
- Create: `frontend/src/features/ask/hooks/useAskStream.ts`
- Test: `frontend/src/features/ask/hooks/useAskStream.test.tsx`

**Interfaces:**
- Consumes: `streamAsk`（`lib/sse.ts`）、`buildAskBody`/`emptyTurn`/`historyToTurn`/`TurnState`（`lib/conversation.ts`）、`HistoryItem`（`schemas.ts`）。
- Produces: `useAskStream(): { turns: TurnState[]; conversationId: string | null; streaming: boolean; send(question: string): void; loadConversation(items: HistoryItem[], id: string): void; newConversation(): void }`。
- 行為：`send` bump 單調序號 + abort 前一串流 + append 空 turn + 串流更新該 turn；**所有 state 更新 gate `mySeq === seqRef.current`（雙重 latest-wins）**；不假設增量（單一大 token 也累積正確）；`error` event → errorMsg「問答服務發生錯誤，請稍後再試。」；網路 throw（非 abort）→「查詢逾時或失敗，請稍後再試。」；無 token 無 notice 收尾 →「沒有取得回答，請稍後再試。」；`done` 擷取 conversation_id/qa_id/thinking_ms/offer_report。

- [ ] **Step 1: 寫失敗測試 `useAskStream.test.tsx`**

```tsx
import { describe, expect, test, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook, waitFor } from '@testing-library/react'

// 可控的 streamAsk mock：每次呼叫從佇列取一個 async generator factory。
// 用 vi.hoisted 宣告 genQueue —— vi.mock 會提升到 import 之上，factory 不能引用
// 一般外層 const（會 ReferenceError），須經 hoisted 共享。
const { genQueue } = vi.hoisted(() => ({ genQueue: [] as Array<() => AsyncGenerator<unknown>> }))
vi.mock('../lib/sse', () => ({
  streamAsk: () => {
    const make = genQueue.shift()
    if (!make) throw new Error('no gen queued')
    return make()
  },
}))

import { useAskStream } from './useAskStream'
import type { AskEvent } from '../lib/sseEvents'

function gen(events: AskEvent[], opts: { hang?: boolean } = {}): () => AsyncGenerator<AskEvent> {
  return async function* () {
    for (const e of events) yield e
    if (opts.hang) await new Promise(() => {}) // 永不結束（模擬仍在飛行）
  }
}

beforeEach(() => { genQueue.length = 0 })
afterEach(() => vi.clearAllMocks())

describe('useAskStream', () => {
  test('send 累積 token 並於 done 取回 conversation_id', async () => {
    genQueue.push(gen([
      { event: 'sources', data: [] },
      { event: 'token', data: '你' },
      { event: 'token', data: '好' },
      { event: 'done', data: { conversation_id: 'c1', qa_id: 'q1', thinking_ms: 800 } },
    ]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('hi'))
    await waitFor(() => expect(result.current.turns[0]?.phase).toBe('done'))
    expect(result.current.turns[0].answer).toBe('你好')
    expect(result.current.turns[0].qaId).toBe('q1')
    expect(result.current.conversationId).toBe('c1')
  })

  test('單一大 token 也正確', async () => {
    genQueue.push(gen([{ event: 'token', data: '整段答案' }, { event: 'done', data: {} }]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('hi'))
    await waitFor(() => expect(result.current.turns[0]?.phase).toBe('done'))
    expect(result.current.turns[0].answer).toBe('整段答案')
  })

  test('error 事件 → errorMsg', async () => {
    genQueue.push(gen([{ event: 'error', data: { detail: 'x' } }]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('hi'))
    await waitFor(() => expect(result.current.turns[0]?.phase).toBe('error'))
    expect(result.current.turns[0].errorMsg).toContain('問答服務發生錯誤')
  })

  test('空串流（無 token 無 notice）→ 沒有取得回答', async () => {
    genQueue.push(gen([{ event: 'sources', data: [] }, { event: 'done', data: {} }]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('hi'))
    await waitFor(() => expect(result.current.turns[0]?.errorMsg).toContain('沒有取得回答'))
  })

  test('latest-wins：新 send 後舊串流的後續事件不寫入', async () => {
    // 第一個串流 hang（吐一個 token 後不結束）；第二個正常完成
    genQueue.push(gen([{ event: 'token', data: '舊' }], { hang: true }))
    genQueue.push(gen([{ event: 'token', data: '新' }, { event: 'done', data: { conversation_id: 'c2' } }]))
    const { result } = renderHook(() => useAskStream())
    act(() => result.current.send('q1'))
    await waitFor(() => expect(result.current.turns[0]?.answer).toBe('舊'))
    act(() => result.current.send('q2'))
    await waitFor(() => expect(result.current.conversationId).toBe('c2'))
    // 應有兩輪；第二輪完成，第一輪保持「舊」（未被污染、未被新內容覆寫）
    expect(result.current.turns).toHaveLength(2)
    expect(result.current.turns[1].answer).toBe('新')
    expect(result.current.turns[0].answer).toBe('舊')
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/hooks/useAskStream.test.tsx`
Expected: FAIL（hook 不存在）

- [ ] **Step 3: 寫 `useAskStream.ts`**

```ts
import { useCallback, useRef, useState } from 'react'
import { streamAsk } from '../lib/sse'
import { buildAskBody, emptyTurn, historyToTurn, type TurnState } from '../lib/conversation'
import type { HistoryItem } from '../schemas'

const HTTP = /^https?:\/\//i

export interface UseAskStream {
  turns: TurnState[]
  conversationId: string | null
  streaming: boolean
  send: (question: string) => void
  loadConversation: (items: HistoryItem[], id: string) => void
  newConversation: () => void
}

export function useAskStream(): UseAskStream {
  const [turns, setTurns] = useState<TurnState[]>([])
  const [conversationId, setConversationId] = useState<string | null>(null)
  const [streaming, setStreaming] = useState(false)
  const seqRef = useRef(0)
  const ctrlRef = useRef<AbortController | null>(null)
  const idRef = useRef(0)

  const cancelActive = useCallback(() => {
    seqRef.current += 1 // 讓飛行中串流的 latest-wins 立刻失效
    if (ctrlRef.current) {
      ctrlRef.current.abort()
      ctrlRef.current = null
    }
  }, [])

  const send = useCallback(
    (question: string) => {
      const q = question.trim()
      if (!q) return
      cancelActive()
      const mySeq = seqRef.current
      const turnId = `t${(idRef.current += 1)}`
      setStreaming(true)
      setTurns((prev) => [...prev, emptyTurn(turnId, q)])
      const ctrl = new AbortController()
      ctrlRef.current = ctrl

      const update = (fn: (t: TurnState) => TurnState) => {
        if (mySeq !== seqRef.current) return // latest-wins：陳舊串流不寫 UI
        setTurns((prev) => prev.map((t) => (t.id === turnId ? fn(t) : t)))
      }

      void (async () => {
        let started = false
        let notice = false
        try {
          for await (const evt of streamAsk(buildAskBody(q, conversationId), ctrl.signal)) {
            if (mySeq !== seqRef.current) return
            if (evt.event === 'sources') {
              update((t) => ({ ...t, sources: evt.data }))
            } else if (evt.event === 'status') {
              update((t) => ({
                ...t,
                stage: evt.data.stage,
                webUsed: t.webUsed || evt.data.stage === 'searching_web',
              }))
            } else if (evt.event === 'ext_sources') {
              update((t) => ({ ...t, extSources: evt.data.filter((s) => HTTP.test(s.url)) }))
            } else if (evt.event === 'token') {
              started = true
              update((t) => ({ ...t, answer: t.answer + evt.data, phase: 'streaming' }))
            } else if (evt.event === 'notice') {
              notice = true
              started = true
              update((t) => ({ ...t, phase: 'notice', notice: evt.data }))
            } else if (evt.event === 'done') {
              if (evt.data.conversation_id && mySeq === seqRef.current) {
                setConversationId(evt.data.conversation_id)
              }
              update((t) => ({
                ...t,
                qaId: evt.data.qa_id ?? null,
                thinkingMs: typeof evt.data.thinking_ms === 'number' ? evt.data.thinking_ms : t.thinkingMs,
                offerReport: Boolean(evt.data.offer_report),
                reportTitle: evt.data.report_title ?? null,
                phase: t.phase === 'notice' ? 'notice' : 'done',
              }))
            } else if (evt.event === 'error') {
              update((t) => ({ ...t, phase: 'error', errorMsg: '問答服務發生錯誤，請稍後再試。' }))
              return
            }
          }
          if (mySeq !== seqRef.current) return
          if (!started) {
            update((t) => ({ ...t, phase: 'error', errorMsg: '沒有取得回答，請稍後再試。' }))
          } else if (!notice) {
            update((t) => (t.phase === 'error' ? t : { ...t, phase: 'done' }))
          }
        } catch (e) {
          if (mySeq !== seqRef.current) return
          if (e instanceof DOMException && e.name === 'AbortError') return
          update((t) => ({ ...t, phase: 'error', errorMsg: '查詢逾時或失敗，請稍後再試。' }))
        } finally {
          if (mySeq === seqRef.current) {
            ctrlRef.current = null
            setStreaming(false)
          }
        }
      })()
    },
    [cancelActive, conversationId],
  )

  const loadConversation = useCallback(
    (items: HistoryItem[], id: string) => {
      cancelActive()
      setStreaming(false)
      setConversationId(id)
      setTurns(items.map((it, i) => historyToTurn(it, `h${i}`)))
    },
    [cancelActive],
  )

  const newConversation = useCallback(() => {
    cancelActive()
    setStreaming(false)
    setConversationId(null)
    setTurns([])
  }, [cancelActive])

  return { turns, conversationId, streaming, send, loadConversation, newConversation }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/hooks/useAskStream.test.tsx`
Expected: PASS（5 案）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ask/hooks/useAskStream.ts frontend/src/features/ask/hooks/useAskStream.test.tsx
git commit -m "$(cat <<'EOF'
feat(ask): useAskStream 串流核心（latest-wins + 多輪）（Phase 3 Task 7）

AbortController + 單調序號雙重 latest-wins，gate 所有 UI 寫入；不假設增量；
done 擷取 conversation_id/qa_id/thinking_ms；error/timeout/空串流訊息對映。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: useConversations 歷史側欄 hook

**Files:**
- Create: `frontend/src/features/ask/hooks/useConversations.ts`
- Test: `frontend/src/features/ask/hooks/useConversations.test.tsx`

**Interfaces:**
- Consumes: `getConversations`/`deleteConversation`（`api.ts`）、TanStack Query。
- Produces: `useConversations(): { conversations: ConversationSummary[]; isLoading: boolean; isError: boolean; remove(id: string): void; refresh(): void }`。`remove` 成功後 invalidate `['conversations']`。

- [ ] **Step 1: 寫失敗測試 `useConversations.test.tsx`**

```tsx
import { describe, expect, test, vi, afterEach } from 'vitest'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactNode } from 'react'

vi.mock('../api', () => ({
  getConversations: vi.fn().mockResolvedValue([{ conversation_id: 'c1', title: 'Q', last_at: null, turn_count: 1 }]),
  deleteConversation: vi.fn().mockResolvedValue({ ok: true }),
}))

import { useConversations } from './useConversations'
import * as api from '../api'

afterEach(() => vi.clearAllMocks())

function wrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('useConversations', () => {
  test('載入清單', async () => {
    const { result } = renderHook(() => useConversations(), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))
  })

  test('remove 呼叫 deleteConversation', async () => {
    const { result } = renderHook(() => useConversations(), { wrapper: wrapper() })
    await waitFor(() => expect(result.current.conversations).toHaveLength(1))
    act(() => result.current.remove('c1'))
    await waitFor(() => expect(api.deleteConversation).toHaveBeenCalledWith('c1'))
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/hooks/useConversations.test.tsx`
Expected: FAIL（hook 不存在）

- [ ] **Step 3: 寫 `useConversations.ts`**

```ts
import { useCallback } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { deleteConversation, getConversations } from '../api'
import type { ConversationSummary } from '../schemas'

export interface UseConversations {
  conversations: ConversationSummary[]
  isLoading: boolean
  isError: boolean
  remove: (id: string) => void
  refresh: () => void
}

export function useConversations(): UseConversations {
  const qc = useQueryClient()
  const list = useQuery({
    queryKey: ['conversations'],
    queryFn: getConversations,
    retry: false,
    refetchOnWindowFocus: false,
  })
  const del = useMutation({
    mutationFn: (id: string) => deleteConversation(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['conversations'] })
    },
  })
  // refresh 必須穩定（AskPage 以它作 useEffect 依賴；不穩定會每次 render 觸發失效迴圈）
  const refresh = useCallback(() => {
    void qc.invalidateQueries({ queryKey: ['conversations'] })
  }, [qc])
  return {
    conversations: list.data ?? [],
    isLoading: list.isLoading,
    isError: list.isError,
    remove: del.mutate,
    refresh,
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/hooks/useConversations.test.tsx`
Expected: PASS（2 案）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ask/hooks/useConversations.ts frontend/src/features/ask/hooks/useConversations.test.tsx
git commit -m "$(cat <<'EOF'
feat(ask): useConversations 歷史側欄 hook（清單/刪除/invalidate）（Phase 3 Task 8）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: 流程步驟面板（deriveProcess 純函式 + ProcessSteps 元件）

**Files:**
- Create: `frontend/src/features/ask/lib/process.ts`
- Create: `frontend/src/features/ask/components/ProcessSteps.tsx`
- Test: `frontend/src/features/ask/lib/process.test.ts`

**Interfaces:**
- Consumes: `TurnState`（`lib/conversation.ts`）。
- Produces:
  - `lib/process.ts`：`type StepState = 'pending' | 'active' | 'done'`；`interface ProcStep { key: string; label: string; state: StepState; hidden: boolean }`；`thinkingLabel(ms: number | null): string | null`；`deriveProcess(turn: TurnState): { steps: ProcStep[]; headLabel: string; headDone: boolean }`。
  - `ProcessSteps.tsx`：`function ProcessSteps({ turn }: { turn: TurnState }): JSX.Element`（可折疊面板，預設收合）。
- 對照 ask.js:276-425。stage→活躍步驟對映：`understanding→understand 活躍`、`retrieved→reading 活躍（understand/retrieved 完成、retrieved 標籤「找到 N 篇相關研報」）`、`reading→reading 活躍`、`searching_web→web 活躍`、`generating→generate 活躍`；`phase!=streaming`（done/notice/error）→全部 done。web 步驟僅 `webUsed` 時顯示。thinking 標籤：`已思考 ${max(1, round(ms/1000))} 秒`，ms 無效回 null。

- [ ] **Step 1: 寫失敗測試 `process.test.ts`**

```ts
import { describe, expect, test } from 'vitest'
import { deriveProcess, thinkingLabel } from './process'
import { emptyTurn } from './conversation'

const turnAt = (over: Partial<ReturnType<typeof emptyTurn>>) => ({ ...emptyTurn('t', 'q'), ...over })

describe('thinkingLabel', () => {
  test('ms 轉秒、至少 1 秒', () => {
    expect(thinkingLabel(2400)).toBe('已思考 2 秒')
    expect(thinkingLabel(200)).toBe('已思考 1 秒')
  })
  test('無效回 null', () => {
    expect(thinkingLabel(null)).toBeNull()
  })
})

describe('deriveProcess', () => {
  test('understanding：understand 活躍其餘 pending', () => {
    const { steps } = deriveProcess(turnAt({ stage: 'understanding' }))
    expect(steps.find((s) => s.key === 'understand')?.state).toBe('active')
    expect(steps.find((s) => s.key === 'reading')?.state).toBe('pending')
  })
  test('retrieved：understand/retrieved done、reading active、retrieved 標籤含篇數', () => {
    const { steps } = deriveProcess(
      turnAt({ stage: 'retrieved', sources: [{ n: 1, report_id: 'r', file_name: 'f', market: null, report_date: null, is_latest: false }] }),
    )
    expect(steps.find((s) => s.key === 'understand')?.state).toBe('done')
    expect(steps.find((s) => s.key === 'retrieved')?.state).toBe('done')
    expect(steps.find((s) => s.key === 'retrieved')?.label).toBe('找到 1 篇相關研報')
    expect(steps.find((s) => s.key === 'reading')?.state).toBe('active')
  })
  test('web 步驟僅 webUsed 顯示', () => {
    expect(deriveProcess(turnAt({})).steps.find((s) => s.key === 'web')?.hidden).toBe(true)
    expect(deriveProcess(turnAt({ webUsed: true })).steps.find((s) => s.key === 'web')?.hidden).toBe(false)
  })
  test('phase done：全部 done + 思考秒數標題', () => {
    const { steps, headDone, headLabel } = deriveProcess(turnAt({ phase: 'done', thinkingMs: 1500 }))
    expect(steps.every((s) => s.state === 'done')).toBe(true)
    expect(headDone).toBe(true)
    expect(headLabel).toBe('已思考 2 秒')
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/lib/process.test.ts`
Expected: FAIL（模組不存在）

- [ ] **Step 3: 寫 `lib/process.ts`**

```ts
import type { TurnState } from './conversation'

export type StepState = 'pending' | 'active' | 'done'
export interface ProcStep {
  key: string
  label: string
  state: StepState
  hidden: boolean
}

// stage → 「活躍步驟索引」（understand=0 retrieved=1 reading=2 web=3 generate=4）
const STAGE_ACTIVE: Record<string, number> = {
  understanding: 0,
  retrieved: 2,
  reading: 2,
  searching_web: 3,
  generating: 4,
}

export function thinkingLabel(ms: number | null): string | null {
  return typeof ms === 'number' && ms >= 0 ? `已思考 ${Math.max(1, Math.round(ms / 1000))} 秒` : null
}

export function deriveProcess(turn: TurnState): { steps: ProcStep[]; headLabel: string; headDone: boolean } {
  const done = turn.phase !== 'streaming'
  const active = done ? 5 : turn.stage != null ? STAGE_ACTIVE[turn.stage] ?? 0 : 0
  const st = (idx: number): StepState => (done || idx < active ? 'done' : idx === active ? 'active' : 'pending')
  const retrievedLabel = turn.sources.length ? `找到 ${turn.sources.length} 篇相關研報` : '檢索研報'
  const steps: ProcStep[] = [
    { key: 'understand', label: '理解問題', state: st(0), hidden: false },
    { key: 'retrieved', label: retrievedLabel, state: st(1), hidden: false },
    { key: 'reading', label: '閱讀重點、整理回答', state: st(2), hidden: false },
    { key: 'web', label: '搜尋網路補充', state: st(3), hidden: !turn.webUsed },
    { key: 'generate', label: '生成回答', state: st(4), hidden: false },
  ]
  return { steps, headLabel: done ? thinkingLabel(turn.thinkingMs) ?? '處理過程' : '正在思考', headDone: done }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/lib/process.test.ts`
Expected: PASS（6 案）

- [ ] **Step 5: 寫 `ProcessSteps.tsx`（可折疊面板，預設收合）**

> 結構對照 ask.js:291-323 + PROC_ICO（ask.js:284-288）。用 `useState(false)` 控制展開。step icon：pending=空圈、active=旋轉 spinner（`<span className={styles.spin}>`）、done=打勾 SVG。head 顯示 `headLabel`（done 時打勾、否則 spinner）。隱藏 `hidden` 的 step。每個 step `data-step`/`data-state`、面板 `data-testid="ask-process"`、head button `aria-expanded`。SVG 圖示（chev/done/pending）可自 ask.js:270/284-288 複製。

```tsx
import { useState } from 'react'
import type { TurnState } from '../lib/conversation'
import { deriveProcess } from '../lib/process'
import styles from './AskPage.module.css'

export function ProcessSteps({ turn }: { turn: TurnState }) {
  const [open, setOpen] = useState(false)
  const { steps, headLabel, headDone } = deriveProcess(turn)
  return (
    <div className={styles.process} data-testid="ask-process">
      <button
        type="button"
        className={styles.processHead}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.procIco} aria-hidden="true">
          {headDone ? '✓' : <span className={styles.spin} />}
        </span>
        <span>{headLabel}</span>
      </button>
      {open && (
        <ol className={styles.processSteps}>
          {steps
            .filter((s) => !s.hidden)
            .map((s) => (
              <li key={s.key} data-step={s.key} data-state={s.state} className={styles.step}>
                <span className={styles.stepIco} aria-hidden="true">
                  {s.state === 'done' ? '✓' : s.state === 'active' ? <span className={styles.spin} /> : '○'}
                </span>
                <span>{s.label}</span>
              </li>
            ))}
        </ol>
      )}
    </div>
  )
}
```

> 註：上方用文字符號（✓/○）代替 inline SVG 以簡化；若要視覺平價，改用 ask.js:284-288 的 SVG（複製為元件常數）。step 的 spinner 動畫 class `styles.spin` 於 Task 12 的 CSS 定義。

- [ ] **Step 6: typecheck（元件依賴 Task 12 的 CSS module，若 `AskPage.module.css` 尚未建，先建空檔以過 import）**

Run: `cd frontend && test -f src/features/ask/components/AskPage.module.css || echo "/* placeholder; filled in Task 12 */" > src/features/ask/components/AskPage.module.css; npx tsc --noEmit`
Expected: 0 error

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/ask/lib/process.ts frontend/src/features/ask/lib/process.test.ts frontend/src/features/ask/components/ProcessSteps.tsx frontend/src/features/ask/components/AskPage.module.css
git commit -m "$(cat <<'EOF'
feat(ask): 流程步驟面板 deriveProcess 純函式 + ProcessSteps 元件（Phase 3 Task 9）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Turn 元件（答案/來源/外部來源/動作列/離題卡/引用）

**Files:**
- Create: `frontend/src/features/ask/components/Turn.tsx`
- Test: `frontend/src/features/ask/components/Turn.test.tsx`

**Interfaces:**
- Consumes: `TurnState`（`lib/conversation.ts`）、`renderMarkdown`（`lib/markdown.tsx`）、`ProcessSteps`（Task 9）、`sendFeedback`（`api.ts`）、`mLabel`/`mColor`/`fmtDate`（`features/search/components/meta.ts`，跨 feature import）。
- Produces: `function Turn({ turn, onCite, onFeedback }: { turn: TurnState; onCite: (reportId: string) => void; onFeedback?: (qaId: string, value: 'like' | 'dislike') => void }): JSX.Element`。
- 對照 ask.js:78-119、244-263、528-554。職責：
  - 問題泡泡（`data-testid="ask-q"`，純文字）。
  - `<ProcessSteps turn={turn} />`。
  - phase==='notice'：離題卡（`data-testid="ask-notice"`，標題「無法回答此問題」+ notice 內文；不顯示動作列/來源）。
  - phase==='error'：錯誤訊息（`data-testid="ask-error"`，純文字 errorMsg）。
  - 否則答案泡泡（`data-testid="ask-answer"`）：`renderMarkdown(turn.answer, turn.sources.length, (n) => { const s = turn.sources.find(x => x.n === n); if (s) onCite(s.report_id) })`；streaming 時尾隨 caret `<span className={styles.caret} />`。
  - 來源清單（`data-testid="ask-sources"`）：每筆 `<button data-testid="ask-src" onClick={() => onCite(s.report_id)}>` 含 n、市場 badge（`mColor`/`mLabel`）、檔名、日期（`fmtDate`）、`is_latest` 時「最新」徽章。空則不渲染。
  - 外部來源（`data-testid="ask-ext"`）：http-only `<a>` 連結，badge「網路」+ 標題（`title || url`）+ 網域。
  - 動作列（phase==='done' 且非 notice 時顯示）：讚/倒讚/複製 +（有來源時）可折疊「資料來源 N」/「外部參考 N」。讚/倒讚互斥（本地 state，初始取 `turn.feedback`），點擊呼叫 `onFeedback?.(turn.qaId, value)` 或內建 `sendFeedback`；複製 `navigator.clipboard.writeText(turn.answer)`（fallback execCommand，移植 ask.js:570-582）。
  - **不渲染** offer_report（Phase 4）。
- 引用點擊：透過 `renderMarkdown` 的 onCite 回呼，對映本輪 `sources`（`maxCite = turn.sources.length`），避免跨輪。

- [ ] **Step 1: 寫失敗測試 `Turn.test.tsx`**

```tsx
import { describe, expect, test, vi, afterEach } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { Turn } from './Turn'
import { emptyTurn } from '../lib/conversation'
import type { TurnState } from '../lib/conversation'

vi.mock('../api', () => ({ sendFeedback: vi.fn().mockResolvedValue(undefined) }))
import * as api from '../api'

afterEach(() => vi.clearAllMocks())

const wrap = (t: TurnState, onCite = vi.fn()) =>
  render(
    <MantineProvider theme={theme}>
      <Turn turn={t} onCite={onCite} />
    </MantineProvider>,
  )

const doneTurn = (over: Partial<TurnState> = {}): TurnState => ({
  ...emptyTurn('t1', '台積電?'),
  phase: 'done',
  answer: '台積電[1] 表現佳。',
  qaId: 'q1',
  sources: [{ n: 1, report_id: 'r1', file_name: 'a.pdf', market: 'TW', report_date: '2026-01-01', is_latest: true }],
  ...over,
})

describe('Turn', () => {
  test('渲染問題與答案 markdown', () => {
    wrap(doneTurn())
    expect(screen.getByTestId('ask-q')).toHaveTextContent('台積電?')
    expect(screen.getByTestId('ask-answer')).toHaveTextContent('台積電[1] 表現佳。')
  })

  test('來源清單可點 → onCite(report_id)', () => {
    const onCite = vi.fn()
    wrap(doneTurn(), onCite)
    fireEvent.click(screen.getAllByTestId('ask-src')[0])
    expect(onCite).toHaveBeenCalledWith('r1')
  })

  test('引用 chip 點擊 → onCite(本輪來源 report_id)', () => {
    const onCite = vi.fn()
    wrap(doneTurn(), onCite)
    fireEvent.click(screen.getByTestId('ask-answer').querySelector('.cite')!)
    expect(onCite).toHaveBeenCalledWith('r1')
  })

  test('離題卡', () => {
    wrap(doneTurn({ phase: 'notice', notice: '無法回答此問題的內容' }))
    expect(screen.getByTestId('ask-notice')).toHaveTextContent('無法回答此問題的內容')
    expect(screen.queryByTestId('ask-answer')).toBeNull()
  })

  test('讚回饋呼叫 sendFeedback', () => {
    wrap(doneTurn())
    fireEvent.click(screen.getByRole('button', { name: '讚' }))
    expect(api.sendFeedback).toHaveBeenCalledWith('q1', 'like')
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/components/Turn.test.tsx`
Expected: FAIL（元件不存在）

- [ ] **Step 3: 寫 `Turn.tsx`**

> 完整實作：問題泡泡 + ProcessSteps + 條件渲染（notice/error/answer）+ 來源/外部來源 + 動作列。markdown 用 `renderMarkdown(turn.answer, turn.sources.length, onCiteN)`，其中 `onCiteN = (n) => { const s = turn.sources.find(x => x.n === n); if (s) onCite(s.report_id) }`。動作列讚/倒讚以本地 `useState<'like'|'dislike'|null>(turn.feedback)` 控制互斥高亮，點擊呼叫 `onFeedback?.(turn.qaId!, v) ?? sendFeedback(turn.qaId!, v)`（qaId 為 null 時禁用）。複製鈕用 `navigator.clipboard?.writeText(turn.answer)`，無安全脈絡 fallback 用隱藏 textarea + `document.execCommand('copy')`（移植 ask.js:576-582）。市場 badge/日期用 `mColor`/`mLabel`/`fmtDate`（`import { mColor, mLabel, fmtDate } from '../../search/components/meta'`）。所有 class 走 `AskPage.module.css`。testid 依測試：`ask-q`/`ask-answer`/`ask-sources`/`ask-src`/`ask-ext`/`ask-notice`/`ask-error`；讚/倒讚/複製鈕 `aria-label` 為「讚」「倒讚」「複製回答」。caret span 於 `phase==='streaming'` 顯示。

> 關鍵骨架（實作者補齊細節）：

```tsx
import { useState } from 'react'
import type { TurnState } from '../lib/conversation'
import { renderMarkdown } from '../lib/markdown'
import { ProcessSteps } from './ProcessSteps'
import { sendFeedback } from '../api'
import { mColor, mLabel, fmtDate } from '../../search/components/meta'
import styles from './AskPage.module.css'

interface TurnProps {
  turn: TurnState
  onCite: (reportId: string) => void
  onFeedback?: (qaId: string, value: 'like' | 'dislike') => void
}

export function Turn({ turn, onCite, onFeedback }: TurnProps) {
  const [fb, setFb] = useState<'like' | 'dislike' | null>(turn.feedback)
  const onCiteN = (n: number) => {
    const s = turn.sources.find((x) => x.n === n)
    if (s) onCite(s.report_id)
  }
  const feedback = (v: 'like' | 'dislike') => {
    if (!turn.qaId) return
    setFb(v)
    if (onFeedback) onFeedback(turn.qaId, v)
    else void sendFeedback(turn.qaId, v)
  }
  const copy = () => {
    if (navigator.clipboard && window.isSecureContext) void navigator.clipboard.writeText(turn.answer)
    else {
      const ta = document.createElement('textarea')
      ta.value = turn.answer
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.focus()
      ta.select()
      try {
        document.execCommand('copy')
      } catch {
        /* ignore */
      }
      document.body.removeChild(ta)
    }
  }
  return (
    <div className={styles.turn}>
      <div className={styles.msgUser} data-testid="ask-q">
        {turn.q}
      </div>
      <ProcessSteps turn={turn} />
      {turn.phase === 'notice' ? (
        <div className={styles.notice} data-testid="ask-notice">
          <div className={styles.noticeTitle}>無法回答此問題</div>
          <div>{turn.notice}</div>
        </div>
      ) : turn.phase === 'error' ? (
        <div className={styles.error} data-testid="ask-error">
          {turn.errorMsg}
        </div>
      ) : (
        <div className={styles.msgBot} data-testid="ask-answer" aria-live="polite">
          {renderMarkdown(turn.answer, turn.sources.length, onCiteN)}
          {turn.phase === 'streaming' && <span className={styles.caret} />}
        </div>
      )}
      {turn.sources.length > 0 && turn.phase !== 'notice' && (
        <div className={styles.sources} data-testid="ask-sources">
          {turn.sources.map((s) => (
            <button key={s.n} type="button" className={styles.src} data-testid="ask-src" onClick={() => onCite(s.report_id)}>
              <span className={styles.srcN}>{s.n}</span>
              {s.market && (
                <span className={styles.badge} style={{ background: mColor(s.market) }}>
                  {mLabel(s.market)}
                </span>
              )}
              <span className={styles.srcName}>{s.file_name}</span>
              {s.report_date && <span className={styles.srcDate}>{fmtDate(s.report_date)}</span>}
              {s.is_latest && <span className={styles.srcLatest}>最新</span>}
            </button>
          ))}
        </div>
      )}
      {turn.extSources.length > 0 && turn.phase !== 'notice' && (
        <div className={styles.ext} data-testid="ask-ext">
          {turn.extSources.map((s, i) => (
            <a key={i} className={styles.extLink} href={s.url} target="_blank" rel="noopener noreferrer">
              <span className={styles.extBadge}>網路</span>
              <span>{s.title || s.url}</span>
            </a>
          ))}
        </div>
      )}
      {turn.phase === 'done' && (
        <div className={styles.actions}>
          <button type="button" aria-label="讚" className={fb === 'like' ? styles.on : undefined} disabled={!turn.qaId} onClick={() => feedback('like')}>
            讚
          </button>
          <button type="button" aria-label="倒讚" className={fb === 'dislike' ? styles.on : undefined} disabled={!turn.qaId} onClick={() => feedback('dislike')}>
            倒讚
          </button>
          <button type="button" aria-label="複製回答" onClick={copy}>
            複製
          </button>
        </div>
      )}
    </div>
  )
}
```

> 註：讚/倒讚鈕文字「讚/倒讚」可改為 ask.js:267-269 的 inline SVG（aria-label 維持「讚」「倒讚」「複製回答」以對齊測試）。

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/components/Turn.test.tsx`
Expected: PASS（5 案）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ask/components/Turn.tsx frontend/src/features/ask/components/Turn.test.tsx
git commit -m "$(cat <<'EOF'
feat(ask): Turn 元件（答案/來源/動作列/離題卡/引用對映）（Phase 3 Task 10）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: AskComposer + ConversationSidebar + ConfirmDialog

**Files:**
- Create: `frontend/src/features/ask/components/AskComposer.tsx`
- Create: `frontend/src/features/ask/components/ConfirmDialog.tsx`
- Create: `frontend/src/features/ask/components/ConversationSidebar.tsx`
- Test: `frontend/src/features/ask/components/AskComposer.test.tsx`
- Test: `frontend/src/features/ask/components/ConversationSidebar.test.tsx`

**Interfaces:**
- Produces:
  - `AskComposer({ onSend, disabled, examples }: { onSend: (q: string) => void; disabled?: boolean; examples?: string[] }): JSX.Element` — textarea（Enter 送出、Shift+Enter 換行，移植 ask.js:601-603）、送出鈕、空狀態範例按鈕（點擊填入並送出，ask.js:604-606）。`data-testid="ask-input"`/`ask-send`。
  - `ConfirmDialog({ opened, title, body, confirmLabel, onConfirm, onCancel }): JSX.Element` — 受控 Mantine `Modal`（取代 confirm.js promise 版；焦點/Esc 由 Mantine 處理）。
  - `ConversationSidebar({ conversations, activeId, onOpen, onDelete, onNew }: { conversations: ConversationSummary[]; activeId: string | null; onOpen: (id: string) => void; onDelete: (id: string) => void; onNew: () => void }): JSX.Element` — 歷史清單（每筆開啟 + 刪除鈕）、「新對話」鈕、active 標記。刪除先開 ConfirmDialog，確認才 `onDelete`。空清單顯示「尚無歷史對話」。`data-testid="ask-hist-item"`/`ask-hist-open`/`ask-hist-del`/`ask-new`。

- [ ] **Step 1: 寫失敗測試 `AskComposer.test.tsx` 與 `ConversationSidebar.test.tsx`**

```tsx
// AskComposer.test.tsx
import { describe, expect, test, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { AskComposer } from './AskComposer'

const wrap = (props: Parameters<typeof AskComposer>[0]) =>
  render(
    <MantineProvider theme={theme}>
      <AskComposer {...props} />
    </MantineProvider>,
  )

describe('AskComposer', () => {
  test('Enter 送出（去頭尾空白）', () => {
    const onSend = vi.fn()
    wrap({ onSend })
    const ta = screen.getByTestId('ask-input')
    fireEvent.change(ta, { target: { value: '  你好  ' } })
    fireEvent.keyDown(ta, { key: 'Enter' })
    expect(onSend).toHaveBeenCalledWith('你好')
  })
  test('Shift+Enter 不送出', () => {
    const onSend = vi.fn()
    wrap({ onSend })
    const ta = screen.getByTestId('ask-input')
    fireEvent.change(ta, { target: { value: 'x' } })
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true })
    expect(onSend).not.toHaveBeenCalled()
  })
  test('空白不送出', () => {
    const onSend = vi.fn()
    wrap({ onSend })
    fireEvent.click(screen.getByTestId('ask-send'))
    expect(onSend).not.toHaveBeenCalled()
  })
})
```

```tsx
// ConversationSidebar.test.tsx
import { describe, expect, test, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { ConversationSidebar } from './ConversationSidebar'

const items = [{ conversation_id: 'c1', title: '問題一', last_at: null, turn_count: 1 }]
const wrap = (props: Partial<Parameters<typeof ConversationSidebar>[0]> = {}) =>
  render(
    <MantineProvider theme={theme}>
      <ConversationSidebar conversations={items} activeId={null} onOpen={vi.fn()} onDelete={vi.fn()} onNew={vi.fn()} {...props} />
    </MantineProvider>,
  )

describe('ConversationSidebar', () => {
  test('渲染清單、開啟呼叫 onOpen', () => {
    const onOpen = vi.fn()
    wrap({ onOpen })
    fireEvent.click(screen.getByTestId('ask-hist-open'))
    expect(onOpen).toHaveBeenCalledWith('c1')
  })
  test('刪除需確認後才 onDelete', () => {
    const onDelete = vi.fn()
    wrap({ onDelete })
    fireEvent.click(screen.getByTestId('ask-hist-del'))
    // ConfirmDialog 開啟 → 點確認
    fireEvent.click(screen.getByRole('button', { name: '刪除' }))
    expect(onDelete).toHaveBeenCalledWith('c1')
  })
  test('新對話呼叫 onNew', () => {
    const onNew = vi.fn()
    wrap({ onNew })
    fireEvent.click(screen.getByTestId('ask-new'))
    expect(onNew).toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/components/AskComposer.test.tsx src/features/ask/components/ConversationSidebar.test.tsx`
Expected: FAIL（元件不存在）

- [ ] **Step 3: 寫三個元件**

```tsx
// AskComposer.tsx
import { useState } from 'react'
import styles from './AskPage.module.css'

interface AskComposerProps {
  onSend: (q: string) => void
  disabled?: boolean
  examples?: string[]
}

export function AskComposer({ onSend, disabled, examples = [] }: AskComposerProps) {
  const [value, setValue] = useState('')
  const submit = (text?: string) => {
    const q = (text ?? value).trim()
    if (!q || disabled) return
    onSend(q)
    setValue('')
  }
  return (
    <div className={styles.composer}>
      {examples.length > 0 && (
        <div className={styles.examples}>
          {examples.map((ex) => (
            <button key={ex} type="button" className={styles.ex} onClick={() => submit(ex)}>
              {ex}
            </button>
          ))}
        </div>
      )}
      <textarea
        data-testid="ask-input"
        className={styles.input}
        value={value}
        placeholder="輸入你的問題…"
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <button type="button" data-testid="ask-send" className={styles.send} disabled={disabled} onClick={() => submit()}>
        送出
      </button>
    </div>
  )
}
```

```tsx
// ConfirmDialog.tsx
import { Button, Group, Modal, Text } from '@mantine/core'

interface ConfirmDialogProps {
  opened: boolean
  title?: string
  body?: string
  confirmLabel?: string
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({ opened, title = '確認', body = '', confirmLabel = '確認', onConfirm, onCancel }: ConfirmDialogProps) {
  return (
    <Modal opened={opened} onClose={onCancel} title={title} centered size="sm">
      <Text size="sm" mb="md">
        {body}
      </Text>
      <Group justify="flex-end">
        <Button variant="default" onClick={onCancel}>
          取消
        </Button>
        <Button color="red" onClick={onConfirm}>
          {confirmLabel}
        </Button>
      </Group>
    </Modal>
  )
}
```

```tsx
// ConversationSidebar.tsx
import { useState } from 'react'
import type { ConversationSummary } from '../schemas'
import { ConfirmDialog } from './ConfirmDialog'
import styles from './AskPage.module.css'

interface ConversationSidebarProps {
  conversations: ConversationSummary[]
  activeId: string | null
  onOpen: (id: string) => void
  onDelete: (id: string) => void
  onNew: () => void
}

export function ConversationSidebar({ conversations, activeId, onOpen, onDelete, onNew }: ConversationSidebarProps) {
  const [pending, setPending] = useState<string | null>(null)
  return (
    <aside className={styles.sidebar}>
      <button type="button" data-testid="ask-new" className={styles.newBtn} onClick={onNew}>
        + 新對話
      </button>
      <div className={styles.histList}>
        {conversations.length === 0 ? (
          <div className={styles.histEmpty}>尚無歷史對話</div>
        ) : (
          conversations.map((c) => (
            <div
              key={c.conversation_id}
              data-testid="ask-hist-item"
              className={c.conversation_id === activeId ? `${styles.histItem} ${styles.active}` : styles.histItem}
            >
              <button type="button" data-testid="ask-hist-open" className={styles.histOpen} title={c.title} onClick={() => onOpen(c.conversation_id)}>
                {c.title}
              </button>
              <button type="button" data-testid="ask-hist-del" aria-label="刪除此對話" className={styles.histDel} onClick={() => setPending(c.conversation_id)}>
                ×
              </button>
            </div>
          ))
        )}
      </div>
      <ConfirmDialog
        opened={pending !== null}
        title="刪除此對話？"
        body="將永久移除整個對話串，無法復原。"
        confirmLabel="刪除"
        onCancel={() => setPending(null)}
        onConfirm={() => {
          if (pending) onDelete(pending)
          setPending(null)
        }}
      />
    </aside>
  )
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/components/AskComposer.test.tsx src/features/ask/components/ConversationSidebar.test.tsx`
Expected: PASS（6 案）

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/ask/components/AskComposer.tsx frontend/src/features/ask/components/ConfirmDialog.tsx frontend/src/features/ask/components/ConversationSidebar.tsx frontend/src/features/ask/components/AskComposer.test.tsx frontend/src/features/ask/components/ConversationSidebar.test.tsx
git commit -m "$(cat <<'EOF'
feat(ask): AskComposer/ConversationSidebar/ConfirmDialog（Phase 3 Task 11）

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12: AskPage 組裝 + 路由 + CSS + e2e + 最終驗證

**Files:**
- Create: `frontend/src/features/ask/AskPage.tsx`
- Modify: `frontend/src/features/ask/components/AskPage.module.css`（填入完整樣式，取代 Task 9 佔位）
- Modify: `frontend/src/App.tsx`（加 `/ask` lazy route）
- Create: `frontend/e2e/ask.spec.mjs`
- Test: `frontend/src/features/ask/AskPage.test.tsx`

**Interfaces:**
- Consumes: `useAskStream`（Task 7）、`useConversations`（Task 8）、`getConversation`（`api.ts`）、`Turn`/`AskComposer`/`ConversationSidebar`（Task 10/11）、`ReportDetailModal`（`features/search/components/ReportDetailModal`）。
- Produces: `export default function AskPage(): JSX.Element`。
- 職責：組裝側欄 + thread + composer；`const ask = useAskStream()`；`const convos = useConversations()`；citation modal 用 `useState<string | null>(modalId)`；`onSend = ask.send` 後 `convos.refresh()`（done 後刷新清單——簡化：每次 send 後刷新，或在 streaming 轉 false 的 effect 刷新）；`onOpenConversation = async (id) => { const items = await getConversation(id); ask.loadConversation(items, id) }`；`onNew = ask.newConversation`；引用點擊 `onCite = (reportId) => setModalId(reportId)`。空 thread 顯示範例提問。

- [ ] **Step 1: 寫失敗測試 `AskPage.test.tsx`**

```tsx
import { describe, expect, test, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../theme'
import AskPage from './AskPage'

vi.mock('./api', () => ({
  getConversations: vi.fn().mockResolvedValue([]),
  getConversation: vi.fn().mockResolvedValue([]),
  deleteConversation: vi.fn(),
  sendFeedback: vi.fn(),
}))

afterEach(() => vi.restoreAllMocks())

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MemoryRouter initialEntries={['/ask']}>
      <QueryClientProvider client={qc}>
        <MantineProvider theme={theme}>
          <AskPage />
        </MantineProvider>
      </QueryClientProvider>
    </MemoryRouter>,
  )
}

describe('AskPage', () => {
  test('渲染輸入框與新對話鈕', async () => {
    renderPage()
    await waitFor(() => expect(screen.getByTestId('ask-input')).toBeInTheDocument())
    expect(screen.getByTestId('ask-new')).toBeInTheDocument()
  })
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/AskPage.test.tsx`
Expected: FAIL（AskPage 不存在）

- [ ] **Step 3: 寫 `AskPage.tsx`**

```tsx
import { useEffect, useRef, useState } from 'react'
import { useAskStream } from './hooks/useAskStream'
import { useConversations } from './hooks/useConversations'
import { getConversation } from './api'
import { Turn } from './components/Turn'
import { AskComposer } from './components/AskComposer'
import { ConversationSidebar } from './components/ConversationSidebar'
import { ReportDetailModal } from '../search/components/ReportDetailModal'
import styles from './components/AskPage.module.css'

const EXAMPLES = ['台積電最新展望如何？', 'AI 伺服器散熱有哪些重點？', '近期半導體產業趨勢']

export default function AskPage() {
  const ask = useAskStream()
  const convos = useConversations()
  const [modalId, setModalId] = useState<string | null>(null)
  const wasStreaming = useRef(false)

  // 串流由 true→false（一輪結束）後刷新側欄對話清單（convos.refresh 為 useCallback 穩定）
  useEffect(() => {
    if (wasStreaming.current && !ask.streaming) convos.refresh()
    wasStreaming.current = ask.streaming
  }, [ask.streaming, convos.refresh])

  const openConversation = async (id: string) => {
    try {
      const items = await getConversation(id)
      ask.loadConversation(items, id)
    } catch {
      /* 載入失敗不破壞現況 */
    }
  }

  return (
    <div className={styles.page}>
      <ConversationSidebar
        conversations={convos.conversations}
        activeId={ask.conversationId}
        onOpen={openConversation}
        onDelete={convos.remove}
        onNew={ask.newConversation}
      />
      <main className={styles.main}>
        <div className={styles.thread} data-testid="ask-thread">
          {ask.turns.length === 0 ? (
            <div className={styles.empty} data-testid="ask-empty">
              <div className={styles.emptyTitle}>有什麼想問的？</div>
            </div>
          ) : (
            ask.turns.map((t) => <Turn key={t.id} turn={t} onCite={setModalId} />)
          )}
        </div>
        <AskComposer onSend={ask.send} disabled={ask.streaming} examples={ask.turns.length === 0 ? EXAMPLES : []} />
      </main>
      <ReportDetailModal reportId={modalId} onClose={() => setModalId(null)} />
    </div>
  )
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/ask/AskPage.test.tsx`
Expected: PASS（1 案）

- [ ] **Step 5: 填入 `AskPage.module.css`（取代 Task 9 佔位）**

> 提供清晰、可用的版面樣式（不需逐像素對齊舊頁；視覺平價可後續微調）。至少涵蓋：`.page`（grid：sidebar 260px + main 1fr）、`.sidebar`/`.newBtn`/`.histList`/`.histItem`/`.histOpen`/`.histDel`/`.active`/`.histEmpty`、`.main`/`.thread`/`.empty`/`.emptyTitle`、`.turn`/`.msgUser`/`.msgBot`/`.notice`/`.noticeTitle`/`.error`/`.caret`（閃爍動畫）、`.sources`/`.src`/`.srcN`/`.badge`/`.srcName`/`.srcDate`/`.srcLatest`、`.ext`/`.extLink`/`.extBadge`、`.actions`/`.on`、`.composer`/`.examples`/`.ex`/`.input`/`.send`、`.process`/`.processHead`/`.processSteps`/`.step`/`.stepIco`/`.procIco`/`.spin`（旋轉 keyframes）。可參考舊頁 `web/static/index.html` 的 `.ask-*` CSS 取色與間距。

```css
/* frontend/src/features/ask/components/AskPage.module.css（節錄關鍵；實作者補齊上列 class） */
.page { display: grid; grid-template-columns: 260px 1fr; gap: 16px; height: calc(100vh - 120px); }
.sidebar { display: flex; flex-direction: column; gap: 8px; border-right: 1px solid #e9ecef; padding-right: 12px; overflow: auto; }
.main { display: flex; flex-direction: column; min-height: 0; }
.thread { flex: 1; overflow: auto; padding: 8px; }
.turn { margin-bottom: 24px; }
.msgUser { font-weight: 600; margin-bottom: 8px; }
.msgBot { line-height: 1.7; }
.caret { display: inline-block; width: 7px; height: 1.1em; background: #228be6; margin-left: 2px; vertical-align: text-bottom; animation: blink 1s step-end infinite; }
@keyframes blink { 50% { opacity: 0; } }
.spin { display: inline-block; width: 12px; height: 12px; border: 2px solid #ced4da; border-top-color: #228be6; border-radius: 50%; animation: spin 0.7s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
.composer { display: flex; gap: 8px; align-items: flex-end; border-top: 1px solid #e9ecef; padding-top: 12px; }
.input { flex: 1; min-height: 44px; max-height: 160px; resize: vertical; padding: 10px; border: 1px solid #ced4da; border-radius: 8px; font: inherit; }
.src { display: inline-flex; gap: 6px; align-items: center; margin: 4px 6px 0 0; padding: 4px 8px; border: 1px solid #dee2e6; border-radius: 8px; background: #fff; cursor: pointer; }
.badge { color: #fff; border-radius: 4px; padding: 0 6px; font-size: 12px; }
.actions button.on { color: #228be6; }
/* …其餘 class 由實作者補齊（見上列清單） */
```

- [ ] **Step 6: 加 `/ask` 路由到 `App.tsx`**

在 `App.tsx` 的 lazy import 區加：

```tsx
const AskPage = lazy(() => import('./features/ask/AskPage'))
```

在 router 的 routes 陣列（`/search` 之後、`*` 之前）加：

```tsx
    {
      path: '/ask',
      element: (
        <Layout>
          <Suspense fallback={<RouteFallback />}>
            <AskPage />
          </Suspense>
        </Layout>
      ),
    },
```

- [ ] **Step 7: 寫 e2e `frontend/e2e/ask.spec.mjs`**

> 沿用 `search.spec.mjs` 慣例：BASE 取 `ASK_BASE_URL ?? SEARCH_BASE_URL ?? MONITOR_BASE_URL ?? 'http://localhost:8097'`；creds 讀 repo `.env`；login flow；結束斷言 0 `console.error`。**對工作樹起的 :8098 跑**（非 :8097）。

```js
import { test, expect } from '@playwright/test'
import { readFileSync } from 'node:fs'

const BASE = process.env.ASK_BASE_URL ?? process.env.SEARCH_BASE_URL ?? process.env.MONITOR_BASE_URL ?? 'http://localhost:8097'

function creds() {
  const env = readFileSync(new URL('../../.env', import.meta.url), 'utf8')
  const u = env.match(/REPORT_MARK_ACCESS_USERNAME=(.*)/)?.[1]?.trim()
  const p = env.match(/REPORT_MARK_ACCESS_PASSWORD=(.*)/)?.[1]?.trim()
  return { u, p }
}

async function login(page) {
  const { u, p } = creds()
  await page.goto(`${BASE}/app/ask`)
  if (page.url().includes('/login')) {
    await page.fill('input[name="username"]', u)
    await page.fill('input[name="password"]', p)
    await page.click('button[type="submit"]')
    await page.waitForURL(/\/app\/ask/)
  }
}

test('ask：提問串流 + 來源 + 多輪', async ({ page }) => {
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))
  await login(page)
  await expect(page.getByTestId('ask-input')).toBeVisible()
  await page.getByTestId('ask-input').fill('台積電最新展望如何？')
  const resp = page.waitForResponse((r) => r.url().includes('/api/ask'))
  await page.getByTestId('ask-send').click()
  await resp
  // 串流答案出現（等回答泡泡有文字）
  await expect(page.getByTestId('ask-answer').first()).not.toBeEmpty({ timeout: 60000 })
  expect(errors).toEqual([])
})
```

- [ ] **Step 8: 最終驗證（全套）**

Run: `cd frontend && rtk proxy npx vitest run src`
Expected: 全綠（含既有 + Phase 3 新測；若 worker-pool flake，單跑 `src/features/ask` 確認）

Run: `cd frontend && rtk proxy npm run build`
Expected: exit 0（tsc + vite）

Run: `cd frontend && rtk proxy npm run lint`
Expected: exit 0

Run（live e2e，對工作樹 :8098；控制端先 `uv run uvicorn web.server:app --host 127.0.0.1 --port 8098` 起後端、authenticated `curl /api/search` 預熱 BGE-M3、`ASK_BASE_URL=http://localhost:8098 npx playwright test e2e/ask.spec.mjs`、用後 kill :8098）：
Expected: 1 passed、0 console error

- [ ] **Step 9: Commit**

```bash
git add frontend/src/features/ask/AskPage.tsx frontend/src/features/ask/components/AskPage.module.css frontend/src/App.tsx frontend/e2e/ask.spec.mjs frontend/src/features/ask/AskPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(ask): AskPage 組裝 + /app/ask 路由 + CSS + e2e（Phase 3 Task 12）

掛 /app/ask（lazy route），組裝側欄/thread/composer + 引用 modal；不 cutover。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## 完成準則

- 12 任務全綠：`npx vitest run src`（含既有測試不退化）、`npm run build`、`npm run lint` exit 0、`e2e/ask.spec.mjs` 對 :8098 1 passed。
- `/app/ask` 可登入 → 提問串流 → 來源/引用 modal → 多輪續問沿用 conversation_id → 歷史側欄開啟/刪除。
- 後端零變動、無 schema 變動、無 cutover、無 `dangerouslySetInnerHTML`。
- 深度研報（offer_report/`/api/report`/PDF）未做（Phase 4）。
