# 前端重構為 React SPA — Phase 0/1：地基 ＋ monitor 垂直切片

- 日期：2026-06-29
- 狀態：設計定稿，待實作計畫（writing-plans）
- 範圍：本 spec 只涵蓋 **SPA 地基** 與 **第一個垂直切片（monitor 頁）**。其餘現有頁遷移、新頁、`/` 切換、vanilla 退役各自另立 spec。

---

## 1. 背景與決策

2026-06-15 曾決策「現階段不上 React，走零工具鏈漸進現代化」。當時為真：前端僅 `index.html` 一頁有真實互動、零元件複用、零工具鏈是最大資產。該決策同時列出「重新評估觸發條件」。

到 2026-06-29，條件已實質越過觸發線：

| 指標 | 2026-06-15 | 2026-06-29 |
|---|---|---|
| 有真實互動的頁面 | 僅 index 一頁 | index 含**檢索**＋**問答**兩大互動模式（後者多輪對話＋SSE 串流） |
| `web/static/app/` 模組 | 10 | 16（多 `ask.js` 676 行、`markdown.js`、`confirm.js`、`dropdown.js`、`eta.js`） |
| 共用 a11y 元件 | 1 個 modal | modal + confirm + dropdown |
| 狀態管理 | 單一 `state` 物件 | `state.js` 單例 ＋ `ask.js` 自帶模組級狀態（`turns`/`conversationId`/`currentAskCtrl`）＋ 三條手寫競態序號 |
| index.html | ESM 化後 514 行 | 又長回 1031 行 |

更關鍵的驅動：**有具體即將到來的多頁＋圖表為主＋表單/後台 CRUD 開發**。此類工作正是 React 生態（routing、元件複用、表單庫、資料抓取快取）回本最高的場景。

**決策（翻轉 2026-06-15）**：採 React，**全面重寫為單一 SPA**；以「地基優先、平價閘門逐頁切換」的方式安全到達，而非大爆炸式一次替換。後端 API 完全不動。

---

## 2. 目標與非目標

### 本 spec 目標（Phase 0/1）
1. 站起 Vite + React 19 + TypeScript 的 SPA 骨架，掛在 `/app` 子路徑，與現有 vanilla 頁共存。
2. 由 FastAPI 服務 SPA shell（catch-all）與雜湊資產，沿用既有 `tf_session` 認證，**不改認證白名單、不改 systemd unit**。
3. 建立會被後續每頁複用的共用地基層：typed API client、Zod schema/型別、Mantine 主題（映射既有金色品牌）、已測純函式移植管線、app shell。
4. 把 **monitor** 端到端遷進 SPA 並達平價，證明整條管線（build → 服務 → 認證 → API → 渲染 → make/systemd 部署）。
5. 建立 Vitest 測試骨架與 Playwright 平價驗證法。

### 非目標（明確排除，留待後續 spec）
- browse / search / 檢視/分組 / ask / report / help / login 的遷移。
- 任何新頁（圖表/儀表板、表單/後台 CRUD）。
- 把 `/` 切到 SPA、退役 vanilla、刪 `web/static`。
- 任何後端**業務邏輯**或 schema 變動（`app/**`、`db/**`、既有 `/api/*` 行為一律不動）。唯一允許的後端改動是 §4.3 在 `web/server.py` **新增服務 SPA 的靜態路由**（catch-all shell + 雜湊資產掛載），純服務、無業務邏輯。

---

## 3. 技術棧（確切版本，已查證｜2026-06-29）

> 版本均經背景研究工作流查證 npm dist-tags / 官方 release notes，並對最高風險的 React 19 相容性做對抗式驗證（4/4 confirmed）。一律取**最新穩定版**。

