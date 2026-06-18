# 全站過渡動畫 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把前端所有「`display:none` 硬切」與「JS 重繪硬換」的物件，改為克制、與現有一致的進退場動畫。

**Architecture:** 純前端、零工具鏈。四種機制：(A) `@starting-style`+`transition-behavior: allow-discrete` 做覆蓋層開關（modal、抽屜）；(B) `max-height`+`opacity` 做容器整段收合（手機篩選面板、來源清單）；(C) `display`+`allow-discrete` 淡入做逐項揭露（更多片段）；(D) 一次性 keyframe class `.anim-enter` 做 JS 重繪/模式切換。動畫時長與曲線集中為 `tokens.css` 的 token，`prefers-reduced-motion` 於 token 統一歸零。

**Tech Stack:** 原生 ESM（`web/static/app/*.js`）、各頁 inline `<style>`、共用 `web/static/tokens.css`。無建置步驟、無 JS 測試框架；驗證＝`node --check`（JS 語法）＋ Playwright 視覺驗收。

## Global Constraints

- 設計依據：`docs/superpowers/specs/2026-06-18-web-transitions-design.md`（已於規劃階段細化機制）。
- **風格克制、與現有一致**：沿用 `cubic-bezier(.22,1,.36,1)`，時長 120/180/240ms，僅淡入＋輕微位移/縮放。
- **不碰後端、API、資料流**；既有微互動（hover、按壓縮放、卡片 `rise`、score-fill、spin、shimmer、骨架屏）維持不變。
- **新動畫一律用 token 計時**（`var(--dur-1|2|3)`、`var(--ease-out)`），以便 reduced-motion 統一歸零。
- **可見性改用 class，不用 `hidden` 屬性**（`[hidden]{display:none}` 特異度低，會與自訂 `display` 衝突）。
- **不可退化的行為**：modal/抽屜的焦點陷阱、`document.body.style.overflow` 鎖捲動、關閉還原焦點、Escape 關閉——動畫只改視覺，不改 JS 時序語意。
- **commit 慣例**：Conventional Commits，scope 用 `motion`；標題＜70 字、本文用繁體中文說明 what/why；`git add <path>` 明確加檔。每個 commit 結尾加：
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```
- **Playwright 前置**：app 需在執行中且可連（內網 `http://localhost:8097` 或本機 dev server），首次進站需先以共用帳密登入；`/static` 在登入後才可存取。

## File Structure

| 檔案 | 角色 | 本計畫變更 |
|---|---|---|
| `web/static/tokens.css` | 跨頁設計 token | 新增動畫 token＋reduced-motion 歸零（Task 1） |
| `web/static/app/dom.js` | DOM 小工具 | 新增 `animEnter()`（Task 1） |
| `web/static/index.html` | 檢索頁標記＋inline CSS | 新增 keyframe/`.anim-enter`；改寫 modal/抽屜/面板/片段/清除鈕 CSS；移除三處 `hidden` 屬性（Task 1–6） |
| `web/static/app/modal.js` | 完整報告 modal | 摘要 `hidden`→`.show`（Task 2） |
| `web/static/app/ask.js` | 問答 | 歷史抽屜 `hidden`→`.open`、Escape 判斷（Task 3） |
| `web/static/app/render.js` | 結果渲染 | 清除鈕 `hidden`→`.show`；排序重繪後 `animEnter`（Task 6） |
| `web/static/app/main.js` | 進入點/全域事件 | 檢視/分組/模式切換 `animEnter`（Task 6） |

---

### Task 1: 動畫基礎（token、keyframe、helper）

**Files:**
- Modify: `web/static/tokens.css`
- Modify: `web/static/index.html`（新增 keyframe + `.anim-enter`，並登錄到 reduced-motion 區塊）
- Modify: `web/static/app/dom.js`

**Interfaces:**
- Produces:
  - CSS 變數 `--ease-out`、`--ease-in`、`--dur-1`、`--dur-2`、`--dur-3`（全頁可用）。
  - CSS class `.anim-enter`（套用即播放一次 `fade-rise-in`）。
  - `animEnter(el: Element): void`（from `dom.js`）。

