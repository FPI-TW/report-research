# 問答對話輸出優化（本文排版 × markdown 補洞 × 串流動效）

日期：2026-07-06
分支：`feat/react-spa-rebuild`
狀態：設計定案，待 writing-plans

## 背景與問題

問答頁（`/app/ask`）的助理回答「看起來有問題」。經稽核，根因是**答案本文有一份精確的設計稿規範（`docs/design/廷豐智能研報.dc.html`），但目前 React 實作幾乎沒把它落地**：本文的段落、清單、連結、程式碼全部落到瀏覽器預設值，與整套金色／襯線品牌系統嚴重不搭；同時 markdown 解析器漏接數種語法，導致原始符號外洩。

四類使用者感受到的問題全部匯流到同一批根因：

| 使用者感受 | 根因 |
|---|---|
| markdown 排版殘留 | 解析器未處理引言 `>`、表格 `\|…\|`；標題漏字級 |
| 間距與字體排版 | `.body` 無 `p/ul/li` 排版樣式 → 吃瀏覽器預設（p 上下 ~16px、ul 縮排 40px、li 無間距、標題與內文同大） |
| 引用與來源顯示 | 膠囊/來源本身符合設計稿；連結為瀏覽器藍底線，與金色系不搭 |
| 串流／思考動態 | 游標是本文結構後的獨立區塊元素 → 串流時掉到下一行左側閃爍 |

### 現況事實（稽核結論）

- 全域 `frontend/src/styles/tokens.css` 只有 CSS 變數與 `body` 基調，**無** `p/ul/ol/li/a/code/pre/blockquote` 任何基礎排版。
- `frontend/src/features/ask/AssistantMessage.module.css` 的 `.body` 只定義 `.tf-cite`（引用膠囊）與 `.tf-md-h`（標題），且 `.tf-md-h` **漏 `font-size`**。
- `frontend/src/lib/askMarkdown.tsx` 的 `renderAnswer` 支援：段落、`ul/ol`、`h1~6→h3/h4`、程式碼圍欄、行內（粗體/斜體/行內 code/http 連結/`[n]` 膠囊）。**不支援**：引言、表格。
- 已符合設計稿、**不動**的部分：使用者金色氣泡（`UserMessage`）、引用膠囊、操作列（讚/倒讚/複製/資料來源）、思考步驟卡（`ThinkingSteps`）。

### 非目標（Out of scope）

- 後端零改動（`app/` 完全不碰）。
- 不改對話流版面、側欄、來源抽屜、深度研報面板的既有結構。
- 不做程式碼區塊的「逐塊複製」按鈕（問答答案幾乎不含 code block，且已有整段答案複製鈕）——YAGNI。
- 不做串流中「逐區塊進場動畫」（會與逐字打字成長打架、造成閃爍；打字感由行內游標承載）。
- 段落軟換行的 CJK 空格問題不在此次處理範圍。

## 設計稿目標數值（來自 `廷豐智能研報.dc.html`）

- 段落 `p`：`font-size:15px; line-height:1.7; color:#344054(--tf-text-2); margin:0 0 12px`
- 清單 `ul`：`margin:0 0 12px; padding-left:20px`
- 清單項 `li`：`font-size:15px; line-height:1.7; color:#344054; margin-bottom:6px`
- 標題 `h3`：`font-family:Noto Serif TC; font-weight:700; font-size:16px; color:#101828(--tf-text-1); margin:18px 0 8px`
- 引用膠囊：`#faf3e3(--tf-gold-tint) 底; #8a5a0f(--tf-gold-text); 999px; 11.5px; 700; padding:0 6px`（現況已符合）

## 方案

在既有 CSS Modules + 自製 markdown 渲染的架構上就地強化。只動兩個檔案的產出樣式與解析邏輯，外加測試。不引入 markdown 函式庫（維持零工具鏈、XSS 由 React 文字轉義保證的既有安全模型）。

### A. 本文排版樣式 — `AssistantMessage.module.css`

