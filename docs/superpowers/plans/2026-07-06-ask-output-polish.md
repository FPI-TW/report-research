# 問答對話輸出優化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 `/app/ask` 助理回答本文的排版對齊設計稿、補齊 markdown 解析漏洞（引言/表格不再外洩）、修正串流游標掉行，使問答輸出視覺一致且專業。

**Architecture:** 就地強化既有 CSS Modules 與自製 markdown 渲染器，只動三個前端檔案的樣式與解析邏輯，外加測試。不引入 markdown 函式庫（維持零工具鏈與「React 文字轉義保證 XSS 安全」的既有模型）。後端完全不碰。

**Tech Stack:** React 19 + TypeScript、CSS Modules、Vitest + @testing-library/react（jsdom）、Vite。

## Global Constraints

- **後端零改動**：只動 `frontend/`，不碰 `app/`、不改 API/schema。
- **不引入 markdown 函式庫**：延用 `askMarkdown.tsx` 自製解析器；XSS 安全靠 React 自動文字轉義（原始 HTML 不得被解讀為標籤）。
- **忠於設計稿數值**（`docs/design/廷豐智能研報.dc.html`）：p `margin 0 0 12px`；ul `padding-left 20px`；li `margin-bottom 6px`；h3 `16px` 襯線 `--tf-text-1`；引用膠囊維持現況。
- **樣式一律用 `tokens.css` 變數**（`--tf-*`），不寫死色碼。
- **CSS Modules 注意**：`.body` 內用**標籤選擇器**（`h3/p/ul/li/a/code/pre/blockquote/table/th/td`）自動受 `.body` scope；渲染器用字面 `className="tableWrap"` 的類，CSS 需用 `:global(.tableWrap)`（比照既有 `:global(.tf-cite)`/`:global(.tf-md-h)`）。
- **測試以標籤/屬性選擇器斷言**，不依賴 CSS Modules 雜湊類名（`css:true`）。若 `rtk` 遮蔽 vitest 非零 exit code，改用 `rtk proxy npx vitest run <file>`。
- **YAGNI**：不做程式碼區塊逐塊複製鈕、不做串流逐區塊進場動畫。
- **共用工作目錄**：只 `git add` 明確檔案，禁用 `git add -A`/`.`；提交前 `git diff --staged --stat` 驗範圍。提交訊息尾附
  `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`。

---

## File Structure

| 檔案 | 職責 | 本計畫異動 |
|---|---|---|
| `frontend/src/features/ask/AssistantMessage.module.css` | 助理訊息（本文/操作列/游標）樣式 | Task 1 新增本文排版；Task 3 換串流游標 |
| `frontend/src/features/ask/AssistantMessage.tsx` | 助理訊息元件 | Task 3 串流旗標、移除獨立游標 span |
| `frontend/src/lib/askMarkdown.tsx` | 自製 markdown → React 渲染 | Task 2 新增引言/表格解析 |
| `frontend/src/lib/askMarkdown.test.tsx` | 渲染器測試 | Task 2 新增測試 |
| `frontend/src/features/ask/AssistantMessage.test.tsx` | 元件測試 | Task 3 新增串流旗標測試 |

---

## Task 1：本文排版樣式（對齊設計稿）

純 CSS，無單元測試（jsdom 不可靠地套用 CSS Module 計算樣式）。以 build/typecheck/lint 通過 + 人工視覺確認驗收。行為/結構測試落在 Task 2、3。

**Files:**
- Modify: `frontend/src/features/ask/AssistantMessage.module.css`

**Interfaces:**
- Consumes: `tokens.css` 變數；渲染器產出的 `<p>/<ul>/<ol>/<li>/<h3>/<h4>/<a>/<strong>/<code>/<pre>/<blockquote>` 及 `:global(.tableWrap) > <table>`（表格由 Task 2 產出，本任務可先備妥樣式）。
- Produces: `.body` 下的完整本文排版樣式，供 Task 2/3 產出的元素套用。

- [ ] **Step 1: 在 `.body :global(.tf-md-h)` 規則之後插入本文排版樣式**

在 `AssistantMessage.module.css` 第 4 行（`.body :global(.tf-md-h) { ... }`）之後、`.caret` 之前插入：

