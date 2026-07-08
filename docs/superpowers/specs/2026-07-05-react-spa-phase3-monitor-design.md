# Phase 3 — 監控頁遷移（`/app/monitor`）設計規格

**日期:** 2026-07-05
**分支:** `feat/react-spa-rebuild`
**承接:** Phase 0（骨架/導覽/ConversationList）、Phase 1（檢索頁 `/app/search`）、Phase 2（問答+深度研報 `/app/ask`）皆已完成、審查、live-e2e 綠。

## 目標

把「研報導入監控」頁從 vanilla（`web/static/monitor.html` + `app/eta.js`/`meta.js`）遷移到 React 19 SPA 的 `/app/monitor`，逐像素落地設計權威 `docs/design/廷豐智能研報.dc.html` 的 MONITOR 畫面，資料源沿用 `GET /api/progress`（**後端零改動**）。目前 `src/features/monitor/MonitorPage.tsx` 僅為 3 行 stub，本 Phase 以完整實作取代之。

## 設計權威與範圍邊界

- **設計權威 = `.dc.html` 的 `isMonitor` 畫面（第 389–460 行）。** 版面、文案、色票、間距以其為準。
- **功能對等 = vanilla `monitor.html` 的 `tick()` 行為。** 資料映射、速率/ETA、閒置態、LIVE 健康態以其為準。
- 兩者衝突時，於「綁定決策」逐條裁定。
- **不含 cutover**：`/` 仍服務 vanilla index，本頁只在 `/app/monitor`。不動 `src/features/monitor/` 以外既有檔（路由 `App.tsx:/monitor` 已存在，指向本頁）。
- **後端零改動**：只消費既有 `GET /api/progress`，不新增/修改任何後端路由或回應。

## 後端契約（`GET /api/progress`，消費、零改動）

每次輪詢回傳（欄位取自 `web/server.py` 的 `progress()`）:

```jsonc
{
  "ts": "HH:MM:SS",                                  // 伺服器產生此快照的時刻（字串）
  "db": {
    "reports": 12345,                                // 已導入報告總數
    "chunks": 456789,                                // 向量片段總數
    "markets": [{ "market": "TW", "count": 8123 }]   // 市場分佈；list of {market, count}
  },
  "summary": { "done": 900, "total": 1000, "remaining": 100, "pct": 90.0 },
  "tagging": { "done": 800, "total": 1000, "fail": 200, "pct": 80.0 } | null,  // 無執行中→null；fail＝未標數＝剩餘
  "ingest":  { "ingested": 42, "chunks": 3100, "fail": 1 } | null,             // 無執行中→null（或 {ingested:0,...}）
  "pipelines": { "web": true, "ingest": false, "tag": true, "summaries": false },  // 恆 4 條
  "orchestrator": { "raw": "...", "timestamp": "..."|null, "status": "running"|"done"|"unknown", "label": "編排器執行中" } | null
}
```

Zod schema（`tagging`/`ingest`/`orchestrator` 皆 `.nullable()`；未知欄位寬鬆）驗證後消費。`getJSON` 於 401 導向登入。

## 綁定決策

1. **後端零改動；設計權威＝`.dc.html`，功能對等＝vanilla `tick()`。**
2. **輪詢間隔 = 5 秒**（`.dc.html` 文案「30 秒」與 vanilla「2 秒」之折衷；使用者拍板）。即時時鐘為本地 1 秒、不吃請求。
3. **全部面板落地**：KPI 4 卡 + 語意標註 + 報告導入 + 摘要生成 + 處理管線 + **市場分佈**（後者與檢索頁已排除的「市場分組 drill-in」是不同功能，本頁在範圍內；使用者拍板）。
4. **KPI 卡第三行 = 情境副字（非「自開頁增量」）**，忠實 vanilla：
   - 已導入報告 → 「{markets.length} 個市場」
   - 總片段 CHUNKS → 「向量片段總數」
   - 標註進度 % → 「已標註 {tagging.done} / {tagging.total}」
   - 摘要進度 % → 「已生成 {summary.done} / {summary.total}」
5. **速率/ETA = 移植 vanilla 演算法**（開頁基準法 + `eta.js` 文字格式化），純函式 + vitest（移植 `eta.test.mjs`）。
6. **LIVE 健康態**：最後一次輪詢成功→綠「LIVE」脈動；失敗（非 401）→「重連中」且保留上一筆好資料（不閃爍/不清空）。
7. **繁中文案逐字**對齊 `.dc.html`；數字 `tabular-nums`。
8. **消費既有 Phase 0/1 資產不修改**：`lib/api.ts`(getJSON/redirectToLogin)、`lib/meta.ts`(marketColor/marketLabel)、`styles/tokens.css`（`tf-pulse`/`tf-indet` keyframes 已存在）。

## 架構與資料流