在 `.body` 底下補齊（值取 `tokens.css` 變數，數值對齊設計稿）：

- `.body p`：`margin: 0 0 12px`（`font-size/line-height/color` 已由 `.body` 提供 15/1.7/text-2）
- `.body ul, .body ol`：`margin: 0 0 12px; padding-left: 20px`
- `.body li`：`margin-bottom: 6px`（大小/行高/顏色繼承 `.body`）
- `.tf-md-h`：補字級 — `h3.tf-md-h { font-size: 16px }`、`h4.tf-md-h { font-size: 14.5px }`
- `.body a`：`color: var(--tf-gold-text); text-decoration: underline; text-underline-offset: 2px`；hover → `color: var(--tf-gold-hover)`
- `.body strong`：`color: var(--tf-text-1); font-weight: 700`
- `.body code`（行內）：`font-family: var(--tf-mono); font-size: 0.9em; background: var(--tf-gold-tint); border: 1px solid var(--tf-border-weak); border-radius: 5px; padding: 1px 5px`
- `.body pre`：`background: var(--tf-canvas); border: 1px solid var(--tf-border); border-radius: var(--tf-radius-md); padding: 12px 14px; margin: 0 0 12px; overflow-x: auto`；`.body pre code`：`background: none; border: none; padding: 0; font-size: 13px; line-height: 1.6`
- `.body blockquote`（品牌 callout，對齊深度研報引言風格）：`border-left: 3px solid var(--tf-gold-strong); background: var(--tf-gold-tint); padding: 8px 14px; margin: 0 0 12px; border-radius: 0 var(--tf-radius-md) var(--tf-radius-md) 0; color: var(--tf-text-2)`；`.body blockquote p:last-child { margin-bottom: 0 }`
- 表格（見 B 產出 `.tableWrap > table`）：`.body .tableWrap { overflow-x: auto; margin: 0 0 12px }`；`.body table { border-collapse: collapse; font-size: 14px }`；`.body th, .body td { border: 1px solid var(--tf-border); padding: 6px 10px; text-align: left; vertical-align: top }`；`.body thead th { background: var(--tf-border-weak); color: var(--tf-text-1); font-weight: 700 }`
- 收尾：`.body > :last-child { margin-bottom: 0 }`（避免答案末塊與操作列之間多一截空白）

### B. 解析器補洞 — `askMarkdown.tsx`

在 `renderAnswer` 的行掃描迴圈中新增兩種區塊，維持既有「逐行累積 → flush」的模式，並確保原始符號不再外洩：

1. **引言 `>`**
   - 偵測 `^>\s?(.*)$`，連續 `>` 行累積為一個 `quote` 緩衝；遇非引言行時 flush。
   - flush → `<blockquote>`，內含以 `\n` 分段的內容，每段走 `renderInline`（支援 `[n]`、粗體等）。
   - 與其他 flush（para/ul/ol）互斥處理，納入 `flushAll`。

2. **GFM 表格**
   - 偵測條件（標準 GFM）：目前行為表頭 `| a | b |`，**且下一行**為分隔列 `|? *:?-{1,}:? *(\| *:?-{1,}:? *)+\|?`。二者皆滿足才進入表格模式。
   - 消費後續連續的 `| … |` 資料列，直到非表格行為止。
   - 產出 `<div className="tableWrap"><table><thead><tr><th>…</tr></thead><tbody><tr><td>…</tr>…</tbody></table></div>`；每個 `th/td` 內容走 `renderInline`。
   - 若缺分隔列 → 不進入表格模式，維持現有段落行為（GFM 標準；避免誤判）。

3. **串流行內游標（見 C）**：解析器不變，游標以純 CSS 偽元素實作。

實作備註：新增區塊使解析器狀態機略為變大。為維持可讀性與可測性，允許把 `renderAnswer` 內的行掃描重構為清楚的區塊累積器，但**對外介面 `renderAnswer(md, sourceCount, onCite)` 簽章不變**，既有呼叫端（`AssistantMessage`）零改動。

