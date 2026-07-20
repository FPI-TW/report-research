# Handoff: 廷豐智能研報 — 檢索頁改版（Bento 簡報牆）

## Overview
The **檢索頁 (search page)** of 廷豐智能研報, a securities-research platform used daily by
brokerage analysts. The redesign replaces the old marketing-style centered hero with an
analyst's **daily briefing surface**: the search field is the primary actor, and the corpus
below is laid out as a bento wall of tiles (headline · stats · corpus composition · list).

Two states are documented:
- **Browse state** (`search-redesign.html`) — first visit / empty query. Shows 最新入庫 (latest intake).
- **Query state** (`search-redesign-query.html`) — after a query (`AI 伺服器散熱`). Shows ranked results.

The left **SideRail is out of scope** — it is provided by the existing AppShell/SideRail and is
included in the mocks only so page proportions read correctly. Do **not** rebuild it from these files.

## About the Design Files
The files in this bundle are **design references authored in HTML/CSS** — prototypes that show the
intended look and behavior. They are **not** production code to ship directly. The task is to
**recreate these designs in the target codebase's existing environment**. Per the project's own
notes this migrates back into **React + CSS Modules**, reusing the existing `--tf-*` design tokens,
with motion via Motion (Framer Motion) and **no change to backend API contracts**. If you are
starting fresh, pick the framework that best fits the project and implement there.

The existing search feature lives (per source headers) under
`src/features/search/*` with tokens in `src/styles/tokens.css` — reuse those modules and tokens
rather than introducing new ones.

## Fidelity
**High-fidelity.** Final colors, typography, spacing, radii, shadows, and interactions are all
specified below and in `redesign.css`. Recreate the UI to match, using the codebase's existing
component library and CSS-Module patterns. Every measurement here is authoritative; when in doubt,
read the exact value in `redesign.css`.

---

## Screens / Views

### 1. Browse state — Bento 簡報牆 (`search-redesign.html`)

**Purpose:** Analyst lands here; scans what's newly ingested and the shape of the corpus, or starts a search.

**Layout (main content column):**
- App shell: CSS grid `216px | minmax(0,1fr)` (rail | main), `min-height:100vh`.
- Content wrapper: `max-width:1000px; margin:0 auto; padding:44px 32px 88px`.
- Vertical order: **Search field → market chips → bento grid**.
- Bento grid: `display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:14px; margin-top:22px`.
  Tile placement (explicit grid lines):
  - **Feature (headline)** — `grid-column:1/3; grid-row:1/3` (2×2, top-left)
  - **Stat A** — `grid-column:3; grid-row:1`
  - **Stat B** — `grid-column:4; grid-row:1`
  - **Composition** — `grid-column:3/5; grid-row:2`
  - **List (更多最新入庫)** — `grid-column:1/5; grid-row:3` (full-width bottom)

**Components:**