```
MonitorPage
├─ useProgress()   react-query：queryKey ['progress']、queryFn getJSON('/api/progress', progressSchema)、
│                  refetchInterval 5000、placeholderData keepPrevious（保留上一筆，避免閃爍）、
│                  401→getJSON 導向登入；query.isError（非 401）→ LIVE 顯「重連中」、沿用 data
├─ useClock()      本地 useState + setInterval 1s，回傳 "HH:MM:SS"（牆鐘，非伺服器 ts）
├─ useRates(prog)  持有 baseline ref；每次 data 變更以 computeRates 算 {rpm,cps,spm,tpm}
└─ 呈現子元件（純，靠 props）
```

- `MonitorPage` 只組合 hooks + 佈局，無業務邏輯（handler 保持薄）。
- react-query 的 `refetchInterval` 於分頁背景時預設暫停（`refetchIntervalInBackground` 不設，省資源）。

## 速率/ETA 演算法（移植，純函式）

**`computeRates(current, baseline, elapsedSec)`** — 移植 vanilla `rate()`：

- `current = { reports, chunks, sumDone, tagDone|null, sumDone|null }`（`tagDone`/`sumDone` 可為 null）。
- 首次呼叫（尚無 baseline）→ 回 `{rpm:null,cps:null,spm:null,tpm:null}` 並由 hook 記錄 `baseline = {...current, t: now}`。
- `elapsedSec < 8`（開頁暖機）→ 回上一筆（初始全 null）。
- 否則：
  - `rpm = (reports − base.reports) / elapsedSec × 60`
  - `cps = (chunks − base.chunks) / elapsedSec`
  - `spm = sumDone==null ? null : (sumDone − base.sum) / elapsedSec × 60`
  - `tpm = tagDone==null ? null : (tagDone − base.tag) / elapsedSec × 60`
- hook `useRates` 以 `useRef` 持 baseline + lastRate；時間用 `Date.now()`（React；毫秒差同義）。baseline 於元件掛載首筆資料捕獲，之後不變（＝開頁基準）。

**`rateText(remaining, rate, unit)`**（移植 `eta.js`，純函式）:
- `rate == null` → 「速率 計算中…」
- 組 `速率 {rate.toFixed(1)} {unit}/分`
- `rate < 0.05` → 只回速率行（不給 ETA）
- `remaining <= 0` → `… · 已完成`
- 否則 `mins = remaining/rate`；`mins<90` → `~{round(mins)} 分`，否則 `~{(mins/60).toFixed(1)} 時`；回 `… · 預估剩餘 {eta}`

**`ingestRateText(rpm, cps)`**（移植 `eta.js`，純函式）:
- `rpm == null` → 「速率 計算中…」
- 組 `速率 {rpm.toFixed(1)} 篇/分`；`cps == null` → 只回速率行，否則 `… · {cps.toFixed(1)} 片段/秒`

面板速率行的 remaining/unit 對應（忠實 vanilla）:
- 語意標註：`rateText(tagging.fail, tpm, "標註")`（`tagging.fail`＝未標＝剩餘）
- 摘要生成：`rateText(summary.remaining, spm, "摘要")`
- 報告導入：`ingestRateText(rpm, cps)`（導入無已知總量，不給 ETA）

## UI 結構（逐段對齊 `.dc.html`）

1. **頁首**（`max-width:1120px` 置中）：左 `h2「研報導入監控」`（Noto Serif TC 700 26px）+ 副字「{db.reports} 篇已導入 · {alive}/4 條管線執行中」（`alive` = `pipelines` 中 true 的數）；右 LIVE pill（`#faf3e3`/`#8a5a0f`、綠脈動點 `tf-pulse`，失敗→「重連中」）+ 即時時鐘（mono，`useClock`）。
2. **KPI 網格**（`kpiGridStyle`，4 卡）：白卡圓角 12、label 12px、value 26px 700 tabular、suffix、第三行情境副字（見決策 4）。四卡＝已導入報告 / 總片段 CHUNKS / 標註進度%（suffix `%`）/ 摘要進度%（suffix `%`）。
3. **面板列 1**（`panelGridStyle`，2 欄）：
   - **語意標註**：`{tagging.done}` 大字 + `/ {tagging.total} 篇` + 右上 `{pct}%`（金）；9px 進度條（寬 = pct%，`.34s` 緩動）；速率行。`tagging==null`→閒置態「目前無執行中的標註」、不顯條。
   - **報告導入**：單行狀態（省略號截斷），依序 fallback：`orchestrator.label`（有）→ `ingest` 有值 → 「本輪已導入 {ingest.ingested} 篇 · 失敗 {ingest.fail}」→ 皆無 → 「目前無執行中的導入」；不定量條（`pipelines.ingest` 為 true→`tf-indet` 流動，false→靜止滿條/灰）；速率行 `ingestRateText(rpm, cps)`。