| 關注點 | 套件 | 版本 | 關鍵約束 / 備註 |
|---|---|---|---|
| 框架 | `react`, `react-dom` | **19.2.7** | 樞紐版本：同時滿足 Mantine 9（`^19.2.0`）與 React Router 8（`>=19.2.7`）。**鎖 19.2.7**，勿降到 19.0/19.1 |
| 型別 | `@types/react`, `@types/react-dom` | `^19` | 強制分開裝；混到 v18 型別會出 ref-as-prop / JSX namespace 錯誤 |
| 建置 | `vite` | **8.1.0** | Rolldown bundler；ESM-only |
| React 插件 | `@vitejs/plugin-react` | **6.0.3** | peer `vite ^8`（勿配 Vite 7）；自動 JSX runtime 為預設，勿改 `classic` |
| 路由 | `react-router` | **8.0.1** | v8 **移除 `react-router-dom`**；核心 API 從 `react-router`、DOM API（`RouterProvider`）從 `react-router/dom`。Data mode（`createBrowserRouter`），**不**用 Framework/SSR mode。peer `react >=19.2.7` |
| 資料抓取 | `@tanstack/react-query` | **5.101.2** | peer `react ^18 || ^19`。SSE 非原生（見 §7） |
| 元件庫 | `@mantine/core`, `@mantine/hooks`, `@mantine/form` | **9.4.1** | 三者版本鎖定一致。v9 **要求** React `^19.2.0`（已棄 React 18）。需 PostCSS 設定 |
| PostCSS | `postcss`, `postcss-preset-mantine`, `postcss-simple-vars` | 最新 | Mantine 9 CSS 函式/斷點 mixin 必需 |
| 驗證/型別 | `zod` | **4.4.3** | greenfield 用 v4（`npm i zod` 預設即 v4）；需 TS ≥ 5.5 |
| 表單 | `react-hook-form` | **7.80.0** | peer `react ^19`（v8 仍 beta，不用） |
| 表單×Zod | `@hookform/resolvers` | **5.4.0** | `zodResolver` runtime 偵測 Zod 3/4；需 `>=5.2.2` 修正 Zod4 輸出型別；peer `react-hook-form ^7.55.0` |
| 測試 | `vitest` | **4.1.9** | 接 `vitest/config` 復用 `@vitejs/plugin-react` |
| 測試 | `@testing-library/react` | **16.3.2** | React 19 需 `>=16.1`；peer `react ^18 || ^19` |
| 測試 | `@testing-library/dom` | **10.4.1** | **RTL v16 起須單獨裝**（peer dep，否則 "Cannot find module"） |
| 測試 | `@testing-library/jest-dom` | **6.9.1** | setup 匯入 `@testing-library/jest-dom/vitest` |
| 測試 | `jsdom` | **29.1.1** | 預設環境；要更快可換 `happy-dom` |
| 語言 | `typescript` | **6.0.3** | **鎖 6.0.x**：typescript-eslint 上限 `<6.1.0` |
| Lint | `eslint` | **10.6.0** | **flat config only**（`eslint.config.js`），舊 `.eslintrc` 不支援 |
| Lint | `typescript-eslint` | **8.62.0** | `tseslint.config()` helper |
| Lint | `eslint-plugin-react-hooks` | **7.1.1** | v7 已併入 React Compiler「Rules of React」規則 |
| Lint | `eslint-plugin-react-refresh` | **0.5.3** | `configs.vite`；仍 0.x，minor 視為可能 breaking |
| 格式 | `prettier` | **3.9.1** | 與 ESLint 分離；加 `eslint-config-prettier` 殿後 |

**Node 執行/建置版本**：綁定底線由 React Router 8 拉到 **Node 22.22+**（其餘 Vite 8 / ESLint 10 / jsdom 29 只要 20.19+）。採最新版 ⇒ 開發機與部署機安裝 **Node 22 LTS（或 24）**。Node 僅為 build-time 依賴，正式環境執行期仍只有 uvicorn。

**圖表庫**：延到「圖表頁 spec」再定（候選 `@mantine/charts`＝Recharts 3+、或 visx/ECharts）。注意後端 `app/services/chart.py` 的 SVG 是給 PDF 用，與前端互動圖表是兩回事，不混用。

**備選（若想壓低 Node 底線到 20.19）**：改用 `react-router@7.18.0`（peer `react >=18`、Node 20+、保留 `react-router-dom`）。本 spec 採 v8 以符「最新版」要求。

---

## 4. 架構

### 4.1 Repo 佈局（Python 與 Node 工具鏈乾淨分離）
```
report-mark/
├─ app/  web/                 # 既有 Python（後端完全不動）
├─ web/static/                # 既有 vanilla 頁（過渡期保留，逐頁退役）
└─ frontend/                  # 新增：SPA 原始碼
   ├─ index.html
   ├─ package.json  package-lock.json
   ├─ tsconfig.json  vite.config.ts  vitest.config.ts
   ├─ postcss.config.cjs  eslint.config.js  .prettierrc
   ├─ src/
   │  ├─ main.tsx             # createRoot + Providers + RouterProvider
   │  ├─ App.tsx              # 路由表 / layout / 404
   │  ├─ theme.ts             # Mantine 主題（映射 tokens.css 品牌）
   │  ├─ lib/
   │  │  ├─ api.ts            # typed fetch client（401→/login、SSE helper 介面）
   │  │  ├─ schemas.ts        # Zod schema + z.infer 型別
   │  │  └─ eta.ts            # 由 web/static/app/eta.js 移植
   │  ├─ features/monitor/    # monitor 切片（元件 + hooks）
   │  └─ test/setup.ts        # jest-dom/vitest 註冊
   └─ dist/                   # vite build 產物（gitignore），FastAPI 服務此處
```
`frontend/node_modules` 與 `frontend/dist` 一律 gitignore。