- **Search field** (`.field`)
  - Size: full-width, `height:60px`; `padding:0 12px 0 20px`; `gap:15px`; `border-radius:16px` (`--tf-radius-panel`).
  - Border: `1.5px solid var(--tf-ink)` (#1e5175). Background `#fff`.
  - Shadow (resting): `var(--tf-ring-ink), var(--tf-shadow-float)` = `0 0 0 3px rgba(30,81,117,.12), 0 8px 24px rgba(22,42,58,.07)`.
  - Focus-within: `0 0 0 4px rgba(30,81,117,.14), 0 8px 24px rgba(22,42,58,.07)` (transition `box-shadow 160ms` ease-out).
  - Leading icon: inline SVG magnifier, 19×19, `stroke:var(--tf-ink)`, `stroke-width:2`, round caps.
  - Input: `font-size:16.5px; color:var(--tf-text-1)`; placeholder `搜尋產業、公司、事件…` in `--tf-text-4`.
  - Trailing `<kbd class="key">Enter</kbd>`: mono 10.5px, `--tf-text-3`, border `1px` (`bottom 2px`) `--tf-border`, radius 5px, padding `4px 8px`, bg `--tf-canvas`. It replaces a submit button — it signals *how to submit*, not an extra control.

- **Market chips** (`.chips` / `.chip`) — filter control, wraps.
  - Row: `flex; flex-wrap:wrap; gap:6px; margin-top:16px`.
  - Chip: border `1px solid var(--tf-border)`, bg `#fff`, color `--tf-text-2`, radius pill, padding `5px 12px`, `12.5px`.
  - Count `.n`: mono 10.5px, `--tf-text-4`, `margin-left:7px`.
  - Hover: `border-color:var(--tf-ink); color:var(--tf-text-1)` (transition 120ms).
  - **Selected** (`[data-on]`, carries `--c` = that market color): `border-color:var(--c); color:var(--c); background:color-mix(in srgb, var(--c) 7%, #fff); font-weight:600`; its `.n` = `--c` at `opacity:.7`.
  - Browse chips + counts: 全部 5,094 (uses `--tf-ink`), 台股 2,860, 美股 1,120, 港股 402, 陸股 355, 台指期 128, 外匯 96, 總經 88, 全球 30, 加密 15.

- **Feature tile** (`.tile.feature.link`) — the one latest report, enlarged.
  - Tile base: bg `#fff`, border `1px solid var(--tf-border)`, radius `14px`, padding `20px`. `position:relative; overflow:hidden; display:flex; flex-direction:column`.
  - Left color edge: `::before` `position:absolute; left/top/bottom:0; width:4px; background:var(--c)` (market color, set via inline `style="--c:var(--mkt-TW)"`).
  - `.top` row: `space-between`. Left `.mk` = `台股 · 頭條` (mono 11px, `color:var(--c)`, `font-weight:600`, `letter-spacing:.04em`). Right = `.new` badge (see Design Tokens → gold semantics).
  - `.title`: serif 22px, `line-height:1.42`, `--tf-text-1`, `margin-top:16px`.
  - `.data`: mono 11.5px, `--tf-text-4`, `margin-top:12px` — e.g. `2330 · 3017 · 元大投顧`.
  - `.sum`: 13px, `--tf-text-2`, `line-height:1.7`, `margin-top:14px`, clamped to 4 lines (`-webkit-line-clamp:4`).
  - `.foot`: `margin-top:auto; padding-top:16px`; mono 11px `--tf-text-3` — e.g. `2026-07-15 · 查看全文 ›`.
  - Hover (`.link:hover`): `box-shadow:var(--tf-shadow-float); transform:translateY(-2px); border-color:var(--tf-ink-line)` (transition 150ms ease-out).

- **Stat tiles** (`.tile.stat`) — `flex-direction:column; justify-content:center`.
  - `.num`: **serif** 32px, `--tf-ink-deep`, `line-height:1`, `font-variant-numeric:tabular-nums`. `.num.gold` → `--tf-gold-strong`.
  - `.lbl`: mono 10px, `letter-spacing:.14em`, `--tf-text-4`, `margin-top:10px`.
  - Content: Stat A = `5,094` / `篇研報 · DOCS`; Stat B = `9` (gold) / `涵蓋市場 · MARKETS`.

- **Composition tile** (`.tile.comp`) — corpus market makeup; a readout, not a control.
  - `.h`: mono 10px, `letter-spacing:.14em`, `--tf-text-4` — `語料庫組成`.
  - `.spectrum`: `flex; gap:2px; height:8px; margin-top:14px`. Each `span` = one market: `flex-grow:<count>` (proportional width), `background:var(--c)`, `border-radius:2px; min-width:2px`.
  - Segment order & `flex-grow` = real counts: TW 2860, US 1120, HK 402, CN 355, WTX 128, FX 96, MACRO 88, GLOBAL 30, CRYPTO 15.
  - Entrance animation `segIn`: `scaleX(0)→(1)`, 640ms ease-out, `transform-origin:left center`, staggered `animation-delay` 0/40/80/…/320ms.
  - `.lgs` legend: `flex-wrap; gap:10px 16px; margin-top:14px`; mono 11px `--tf-text-3`; each item has a 7×7 `border-radius:2px` swatch `i` (bg `--c`) + `.mk` (color `--c`, bold) + percent. Shown: 台股 56%, 美股 22%, 港股 8%, 陸股 7%, 台指期 2.5%.

- **List tile** (`.tile.list`) — remaining latest reports; `padding:4px 20px`.
  - `.lh` header row: `.t` serif 14px `--tf-text-1` (`更多最新入庫`); `.m` mono 11px `--tf-text-4`, pushed right (`margin-left:auto`) — `2026 年 7 月 · 共 6 篇`.
  - `.lrow`: grid `48px | minmax(0,1fr) | auto`, `gap:18px; align-items:center; padding:13px 0; border-top:1px solid var(--tf-border-weak)`; pointer.
    - `.code`: mono 11px, bold, `color:var(--c)`, `letter-spacing:.03em` (market abbreviation TW/US/HK/CN/WTX).
    - middle cell: `.ltitle` serif 14.5px `--tf-text-1`, single-line ellipsis; `.lsrc` mono 11px `--tf-text-4`, `margin-left:10px`.
    - `.ldate`: mono 11px `--tf-text-4`, nowrap.
  - Rows (browse): US Nvidia 07-14, HK 騰訊 07-12, CN 半導體設備 07-11, TW 台積電 07-10, WTX 台指期週報 07-09.

### 2. Query state — search results (`search-redesign-query.html`)

**Purpose:** After a query; scan results ranked by relevance. Sample query: `AI 伺服器散熱`.

**Layout:** Search field (with value) → chips (hit counts) → **hit-composition bar** → results header → **feature tile (top hit)** → ranked result rows. No month grouping (time is not the axis here).

**Components (deltas from browse):**

- **Search field** — same component, `input value="AI 伺服器散熱"`.
- **Chips** — hit counts: 全部 128, 台股 86, 美股 27, 陸股 9, 港股 4, 台指期 2.
- **Hit-composition bar** (`.hitbar`) — spectrum re-distributed to the *hits*, not the whole corpus.
  - `flex; align-items:center; gap:20px; margin-top:22px; padding:16px 20px; background:#fff; border:1px solid var(--tf-border); border-radius:14px`.
  - `.spectrum`: `flex:1; height:8px; gap:2px`; segments TW 86, US 27, CN 9, HK 4, WTX 2 (same segIn animation).
  - `.read`: mono 12px `--tf-text-3`, nowrap — `找到 <b>128</b> 篇 · 台股佔 <span class="gold">67%</span>`; `.read b` = 15px `--tf-ink-deep`; `.gold` = `--tf-gold-strong`.
- **Results header** (`.resultsHead`): `flex; margin-top:26px; padding-bottom:12px; border-bottom:1px solid var(--tf-border)`. `.t` serif 15px (`搜尋結果`); `.m` mono 11px `--tf-text-4` right-aligned (`依相關度排序`).
- **Feature tile — top hit** (`.tile.feature.link.featureRow`, `margin-top:18px`): same feature component, but `.top` right slot holds a **score** instead of the 最新 badge:
  - `.score` (right-aligned): `.pct` mono 15px `--tf-ink` (`92%`); `.hits` mono 10.5px `--tf-text-4` (`14 段命中`).
  - Title may still carry an inline `.new` (最新) badge. `.data` includes date: `2330 · 3017 · 元大投顧 · 07-15`. `.foot` = `查看全文 ›`.
- **Result rows** (`.rrow`) — grid `48px | minmax(0,1fr) | 96px`, `gap:20px; align-items:start; padding:16px 2px; border-top:1px solid var(--tf-divider); border-radius:8px`.
  - Hover: `background:#fff; box-shadow:var(--tf-shadow-xs)` (transition 120ms).
  - `.code` mono 11px bold `--c` (padding-top 3px); `.rtitle` serif 15px single-line ellipsis; `.meta` mono 11px `--tf-text-4` (`3017 · 3653 · 群益投顧 · 07-13`); `.sum` 12.5px `--tf-text-2` line 1.6 clamp 2.
  - `.score` right column: `.pct` mono 14px `--tf-ink`; `.hits` mono 10px `--tf-text-4` (`margin-top:4px`).
  - Rows: TW 散熱模組 88% / 11段, US Nvidia 85% / 9段, TW 機殼與導軌 81% / 8段, CN 半導體設備 76% / 6段.

---

## Interactions & Behavior
- **Submit:** Enter key in the field runs the search (no submit button; the `Enter` kbd communicates this). Browse → query state on submit.
- **Chips:** single-select market filter (`全部` default). Selecting re-filters results and (in query state) would re-shape the hit-composition spectrum. Selected chip adopts the market color.
- **Composition spectrum / hit bar:** display-only readout. In query state it reflects the hit set, not the whole corpus. Segment widths are strictly proportional (`flex-grow` = count).
- **Tile / row hover:** feature & list tiles and result rows are clickable (navigate to the report). Feature/stat/comp tiles that are `.link` lift on hover (`translateY(-2px)` + float shadow); result rows raise a surface bg + xs shadow.
- **Entrance animation:** spectrum segments scale in on X (`segIn`, 640ms, ease `cubic-bezier(.22,1,.36,1)`), staggered 40ms per segment. Implement with Motion; keep the stagger.
- **Keyboard focus:** visible focus ring `outline:2px solid var(--tf-ink); outline-offset:2px` on all focusable elements — preserve.
- **Reduced motion:** under `prefers-reduced-motion:reduce`, all animations/transitions are disabled — preserve.

## Responsive behavior
Single breakpoint at **`max-width:900px`**:
- Rail hidden (`.rail{display:none}`); app collapses to one column; content padding `28px 18px 80px`.
- Field `height:56px`; `.key` hidden.
- Bento grid → `repeat(2,minmax(0,1fr))`; feature/comp/list span both columns (`grid-column:1/3`, `grid-row:auto`); stats sit side-by-side; feature title drops to 19px.
- `.hitbar` stacks (`flex-direction:column; align-items:stretch`).
- `.rrow` → 2-col (`40px | 1fr`); title wraps; `.score` moves to a full-width row below (`grid-column:1/3`, inline baseline layout).
- `.lrow .ltitle` allowed to wrap.

## State Management
- `query: string` — search text (empty ⇒ browse state, non-empty ⇒ query state).
- `activeMarket: MarketCode | 'ALL'` — selected chip; default `ALL`.
- `results` / `latest` — fetched lists (browse: latest by intake time; query: ranked by relevance score). Backend API contracts unchanged — wire to existing endpoints.
- Derived: corpus composition counts (browse) and hit composition counts (query) drive the spectrum `flex-grow` values and the readout numbers.
- Per-result fields: `market`, `code`, `title`, `tickers/source` (meta), `summary`, `date`, and (query only) `relevancePct`, `hitSegments`, `isLatest` (drives 最新 badge).

## Design Tokens
All values live in `redesign.css` `:root` and mirror the existing `src/styles/tokens.css`. **No color was changed** from the current brand palette.

**Neutrals / surfaces:** `--tf-canvas #f7f6f2` (warm paper bg) · `--tf-surface #ffffff` · `--tf-sidebar #fbfaf6` · `--tf-border #e7e4dc` · `--tf-border-weak #f3f1eb` · `--tf-divider #efede7`.
**Text:** `--tf-text-1 #212b33` · `--tf-text-2 #40525f` · `--tf-text-3 #6e7e89` · `--tf-text-4 #9aa5ae`.
**Ink (墨青, primary):** `--tf-ink #1e5175` · `--tf-ink-deep #163a54` · `--tf-ink-hover #174260` · `--tf-ink-tint #eaf1f6` · `--tf-ink-line #dce6ee` · `--tf-on-ink #ffffff`.
**Gold (鎏金, accent — semantic only, see below):** `--tf-gold-text #8a5a0f` · `--tf-gold-strong #ae7415` · `--tf-gold-hover #7a4f0c` · `--tf-gold-tint #f8f1e0` · `--tf-gold-line #ead9ae` · `--tf-gold-panel #fbf7ec`.
**Market semantic colors (`--c` per element):** TW `#237a46` · US `#2e5fa3` · HK `#b26a0b` · CN `#b03a30` · FX `#0e7d74` · WTX `#6e48a8` · MACRO `#a03052` · GLOBAL `#4a4fa0` · CRYPTO `#7d6234`.
**Radius:** card 12 · panel 16 · pill 999 · md 8 · **tile 14**.
**Shadow:** xs `0 1px 3px rgba(22,42,58,.05)` · float `0 8px 24px rgba(22,42,58,.07)` · ring-ink `0 0 0 3px rgba(30,81,117,.12)`.
**Type families:** serif `'Noto Serif TC', Georgia, serif` (titles & big numbers) · sans `'Noto Sans TC', system…` (body) · mono `ui-monospace, SFMono-Regular, 'SF Mono', Menlo, Consolas, 'Noto Sans Mono CJK TC', monospace` (all data: codes, dates, counts, percentages).
**Easing:** `--tf-ease-out cubic-bezier(0.22, 1, 0.36, 1)`.
**Type sizes used:** feature title 22 / stat num 32 / result & feature-hit title 15 / list title 14.5 / body-summary 12.5–13 / mono data 10–12 / kbd 10.5.

**Two hard color rules to preserve:**
1. **Gold = semantic, not decorative.** Only the 最新 badge and the "markets" stat number use gold. Don't sprinkle it.
2. **Color = the corpus's grammar.** The same market color set drives the spectrum, the legend swatches, the tile left-edge, and the row/list codes — one speaks aggregate proportion, the other per-item identity.

## Typography / language
- Traditional Chinese, `lang="zh-Hant"`. Titles are serif; all numeric/identifier data is monospace (tabular).
- No emoji anywhere — hierarchy is built from serif, hairlines, and whitespace.

## Assets
- **No external assets.** All icons are **inline SVG** (magnifier lens in the field; the four rail nav icons — search / chat-QA / radar / monitor). The brand mark in the rail is an inline SVG (`#163a54` rounded square + gold `廷` in serif) — but the rail is out of scope; use the app's real SideRail.
- No web fonts are bundled; families resolve via the token stacks. If the target app ships Noto Serif TC / Noto Sans TC, they render as intended; otherwise the fallbacks apply.
- Numbers (5,094; per-market counts; relevance %) are **illustrative** but internally consistent (proportions are real).

## Files (in this bundle)
- `redesign.css` — the complete stylesheet for the new design (both states). **Authoritative source of every value.**
- `search-redesign.html` — browse state (Bento 簡報牆).
- `search-redesign-query.html` — query state (ranked results).
- `styles.css` — the **current/baseline** stylesheet (flattened from the existing React + CSS Modules); documents the existing token system and component classes you already have.
- `search.html` — the **current** production page (baseline / control), for comparison.

Reference only (not bundled): `search-redesign-explore.html` (A/B/C) and `search-redesign-explore-2.html`
(D/E/F) hold the six explored layout directions; the chosen one is **F · Bento 簡報牆**.