```css
.body h3 { font-size: 16px; }
.body h4 { font-size: 14.5px; }
.body p { margin: 0 0 12px; }
.body ul, .body ol { margin: 0 0 12px; padding-left: 20px; }
.body li { margin-bottom: 6px; }
.body li:last-child { margin-bottom: 0; }
.body a { color: var(--tf-gold-text); text-decoration: underline; text-underline-offset: 2px; }
.body a:hover { color: var(--tf-gold-hover); }
.body strong { color: var(--tf-text-1); font-weight: 700; }
.body code { font-family: var(--tf-mono); font-size: 0.9em; background: var(--tf-gold-tint); border: 1px solid var(--tf-border-weak); border-radius: 5px; padding: 1px 5px; }
.body pre { background: var(--tf-canvas); border: 1px solid var(--tf-border); border-radius: var(--tf-radius-md); padding: 12px 14px; margin: 0 0 12px; overflow-x: auto; }
.body pre code { background: none; border: none; padding: 0; font-size: 13px; line-height: 1.6; }
.body blockquote { border-left: 3px solid var(--tf-gold-strong); background: var(--tf-gold-tint); padding: 8px 14px; margin: 0 0 12px; border-radius: 0 var(--tf-radius-md) var(--tf-radius-md) 0; color: var(--tf-text-2); }
.body :global(.tableWrap) { overflow-x: auto; margin: 0 0 12px; }
.body table { border-collapse: collapse; font-size: 14px; }
.body th, .body td { border: 1px solid var(--tf-border); padding: 6px 10px; text-align: left; vertical-align: top; }
.body thead th { background: var(--tf-border-weak); color: var(--tf-text-1); font-weight: 700; }
.body > :last-child { margin-bottom: 0; }
```

- [ ] **Step 2: 驗證 build 與靜態檢查通過**

Run: `cd frontend && npm run build && npm run lint`
Expected: 皆 exit 0（CSS 變更不影響 tsc/eslint；vite build 成功）。

- [ ] **Step 3: Commit**

```bash
cd /mnt/c/Users/User/Desktop/Project/report-mark
git add frontend/src/features/ask/AssistantMessage.module.css
git diff --staged --stat
git commit -m "$(cat <<'EOF'
feat(問答): 答案本文排版對齊設計稿

補齊 .body 的 p/ul/ol/li 間距、標題字級（h3 16px）、金色連結、行內
code、程式碼區塊、引言 callout 與表格樣式，取代先前吃瀏覽器預設值
造成的鬆散排版與藍色底線連結。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2：解析器補洞 — 引言 `>` 與 GFM 表格

TDD。整體替換 `renderAnswer` 的行掃描（`for...of` → 索引 `for` 以支援表格前瞻），新增引言與表格區塊，杜絕 `>`/`|` 字面外洩。`renderAnswer` 對外簽章不變。

**Files:**
- Modify: `frontend/src/lib/askMarkdown.tsx`（`renderAnswer`）
- Test: `frontend/src/lib/askMarkdown.test.tsx`

**Interfaces:**
- Consumes: 既有 `renderInline(text, sourceCount, onCite, keyBase)`（不動）。
- Produces: `renderAnswer(md: string, sourceCount: number, onCite: (n: number) => void): ReactNode`（簽章不變）。新增輸出 `<blockquote>`、`<div className="tableWrap"><table><thead><tr><th>…</thead><tbody><tr><td>…</tbody></table></div>`。

- [ ] **Step 1: 寫失敗測試**

在 `askMarkdown.test.tsx` 末尾追加（檔頭已 import `render, screen, fireEvent`、`expect, test, vi`、`renderAnswer`）：

```tsx
test('引言 > 渲染為 blockquote，不外洩 > 字面', () => {
  const { container } = render(<div>{renderAnswer('> 這是引言\n> 第二行', 0, () => {})}</div>)
  const bq = container.querySelector('blockquote')
  expect(bq).toBeTruthy()
  expect(bq?.textContent).toContain('這是引言')
  expect(container.textContent).not.toContain('>')
})

test('GFM 表格渲染為 table，含表頭與資料列，不外洩 | 字面', () => {
  const md = '| 券商 | 評等 |\n| --- | --- |\n| 元大 | 買進 |\n| 凱基 | 中立 |'
  const { container } = render(<div>{renderAnswer(md, 0, () => {})}</div>)
  expect(container.querySelector('table')).toBeTruthy()
  expect(container.querySelectorAll('th')).toHaveLength(2)
  expect(container.querySelectorAll('tbody tr')).toHaveLength(2)
  expect(container.querySelectorAll('td')).toHaveLength(4)
  expect(container.textContent).not.toContain('|')
})

