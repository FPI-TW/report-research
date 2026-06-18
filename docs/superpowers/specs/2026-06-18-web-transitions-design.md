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

## 二、四種進退場機制（規劃階段依實際 markup 細化）

> 原設計擬以 `grid-template-rows: 0fr→1fr` 做高度展開，但讀 markup 後發現：篩選面板桌機為 `display:contents`、且面板/來源清單皆為「多個並列子節點（含 flex gap）」或「JS 動態渲染的兄弟節點」，grid-rows 需額外包一層 wrapper 才能收合，會破壞桌機版面並牽動渲染 JS。故改用下列零 wrapper、零渲染 JS 改動的機制：

### A. 覆蓋層開關（modal、歷史抽屜）— `@starting-style` + `allow-discrete`
- 對元素設 `transition: opacity var(--dur-3) var(--ease-out), transform var(--dur-3) var(--ease-out), display var(--dur-3) allow-discrete;`
- 進場初始態以 `@starting-style { ... }` 定義（如 `opacity:0; transform: translateY(8px) scale(.98)`）。
- 退場由 `allow-discrete` 讓 `display:none` 延到動畫結束才套用。

### B. 容器整段收合（手機篩選面板、來源／外部來源清單）— `max-height` + `opacity`
- 容器本身 `overflow:hidden; max-height:0; opacity:0; transition: max-height var(--dur-2) var(--ease-out), opacity var(--dur-2);`，`.open` 時 `max-height: <足夠上限>; opacity:1`。
- 整個容器收合 → 內部 flex gap 被 `overflow:hidden` 裁掉，不殘留間距；無需 wrapper、不動渲染 JS。上限取保守值（內容有界：篩選 5 段、來源 ≤ ~12 卡）。

### C. 逐項揭露（卡片內「顯示更多片段」）— `display` + `allow-discrete` 淡入
- `.passage { transition: opacity var(--dur-2) var(--ease-out), transform var(--dur-2) var(--ease-out), display var(--dur-2) allow-discrete; }`
- `.passage.hidden { display:none; opacity:0; transform: translateY(-4px); }`；移除 `.hidden` 即淡入（元素早已在 DOM、具前一計算樣式，毋須 `@starting-style`，亦不會在首次渲染時與卡片 rise 重複觸發）。

### D. JS 重繪／模式切換 — 一次性 keyframe class `.anim-enter`
- `@keyframes fade-rise-in { from { opacity:0; transform: translateY(6px);} to { opacity:1; transform:none; } }`；`.anim-enter { animation: fade-rise-in var(--dur-2) var(--ease-out); }`
- JS 重繪後對目標區加 `.anim-enter`、`animationend` 後移除（重播前先 remove + 強制 reflow）。用於：檢視切換、表頭排序、分組依據變更、模式切換時「出現的那一側」。退場側維持瞬間隱藏（避免兩側同時佔位造成 reflow 跳動）。

## 三、逐物件規格

| 物件 | 現況 | 機制 | 動畫 | 時長 |
|---|---|---|---|---|
| 報告 modal 背景 `#modalBackdrop` | `.open` display 切換 | A | 淡入／淡出 | dur-3 |
| 報告 modal 面板 `.modal` | 隨背景出現 | A | `translateY(8px) scale(.98)`→正常＋淡入；**手機版自底部上滑（sheet：`translateY(100%)`→0）** | dur-3 |
| modal 摘要列 `#modalSummary` | `hidden` 屬性 → 改 `.show` class | A（純淡入） | 淡入（單一元素，不做高度補間以免加 wrapper） | dur-2 |
| 手機篩選面板 `#filterGroups` | `.open`（僅手機斷點） | B | max-height＋淡入展開／收合 | dur-2 |
| 來源／外部來源展開 `#askSources`/`#askExtSources` | `.open` | B | max-height＋淡入（chevron 旋轉已有） | dur-2 |
| 問答歷史抽屜 `#askHistDrawer` ＋ `.ask-hist-backdrop` | `hidden` 屬性 → 改 `.open` class | A | 抽屜自右滑入（`translateX(100%)`→0）＋背景淡入；關閉時反向滑出 | dur-3 |
| 顯示更多片段 `.passage.hidden` | `.hidden`（display:none） | C | 揭露時淡入＋輕微下滑 | dur-2 |
| 檢視切換（卡/列/表/組） | JS 重繪硬換 | D | 重繪後 `#results` 加 `.anim-enter` 交叉淡入；既有逐項 `rise` 不受影響 | dur-2 |
| 表頭排序／分組依據變更 | JS 重繪硬換 | D | 同上，重繪區 `.anim-enter` | dur-2 |
| 搜尋 ↔ 問答模式 | `hidden`＋`body.ask-mode` | D | 對「出現的那一側」(askPanel／results) 加 `.anim-enter`；隱藏側維持瞬間（沿用既有 `hidden`） | dur-2 |
| 清除篩選鈕 `.clear-filters` | `hidden` 屬性 → 改 `.show` class | A | scale(.9)→1＋淡入出現／消失 | dur-1 |