- [ ] **Step 1: tokens.css 新增動畫 token**

於 `:root` 內、`--shadow` 與 `font-family` 之間插入：

```css
  --shadow: 0 1px 2px rgba(0, 0, 0, .04), 0 8px 24px rgba(0, 0, 0, .06);
  /* 全站動畫 token（進退場過渡共用；reduced-motion 時於檔案底部統一歸零）*/
  --ease-out: cubic-bezier(.22, 1, .36, 1);
  --ease-in: cubic-bezier(.4, 0, 1, 1);
  --dur-1: 120ms;
  --dur-2: 180ms;
  --dur-3: 240ms;
  font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text",
```

- [ ] **Step 2: tokens.css 底部新增 reduced-motion 歸零**

在檔尾 `:focus-visible { ... }` 那一行之後追加：

```css

/* 尊重系統「減少動態效果」：把動畫時長歸零，所有以 token 計時的過渡/動畫自動失效 */
@media (prefers-reduced-motion: reduce) {
  :root { --dur-1: 0.01ms; --dur-2: 0.01ms; --dur-3: 0.01ms; }
}
```

- [ ] **Step 3: index.html 新增共用 keyframe 與 `.anim-enter`**

在 `/* 尊重系統「減少動態效果」偏好 */` 這個註解（reduced-motion `@media` 區塊）**之前**插入：

```css
  /* ───── 進退場過渡動畫（共用 keyframe / helper class）───── */
  @keyframes fade-rise-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  .anim-enter { animation: fade-rise-in var(--dur-2) var(--ease-out); }

```

- [ ] **Step 4: index.html 把 `.anim-enter` 登錄到 reduced-motion 區塊**

在 reduced-motion `@media` 區塊內，`.view-switch button { transition: none; }` 之後、區塊結尾 `}` 之前，加一行：

```css
    .view-switch button { transition: none; }
    .anim-enter { animation: none; }
  }
```

- [ ] **Step 5: dom.js 新增 animEnter**

在 `dom.js` 末尾（`export const $$ = ...` 之後）追加：

```js

// 一次性「進場」動畫：先移除→強制 reflow→重加，讓重複切換能重播；動畫結束即清除 class。
export function animEnter(el) {
  if (!el) return;
  el.classList.remove("anim-enter");
  void el.offsetWidth;
  el.classList.add("anim-enter");
  el.addEventListener("animationend", () => el.classList.remove("anim-enter"), { once: true });
}
```

- [ ] **Step 6: 語法檢查**

Run: `node --check web/static/app/dom.js`
Expected: 無輸出（exit 0）。

- [ ] **Step 7: 視覺/變數檢查（Playwright）**

開 app 並登入，導到檢索頁，於 console 執行：
```js
getComputedStyle(document.documentElement).getPropertyValue('--dur-2')
```
Expected: 回傳 ` 180ms`（前導空白可接受）。頁面無 console 錯誤、外觀與先前一致（此 task 尚不改任何切換行為）。

- [ ] **Step 8: Commit**

```bash
git add web/static/tokens.css web/static/index.html web/static/app/dom.js
git commit -m "$(cat <<'EOF'
feat(motion): 新增動畫 token、進場 keyframe 與 animEnter helper

集中過渡曲線/時長為 tokens（--ease-out、--dur-1/2/3），並在
prefers-reduced-motion 統一歸零；新增共用 .anim-enter keyframe 與
dom.js animEnter()，供後續 JS 重繪/模式切換重播進場動畫。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: 完整報告 modal 進退場 + 摘要淡入

**Files:**
- Modify: `web/static/index.html`（`.modal-backdrop`、`.modal`、`.modal-summary`、手機 modal 區塊、`#modalSummary` 標記）
- Modify: `web/static/app/modal.js`

**Interfaces:**
- Consumes: `--dur-3`、`--dur-2`、`--ease-out`、`@starting-style`/`allow-discrete`（Task 1 與瀏覽器原生）。
- Produces: `.modal-summary.show`（取代 `[hidden]` 控制摘要顯示）。