### C. 串流動效 — `AssistantMessage.tsx` + CSS

- **移除**目前 `renderAnswer(...)` 後方的獨立 `<span className={styles.caret} />`（區塊級相鄰導致游標掉到下一行）。
- 改為在串流階段給 `.body` 加 `streaming` 類，游標以 CSS 偽元素貼在最後一塊尾端行內：
  - `.body.streaming > :last-child::after { content:''; display:inline-block; width:7px; height:1em; vertical-align:text-bottom; margin-left:2px; background: var(--tf-gold-text); animation: tf-pulse 1s steps(2) infinite }`
  - 最後一塊為 `<p>` 時游標貼在段末（最常見情境，效果最佳）；為 `<ul>/<pre>/<table>` 時貼其後（少見，可接受）。
- 保留整輪 `.tf-reveal` 進場淡入上浮（`AskPage` 既有，不動）。
- `prefers-reduced-motion` 已由 `tokens.css` 全域歸零（游標動畫自動停止，靜態方塊仍在，不影響可用性）。

### 檔案異動清單

| 檔案 | 異動 |
|---|---|
| `frontend/src/features/ask/AssistantMessage.module.css` | 新增本文排版樣式（A）、串流游標偽元素（C），移除舊 `.caret` sibling 樣式 |
| `frontend/src/features/ask/AssistantMessage.tsx` | 串流時 `.body` 加 `streaming` 類、移除獨立 caret span（C） |
| `frontend/src/lib/askMarkdown.tsx` | 新增引言、表格解析（B） |
| `frontend/src/lib/askMarkdown.test.tsx` | 新增引言/表格/連結色/防外洩測試（E） |
| `frontend/src/features/ask/AssistantMessage.test.tsx` | 串流時本文帶 `streaming` 類、無獨立 caret span 的斷言（E） |

## 測試（TDD）

沿用 Vitest + `@testing-library/react`，`renderAnswer` 直接渲染測 DOM。

**`askMarkdown.test.tsx` 新增：**
- 引言 `> 這是引言` → 產生 `<blockquote>`，`> ` 不以字面出現。
- 表格（表頭 + `|---|` + 資料列）→ 產生 `<table>`，`<th>` 2 個、`<td>` 對應數量；`|` 不以字面外洩。
- 表格儲存格內 `[1]` 仍渲染為可點膠囊。
- 缺分隔列的 `| a | b |` → 不誤判為表格（維持段落）。
- 連結金色由 `.body a` CSS 選擇器提供，`renderAnswer` 產出的 `<a>` 本身無類名；jsdom 對 CSS Module 計算色不可靠，故**不**做顏色斷言。連結測試維持既有 http-only + `rel` 驗證即可。
- 既有測試（`[n]` 界內外、標題清單成塊、XSS 不解讀、http-only 連結）全數維持綠。

**`AssistantMessage.test.tsx` 新增：**
- `phase:'streaming'` 時 `.body` 帶 `streaming` 類、且無獨立 caret `<span>`。
- `phase:'done'` 時 `.body` 不帶 `streaming` 類。

**收斂：** `npx vitest run`（前端）＋ `npx tsc --noEmit` ＋ `eslint` 全綠；後端測試不受影響（未改動）。

## 部署

- 純前端；需 `npm run build` 產出 dist；`web/static` 由 `_NoCacheStatic` 服務（React SPA 部署照既有流程）。後端零改動、無 schema 變更、免 DB 遷移。
- 尚未 cutover 的 `/app` 共存不受影響。

## 風險與緩解

- **表格解析誤判**：採嚴格 GFM（必須有分隔列）降低誤判；缺分隔列時回退段落，不外洩結構性錯誤。
- **串流游標貼在非段落塊尾**：少見（答案通常以段落結尾），視覺可接受；不因此增加解析器複雜度。
- **解析器重構回歸**：介面簽章不變 + 既有測試全綠護欄；新增測試覆蓋新語法。
