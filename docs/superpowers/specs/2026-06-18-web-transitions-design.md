# 設計：全站過渡動畫（網頁物件進退場）

- 日期：2026-06-18
- 範圍：把前端所有「`display:none` 硬切」與「重繪硬換」的物件，補上克制、與現有一致的進退場動畫。
- 不碰：後端、API、資料流；既有微互動（hover、按壓縮放、卡片 `rise`、進度條、shimmer、骨架屏、spin）維持不變，只把曲線／時長收斂成共用 token。

## 決策（brainstorming 定案）

1. 範圍 = **全部硬切點**（modal、面板、抽屜、模式切換、檢視切換、各種出現/消失）。
2. 風格 = **克制、與現有一致**（沿用 `cubic-bezier(.22,1,.36,1)`，120–240ms，淡入＋輕微位移/縮放）。
3. 機制 = **現代純 CSS**：`@starting-style` + `transition-behavior: allow-discrete`（display 開關）、`grid-template-rows: 0fr→1fr`（高度展開）。舊瀏覽器**優雅降級成瞬間顯示**，功能不壞。

## 背景

- 前端為零工具鏈原生 ESM（`web/static/app/*.js`）＋各頁 inline `<style>`，共用 `web/static/tokens.css`。
- 現況硬切點分三類：
  - **class 切換**（`.open`/`.show`）：`#modalBackdrop`、`#filterGroups`（手機）、`#askSources`、`#askExtSources`、`#meta`。
  - **`hidden` 屬性切換**：`#askHistDrawer`、`#askEmpty`/`#askQuestion`/`#askAnswer`（著陸↔聊天）、`#resultsBar`、`.search`/`#examples`/`#askPanel`/`#results`（搜尋↔問答，由 `body.ask-mode` 帶動）、`#modalSummary`、清除篩選鈕。
  - **JS 重繪**：檢視切換（卡/列/表/組）整段重畫 `#results`。
- 兩類 toggle 本質都是 `display:none` 開關，`display` 無法用一般 CSS transition 補間 → 這就是會「瞬間跳」的原因。

## 一、Motion tokens（集中於 `tokens.css`）

於 `:root` 新增：
```css
--ease-out: cubic-bezier(.22, 1, .36, 1);  /* 提取自現有曲線 */
--ease-in:  cubic-bezier(.4, 0, 1, 1);
--dur-1: 120ms;  /* 微互動：按鈕出現/消失、resultsBar */
--dur-2: 180ms;  /* 面板展開、檢視交叉淡入 */
--dur-3: 240ms;  /* modal、模式切換、抽屜 */
```
**降級集中化**：於 `tokens.css` 的 `@media (prefers-reduced-motion: reduce)` 將 `--dur-1/2/3` 設為 `0.01ms`。所有以 token 表示時長的過渡自動歸零，毋須各頁維護降級清單（各頁既有的 reduced-motion 區塊保留，新動畫一律改用 token）。

## 二、兩種進退場機制

### A. 覆蓋層／開關（modal、抽屜、模式切換）
- 對元素設 `transition: opacity var(--dur-3) var(--ease-out), transform var(--dur-3) var(--ease-out), display var(--dur-3) allow-discrete;`
- 進場初始態以 `@starting-style { ... }` 定義（如 `opacity:0; transform: translateY(8px) scale(.98)`）。
- 退場由 `allow-discrete` 讓 `display:none` 延到動畫結束才套用。

### B. 「展開」型面板（篩選面板、來源清單、更多片段、摘要列）
- 外層 `display: grid; grid-template-rows: 0fr; transition: grid-template-rows var(--dur-2) var(--ease-out), opacity var(--dur-2);`，內層 `min-height: 0; overflow: hidden;`。
- 展開時切到 `grid-template-rows: 1fr` + `opacity: 1` → 真正的高度補間，無需 JS 量測高度。

## 三、逐物件規格