- [ ] **Step 1: 改寫 `.modal-backdrop`（淡入/淡出）**

把：
```css
  .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45);
    display: none; align-items: center; justify-content: center; z-index: 50; padding: 24px; }
  .modal-backdrop.open { display: flex; }
```
改為：
```css
  .modal-backdrop { position: fixed; inset: 0; background: rgba(0,0,0,.45);
    display: none; align-items: center; justify-content: center; z-index: 50; padding: 24px;
    opacity: 0; transition: opacity var(--dur-3) var(--ease-out), display var(--dur-3) allow-discrete; }
  .modal-backdrop.open { display: flex; opacity: 1; }
  @starting-style { .modal-backdrop.open { opacity: 0; } }
```

- [ ] **Step 2: 改寫 `.modal`（進場上滑＋縮放）**

把：
```css
  .modal { background: var(--bg); width: min(1000px, 100%); height: 90dvh;
    border-radius: 16px; display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 24px 60px rgba(0,0,0,.3); }
```
改為：
```css
  .modal { background: var(--bg); width: min(1000px, 100%); height: 90dvh;
    border-radius: 16px; display: flex; flex-direction: column; overflow: hidden;
    box-shadow: 0 24px 60px rgba(0,0,0,.3);
    transition: opacity var(--dur-3) var(--ease-out), transform var(--dur-3) var(--ease-out); }
  @starting-style { .modal-backdrop.open .modal { opacity: 0; transform: translateY(8px) scale(.98); } }
```

- [ ] **Step 3: 摘要列改用 `.show` class（淡入）**

把：
```css
  .modal-summary { padding: 12px 20px; border-bottom: 1px solid var(--sep);
    font-size: 13.5px; line-height: 1.6; color: var(--label-2); background: var(--card); }
  .modal-summary[hidden] { display: none; }
```
改為：
```css
  .modal-summary { display: none; padding: 12px 20px; border-bottom: 1px solid var(--sep);
    font-size: 13.5px; line-height: 1.6; color: var(--label-2); background: var(--card);
    transition: opacity var(--dur-2) var(--ease-out); }
  .modal-summary.show { display: block; opacity: 1; }
  @starting-style { .modal-summary.show { opacity: 0; } }
```

- [ ] **Step 4: 手機版 modal 改為自底部上滑 sheet**

把：
```css
  @media (max-width: 880px) {
    .sidebar { position: static; }
    .modal-backdrop { padding: 0; }
    .modal { width: 100%; height: 100dvh; border-radius: 0; }
  }
```
改為：
```css
  @media (max-width: 880px) {
    .sidebar { position: static; }
    .modal-backdrop { padding: 0; }
    .modal { width: 100%; height: 100dvh; border-radius: 0; }
    /* 手機：modal 改為自底部上滑的 sheet（覆寫桌機 @starting-style）*/
    @starting-style { .modal-backdrop.open .modal { opacity: 1; transform: translateY(100%); } }
  }
```

- [ ] **Step 5: 移除 `#modalSummary` 的 `hidden` 屬性**

把 `<div class="modal-summary" id="modalSummary" hidden></div>`
改為 `<div class="modal-summary" id="modalSummary"></div>`

- [ ] **Step 6: modal.js 摘要顯示改用 class**

把（reset）`msum.textContent = ""; msum.hidden = true;`
改為 `msum.textContent = ""; msum.classList.remove("show");`

把（填入）`if (d.summary) { msum.textContent = d.summary; msum.hidden = false; }`
改為 `if (d.summary) { msum.textContent = d.summary; msum.classList.add("show"); }`

- [ ] **Step 7: 語法檢查**

Run: `node --check web/static/app/modal.js`
Expected: 無輸出（exit 0）。

- [ ] **Step 8: 視覺驗收（Playwright）**