**刻意不單獨處理（避免過度工程／牽動過多 JS）**：
- `#resultsBar`：與結果同時出現，卡片 `rise` 已提供動態，折入結果進場，不另加動畫。
- 著陸 ↔ 聊天（`#askEmpty`/`#askQuestion`/`#askAnswer` 子狀態）：進入問答模式時 `askPanel` 的 `.anim-enter` 已涵蓋整體出現感，且 `thinking()` spinner 提供載入動態；不再逐元素處理。

## 四、JS 最小改動

1. **少數 `hidden` 屬性 → class**（`[hidden]{display:none}` 特異度低，會與自訂 `display` 衝突，故凡需動畫者一律改用 class，僅改可見性寫法、不改邏輯）：
   - `#askHistDrawer`：`ask.js` `openHistory`/`closeHistory` 改用 `classList.add/remove("open")`；Escape 判斷 `!drawer.hidden` → `drawer.classList.contains("open")`；HTML 移除 `hidden`。
   - `#modalSummary`：`modal.js` `msum.hidden = true/false` → `msum.classList.toggle("show", …)`；HTML 移除 `hidden`、CSS 移除 `.modal-summary[hidden]` 規則。
   - `#clearFilters`：`render.js` `updateViewBar()` 的 `cf.hidden = !n` → `cf.classList.toggle("show", !!n)`；HTML 移除 `hidden`。
2. **一次性進場 helper**（共用）：新增 `animEnter(el)`（remove class → 強制 reflow → add class → `animationend` once 移除），匯出供下列呼叫：
   - 檢視切換：`main.js` view-switch onclick，`paintResults(false)` 後 `animEnter($("#results"))`。
   - 分組依據：`main.js` `#groupBy` onchange，重繪後 `animEnter($("#results"))`。
   - 表頭排序：`render.js` `doSort()`，`paintResults(false)` 後 `animEnter($("#results"))`。
   - 模式切換：`main.js` `applyMode()`，進入問答 `animEnter($("#askPanel"))`；切回檢索（重繪後）`animEnter($("#results"))`。隱藏側仍用既有 `hidden`（瞬間）。
3. 其餘（modal 開關、抽屜滑動、篩選/來源展開、更多片段揭露）**全在 CSS**，JS 維持既有 class 切換不動。

## 五、驗證

- Playwright 開 `http://localhost:8097`（先登入），逐一觸發：開/關 modal、手機篩選展開、來源展開、開/關歷史抽屜、搜尋↔問答切換、四種檢視切換、清除篩選出現/消失、顯示更多片段。截圖／錄影確認：過渡順暢、無殘留卡住（退場後確實 `display:none`）、無位移抖動、焦點陷阱與背景鎖捲動仍正常。
- 切 `prefers-reduced-motion: reduce`：全部瞬間完成、功能不壞。
- 不支援 `@starting-style` 的瀏覽器：確認降級為瞬間顯示、功能正常。

## 風險與緩解

- **退場殘留**：`allow-discrete` 漏設會讓元素退場時瞬間消失（無動畫）但不會卡住；逐物件以 Playwright 驗收。
- **grid 0fr→1fr 內層需 `min-height:0; overflow:hidden`**，否則內容撐高不收合；列入 checklist。
- **焦點管理不可退化**：modal/抽屜的 focus trap、`document.body.style.overflow` 鎖捲動、關閉還原焦點，動畫化後行為不得改變（動畫只改視覺，不改 JS 時序語意）。