4. **面板列 2**（2 欄）：
   - **摘要生成**：同語意標註結構（done/total/pct/條/速率），`summary` 恆存在。
   - **處理管線**：4 列 = Web 服務(`web`)/報告導入(`ingest`)/語意標註(`tag`)/摘要生成(`summaries`)；每列 dot（綠=執行中/灰=已停止）+ 名稱 + 狀態徽章「執行中/已停止」。
5. **市場分佈**：白卡；每列（依 count 由大到小排序）= 市場標籤（`marketLabel`，`meta.ts`）+ 長條（寬 = count/max×100%、色 = `marketColor`）+ count（tabular）+ 佔比%。空→「—」。
6. **頁尾**：置中細字「資料每 5 秒自動更新 · 廷豐智能研報導入管線」。

（數字 count-up 動畫為選用 polish；預設以 `tabular-nums` 直接顯示即可，不阻塞驗收。）

## 狀態與邊界情形

- **首筆載入中**（`isLoading` 且無 placeholder）：頁面骨架 + LIVE「連線中…」；面板顯 `—`。
- **`tagging`/`ingest` 為 null**：對應面板閒置態文案、不顯進度/速率 ETA（速率行顯「計算中…」或閒置字）。
- **輪詢失敗（非 401）**：`query.isError` → LIVE「重連中」；沿用 `data`（keepPrevious）不清空；恢復即回「LIVE」。
- **401**：`getJSON` 導向 `/login`（與全站一致）。
- **暖機期（開頁 <8s）**：速率行「速率 計算中…」。
- 全數字 `font-variant-numeric: tabular-nums`。

## 測試策略

- **`rate.ts` 純函式（vitest）**：移植 `eta.test.mjs` 全部案例（rateText 的 null/計算中、<0.05 無 ETA、已完成、分/時分界 90；ingestRateText 的 null/僅速率/雙段）；補 `computeRates`：首筆回全 null 並記 baseline、暖機 <8s 回上一筆、正常成長算 rpm/cps/spm/tpm、`tagDone`/`sumDone` 為 null 時對應回 null。
- **元件測試（vitest + @testing-library）**：以 mock progress 物件渲染
  - 頁首副字「{reports} 篇已導入 · {alive}/4」與 LIVE 計數；輪詢錯誤 → 「重連中」且仍顯上一筆數字。
  - KPI 4 卡值 + 情境副字（含「{n} 個市場」）。
  - 語意標註/摘要面板：done/total/pct、進度條寬、速率行文字；`tagging==null` → 閒置態。
  - 報告導入：`pipelines.ingest` true/false → 不定量/靜止條切換。
  - 處理管線：4 列狀態徽章對 `pipelines` 布林。
  - 市場分佈：列數/排序（count desc）、長條寬（count/max）、色（marketColor）、空態。
- **e2e（Playwright，輕量）**：登入 → `/app/monitor` → 斷言 `研報導入監控` 標題、KPI 卡、LIVE、至少一條處理管線列。監控唯讀確定性，較問答穩定；可納入既有 e2e 或後續手動跑（與 Phase 2 同慣例：直呼 binary、對工作樹 :8098）。

## 檔案結構（`frontend/src/features/monitor/`）

| 檔案 | 責任 |
|---|---|
| `MonitorPage.tsx`（取代 stub） | 佈局 + 組合 hooks + 頁首/頁尾 |
| `MonitorPage.module.css` | 頁面/卡片/面板/進度條/市場列樣式（tokens.css 變數） |
| `progressSchema.ts` | Zod `progressSchema` + 型別（`Progress`, `Tagging`, `Ingest`, `Pipelines`, `MarketCount`…） |
| `useProgress.ts` | react-query 輪詢 hook（5s、keepPrevious、schema） |
| `useClock.ts` | 本地 1s 時鐘 hook |
| `useRates.ts` | baseline ref + `computeRates` 呼叫，回 `{rpm,cps,spm,tpm}` |
| `rate.ts` | 純函式 `computeRates` + `rateText` + `ingestRateText`（移植） |
| `rate.test.ts` | 移植 + 擴充純函式測試 |
| `KpiGrid.tsx` / `KpiCard.tsx` | KPI 4 卡 |
| `ProgressPanel.tsx` | 語意標註/摘要生成共用（done/total/pct/條/速率） |
| `IngestPanel.tsx` | 報告導入（不定量條 + ingestRate） |
| `PipelineStatus.tsx` | 4 條管線狀態列 |
| `MarketDistribution.tsx` | 市場分佈長條 |
| 各 `*.test.tsx` | 對應元件測試 |

消費（不修改）：`lib/api.ts`、`lib/meta.ts`、`components/shell/*`（AppShell 已含導覽/帳號/登出）、`styles/tokens.css`、`components/primitives/*`（如需 Icon）。

## 不在本 Phase 範圍

- **Cutover**（`/` → `/app/search` 307、退役 vanilla web/static、flip basename）— 獨立後續 Phase。
- **help 頁**（`web/static/help.html`）遷移。
- 任何後端變更；任何非監控頁的功能。