登入→檢索→點任一卡片「查看完整報告」。
Expected: 背景淡入、面板自下方 8px＋scale(.98) 滑入淡入；有摘要時摘要淡入。按 ✕／點背景／Esc 關閉時背景淡出（面板隨之淡出），關閉後焦點回到觸發按鈕。將視窗縮到手機寬度再開一次：面板自畫面底部上滑。
備註：關閉時 `modalBody` 立即清空（釋放 PDF iframe）→ 淡出期間短暫顯示深灰底，屬可接受。

- [ ] **Step 9: Commit**

```bash
git add web/static/index.html web/static/app/modal.js
git commit -m "$(cat <<'EOF'
feat(motion): modal 淡入縮放進場、手機底部 sheet、摘要淡入

完整報告 modal 改用 @starting-style + allow-discrete：背景淡入淡出、
面板上滑縮放進場，手機改為自底部上滑的 sheet；摘要列改用 .show class
取代 hidden 屬性以支援淡入。焦點陷阱與鎖捲動行為不變。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 問答歷史抽屜滑入/滑出

**Files:**
- Modify: `web/static/index.html`（`.ask-hist-drawer`/`.ask-hist-backdrop`/`.ask-hist-panel`、`#askHistDrawer` 標記）
- Modify: `web/static/app/ask.js`

**Interfaces:**
- Consumes: `--dur-3`、`--ease-out`、`allow-discrete`/`@starting-style`。
- Produces: `.ask-hist-drawer.open`（取代 `hidden` 屬性）。

- [ ] **Step 1: 改寫抽屜 CSS（背景淡入＋面板右側滑入）**

把：
```css
  .ask-hist-drawer { position: fixed; inset: 0; z-index: 50; }
  .ask-hist-drawer[hidden] { display: none; }
  .ask-hist-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.28); }
  .ask-hist-panel { position: absolute; top: 0; right: 0; height: 100dvh; width: min(420px, 92vw);
    background: var(--card); box-shadow: -8px 0 30px rgba(0,0,0,.12); display: flex; flex-direction: column;
    padding: max(env(safe-area-inset-top), 16px) 16px 16px; }
```
改為：
```css
  .ask-hist-drawer { position: fixed; inset: 0; z-index: 50; display: none;
    transition: display var(--dur-3) allow-discrete; }
  .ask-hist-drawer.open { display: block; }
  .ask-hist-backdrop { position: absolute; inset: 0; background: rgba(0,0,0,.28);
    opacity: 0; transition: opacity var(--dur-3) var(--ease-out); }
  .ask-hist-drawer.open .ask-hist-backdrop { opacity: 1; }
  .ask-hist-panel { position: absolute; top: 0; right: 0; height: 100dvh; width: min(420px, 92vw);
    background: var(--card); box-shadow: -8px 0 30px rgba(0,0,0,.12); display: flex; flex-direction: column;
    padding: max(env(safe-area-inset-top), 16px) 16px 16px;
    transform: translateX(100%); transition: transform var(--dur-3) var(--ease-out); }
  .ask-hist-drawer.open .ask-hist-panel { transform: none; }
  @starting-style {
    .ask-hist-drawer.open .ask-hist-backdrop { opacity: 0; }
    .ask-hist-drawer.open .ask-hist-panel { transform: translateX(100%); }
  }
```

- [ ] **Step 2: 移除 `#askHistDrawer` 的 `hidden` 屬性**

把 `<div class="ask-hist-drawer" id="askHistDrawer" hidden>`
改為 `<div class="ask-hist-drawer" id="askHistDrawer">`

- [ ] **Step 3: ask.js 開關改用 class**

把（`openHistory` 內）`drawer.hidden = false;`
改為 `drawer.classList.add("open");`

把 `function closeHistory() { $("#askHistDrawer").hidden = true; }`
改為 `function closeHistory() { $("#askHistDrawer").classList.remove("open"); }`

- [ ] **Step 4: ask.js Escape 判斷改用 class**

把 `if (e.key === "Escape" && !$("#askHistDrawer").hidden) closeHistory();`
改為 `if (e.key === "Escape" && $("#askHistDrawer").classList.contains("open")) closeHistory();`

- [ ] **Step 5: 語法檢查**