### 4.2 過渡期共存：SPA 掛 `/app` 子路徑
- Vite `base: '/app/'`（**尾斜線必要**）、React Router `basename: '/app'`（**無尾斜線**）。兩者不一致是子路徑掛載最常見的故障。
- 新 monitor＝`/app/monitor`。現有 vanilla 路由（`/`、`/monitor`、`/help`、`/login`…）原封不動，用 URL 前綴乾淨隔離，catch-all 不撞 vanilla。
- `createBrowserRouter` 實例建在**模組層（render 樹之外）**，勿放進 component / useState，否則 re-render 會丟失 router 狀態。
- 達平價後：把舊 `GET /monitor` 改 **redirect 到 `/app/monitor`**；舊 `monitor.html` 留到正式退役。終局再把 basename 改 `/`、退役 vanilla。

### 4.3 FastAPI 服務 SPA（沿用既有認證，零後端邏輯變動）
在 `web/server.py` 新增（僅服務靜態與 shell，無業務邏輯）：
- **雜湊資產** `GET /app/assets/*`：Vite 內容雜湊檔名 → `StaticFiles` 掛載，長快取 `immutable`。
- **SPA shell** catch-all `GET /app/{path:path}` → 回 `frontend/dist/index.html`，`Cache-Control: no-cache`（新 build 即時生效＋支援 client 深連結）。註冊順序須在既有具體路由之後、避免吃掉它們。
- **認證**：既有 deny-by-default 中介層（`web/auth.py` + middleware）白名單只有 `/login`，故 `/app/*` 與其資產**自動需要 `tf_session` cookie**（與今天 `/static` 同模式）。
  - 瀏覽器深連 `/app/monitor` 無 cookie → 中介層伺服器端導向 `/login` → 登入後返回。
  - mid-session fetch 拿 401 → 由 client 端 `api.ts` 導向 `/login`（§7）。
  - **白名單不需改動**——低風險點。

### 4.4 開發迴圈（HMR）
- 新 `make spa-dev`：跑 Vite dev server，`server.proxy` 把 `/api`、`/login`、`/logout` 轉發到 uvicorn:8097。開發時同時跑 `make serve`（uvicorn）＋ `make spa-dev`（Vite）。同源 proxy ⇒ cookie 正常；localhost 走 http 由 `allow_insecure_local` 允許。
- Vite dev server 對 `/app/*` 深連結自動 SPA-fallback 到 index.html。

### 4.5 Mantine 主題映射既有視覺
- `theme.ts`：`createTheme({...})`，`colors.gold` 為從品牌色 `#AE7415` 衍生的 **10 階 `MantineColorsTuple`**（index 0 最淺 → 9 最深，恰 10 格），`primaryColor: 'gold'`、`primaryShade: { light: 7, dark: 6 }`（讓 filled variant 落在 `#AE7415` 那階），`fontFamily` 沿用 `tokens.css` 現值。以 `tokens.css` 品牌色/字級為單一真相源餵進主題。
- Vite 接 `postcss-preset-mantine` + `postcss-simple-vars`（`postcss.config.cjs`）。
- CSS import 順序（`main.tsx`，render 前）：`@mantine/core/styles.css` 先、app 自有 CSS 後（可覆寫）。本 SPA 不混 Tailwind，故用一般 `styles.css`、不需 `styles.layer.css`。

---

## 5. 共用地基層（一次建好、後續每頁複用）

| 單元 | 職責 | 介面 / 備註 |
|---|---|---|
| `lib/api.ts` | typed fetch 包裝：同源自動帶 cookie、**401→`window.location='/login'`**、回傳型別化資料；定義 SSE helper 介面（本切片不用，先留型別供 ask/report 後續用） | 全站唯一 API 出口 |
| `lib/schemas.ts` | Zod schema 同時做 runtime 驗證 ＋ 產 TS 型別（`z.infer`）；本切片先定 `progressSchema → ProgressResponse` | 確立「每端點一 schema」模式 |
| `theme.ts` | Mantine 主題（§4.5） | 視覺貼近金色品牌的單一真相源 |
| `lib/eta.ts` | 由 `web/static/app/eta.js` 原樣移植 `rateText` / `ingestRateText` → TS | 證明「已測純函式直接移植」管線 |
| `App.tsx` / `main.tsx` | `MantineProvider` + `QueryClientProvider` + `RouterProvider`；root layout、404 route | composition root；router 建在模組層 |

