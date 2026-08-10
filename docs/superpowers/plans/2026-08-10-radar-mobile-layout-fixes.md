# 券商觀點雷達行動版排版修正 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓 Radar 的資料檢視在手機使用單欄券商卡片、圖表具備 44px 觸控命中區，並移除 favicon 404，同時維持桌面表格與既有資料語意。

**Architecture:** `ConsensusSummary` 保留單一 `tableView` 與 `selected` 狀態，資料檢視同時渲染桌面表格和行動卡片，再由 CSS 媒體查詢決定顯示模式；卡片與表格共用既有格式化與選取 callback。點圖只在行動媒體查詢放大按鈕與列高。favicon 使用 Vite `public` 靜態資產並由 `index.html` 明確引用。

**Tech Stack:** React 19、TypeScript、CSS Modules、Vitest、Testing Library、Vite、Playwright。

## Global Constraints

- 桌面版保留既有點圖與完整表格。
- `<= 720px` 的資料檢視使用單欄券商卡片，不要求水平滑動。
- 行動版點圖命中區至少 `44 × 44px`，視覺 `.mark` 維持 `11px`。
- 不修改後端 API、資料 schema、共識計算或時間窗控制。
- 不新增第三方套件或外部 favicon 依賴。
- 所有 shell 命令優先加 `rtk`；若環境無 `rtk`，使用原生命令並記錄原因。

---

### Task 1: 行動版券商資料卡片

**Files:**
- Modify: `frontend/src/features/radar/ConsensusSummary.tsx`
- Modify: `frontend/src/features/radar/ConsensusSummary.module.css`
- Test: `frontend/src/features/radar/ConsensusSummary.test.tsx`
- Test: `frontend/src/features/radar/RadarCssContracts.test.ts`

**Interfaces:**
- Consumes: `BrokerSummary[]`、`EpsBasis | null`、`currency: string | null`、`selected: string | null`、`onSelect(key: string): void`。
- Produces: `BrokerDataViews(props: TableProps)`，內含桌面 `BrokerTable` 與行動 `BrokerCards`，兩者共用 `epsForBasis`、`epsExclusionNote`、`reportFreshness`。

- [ ] **Step 1: 在元件測試加入行動卡片的失敗案例**

在 `ConsensusSummary.test.tsx` 的表格檢視測試後新增：

```tsx
it('資料檢視同時提供行動卡片，且卡片可選取同一家券商', () => {
  mount(sampleBrokers())
  fireEvent.click(screen.getByRole('button', { name: '切換表格檢視' }))

  const cards = screen.getByRole('list', { name: '各家目標價與 EPS 行動版' })
  expect(within(cards).getByText('凱基')).toBeInTheDocument()
  expect(within(cards).getByText('NT$478')).toBeInTheDocument()
  expect(within(cards).getByText('FY26E EPS')).toBeInTheDocument()
  expect(within(cards).getByText('2026/07/11')).toBeInTheDocument()

  fireEvent.click(within(cards).getByRole('button', { name: /選取凱基/ }))
  expect(within(screen.getByTestId('consensus-selected')).getByText('凱基')).toBeInTheDocument()
})
```

- [ ] **Step 2: 執行窄測試並確認因行動卡片不存在而失敗**

Run: `npm --prefix frontend test -- ConsensusSummary.test.tsx`

Expected: FAIL，`Unable to find an accessible element with the role "list" and name "各家目標價與 EPS 行動版"`。

- [ ] **Step 3: 在 CSS 契約加入響應式顯示的失敗案例**

在 `RadarCssContracts.test.ts` 匯入同一份 `consensusSummaryCss` 的既有測試區新增：

```ts
it('資料檢視在桌面顯示表格、720px 以下改顯示卡片', () => {
  expectDeclaration(consensusSummaryCss, '.mobileCards', 'display', 'none')
  const mobile = mediaBlock(consensusSummaryCss, 720)
  expectDeclaration(mobile, '.tableWrap', 'display', 'none')
  expectDeclaration(mobile, '.mobileCards', 'display', 'grid')
  expectDeclaration(mobile, '.mobileCards', 'grid-template-columns', '1fr')
})
```

- [ ] **Step 4: 執行 CSS 契約並確認缺少 `.mobileCards` 而失敗**

Run: `npm --prefix frontend test -- RadarCssContracts.test.ts`

Expected: FAIL，訊息指出 `.mobileCards 應存在`。

- [ ] **Step 5: 實作共用資料檢視與行動卡片**

在 `ConsensusSummary.tsx` 將 `tableView` 分支改為：