Run: `node --check web/static/app/ask.js`
Expected: 無輸出（exit 0）。

- [ ] **Step 6: 視覺驗收（Playwright）**

登入→切到「問答」模式→點 composer 左側歷史鈕。
Expected: 背景淡入、面板自右側滑入；點背景／✕／Esc → 面板滑回右側、背景淡出後消失（關閉後抽屜 `display:none`）。

- [ ] **Step 7: Commit**

```bash
git add web/static/index.html web/static/app/ask.js
git commit -m "$(cat <<'EOF'
feat(motion): 問答歷史抽屜右側滑入／滑出

抽屜改用 .open class + allow-discrete：背景淡入、面板自右滑入，關閉時
反向滑出後才 display:none；Escape 判斷改讀 class。取代原 hidden 屬性。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 手機篩選面板與來源清單展開/收合（max-height）

**Files:**
- Modify: `web/static/index.html`（手機 `.filter-groups`、`.ask-sources`）

**Interfaces:**
- Consumes: `--dur-2`、`--ease-out`。
- 無 JS 變更（兩者既有 `.open` class 切換不動）。

- [ ] **Step 1: 手機篩選面板改 max-height 展開**

於 `@media (max-width: 880px)` 內，把：
```css
    .filter-groups { display: none; flex-direction: column; gap: 12px; }
    .filter-groups.open { display: flex; }
```
改為：
```css
    .filter-groups { display: flex; flex-direction: column; gap: 12px;
      max-height: 0; opacity: 0; overflow: hidden;
      transition: max-height var(--dur-2) var(--ease-out), opacity var(--dur-2); }
    .filter-groups.open { max-height: 1200px; opacity: 1; }
```
（桌機 `.filter-groups { display: contents }` 不在此 media 內，不受影響。）

- [ ] **Step 2: 來源／外部來源清單改 max-height 展開**

把：
```css
  .ask-sources { display: none; flex-direction: column; gap: 8px; margin-top: 8px; }
  .ask-sources.open { display: flex; }
```
改為：
```css
  .ask-sources { display: flex; flex-direction: column; gap: 8px; margin-top: 0;
    max-height: 0; opacity: 0; overflow: hidden;
    transition: max-height var(--dur-2) var(--ease-out), opacity var(--dur-2), margin-top var(--dur-2); }
  .ask-sources.open { max-height: 1500px; opacity: 1; margin-top: 8px; }
```
（容器整段收合，內部 flex gap 由 `overflow:hidden` 裁掉；`margin-top` 一併補間避免收合時殘留間距。上限為保守值：篩選 5 段、來源 ≤ ~12 卡。）

- [ ] **Step 3: 視覺驗收（Playwright）**

(a) 縮到手機寬度→點「篩選與排序」→面板平滑展開、再點收合。
(b) 桌機寬度→問答模式提一題→答案下方點「資料來源」→清單以高度展開、再點收合。
Expected: 兩者皆為高度＋淡入的平滑展開/收合，無殘留間距、無內容溢出被切（內容在上限內）。

- [ ] **Step 4: Commit**

```bash
git add web/static/index.html
git commit -m "$(cat <<'EOF'
feat(motion): 手機篩選面板與來源清單以 max-height 展開／收合

兩者為多子節點/動態渲染容器，改用 max-height+opacity 整段收合（overflow
裁掉 flex gap），零 wrapper、不動渲染 JS；沿用既有 .open class 切換。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 卡片「顯示更多片段」淡入揭露

**Files:**
- Modify: `web/static/index.html`（`.passage`）

**Interfaces:**
- Consumes: `--dur-2`、`--ease-out`、`allow-discrete`。
- 無 JS 變更（`render.js` 既有 `classList.remove("hidden")` 觸發）。

- [ ] **Step 1: `.passage` 加過渡、`.hidden` 帶淡出初始態**