**按需移植原則**：地基層**只移植 monitor 用得到的 `eta.ts`**；`meta` / `markdown` / `state` 等留待各自的頁遷移時再搬，不預先全搬。

---

## 6. monitor 垂直切片

### 6.1 端點與資料形狀
沿用既有 `GET /api/progress`（**不改**），回傳：
```
{ ts, db:{reports,chunks,markets}, summary:{done,total,remaining,pct},
  ...runtime（tagging/ingest/pipeline/orchestrator 等欄位） }
```
`progressSchema`（Zod）對齊此形狀；解析失敗即顯示錯誤態（§7）。

### 6.2 資料流與元件
- **抓取**：`useQuery({ queryKey:['progress'], queryFn: api.getProgress, refetchInterval: 2000 })` → `ProgressResponse`。輪詢交給 Query，取代手寫 `setInterval` 取資料。
- **開頁平均速率**：`useMonitorRate` hook，以 `useRef` 存「首個樣本基線」，按現有 `monitor.html` `rate()` 邏輯算 spm/tpm（與現行**位元等價**），餵給移植來的 `rateText` / `ingestRateText`。獨立單元測試。
- **元件**（Mantine 主題化，對位現有版面）：
  - 頂部 tiles：已導入報告 / 總片段 / 標註% / 摘要%（含 `useTween` 數字動畫）。
  - 三面板：標註 TAGGING、導入 INGEST、摘要 SUMMARY（`Progress` 條 ＋ done/total/fail/remaining）。
  - 管線 PIPELINES：web / ingest / tag / sum 狀態點。
  - 市場分佈 MARKETS：各市場橫條。
  - footer：更新時間 ＋ orchestrator pill。
- **clock**：本地 `setInterval` 1s（瀏覽器執行期，無限制）。
- **LIVE 指示**：由 query 狀態驅動（`isError` → 離線、保留上次良好值，等同現有 `setLive(false)`）。

### 6.3 平價標準（這切片「做完」的客觀定義）
1. `/app/monitor` 與舊 `/monitor` **同端點、同 2 秒節奏**；四 tiles／三面板／pipeline／市場分佈全部正確更新。
2. `rateText` / `ingestRateText` 輸出與舊頁**逐字相同**（純函式移植＋移植測試保證）。
3. clock 走、LIVE 隨 fetch 成功/失敗切換。
4. 視覺貼近現有金色品牌。
5. **0 console error**（Playwright 比對，沿用現有 monitor 驗證法）。
6. session 過期（401）會導去 `/login`。

達標後 `/monitor` redirect 到 `/app/monitor`，舊 `monitor.html` 留到正式退役。

---

## 7. 錯誤處理（跨切面，本 spec 一併立好慣例）

- **session 過期 / 401**：`api.ts` 攔 401 → `window.location='/login'`。深連無 cookie 走伺服器端導向；mid-session fetch 走 client 端導向，兩路皆覆蓋。
- **網路錯誤**：TanStack Query 預設退避重試；每路由 React error boundary；monitor 保留上次良好值＋LIVE 熄。
- **API 形狀漂移**：Zod `parse` 失敗即拋型別化錯誤、記錄並顯示錯誤態，而非默默渲染壞資料——呼應後端「逐層守門」防禦姿態。
- **build 失敗**：`make spa-build` 大聲失敗；`restart` 只在 build 成功後執行，故失敗時正式環境續服舊 dist。回滾＝保留前一份 dist 或 git revert 後重 build。
- **SPA 404**：未知 `/app/*` 路由由 React Router catch-all → SPA 內 404 頁。
- **（前瞻硬約束）** 日後遷 ask/report 時，務必沿用 `ask.js` 既有串流強健語意（result/assistant fallback、只對 API 529 重試、逾時若已串出文字則 fail-open）。TanStack Query 不原生支援 SSE：採 `experimental_streamedQuery`（包 `AsyncIterable`）或以 `queryClient.setQueryData` 手動推進 cache，連線本身仍自管。本切片不碰，列為後續 spec 約束。

---

## 8. 測試