test('表格儲存格內 [n] 仍為可點膠囊', () => {
  const onCite = vi.fn()
  const md = '| 標的 | 來源 |\n| --- | --- |\n| 台積電 | [1] |'
  render(<div>{renderAnswer(md, 3, onCite)}</div>)
  fireEvent.click(screen.getByRole('button', { name: '1' }))
  expect(onCite).toHaveBeenCalledWith(1)
})

test('缺分隔列的 | a | b | 不誤判為表格', () => {
  const { container } = render(<div>{renderAnswer('比較 | A | B | 三者', 0, () => {})}</div>)
  expect(container.querySelector('table')).toBeNull()
  expect(container.textContent).toContain('| A | B |')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/askMarkdown.test.tsx`
Expected: 新增 4 個測試 FAIL（無 `blockquote`/`table`，`>`/`|` 外洩）；既有 4 個 PASS。

- [ ] **Step 3: 以完整新版取代 `renderAnswer`**

將 `askMarkdown.tsx` 中的 `export function renderAnswer(...) { ... }`（第 34–66 行）整體替換為：

```tsx
export function renderAnswer(md: string, sourceCount: number, onCite: (n: number) => void): ReactNode {
  const lines = md.split('\n')
  const blocks: ReactNode[] = []
  let para: string[] = []
  let ul: string[] = []
  let ol: string[] = []
  let quote: string[] = []
  let code: string[] | null = null
  let k = 0

  const flushPara = () => { if (para.length) { blocks.push(<p key={`p${k++}`}>{renderInline(para.join(' '), sourceCount, onCite, `p${k}`)}</p>); para = [] } }
  const flushUl = () => { if (ul.length) { const items = ul; blocks.push(<ul key={`ul${k++}`}>{items.map((t, i) => <li key={i}>{renderInline(t, sourceCount, onCite, `ul${k}-${i}`)}</li>)}</ul>); ul = [] } }
  const flushOl = () => { if (ol.length) { const items = ol; blocks.push(<ol key={`ol${k++}`}>{items.map((t, i) => <li key={i}>{renderInline(t, sourceCount, onCite, `ol${k}-${i}`)}</li>)}</ol>); ol = [] } }
  const flushQuote = () => { if (quote.length) { const items = quote; blocks.push(<blockquote key={`bq${k++}`}>{renderInline(items.join(' '), sourceCount, onCite, `bq${k}`)}</blockquote>); quote = [] } }
  const flushAll = () => { flushPara(); flushUl(); flushOl(); flushQuote() }

  const splitRow = (s: string): string[] => {
    const cells = s.trim().split('|').map(c => c.trim())
    if (cells.length && cells[0] === '') cells.shift()
    if (cells.length && cells[cells.length - 1] === '') cells.pop()
    return cells
  }
  const isTableSep = (s: string): boolean => {
    const t = s.trim()
    if (!t.includes('|') || !t.includes('-')) return false
    const cells = splitRow(t)
    return cells.length > 0 && cells.every(c => /^:?-+:?$/.test(c))
  }

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    if (line.trim().startsWith('```')) {
      if (code === null) { flushAll(); code = [] } else { blocks.push(<pre key={`code${k++}`}><code>{code.join('\n')}</code></pre>); code = null }
      continue
    }
    if (code !== null) { code.push(line); continue }
    // GFM 表格：表頭列 + 分隔列（缺分隔列則不進入表格模式）
    if (line.includes('|') && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushAll()
      const headers = splitRow(line)
      i += 1 // 跳過分隔列
      const rows: string[][] = []
      while (i + 1 < lines.length && lines[i + 1].includes('|') && lines[i + 1].trim() !== '') {
        i += 1
        rows.push(splitRow(lines[i]))
      }
      const tk = k++
      blocks.push(
        <div className="tableWrap" key={`tbl${tk}`}>
          <table>
            <thead><tr>{headers.map((h, j) => <th key={j}>{renderInline(h, sourceCount, onCite, `th${tk}-${j}`)}</th>)}</tr></thead>
            <tbody>{rows.map((r, ri) => <tr key={ri}>{r.map((cell, ci) => <td key={ci}>{renderInline(cell, sourceCount, onCite, `td${tk}-${ri}-${ci}`)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )
      continue
    }
    const h = /^(#{1,6})\s+(.*)$/.exec(line)
    if (h) { flushAll(); const lvl = Math.min(h[1].length, 4); const Tag = (lvl <= 3 ? 'h3' : 'h4') as 'h3' | 'h4'; blocks.push(<Tag key={`h${k++}`} className="tf-md-h">{renderInline(h[2], sourceCount, onCite, `h${k}`)}</Tag>); continue }
    const bq = /^>\s?(.*)$/.exec(line)
    if (bq) { flushPara(); flushUl(); flushOl(); quote.push(bq[1]); continue }
    const uli = /^[-*]\s+(.*)$/.exec(line)
    if (uli) { flushPara(); flushOl(); flushQuote(); ul.push(uli[1]); continue }
    const oli = /^\d+\.\s+(.*)$/.exec(line)
    if (oli) { flushPara(); flushUl(); flushQuote(); ol.push(oli[1]); continue }
    if (line.trim() === '') { flushAll(); continue }
    flushUl(); flushOl(); flushQuote(); para.push(line.trim())
  }
  if (code !== null) blocks.push(<pre key={`code${k++}`}><code>{code.join('\n')}</code></pre>)
  flushAll()
  return <>{blocks}</>
}
```

- [ ] **Step 4: 跑測試確認全綠**

Run: `cd frontend && npx vitest run src/lib/askMarkdown.test.tsx`
Expected: 8 個測試全 PASS。

- [ ] **Step 5: 型別與 lint**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: 皆 exit 0。

- [ ] **Step 6: Commit**

```bash
cd /mnt/c/Users/User/Desktop/Project/report-mark
git add frontend/src/lib/askMarkdown.tsx frontend/src/lib/askMarkdown.test.tsx
git diff --staged --stat
git commit -m "$(cat <<'EOF'
feat(問答): markdown 解析支援引言與 GFM 表格

renderAnswer 新增 blockquote（> 行）與 GFM 表格（表頭＋分隔列）解析，
杜絕 > 與 | 字面外洩；儲存格內仍支援 [n] 引用。對外簽章不變，既有
測試全綠。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3：串流行內游標（修游標掉行）

TDD。游標從「本文結構後的獨立區塊 `<span>`」改為 CSS 偽元素，貼在最後一塊尾端行內。以 `data-streaming` 屬性同時作為樣式鉤與測試鉤（避免依賴 CSS Modules 雜湊類名）。

**Files:**
- Modify: `frontend/src/features/ask/AssistantMessage.tsx`
- Modify: `frontend/src/features/ask/AssistantMessage.module.css`
- Test: `frontend/src/features/ask/AssistantMessage.test.tsx`

**Interfaces:**
- Consumes: `Turn.phase`（`'streaming'` 時顯示游標）、`renderAnswer`（不變）。
- Produces: 串流時本文 `<div className={styles.body} data-streaming="">`；非串流時無 `data-streaming` 屬性。移除 `styles.caret` 及其 span。

- [ ] **Step 1: 寫失敗測試**

在 `AssistantMessage.test.tsx` 末尾追加（沿用該檔既有的 `turn()` 工廠與 `noop`）：

```tsx
test('串流中：本文帶 data-streaming（行內游標鉤）', () => {
  const { container } = render(
    <AssistantMessage turn={turn({ phase: 'streaming', answer: '生成中的內容', qaId: null })}
      onCite={noop} onOpenSources={noop} onFeedback={noop} onNoticeRetry={noop} onErrorRetry={noop} />
  )
  expect(container.querySelector('[data-streaming]')).toBeTruthy()
})

test('done：本文不帶 data-streaming', () => {
  const { container } = render(
    <AssistantMessage turn={turn({})}
      onCite={noop} onOpenSources={noop} onFeedback={noop} onNoticeRetry={noop} onErrorRetry={noop} />
  )
  expect(container.querySelector('[data-streaming]')).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/ask/AssistantMessage.test.tsx`
Expected: 「串流中：本文帶 data-streaming」FAIL（目前無此屬性）；「done：不帶」PASS（本就無）。

- [ ] **Step 3: 改 `AssistantMessage.tsx` 本文區塊**

將（第 38–43 行）：

```tsx
      {turn.answer && (
        <div className={styles.body}>
          {renderAnswer(turn.answer, turn.sources.length, onCite)}
          {turn.phase === 'streaming' && <span className={styles.caret} />}
        </div>
      )}
```

替換為：

```tsx
      {turn.answer && (
        <div className={styles.body} data-streaming={turn.phase === 'streaming' ? '' : undefined}>
          {renderAnswer(turn.answer, turn.sources.length, onCite)}
        </div>
      )}
```

- [ ] **Step 4: 改 `AssistantMessage.module.css` 游標**

將 `.caret` 規則（第 5 行）：

```css
.caret { display: inline-block; width: 7px; height: 15px; background: var(--tf-gold-text); margin-left: 2px; vertical-align: text-bottom; animation: tf-pulse 1s steps(2) infinite; }
```

替換為：

```css
.body[data-streaming] > :last-child::after {
  content: '';
  display: inline-block;
  width: 7px;
  height: 1em;
  margin-left: 2px;
  vertical-align: text-bottom;
  background: var(--tf-gold-text);
  animation: tf-pulse 1s steps(2) infinite;
}
```

- [ ] **Step 5: 跑測試確認全綠**

Run: `cd frontend && npx vitest run src/features/ask/AssistantMessage.test.tsx`
Expected: 全 PASS（含既有測試與 2 個新測試）。

- [ ] **Step 6: 全前端收斂（測試＋型別＋lint＋build）**

Run: `cd frontend && npx vitest run && npm run typecheck && npm run lint && npm run build`
Expected: 皆 exit 0。若 `rtk` 遮蔽 vitest 非零 exit，改 `rtk proxy npx vitest run` 覆核。

- [ ] **Step 7: Commit**

```bash
cd /mnt/c/Users/User/Desktop/Project/report-mark
git add frontend/src/features/ask/AssistantMessage.tsx frontend/src/features/ask/AssistantMessage.module.css frontend/src/features/ask/AssistantMessage.test.tsx
git diff --staged --stat
git commit -m "$(cat <<'EOF'
feat(問答): 串流游標改行內偽元素，修掉游標掉行

移除本文結構後的獨立游標 span（區塊相鄰導致串流時游標掉到下一行左側
閃爍），改用 .body[data-streaming] 的 ::after 偽元素貼在末塊尾端行內。
data-streaming 兼作測試鉤。

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## 人工視覺驗收（三個 Task 完成後）

CSS 視覺無法由單元測試涵蓋，需一次人工確認（沿用既有 e2e 模式：對工作樹起的 `:8098` 跑、預熱 BGE-M3、用後 `kill`；核對埠避免誤殺正式 `:8097`）。問一個能觸發清單＋標題＋引用的問題，確認：

- 段落間距緊湊一致、思考卡與首段之間無多餘空白
- 清單縮排 20px、條目有 6px 間距
- 標題明顯大於內文（h3 16px 襯線）
- 外部參考連結為金色（非藍色底線）
- 串流時游標貼在最後一段文字尾端行內閃爍（不掉行）
- 若回答含表格/引言 → 正確渲染為 `<table>`/callout，無 `|`/`>` 外洩

（此為驗收檢查清單，非程式步驟；不需獨立 commit。）

---

## Self-Review

**Spec 覆蓋：**
- A 本文排版 → Task 1 ✓
- B 引言 + 表格解析 → Task 2 ✓
- C 串流行內游標 → Task 3 ✓
- D YAGNI 排除 → Global Constraints 明列（不做逐塊複製鈕/逐區塊動畫）✓
- E 測試 → Task 2（引言/表格/膠囊/防外洩）＋ Task 3（串流旗標）✓

**與 spec 的合理偏差（已在此記錄）：**
- 串流游標鉤採 `data-streaming` **屬性**而非 `.streaming` 類（同為 CSS 偽元素行內游標，屬性更利於測試且免 CSS Modules 雜湊困擾）。
- blockquote 內容以單一 `renderInline` 呈現（不含內層 `<p>`），故 spec 提及的 `.body blockquote p:last-child` 規則不需要，已從 Task 1 CSS 省略。

**型別/命名一致性：** `renderAnswer` 簽章跨任務不變；`splitRow`/`isTableSep` 僅 Task 2 內部使用；`data-streaming` 屬性名於 Task 3 元件與 CSS 一致。

**Placeholder 掃描：** 無 TBD/TODO；每個 code 步驟含完整程式碼。