把：
```css
  .passage { font-size: 14px; line-height: 1.62; color: var(--label-2); letter-spacing: .008em;
    padding-left: 12px; border-left: 2px solid var(--sep); }
  .passage.hidden { display: none; }
```
改為：
```css
  .passage { font-size: 14px; line-height: 1.62; color: var(--label-2); letter-spacing: .008em;
    padding-left: 12px; border-left: 2px solid var(--sep); opacity: 1;
    transition: opacity var(--dur-2) var(--ease-out), transform var(--dur-2) var(--ease-out), display var(--dur-2) allow-discrete; }
  .passage.hidden { display: none; opacity: 0; transform: translateY(-4px); }
```
（移除 `.hidden` 時，元素自 display:none/opacity:0/translateY 過渡到顯示，淡入＋輕微下滑。元素本就在 DOM、具前一計算樣式，故不需 `@starting-style`，亦不會在卡片首次渲染時與 `rise` 重複觸發。）

- [ ] **Step 2: 視覺驗收（Playwright）**

登入→輸入會回傳含多個片段的查詢（如「台積電 先進封裝」）→在某張卡片點「顯示其他 N 段片段」。
Expected: 其餘片段淡入＋輕微下滑出現，按鈕隨後消失。

- [ ] **Step 3: Commit**

```bash
git add web/static/index.html
git commit -m "$(cat <<'EOF'
feat(motion): 卡片「顯示更多片段」改為淡入揭露

.passage 加過渡、.hidden 帶 opacity/transform 初始態，移除 .hidden 即以
display allow-discrete 淡入＋輕微下滑，取代瞬間出現；無 JS 變更。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: JS 重繪/模式切換交叉淡入＋清除鈕縮放淡入

**Files:**
- Modify: `web/static/index.html`（`.clear-filters`、`#clearFilters` 標記）
- Modify: `web/static/app/render.js`（import animEnter、清除鈕 class、排序重繪 animEnter）
- Modify: `web/static/app/main.js`（import animEnter、檢視/分組/模式切換 animEnter）

**Interfaces:**
- Consumes: `animEnter`（Task 1）、`--dur-1`、`--ease-out`、`allow-discrete`/`@starting-style`。
- Produces: `.clear-filters.show`（取代 `[hidden]`）。

- [ ] **Step 1: 清除鈕 CSS 改 `.show`（縮放淡入）**

把：
```css
  .clear-filters { border: none; background: var(--fill); color: var(--label-2);
    font-family: inherit; font-size: 12px; font-weight: 600; cursor: pointer;
    padding: 6px 12px; border-radius: 100px; display: inline-flex; align-items: center; gap: 5px; }
  .clear-filters[hidden] { display: none; }
```
改為：
```css
  .clear-filters { border: none; background: var(--fill); color: var(--label-2);
    font-family: inherit; font-size: 12px; font-weight: 600; cursor: pointer;
    padding: 6px 12px; border-radius: 100px; display: none; align-items: center; gap: 5px;
    opacity: 0; transform: scale(.9);
    transition: opacity var(--dur-1) var(--ease-out), transform var(--dur-1) var(--ease-out), display var(--dur-1) allow-discrete; }
  .clear-filters.show { display: inline-flex; opacity: 1; transform: none; }
  @starting-style { .clear-filters.show { opacity: 0; transform: scale(.9); } }
```

- [ ] **Step 2: 移除 `#clearFilters` 的 `hidden` 屬性**

把 `<button class="clear-filters" id="clearFilters" type="button" hidden>清除篩選</button>`
改為 `<button class="clear-filters" id="clearFilters" type="button">清除篩選</button>`

- [ ] **Step 3: render.js import animEnter**

把 `import { $, $$ } from "/static/app/dom.js";`
改為 `import { $, $$, animEnter } from "/static/app/dom.js";`

- [ ] **Step 4: render.js 清除鈕改用 class**

在 `updateViewBar()` 內，把：
```js
  const cf = $("#clearFilters");
  if (cf) { const n = activeFilterCount(); cf.hidden = !n; cf.textContent = n ? `清除篩選 · ${n}` : "清除篩選"; }
```
改為：
```js
  const cf = $("#clearFilters");
  if (cf) { const n = activeFilterCount(); cf.classList.toggle("show", !!n); cf.textContent = n ? `清除篩選 · ${n}` : "清除篩選"; }
```