```tsx
<BrokerDataViews
  brokers={visible}
  currency={currency}
  basis={basis}
  selected={activeKey}
  onSelect={toggleSelect}
/>
```

新增 `BrokerDataViews` 與 `BrokerCards`。`BrokerCards` 使用以下結構，實際欄位值沿用 `BrokerTable` 的既有分支與註記：

```tsx
function BrokerDataViews(props: TableProps) {
  return (
    <>
      <BrokerTable {...props} />
      <BrokerCards {...props} />
    </>
  )
}

function BrokerCards({ brokers, currency, basis, selected, onSelect }: TableProps) {
  return (
    <ul className={styles.mobileCards} aria-label="各家目標價與 EPS 行動版">
      {brokers.map((broker) => {
        const key = brokerKey(broker)
        const isSelected = key === selected
        return (
          <li key={key} className={`${styles.mobileCard} ${isSelected ? styles.mobileCardOn : ''}`}>
            <button
              type="button"
              className={styles.mobilePick}
              aria-pressed={isSelected}
              aria-label={`選取${brokerName(broker)}`}
              onClick={() => onSelect(key)}
            >
              {brokerName(broker)}
            </button>
            <dl className={styles.mobileFacts}>{/* 五個具名欄位 */}</dl>
          </li>
        )
      })}
    </ul>
  )
}
```

空資料時在卡片清單後顯示與表格相同的 `styles.tableEmpty` 文案。

- [ ] **Step 6: 新增桌面／行動顯示 CSS**

在 `ConsensusSummary.module.css` 新增：

```css
.mobileCards {
  display: none;
  margin: 0;
  padding: 12px;
  list-style: none;
  gap: 10px;
}

.mobileCard {
  padding: 14px;
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-card);
  background: var(--tf-surface);
}

.mobileCardOn { background: var(--tf-graphite-tint); }

@media (max-width: 720px) {
  .tableWrap { display: none; }
  .mobileCards {
    display: grid;
    grid-template-columns: 1fr;
  }
}
```

`.mobilePick` 必須有 `min-height: 44px` 與可見 `:focus-visible`；`.mobileFacts` 使用兩欄 `dt`／`dd`，窄螢幕仍保持欄位名稱與值同行。

- [ ] **Step 7: 執行兩個窄測試並確認通過**

Run: `npm --prefix frontend test -- ConsensusSummary.test.tsx RadarCssContracts.test.ts`

Expected: PASS，無 warning 或未處理 Promise。

- [ ] **Step 8: 提交行動卡片**

```bash
git add frontend/src/features/radar/ConsensusSummary.tsx frontend/src/features/radar/ConsensusSummary.module.css frontend/src/features/radar/ConsensusSummary.test.tsx frontend/src/features/radar/RadarCssContracts.test.ts
git commit -m "fix(radar): 行動版資料檢視改用券商卡片" -m "保留桌面完整表格，讓 720px 以下使用具名欄位卡片並共用既有選取與口徑邏輯，避免水平捲動與表頭遮擋。"
```

### Task 2: 行動版點圖觸控目標

**Files:**
- Modify: `frontend/src/features/radar/BrokerDotPlot.module.css`
- Test: `frontend/src/features/radar/RadarCssContracts.test.ts`

**Interfaces:**
- Consumes: 現有 `.dot`、`.mark`、`.name`、`.track` 與 `.axisRow` CSS 結構。
- Produces: `@media (max-width: 720px)` 下 `44px` 的 `.dot`、`.track` 與券商名行高；桌面 `.dot` 維持 `30px`。

- [ ] **Step 1: 將觸控契約改為桌面 30px、行動 44px**

在 `RadarCssContracts.test.ts` 保留既有桌面契約，另新增：

```ts
it('點圖在 720px 以下提供 44px 觸控命中區且視覺圓點不放大', () => {
  const mobile = mediaBlock(brokerDotPlotCss, 720)
  expectDeclaration(mobile, '.dot', 'width', '44px')
  expectDeclaration(mobile, '.dot', 'height', '44px')
  expectDeclaration(mobile, '.track', 'height', '44px')
  expectDeclaration(brokerDotPlotCss, '.mark', 'width', '11px')
  expectDeclaration(brokerDotPlotCss, '.mark', 'height', '11px')
})
```

- [ ] **Step 2: 執行測試並確認缺少 720px 媒體規則而失敗**

Run: `npm --prefix frontend test -- RadarCssContracts.test.ts`

Expected: FAIL，訊息指出 `@media (max-width: 720px) 應存在` 或 `.dot 應存在`。