| 物件 | 現況 | 機制 | 動畫 | 時長 |
|---|---|---|---|---|
| 報告 modal 背景 `#modalBackdrop` | `.open` display 切換 | A | 淡入／淡出 | dur-3 |
| 報告 modal 面板 `.modal` | 隨背景出現 | A | `translateY(8px) scale(.98)`→正常＋淡入；**手機版自底部上滑（sheet：`translateY(100%)`→0）** | dur-3 |
| modal 摘要列 `#modalSummary` | `hidden` | B | 高度展開＋淡入 | dur-2 |
| 手機篩選面板 `#filterGroups` | `.open` display 切換 | B（僅手機斷點） | 高度展開＋淡入 | dur-2 |
| 來源／外部來源展開 `#askSources`/`#askExtSources` | `.open` | B | 高度展開＋淡入（chevron 旋轉已有） | dur-2 |
| 問答歷史抽屜 `#askHistDrawer` ＋ `.ask-hist-backdrop` | `hidden` | A | 抽屜自右滑入（`translateX(100%)`→0）＋背景淡入 | dur-3 |
| 著陸 ↔ 聊天 `#askEmpty`/`#askQuestion`/`#askAnswer` | `hidden` | A | 交叉淡入＋輸入框輕微位移 | dur-3 |
| 搜尋 ↔ 問答模式 `.search`/`#examples`/`#askPanel`/`#results` | `hidden`＋`body.ask-mode` | A | 交叉淡入（由 `body.ask-mode` 驅動可見性） | dur-3 |
| 檢視切換（卡/列/表/組） | JS 重繪硬換 | JS＋CSS | `#results` 加 `view-enter` 觸發交叉淡入；既有逐項 `rise` 保留 | dur-2 |
| 清除篩選鈕 `.clear-filters` | `hidden` | A | 淡入縮放出現／消失 | dur-1 |
| `#resultsBar` | `hidden` | A | 淡入 | dur-1 |
| 顯示更多片段 `.passage.hidden` | display 切換 | B | 高度展開 | dur-2 |

## 四、JS 最小改動

1. **少數 `hidden` 屬性 → `.open`（或等效 class）**，讓 CSS 能接管動畫；僅改可見性寫法，不改邏輯：
   - `#askHistDrawer`（`ask.js` `openHistory`/`closeHistory`）
   - 著陸↔聊天的 `#askEmpty`/`#askQuestion`/`#askAnswer`（`ask.js`）
   - `#modalSummary`（`modal.js`）
   - 清除篩選鈕、`#resultsBar`（`api.js` 等）
   - 搜尋↔問答可見性：優先改由 `body.ask-mode` 在 CSS 控制 `.search`/`#examples`/`#askPanel`/`#results` 的 opacity/display（A 機制），移除對應 `hidden=` 行（`main.js`）。
   - 註：保留 `hidden` 作為「語意上不可見」時，需確認 CSS 對該元素另設 `display` 並以 `allow-discrete` 參與動畫；為避免 `[hidden]{display:none}` 與自訂 `display` 的特異度衝突，本設計一律改用 class。
2. **檢視切換／結果重繪**：`render.js` 重繪 `#results` 後加 `view-enter` class，`animationend` 後移除（或下次重繪前清除），觸發整區交叉淡入。其餘全在 CSS。

## 五、驗證

- Playwright 開 `http://localhost:8097`（先登入），逐一觸發：開/關 modal、手機篩選展開、來源展開、開/關歷史抽屜、搜尋↔問答切換、四種檢視切換、清除篩選出現/消失、顯示更多片段。截圖／錄影確認：過渡順暢、無殘留卡住（退場後確實 `display:none`）、無位移抖動、焦點陷阱與背景鎖捲動仍正常。
- 切 `prefers-reduced-motion: reduce`：全部瞬間完成、功能不壞。
- 不支援 `@starting-style` 的瀏覽器：確認降級為瞬間顯示、功能正常。

## 風險與緩解

- **退場殘留**：`allow-discrete` 漏設會讓元素退場時瞬間消失（無動畫）但不會卡住；逐物件以 Playwright 驗收。
- **grid 0fr→1fr 內層需 `min-height:0; overflow:hidden`**，否則內容撐高不收合；列入 checklist。
- **焦點管理不可退化**：modal/抽屜的 focus trap、`document.body.style.overflow` 鎖捲動、關閉還原焦點，動畫化後行為不得改變（動畫只改視覺，不改 JS 時序語意）。