- **Vitest + React Testing Library**：monitor 元件、`useMonitorRate`、`useTween`；`eta.test.mjs` 移植成 Vitest `eta.test.ts`。
  - `vitest.config.ts` 復用 `@vitejs/plugin-react`、`environment:'jsdom'`、`setupFiles:'./src/test/setup.ts'`、`globals:true`。
  - `src/test/setup.ts` 匯入 `@testing-library/jest-dom/vitest`。
  - tsconfig `types: ["vitest/globals","@testing-library/jest-dom"]`。
- **Playwright 平價**：登入後載 `/app/monitor`，斷言 ~4 秒內 2 次 `/api/progress` 請求、tiles 填值、clock 走、**0 console error**，與舊 `/monitor` 對照。沿用現有 Playwright 慣例（`/app` 在 auth 後，須先登入；測試憑證讀 repo 根 `.env`）。
- 既有 `node --test web/static/app/*.test.mjs` 維持綠燈到 vanilla 退役。
- 新 make 目標：`make spa-test`（`vitest run`）、`make spa-e2e`（選用）。ESLint 限作用於 `frontend/`。

---

## 9. 部署

- **開發**：`make serve`（uvicorn）＋ `make spa-dev`（Vite HMR）。
- **正式**：`git pull` → `make spa-build`（`vite build` → `frontend/dist`）→ `sudo systemctl restart report-mark-web.service`。
- systemd unit **不變**（仍 uvicorn 靜態服 dist）。Node 22 LTS 在機器上一次性安裝；build 由部署者登入 shell 跑（PATH 有 node），**不經 systemd**，故無需像 `claude` CLI 那樣加 systemd PATH drop-in。
- 資產雜湊 `immutable` 長快取；shell `no-cache`。無 CDN。

---

## 10. 後續拆解路線（各自獨立 spec → plan → 實作，**非本 spec**）

| Phase | 內容 | 風險 |
|---|---|---|
| **0/1（本 spec）** | 地基 ＋ monitor 切片 | 低 |
| 2 | browse + search + 檢視/分組 + 排序（移植 `meta`、清單原語） | 中 |
| 3 | ask 多輪串流 ＋ 每輪引用 ＋ 對話側欄（移植 `markdown`，保串流強健性） | 高 |
| 4 | 深度研報（SSE ＋ 報告 modal ＋ PDF 連結 ＋ report_gate） | 中高 |
| 5 | help + login；basename `/app`→`/`、退役 vanilla、清 `web/static` | 中 |
| 6+ | 新頁（原生 SPA）：圖表/儀表板、表單/後台 CRUD —— 真正的驅動目標 | 視範圍 |

---

## 11. 風險與緩解

- **平價回歸**（全面重寫的主要風險）：以「逐頁、平價閘門、舊頁保留到達標」緩解；最高風險的 ask 串流排到 Phase 3、有 vanilla 參考實作對照。
- **版本鏈相依脆弱**：React 19.2.7 是滿足 Mantine 9 與 React Router 8 的樞紐；Node 22.22+ 是 React Router 8 拉高的底線。lockfile 鎖定、CI/部署校驗 Node 版本。
- **Mantine 視覺漂移**：以 `tokens.css` 為主題真相源，Playwright 截圖對照舊頁。
- **共用工作目錄**：本 repo 有他人並行未提交 WIP；提交只 `git add` 明確路徑，先 `git diff --staged --stat` 驗範圍，勿 `git add -A`/`.`。
- **`research/auto-import` 等唯讀來源** 不受本變更影響（純前端）。

---

## 12. 參考（查證來源）

- React 19：react.dev/versions、react.dev/blog/2024/12/05/react-19、2025/10/01/react-19-2
- Vite 8 / plugin-react 6：vite.dev/releases、vite-plugin-react README、npm dist-tags
- React Router 8：reactrouter.com/upgrading/v7、/start/modes、/api/data-routers/createBrowserRouter、npm peer deps
- TanStack Query 5：tanstack.com/query、streamedQuery 文件、Query discussion #418
- Mantine 9：mantine.dev/changelog/9-0-0、/guides/vite、/theming/colors、npm peer deps（`react ^19.2.0`）
- Zod 4 / resolvers 5：zod.dev/v4、@hookform/resolvers releases、npm
- 測試：@testing-library/react releases（v16.1 起支援 React 19）、vitest npm、jsdom npm
- TS/Lint：typescript-eslint（TS 上限 `<6.1.0`）、eslint-plugin-react-hooks v7 CHANGELOG、ESLint 10 flat config