- [ ] **Step 5: render.js 表頭排序重繪後 animEnter**

在 `bindResultEvents()` 的 `doSort` 內，把：
```js
    const doSort = () => {
      const key = th.dataset.sortKey;
      if (state.tableSort.key === key) state.tableSort.dir = state.tableSort.dir === "asc" ? "desc" : "asc";
      else { state.tableSort.key = key; state.tableSort.dir = "asc"; }
      paintResults(false);
    };
```
改為：
```js
    const doSort = () => {
      const key = th.dataset.sortKey;
      if (state.tableSort.key === key) state.tableSort.dir = state.tableSort.dir === "asc" ? "desc" : "asc";
      else { state.tableSort.key = key; state.tableSort.dir = "asc"; }
      paintResults(false);
      animEnter($("#results"));
    };
```

- [ ] **Step 6: main.js import animEnter**

把 `import { $, $$ } from "/static/app/dom.js";`
改為 `import { $, $$, animEnter } from "/static/app/dom.js";`

- [ ] **Step 7: main.js 檢視切換 animEnter**

把：
```js
$$(".view-switch button").forEach(b => b.onclick = () => {
  if (state.view === b.dataset.view) return;
  state.view = b.dataset.view;
  try { localStorage.setItem("rm_view", state.view); } catch (e) {}
  syncURL();
  state.rows.length ? paintResults(false) : updateViewBar();
});
```
改為：
```js
$$(".view-switch button").forEach(b => b.onclick = () => {
  if (state.view === b.dataset.view) return;
  state.view = b.dataset.view;
  try { localStorage.setItem("rm_view", state.view); } catch (e) {}
  syncURL();
  if (state.rows.length) { paintResults(false); animEnter($("#results")); }
  else updateViewBar();
});
```

- [ ] **Step 8: main.js 分組依據變更 animEnter**

把：
```js
$("#groupBy").onchange = () => {
  state.group = $("#groupBy").value;
  syncURL();
  if (state.rows.length && state.view === "group") paintResults(false);
};
```
改為：
```js
$("#groupBy").onchange = () => {
  state.group = $("#groupBy").value;
  syncURL();
  if (state.rows.length && state.view === "group") { paintResults(false); animEnter($("#results")); }
};
```

- [ ] **Step 9: main.js 模式切換 animEnter（出現側）**

在 `applyMode()` 內，把：
```js
  if (ask) {
    $("#resultsBar").hidden = true;
    $("#meta").classList.remove("show");
    $("#askInput").focus();
  } else {   // 切回檢索：依目前狀態還原結果區（有快取重繪、有查詢重搜、否則瀏覽）
    if (state.rows.length) paintResults(false);
    else if ($("#q").value.trim()) run();
    else loadBrowse();
  }
```
改為：
```js
  if (ask) {
    $("#resultsBar").hidden = true;
    $("#meta").classList.remove("show");
    animEnter($("#askPanel"));
    $("#askInput").focus();
  } else {   // 切回檢索：依目前狀態還原結果區（有快取重繪、有查詢重搜、否則瀏覽）
    if (state.rows.length) { paintResults(false); animEnter($("#results")); }
    else if ($("#q").value.trim()) run();
    else loadBrowse();
  }
```
（只對「出現的那一側」播放 `.anim-enter`；隱藏側維持既有 `hidden` 瞬間隱藏，避免兩側同時佔位 reflow 跳動。新搜尋/瀏覽會走 skeleton→卡片 `rise`，故僅快取重繪路徑加 `animEnter`。）

- [ ] **Step 10: 語法檢查**

Run: `node --check web/static/app/render.js && node --check web/static/app/main.js`
Expected: 無輸出（exit 0）。

- [ ] **Step 11: 視覺驗收（Playwright）**