- [ ] **Step 3: 實作行動觸控尺寸**

在 `BrokerDotPlot.module.css` 新增：

```css
@media (max-width: 720px) {
  .name { line-height: 44px; }
  .track { height: 44px; }
  .dot {
    width: 44px;
    height: 44px;
  }
}
```

不修改 `.mark`、軸位置函式或 DOM 結構。

- [ ] **Step 4: 執行 CSS 契約並確認通過**

Run: `npm --prefix frontend test -- RadarCssContracts.test.ts`

Expected: PASS。

- [ ] **Step 5: 提交觸控修正**

```bash
git add frontend/src/features/radar/BrokerDotPlot.module.css frontend/src/features/radar/RadarCssContracts.test.ts
git commit -m "fix(radar): 放大行動版點圖觸控範圍" -m "僅在 720px 以下把資料點與列高調整為 44px，維持 11px 視覺圓點與桌面資訊密度。"
```

### Task 3: Favicon 與完整驗證

**Files:**
- Create: `frontend/public/favicon.svg`
- Modify: `frontend/index.html`
- Test: `frontend/src/App.test.tsx` 或新增 `frontend/src/faviconContract.test.ts`
- Build output: `frontend/dist/**`（由 `make build-web` 產生；只依專案既有追蹤策略處理）

**Interfaces:**
- Consumes: Vite `public` 目錄靜態資產規則與 `base: '/app/'`。
- Produces: `/app/favicon.svg` 連結與可由 FastAPI SPA 靜態服務取得的建置資產。

- [ ] **Step 1: 新增 favicon HTML 契約測試**

新增 `frontend/src/faviconContract.test.ts`：

```ts
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

describe('favicon contract', () => {
  it('index.html 引用 app base 下的本機 SVG favicon', () => {
    const root = fileURLToPath(new URL('../', import.meta.url))
    const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8')
    const favicon = readFileSync(`${root}public/favicon.svg`, 'utf8')
    expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/app/favicon.svg" />')
    expect(favicon).toContain('<svg')
  })
})
```

- [ ] **Step 2: 執行測試並確認 favicon 缺失而失敗**

Run: `npm --prefix frontend test -- faviconContract.test.ts`

Expected: FAIL，`ENOENT` 或缺少 `<link rel="icon">`。

- [ ] **Step 3: 新增本機 favicon 並引用**

建立 `frontend/public/favicon.svg`，使用 `32 × 32` viewBox、石墨底與金色簡單圖形；在 `frontend/index.html` 的 `<title>` 前加入：

```html
<link rel="icon" type="image/svg+xml" href="/app/favicon.svg" />
```

- [ ] **Step 4: 執行 favicon 契約並確認通過**

Run: `npm --prefix frontend test -- faviconContract.test.ts`

Expected: PASS。

- [ ] **Step 5: 執行完整前端品質檢查**

Run:

```bash
npm --prefix frontend test
npm --prefix frontend run typecheck
npm --prefix frontend run lint
npm --prefix frontend run build
```

Expected: 四個命令均 exit 0；Vitest 無失敗、TypeScript 無錯誤、ESLint 無 error、Vite build 完成。

- [ ] **Step 6: 重建後端實際服務使用的 SPA**

Run: `make build-web`

Expected: `frontend/dist` 更新完成且命令 exit 0。

- [ ] **Step 7: 以隔離 Playwright 系統 Chrome 驗證真實頁面**

Flow: `/app/radar?market=TW&code=1476&window=90` → 切換表格檢視 → 桌面顯示表格；390px 顯示券商卡片 → 回圖表檢查資料點命中區。

驗證值：

```js
{
  desktopTableVisible: true,
  mobileCardsVisible: true,
  mobileTableVisible: false,
  mobileDocumentOverflow: false,
  mobileDotWidth: 44,
  mobileDotHeight: 44,
  faviconErrors: 0,
  pageErrors: 0
}
```

將臨時腳本、截圖與 trace 寫入系統暫存目錄，不寫入 repo。桌面與 390px 行動版各保留一張修正後截圖。

- [ ] **Step 8: 檢查最終差異並提交 favicon／驗證相關變更**

```bash
git diff
git status --short
git add frontend/index.html frontend/public/favicon.svg frontend/src/faviconContract.test.ts frontend/dist
git commit -m "fix(frontend): 補上應用程式 favicon" -m "使用本機 SVG 資產並從 /app 明確引用，移除瀏覽器 favicon 404 與 console 雜訊。"
```

若 `frontend/dist` 依現況未被 Git 追蹤，從 `git add` 清單移除，不改變專案既有產物策略。