登入→檢索→
(a) 點「卡片／列表／表格／分組」切換 → 結果區整段交叉淡入（卡片不重播 rise）。
(b) 表格檢視點欄位表頭排序、分組檢視改「分組依據」→ 結果區交叉淡入。
(c) 切「問答」↔「檢索」→ 出現的那一側淡入上滑。
(d) 點任一篩選 chip → 「清除篩選 · N」縮放淡入；清除後縮放淡出消失。

- [ ] **Step 12: Commit**

```bash
git add web/static/index.html web/static/app/render.js web/static/app/main.js
git commit -m "$(cat <<'EOF'
feat(motion): 檢視/排序/分組/模式切換交叉淡入，清除鈕縮放淡入

JS 重繪後對重繪區加一次性 .anim-enter（檢視切換、表頭排序、分組依據、
模式切換出現側）；清除篩選鈕改 .show class + allow-discrete 縮放淡入，
取代 hidden 屬性。隱藏側維持瞬間隱藏以避免 reflow 跳動。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 整體驗收與降級驗證

**Files:** 無（僅驗證；若發現問題則回到對應 task 修正）

- [ ] **Step 1: 全 JS 語法檢查**

Run: `for f in web/static/app/*.js; do node --check "$f" || echo "FAIL $f"; done`
Expected: 無 `FAIL` 行。

- [ ] **Step 2: Playwright 全互動巡檢**

登入後依序觸發並截圖確認過渡順暢、無殘留卡住、無位移抖動：
1. 開/關完整報告 modal（桌機＋手機寬度 sheet）
2. 開/關問答歷史抽屜
3. 手機寬度展開/收合篩選面板
4. 問答模式展開/收合資料來源
5. 卡片「顯示更多片段」
6. 四種檢視切換、表頭排序、分組依據變更
7. 檢索 ↔ 問答模式切換
8. 篩選 chip 套用/清除（清除鈕縮放淡入/淡出）

關鍵檢查（console 內驗證退場後確實隱藏）：
```js
// 關閉 modal 後
getComputedStyle(document.querySelector('#modalBackdrop')).display    // 'none'
// 關閉歷史抽屜後
document.querySelector('#askHistDrawer').classList.contains('open')   // false
```
Expected: 全部達標；modal 的焦點陷阱、Esc 關閉、背景鎖捲動（開啟時 `document.body.style.overflow === 'hidden'`、關閉後為空）皆正常。

- [ ] **Step 3: reduced-motion 降級驗證**

以 Playwright 模擬 `prefers-reduced-motion: reduce`（emulate media），重跑 Step 2 的代表性互動（modal、抽屜、檢視切換）。
Expected: 全部瞬間完成、功能完全正常、無殘影。

- [ ] **Step 4: 舊瀏覽器降級說明（不需程式）**

確認設計依賴 `@starting-style`/`allow-discrete`；不支援的瀏覽器會略過進場/退場補間 → 退化為瞬間顯示/隱藏，功能不受影響（無需 polyfill）。於 PR 描述註明此降級行為。

- [ ] **Step 5: 收尾**

若 Step 2–3 發現問題，回到對應 task 修正並重跑該 task 的驗收與本 task。全綠後本計畫完成（如需開 PR，於描述彙整上述驗收結果與降級說明）。

## Self-Review

- **Spec coverage**：spec 逐物件表的每一列都對應到 task —— modal 背景/面板/摘要(Task 2)、手機篩選面板/來源清單(Task 4)、歷史抽屜(Task 3)、更多片段(Task 5)、檢視/排序/分組/模式切換(Task 6)、清除鈕(Task 6)；spec 標明「刻意不單獨處理」的 `#resultsBar` 與著陸↔聊天，計畫未建任務（一致）。機制 A/B/C/D 基礎在 Task 1。
- **Placeholder scan**：無 TBD/TODO；每個 code step 皆含可直接套用的完整 old→new 片段或新增片段。
- **Type/識別子一致**：`animEnter` 於 dom.js 定義（Task 1）、於 render.js/main.js import 使用（Task 6）；class 名 `.anim-enter`、`.show`、`.open`、`@keyframes fade-rise-in`、token `--dur-1/2/3`/`--ease-out` 全程一致。
