# 前端 React SPA 地基 ＋ monitor 切片 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 站起 Vite + React 19 + TypeScript 的 SPA（掛 `/app` 子路徑、由現有 FastAPI 服務與認證），並把 monitor 頁端到端遷入以證明整條管線。

**Architecture:** 新 `frontend/` 原始碼樹（與 Python 後端工具鏈分離），Vite build 到 `frontend/dist` 由 FastAPI catch-all 服務 SPA shell、StaticFiles 服務雜湊資產，沿用 `tf_session` cookie 認證並補 return-to-origin glue。共用地基層（typed API client / Zod schema / Mantine 主題 / 純函式移植）一次建好，monitor 作為第一個垂直切片驗證之。後端業務邏輯與 schema 完全不動。

**Tech Stack:** React 19.2.7、TypeScript 6.0.3、Vite 8.1.0、@vitejs/plugin-react 6.0.3、react-router 8.0.1、@tanstack/react-query 5.101.2、@mantine/core·hooks·form 9.4.1、zod 4.4.3、react-hook-form 7.80.0、@hookform/resolvers 5.4.0、Vitest 4.1.9、@testing-library/react 16.3.2、ESLint 10.6.0、Node 22 LTS。

## Global Constraints

> 來源：`docs/superpowers/specs/2026-06-29-frontend-react-spa-foundation-design.md`。每個 task 的要求都隱含包含本節。

- **Node 版本**：綁定底線 **Node 22.22+**（react-router 8 要求）。開發機與部署機用 **Node 22 LTS（或 24）**。Node 僅 build-time 依賴；正式環境執行期仍只有 uvicorn。
- **React 鎖版**：`react` / `react-dom` **鎖 19.2.7**（同時滿足 Mantine 9 的 `^19.2.0` 與 react-router 8 的 `>=19.2.7`）。**勿降到 19.0/19.1**，否則 Mantine ERESOLVE。
- **套件版本**：一律用本節 Tech Stack 列出的確切版本。`@types/react` / `@types/react-dom` 用 `^19`，**必須單獨安裝**。`@testing-library/dom@10` **必須單獨安裝**（RTL v16 起為 peer dep）。`typescript` **鎖 6.0.x**（typescript-eslint 上限 `<6.1.0`）。
- **路由模式**：用 react-router **Data mode**（`createBrowserRouter` + `RouterProvider`），**不**用 Framework/SSR mode、**不**裝 `@react-router/dev`。v8 已移除 `react-router-dom`：核心 API 從 `react-router`、`RouterProvider` 從 `react-router/dom`。`createBrowserRouter` 實例**建在模組層**（render 樹之外）。
- **子路徑掛載**：Vite `base: '/app/'`（**尾斜線必要**）；react-router `basename: '/app'`（**無尾斜線**）。兩者不一致是最常見故障。
- **dist 換版**：`make spa-build` **不得原地 build live `frontend/dist`**；先建到 staging（`frontend/dist.next`），成功後**原子換版**到 `frontend/dist`（rename 替換），失敗時 live `dist` 不動。
- **`next` open-redirect 防護**：登入 return-to-origin 的 `next` **僅接受同源相對路徑**：必須以單一 `/` 開頭，且**拒絕** `//…`、`/\…`、含 `://` 的 schema URL、以及不以 `/` 開頭者；不合法則回退到 `/`。
- **後端範圍**：不改任何**業務邏輯**或 schema（`app/**`、`db/**`、既有 `/api/*` 行為一律不動）。唯一允許的後端改動：`web/server.py` 新增「服務 SPA 的靜態路由（catch-all shell + 雜湊資產掛載）」與「登入 return-to-origin glue」。認證白名單 `_AUTH_ALLOWLIST` **不改**。
- **monitor query 行為**：`useQuery` 明確設 `refetchInterval: 2000, refetchIntervalInBackground: true, retry: false, refetchOnWindowFocus: false, refetchOnReconnect: false`，維持舊頁「每 2 秒一輪；本輪失敗等下一輪」語意。
- **Mantine 主題真相源**：以 `web/static/tokens.css` 的品牌色 `--brand: #ae7415`、`--brand-strong: #8a5a0f`、字體堆疊餵進主題。
- **協作慣例**：對使用者回覆用**繁體中文**；**不加裝飾性 emoji**；commit 用 **Conventional Commits + 繁中 scope**（如 `feat(frontend): …`）；只 `git add` **明確路徑**（禁 `git add -A`/`.`，本 repo 有他人未提交 WIP）；每個 commit 訊息結尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`（多行用 HEREDOC）。
- **測試指令**：後端 `uv run pytest -q <path>`；前端 `cd frontend && npx vitest run <path>`；型別 `cd frontend && npx tsc --noEmit`；建置 `cd frontend && npm run build`。

---

## 檔案結構

```
report-mark/
├─ frontend/                         # 新增（Task 1）
│  ├─ .gitignore  index.html  package.json
│  ├─ tsconfig.json  vite.config.ts  vitest.config.ts
│  ├─ postcss.config.cjs  eslint.config.js  .prettierrc
│  └─ src/
│     ├─ main.tsx                    # createRoot + Providers + RouterProvider（Task 5）
│     ├─ App.tsx                     # 路由表 + layout + 404（Task 5、Task 11）
│     ├─ theme.ts                    # Mantine 主題（Task 5）
│     ├─ lib/
│     │  ├─ api.ts                   # typed fetch + 401 glue（Task 6）
│     │  ├─ schemas.ts               # Zod progressSchema（Task 6）
│     │  └─ eta.ts                   # 移植自 web/static/app/eta.js（Task 7）
│     │  └─ eta.test.ts              # 移植自 eta.test.mjs（Task 7）
│     ├─ features/monitor/
│     │  ├─ rate.ts                  # 純函式 nextRate（Task 8）
│     │  ├─ rate.test.ts             # （Task 8）
│     │  ├─ useMonitorRate.ts        # hook（Task 8）
│     │  ├─ useTween.ts  useTween.test.tsx   # 數字動畫（Task 9）
│     │  ├─ MonitorPage.tsx          # 頁面（Task 9）
│     │  └─ MonitorPage.test.tsx     # RTL（Task 9）
│     └─ test/setup.ts               # jest-dom/vitest 註冊（Task 1）
├─ web/server.py                     # 加 catch-all + 資產掛載 + next glue（Task 3、4、12）
├─ tests/test_spa_serving.py         # 新增（Task 3）
├─ tests/test_auth_next.py           # 新增（Task 4）
├─ Makefile                          # 加 spa-* 目標（Task 2）
└─ docs/superpowers/plans/2026-06-29-frontend-react-spa-foundation.md
```

---

### Task 1: frontend 腳手架與工具鏈（build / lint / test 綠燈）

**Files:**
- Create: `frontend/package.json`, `frontend/.gitignore`, `frontend/index.html`, `frontend/tsconfig.json`, `frontend/vite.config.ts`, `frontend/vitest.config.ts`, `frontend/postcss.config.cjs`, `frontend/eslint.config.js`, `frontend/.prettierrc`, `frontend/src/main.tsx`, `frontend/src/App.tsx`, `frontend/src/test/setup.ts`

**Interfaces:**
- Produces: 可建置的 `frontend/`；npm scripts `dev`/`build`/`test`/`lint`/`typecheck`；Vite `base:'/app/'` + proxy；佔位 `App` 元件。後續 task 覆寫 `main.tsx`/`App.tsx`。

- [ ] **Step 1: 確認 Node 版本符合底線**

Run: `node -v`
Expected: `v22.22.0` 以上（或 v24.x）。若低於 22.22，先安裝 Node 22 LTS 再繼續。

- [ ] **Step 2: 建立 `frontend/package.json`（確切鎖版）**

```json
{
  "name": "report-mark-frontend",
  "private": true,
  "type": "module",
  "engines": { "node": ">=22.22.0" },
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "preview": "vite preview",
    "typecheck": "tsc --noEmit",
    "lint": "eslint .",
    "test": "vitest run"
  },
  "dependencies": {
    "@hookform/resolvers": "5.4.0",
    "@mantine/core": "9.4.1",
    "@mantine/form": "9.4.1",
    "@mantine/hooks": "9.4.1",
    "@tanstack/react-query": "5.101.2",
    "react": "19.2.7",
    "react-dom": "19.2.7",
    "react-hook-form": "7.80.0",
    "react-router": "8.0.1",
    "zod": "4.4.3"
  },
  "devDependencies": {
    "@eslint/js": "10.6.0",
    "@tanstack/react-query-devtools": "5.101.2",
    "@testing-library/dom": "10.4.1",
    "@testing-library/jest-dom": "6.9.1",
    "@testing-library/react": "16.3.2",
    "@types/react": "^19",
    "@types/react-dom": "^19",
    "@vitejs/plugin-react": "6.0.3",
    "eslint": "10.6.0",
    "eslint-config-prettier": "10.1.5",
    "eslint-plugin-react-hooks": "7.1.1",
    "eslint-plugin-react-refresh": "0.5.3",
    "globals": "16.4.0",
    "jsdom": "29.1.1",
    "postcss": "8.5.6",
    "postcss-preset-mantine": "1.18.0",
    "postcss-simple-vars": "7.0.1",
    "prettier": "3.9.1",
    "typescript": "6.0.3",
    "typescript-eslint": "8.62.0",
    "vite": "8.1.0",
    "vitest": "4.1.9"
  }
}
```

- [ ] **Step 3: 建立 `frontend/.gitignore`**

```
node_modules
dist
dist.next
*.local
```

- [ ] **Step 4: 建立設定檔**

`frontend/index.html`：
```html
<!doctype html>
<html lang="zh-Hant">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>廷豐智能研報</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`frontend/tsconfig.json`：
```json
{
  "compilerOptions": {
    "target": "ES2022",
    "useDefineForClassFields": true,
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "verbatimModuleSyntax": true,
    "skipLibCheck": true,
    "types": ["vite/client", "vitest/globals", "@testing-library/jest-dom"]
  },
  "include": ["src"]
}
```

`frontend/vite.config.ts`（`base` 尾斜線；proxy 到 uvicorn 8097）：
```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  base: '/app/',
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://localhost:8097',
      '/login': 'http://localhost:8097',
      '/logout': 'http://localhost:8097',
    },
  },
})
```

`frontend/vitest.config.ts`：
```ts
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: './src/test/setup.ts',
    css: true,
  },
})
```

`frontend/postcss.config.cjs`：
```js
module.exports = {
  plugins: {
    'postcss-preset-mantine': {},
    'postcss-simple-vars': {
      variables: {
        'mantine-breakpoint-xs': '36em',
        'mantine-breakpoint-sm': '48em',
        'mantine-breakpoint-md': '62em',
        'mantine-breakpoint-lg': '75em',
        'mantine-breakpoint-xl': '88em',
      },
    },
  },
}
```

`frontend/eslint.config.js`（ESLint 10 flat config）：
```js
import js from '@eslint/js'
import globals from 'globals'
import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import prettier from 'eslint-config-prettier'

export default tseslint.config(
  { ignores: ['dist', 'dist.next'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
      prettier,
    ],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
  },
)
```

`frontend/.prettierrc`：
```json
{ "semi": false, "singleQuote": true, "printWidth": 100 }
```

- [ ] **Step 5: 建立佔位 `src/` 與測試 setup**

`frontend/src/test/setup.ts`：
```ts
import '@testing-library/jest-dom/vitest'
```

`frontend/src/App.tsx`（佔位，Task 5/11 覆寫）：
```tsx
export default function App() {
  return <div>廷豐智能研報（SPA 地基）</div>
}
```

`frontend/src/main.tsx`（佔位，Task 5 覆寫為含 Providers/Router）：
```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import App from './App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
```

- [ ] **Step 6: 安裝相依並驗證乾淨解析**

Run: `cd frontend && npm install`
Expected: 安裝成功、**無 ERESOLVE peer-dependency 錯誤**（若出現 React 19.0/19.1 相關 ERESOLVE，代表 react 被降版，檢查 package.json 鎖在 19.2.7）。

- [ ] **Step 7: 驗證 build 與 lint 綠燈**

Run: `cd frontend && npm run build && npm run lint`
Expected: `tsc --noEmit` 無錯、`vite build` 產出 `frontend/dist/`、ESLint 0 error。

- [ ] **Step 8: Commit**

```bash
git add frontend/.gitignore frontend/package.json frontend/package-lock.json frontend/index.html \
        frontend/tsconfig.json frontend/vite.config.ts frontend/vitest.config.ts \
        frontend/postcss.config.cjs frontend/eslint.config.js frontend/.prettierrc \
        frontend/src/main.tsx frontend/src/App.tsx frontend/src/test/setup.ts
git commit -m "$(cat <<'EOF'
feat(frontend): 建立 Vite + React 19 + TS SPA 腳手架

掛 /app 子路徑、proxy /api 到 uvicorn 8097，鎖定查證過的最新穩定版
（React 19.2.7／Vite 8.1／Mantine 9.4.1 等）。佔位 App，後續 task 覆寫。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: Makefile 的 spa-* 目標（dev / build 原子換版 / test）

**Files:**
- Modify: `Makefile`（`.PHONY` 行與新增目標）

**Interfaces:**
- Produces: `make spa-dev`、`make spa-build`（staging + 原子換版）、`make spa-test`。供 Task 12 部署與後續使用。

- [ ] **Step 1: 在 `.PHONY` 清單末尾加入 spa 目標**

把 `Makefile:18-22` 的 `.PHONY` 區塊最後一行 `sync-once` 改為：
```make
        sync-once \
        spa-dev spa-build spa-test
```

- [ ] **Step 2: 在 `serve:` 目標之後插入 SPA 區段**

於 `Makefile` `serve:`／`search:` 附近新增：
```make
# ───── 前端 SPA（Vite + React，掛 /app）─────
spa-dev:  ## 啟動 Vite dev server（HMR；需另開 make serve 跑 uvicorn）
	cd frontend && npm run dev

spa-build:  ## 建置 SPA：先產到 staging 再原子換版到 frontend/dist（失敗則 live dist 不動）
	cd frontend && npm ci
	cd frontend && rm -rf dist.next && npx vite build --outDir dist.next
	cd frontend && rm -rf dist.prev && (test -d dist && mv dist dist.prev || true) && mv dist.next dist
	@echo "SPA build 換版完成；如需回滾：cd frontend && rm -rf dist && mv dist.prev dist"

spa-test:  ## 跑前端 Vitest
	cd frontend && npm run test
```

- [ ] **Step 3: 驗證目標可執行**

Run: `make spa-build`
Expected: `frontend/dist/` 更新（含 `index.html` 與 `assets/`），印出「換版完成」。

Run: `make spa-test`
Expected: Vitest 執行（此時可能 0 測試或佔位測試，無錯即可）。

- [ ] **Step 4: Commit**

```bash
git add Makefile
git commit -m "$(cat <<'EOF'
build(frontend): 新增 spa-dev/spa-build/spa-test make 目標

spa-build 先產到 dist.next 再原子換版到 dist，build 失敗時 live dist 不動。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: FastAPI 服務 SPA shell 與雜湊資產

**Files:**
- Modify: `web/server.py`（`STATIC_DIR` 附近加 `SPA_DIST`；`/static` mount 之前加 catch-all 與資產 mount）
- Test: `tests/test_spa_serving.py`

**Interfaces:**
- Consumes: 既有 `_AUTH_ALLOWLIST`、`auth.issue_token`、`STATIC_DIR`。
- Produces: `GET /app/{path}` 回 SPA shell（`frontend/dist/index.html`，no-cache）；`GET /app/assets/*` 服務雜湊資產。供 Task 9/11 的前端路由載入。

- [ ] **Step 1: 寫失敗測試**

`tests/test_spa_serving.py`：
```python
import time

from fastapi.testclient import TestClient

from web import auth
from web.server import app


def _auth_cookies() -> dict[str, str]:
    return {auth.COOKIE_NAME: auth.issue_token(int(time.time()))}


def test_app_deeplink_serves_spa_shell_when_authed():
    client = TestClient(app)
    resp = client.get("/app/monitor", cookies=_auth_cookies())
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert resp.headers.get("cache-control") == "no-cache"
    assert '<div id="root">' in resp.text


def test_app_shell_requires_auth():
    client = TestClient(app)
    resp = client.get("/app/monitor", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login")
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_spa_serving.py -x`
Expected: FAIL（`/app/monitor` 目前無對應路由，shell 斷言不成立）。

> 前置：先確保 `frontend/dist/index.html` 存在（Task 1 的 `npm run build` 已產出）。若不存在先跑 `make spa-build`。

- [ ] **Step 3: 在 `web/server.py` 加 SPA 服務**

在 `STATIC_DIR = ...`（`web/server.py:66`）之後新增：
```python
SPA_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
```

在檔案結尾 `app.mount("/static", ...)`（`web/server.py:886`）**之前**新增（順序重要：具體 API 路由都已在上方註冊，catch-all 只接 `/app/*`）：
```python
# ───── SPA（/app 子路徑；shell + 雜湊資產，純服務無業務邏輯）─────
app.mount(
    "/app/assets",
    _NoCacheStatic(directory=SPA_DIST / "assets", check_dir=False),
    name="spa-assets",
)


@app.get("/app/{spa_path:path}")
async def spa_shell(spa_path: str):
    """SPA shell：所有 /app/* 深連結回同一份 index.html，交給 client 端路由。"""
    index = SPA_DIST / "index.html"
    if not index.is_file():
        raise HTTPException(status_code=503, detail="SPA 尚未建置（make spa-build）")
    return FileResponse(index, headers={"Cache-Control": "no-cache"})
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q tests/test_spa_serving.py`
Expected: PASS（2 passed）。

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_spa_serving.py
git commit -m "$(cat <<'EOF'
feat(web): 由 FastAPI 服務 /app SPA shell 與雜湊資產

catch-all /app/{path} 回 frontend/dist/index.html（no-cache），
/app/assets 以 no-cache StaticFiles 服務；沿用既有 deny-by-default 認證。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 登入 return-to-origin glue（`next` 參數 + open-redirect 防護）

**Files:**
- Modify: `web/server.py`（`require_login` middleware、`login_page`、`login_submit`，並加 `_safe_next` helper）
- Test: `tests/test_auth_next.py`

**Interfaces:**
- Consumes: 既有 `auth.*`、`require_login`、`login_page`、`login_submit`。
- Produces: `_safe_next(raw: str | None) -> str`（回傳安全的同源相對路徑或 `/`）。未登入深連 `/app/*` → `/login?next=…`；登入成功導回 `next`。

- [ ] **Step 1: 寫失敗測試**

`tests/test_auth_next.py`：
```python
import time

from fastapi.testclient import TestClient

from web import auth
from web.server import _safe_next, app


def test_safe_next_accepts_same_origin_relative():
    assert _safe_next("/app/monitor") == "/app/monitor"
    assert _safe_next("/app/monitor?x=1") == "/app/monitor?x=1"


def test_safe_next_rejects_open_redirects():
    assert _safe_next("//evil.com") == "/"
    assert _safe_next("/\\evil.com") == "/"
    assert _safe_next("https://evil.com") == "/"
    assert _safe_next("evil") == "/"
    assert _safe_next(None) == "/"


def test_unauthed_app_deeplink_redirects_with_next():
    client = TestClient(app)
    resp = client.get("/app/monitor", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?next=%2Fapp%2Fmonitor"


def test_login_post_honors_safe_next(monkeypatch):
    monkeypatch.setattr(auth, "check_credentials", lambda u, p: True)
    monkeypatch.setattr(auth, "login_allowed", lambda r: True)
    client = TestClient(app)
    resp = client.post(
        "/login",
        data={"username": "x", "password": "y", "next": "/app/monitor"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/app/monitor"


def test_login_post_ignores_evil_next(monkeypatch):
    monkeypatch.setattr(auth, "check_credentials", lambda u, p: True)
    monkeypatch.setattr(auth, "login_allowed", lambda r: True)
    client = TestClient(app)
    resp = client.post(
        "/login",
        data={"username": "x", "password": "y", "next": "https://evil.com"},
        follow_redirects=False,
    )
    assert resp.headers["location"] == "/"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_auth_next.py -x`
Expected: FAIL（`_safe_next` 未定義 / 尚未帶 `next`）。

- [ ] **Step 3: 加 `_safe_next` 並接上 middleware 與登入**

在 `web/server.py` `_AUTH_ALLOWLIST`（`:126`）之後新增 helper：
```python
from urllib.parse import quote


def _safe_next(raw: str | None) -> str:
    """只接受同源相對路徑：必須以單一 '/' 開頭，拒絕 //、/\\、schema URL。否則回 '/'。"""
    if not raw or not raw.startswith("/"):
        return "/"
    if raw.startswith("//") or raw.startswith("/\\"):
        return "/"
    if "://" in raw:
        return "/"
    return raw
```

把 `require_login` 末尾的非-API 重導（`web/server.py:146`）由：
```python
    return RedirectResponse("/login", status_code=302)
```
改為（保留原路徑供登入後返回）：
```python
    nxt = _safe_next(request.url.path + ("?" + request.url.query if request.url.query else ""))
    target = "/login" if nxt == "/" else "/login?next=" + quote(nxt, safe="")
    return RedirectResponse(target, status_code=302)
```

把 `login_page`（`:846-850`）改為（已登入時導回 `next`、未登入時把 `next` 透傳給表單）：
```python
@app.get("/login")
async def login_page(request: Request):
    nxt = _safe_next(request.query_params.get("next"))
    if auth.verify_token(request.cookies.get(auth.COOKIE_NAME), int(time.time())):
        return RedirectResponse(nxt, status_code=302)
    return _static_page("login.html")
```

把 `login_submit`（`:853-871`）的成功分支改為導回 `next`（其餘錯誤分支保留原樣，但失敗導回時帶回 `next`）：
```python
@app.post("/login")
async def login_submit(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(""),
):
    nxt = _safe_next(next)
    err_q = "&next=" + quote(nxt, safe="") if nxt != "/" else ""
    if not auth.login_allowed(request):
        return RedirectResponse(f"/login?error=insecure{err_q}", status_code=303)
    now = int(time.time())
    ip = auth.client_ip(request)
    if auth.is_locked(ip, now):
        return RedirectResponse(f"/login?error=locked{err_q}", status_code=303)
    if auth.check_credentials(username, password):
        auth.reset(ip)
        resp = RedirectResponse(nxt, status_code=303)
        auth.set_session_cookie(resp, now, secure=auth.request_is_secure(request))
        return resp
    auth.record_failure(ip, now)
    return RedirectResponse(f"/login?error=1{err_q}", status_code=303)
```

> 註：`login.html` 表單需把 `next` 帶進 POST。若 `login.html` 為靜態表單，於 Task 11 之前補一個 `<input type="hidden" name="next">`，由小段 inline JS 從 `location.search` 的 `next` 填入。本步驟後端已可安全處理空 `next`（回 `/`），不阻塞。

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest -q tests/test_auth_next.py`
Expected: PASS（5 passed）。

- [ ] **Step 5: 回歸既有認證測試**

Run: `uv run pytest -q -k "auth or login"`
Expected: 既有認證/登入測試全綠（確認沒有破壞既有行為）。

- [ ] **Step 6: Commit**

```bash
git add web/server.py tests/test_auth_next.py
git commit -m "$(cat <<'EOF'
feat(web): 登入 return-to-origin glue 與 open-redirect 防護

未登入深連 /app/* 導向 /login?next=…，登入後返回原頁；_safe_next 僅放行
同源相對路徑（拒 //、/\\、schema URL）。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: Mantine 主題 + App providers + 路由骨架

**Files:**
- Create: `frontend/src/theme.ts`, `frontend/src/theme.test.ts`
- Modify: `frontend/src/main.tsx`, `frontend/src/App.tsx`

**Interfaces:**
- Consumes: Task 1 的腳手架。
- Produces: `export const theme`（Mantine `createTheme`，`primaryColor:'gold'`、10 階 `gold` 色票）；`main.tsx` 以 `MantineProvider`+`QueryClientProvider`+`RouterProvider` 包裝；`App` 提供 layout 與 404。`createBrowserRouter` 實例於 `App.tsx` 模組層、`basename:'/app'`。

- [ ] **Step 1: 寫主題的失敗測試**

`frontend/src/theme.test.ts`：
```ts
import { theme } from './theme'

test('theme 用品牌金色作 primary，色票恰 10 階', () => {
  expect(theme.primaryColor).toBe('gold')
  expect(theme.colors?.gold).toHaveLength(10)
  expect(theme.colors?.gold?.[7]).toBe('#ae7415')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/theme.test.ts`
Expected: FAIL（`./theme` 不存在）。

- [ ] **Step 3: 建立 `frontend/src/theme.ts`**

```ts
import { createTheme, type MantineColorsTuple } from '@mantine/core'

// 由品牌金 #ae7415（tokens.css --brand）衍生的 10 階色票；index 7 = 品牌主色。
const gold: MantineColorsTuple = [
  '#fbf3e3',
  '#f3e4c6',
  '#e7c98c',
  '#dbaf52',
  '#d09a26',
  '#c98e10',
  '#c58808',
  '#ae7415', // 品牌主色（--brand）
  '#9c6710',
  '#8a5a0f', // --brand-strong
]

export const theme = createTheme({
  colors: { gold },
  primaryColor: 'gold',
  primaryShade: { light: 7, dark: 6 },
  fontFamily:
    '-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", "PingFang TC", "Microsoft JhengHei", system-ui, sans-serif',
  defaultRadius: 'md',
})
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/theme.test.ts`
Expected: PASS。

- [ ] **Step 5: 覆寫 `frontend/src/App.tsx`（模組層 router + layout + 404）**

```tsx
import { createBrowserRouter, RouterProvider } from 'react-router'
import { AppShell, Container, Title, Text } from '@mantine/core'

function Layout({ children }: { children: React.ReactNode }) {
  return (
    <AppShell padding="md">
      <AppShell.Main>
        <Container size="lg">{children}</Container>
      </AppShell.Main>
    </AppShell>
  )
}

function Home() {
  return (
    <Layout>
      <Title order={2}>廷豐智能研報</Title>
      <Text c="dimmed">SPA 地基已就緒。</Text>
    </Layout>
  )
}

function NotFound() {
  return (
    <Layout>
      <Title order={3}>找不到頁面</Title>
    </Layout>
  )
}

// router 建在模組層（render 樹之外），basename 無尾斜線。
export const router = createBrowserRouter(
  [
    { path: '/', element: <Home /> },
    { path: '*', element: <NotFound /> },
  ],
  { basename: '/app' },
)

export default function App() {
  return <RouterProvider router={router} />
}
```

> 註：`RouterProvider` 在 react-router 8 由 `react-router/dom` 匯出；上方從 `react-router` 匯入 `RouterProvider` 在 v8 亦可用（`react-router` re-export DOM router）。若 build 報「RouterProvider 未匯出」，改 `import { RouterProvider } from 'react-router/dom'`。

- [ ] **Step 6: 覆寫 `frontend/src/main.tsx`（Providers + CSS 匯入順序）**

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MantineProvider } from '@mantine/core'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import '@mantine/core/styles.css'
import App from './App'
import { theme } from './theme'

const queryClient = new QueryClient()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="light">
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </MantineProvider>
  </StrictMode>,
)
```

- [ ] **Step 7: 驗證 build + 型別 + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: build 成功、lint 0 error。

- [ ] **Step 8: Commit**

```bash
git add frontend/src/theme.ts frontend/src/theme.test.ts frontend/src/App.tsx frontend/src/main.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): Mantine 金色品牌主題 + Provider/Router 骨架

createTheme 以 tokens.css --brand 衍生 10 階金色；main.tsx 串接
MantineProvider/QueryClientProvider/RouterProvider，router 建在模組層、basename=/app。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 6: typed API client + Zod progressSchema

**Files:**
- Create: `frontend/src/lib/api.ts`, `frontend/src/lib/schemas.ts`, `frontend/src/lib/api.test.ts`, `frontend/src/lib/schemas.test.ts`

**Interfaces:**
- Consumes: 無（葉層）。
- Produces:
  - `schemas.ts`：`progressSchema`（Zod）、`export type ProgressResponse = z.infer<typeof progressSchema>`。
  - `api.ts`：`export class ApiError extends Error { status: number }`；`export function redirectToLogin(): void`；`export async function getJSON<T>(path: string, schema: ZodType<T>, init?: RequestInit): Promise<T>`；`export function getProgress(): Promise<ProgressResponse>`；`export type SseHandlers`（SSE helper 介面占位，本切片不實作）。

- [ ] **Step 1: 寫 schema 失敗測試**

`frontend/src/lib/schemas.test.ts`：
```ts
import { progressSchema } from './schemas'

const sample = {
  ts: '14:00:00',
  db: { reports: 11401, chunks: 360000, markets: [{ market: 'TW', count: 9000 }] },
  summary: { done: 100, total: 200, remaining: 100, pct: 50 },
  tagging: { pct: 99.1, done: 11000, total: 11100, fail: 100 },
  ingest: { ingested: 3, fail: 0 },
  pipelines: { web: true, ingest: false, tag: false, summaries: true },
  orchestrator: { label: '編排器', status: 'running', timestamp: '13:59', raw: '' },
}

test('progressSchema 接受完整 payload', () => {
  expect(progressSchema.parse(sample).db.reports).toBe(11401)
})

test('progressSchema 容許缺 runtime 欄位（tagging/ingest/orchestrator）', () => {
  const minimal = { ts: '14:00:00', db: { reports: 1, chunks: 2, markets: [] }, summary: { done: 0, total: 0, remaining: 0, pct: 0 } }
  expect(progressSchema.parse(minimal).tagging).toBeUndefined()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/schemas.test.ts`
Expected: FAIL（`./schemas` 不存在）。

- [ ] **Step 3: 建立 `frontend/src/lib/schemas.ts`**

```ts
import { z } from 'zod'

const marketSchema = z.object({ market: z.string(), count: z.number() })

export const progressSchema = z.object({
  ts: z.string(),
  db: z.object({
    reports: z.number(),
    chunks: z.number(),
    markets: z.array(marketSchema),
  }),
  summary: z.object({
    done: z.number(),
    total: z.number(),
    remaining: z.number(),
    pct: z.number(),
  }),
  tagging: z
    .object({ pct: z.number(), done: z.number(), total: z.number(), fail: z.number() })
    .optional(),
  ingest: z.object({ ingested: z.number(), fail: z.number() }).optional(),
  pipelines: z
    .object({ web: z.boolean(), ingest: z.boolean(), tag: z.boolean(), summaries: z.boolean() })
    .partial()
    .optional(),
  orchestrator: z
    .object({
      label: z.string().optional(),
      status: z.string().optional(),
      timestamp: z.string().optional(),
      raw: z.string().optional(),
    })
    .optional(),
})

export type ProgressResponse = z.infer<typeof progressSchema>
```

- [ ] **Step 4: 跑 schema 測試確認通過**

Run: `cd frontend && npx vitest run src/lib/schemas.test.ts`
Expected: PASS。

- [ ] **Step 5: 寫 api client 失敗測試**

`frontend/src/lib/api.test.ts`：
```ts
import { afterEach, expect, test, vi } from 'vitest'
import { z } from 'zod'
import { ApiError, getJSON } from './api'

afterEach(() => vi.restoreAllMocks())

test('getJSON 解析並回傳型別化資料', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ a: 1 }), { status: 200 })),
  )
  const out = await getJSON('/api/x', z.object({ a: z.number() }))
  expect(out.a).toBe(1)
})

test('getJSON 遇 401 觸發導向登入並拋 ApiError', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { pathname: '/app/monitor', search: '', assign } as unknown as Location)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })))
  await expect(getJSON('/api/x', z.object({ a: z.number() }))).rejects.toBeInstanceOf(ApiError)
  expect(assign).toHaveBeenCalledWith('/login?next=%2Fapp%2Fmonitor')
})
```

- [ ] **Step 6: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/api.test.ts`
Expected: FAIL（`./api` 不存在）。

- [ ] **Step 7: 建立 `frontend/src/lib/api.ts`**

```ts
import type { ZodType } from 'zod'
import { progressSchema, type ProgressResponse } from './schemas'

export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.name = 'ApiError'
  }
}

/** session 過期 / 未登入時導向登入頁，並帶上目前 SPA 路徑供登入後返回。 */
export function redirectToLogin(): void {
  const next = location.pathname + location.search
  location.assign('/login?next=' + encodeURIComponent(next))
}

export async function getJSON<T>(path: string, schema: ZodType<T>, init?: RequestInit): Promise<T> {
  const resp = await fetch(path, { credentials: 'same-origin', ...init })
  if (resp.status === 401) {
    redirectToLogin()
    throw new ApiError(401, '未登入')
  }
  if (!resp.ok) throw new ApiError(resp.status, `HTTP ${resp.status}`)
  return schema.parse(await resp.json())
}

export function getProgress(): Promise<ProgressResponse> {
  return getJSON('/api/progress', progressSchema, { cache: 'no-store' })
}

/** SSE helper 介面占位：ask/report 後續 spec 實作（本切片不使用）。 */
export type SseHandlers = {
  onDelta?: (text: string) => void
  onDone?: (payload: unknown) => void
  onError?: (err: unknown) => void
}
```

- [ ] **Step 8: 跑全部 lib 測試確認通過**

Run: `cd frontend && npx vitest run src/lib`
Expected: PASS（4 passed）。

- [ ] **Step 9: Commit**

```bash
git add frontend/src/lib/api.ts frontend/src/lib/schemas.ts frontend/src/lib/api.test.ts frontend/src/lib/schemas.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend): typed API client + Zod progressSchema

getJSON 同源帶 cookie、401 導向 /login?next=、用 Zod 解析；progressSchema
對齊 /api/progress 形狀並產出 ProgressResponse 型別。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 7: 移植純函式 eta.ts（rateText / ingestRateText）

**Files:**
- Create: `frontend/src/lib/eta.ts`, `frontend/src/lib/eta.test.ts`
- Reference: `web/static/app/eta.js`, `web/static/app/eta.test.mjs`

**Interfaces:**
- Produces: `export function rateText(remaining: number, rate: number | null, unit: string): string`；`export function ingestRateText(rpm: number | null, cps: number | null): string`。供 Task 9 monitor 面板使用。

- [ ] **Step 1: 寫測試（移植自 eta.test.mjs 的關鍵案例 + TS 化）**

`frontend/src/lib/eta.test.ts`：
```ts
import { ingestRateText, rateText } from './eta'

test('rate 為 null 顯示計算中', () => {
  expect(rateText(100, null, '摘要')).toBe('速率 計算中…')
})

test('rate < 0.05 顯示 0.0 不給 ETA', () => {
  expect(rateText(100, 0.04, '摘要')).toBe('速率 0.0 摘要/分')
})

test('remaining<=0 顯示已完成', () => {
  expect(rateText(0, 1.2, '標註')).toBe('速率 1.2 標註/分 · 已完成')
})

test('ETA 分鐘（<90 分）', () => {
  expect(rateText(60, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~30 分')
})

test('ETA 小時（>=90 分）', () => {
  expect(rateText(200, 2, '摘要')).toBe('速率 2.0 摘要/分 · 預估剩餘 ~1.7 時')
})

test('ingestRateText：rpm null → 計算中', () => {
  expect(ingestRateText(null, null)).toBe('速率 計算中…')
})

test('ingestRateText：cps null → 只顯示速率', () => {
  expect(ingestRateText(3, null)).toBe('速率 3.0 篇/分')
})

test('ingestRateText：兩段', () => {
  expect(ingestRateText(3, 1.5)).toBe('速率 3.0 篇/分 · 1.5 片段/秒')
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/lib/eta.test.ts`
Expected: FAIL（`./eta` 不存在）。

- [ ] **Step 3: 建立 `frontend/src/lib/eta.ts`（與 eta.js 行為位元等價）**

```ts
/** 監控面板速率行（純函式、無 DOM）。移植自 web/static/app/eta.js，行為須逐字相同。 */
export function rateText(remaining: number, rate: number | null, unit: string): string {
  if (rate == null) return '速率 計算中…'
  const line = `速率 ${rate.toFixed(1)} ${unit}/分`
  if (rate < 0.05) return line
  if (remaining <= 0) return `${line} · 已完成`
  const mins = remaining / rate
  const eta = mins < 90 ? `~${Math.round(mins)} 分` : `~${(mins / 60).toFixed(1)} 時`
  return `${line} · 預估剩餘 ${eta}`
}

export function ingestRateText(rpm: number | null, cps: number | null): string {
  if (rpm == null) return '速率 計算中…'
  const line = `速率 ${rpm.toFixed(1)} 篇/分`
  return cps == null ? line : `${line} · ${cps.toFixed(1)} 片段/秒`
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/lib/eta.test.ts`
Expected: PASS（8 passed）。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/lib/eta.ts frontend/src/lib/eta.test.ts
git commit -m "$(cat <<'EOF'
feat(frontend): 移植 eta.ts（rateText/ingestRateText）並補 Vitest

與 web/static/app/eta.js 行為位元等價，確立「已測純函式直接移植」管線。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 8: 開頁平均速率 — 純函式 nextRate + useMonitorRate hook

**Files:**
- Create: `frontend/src/features/monitor/rate.ts`, `frontend/src/features/monitor/rate.test.ts`, `frontend/src/features/monitor/useMonitorRate.ts`

**Interfaces:**
- Consumes: 無（rate.ts 葉層）。
- Produces:
  - `export interface MonitorRate { rpm: number | null; cps: number | null; spm: number | null; tpm: number | null }`
  - `export interface RateSample { reports: number; chunks: number; sumDone: number | null; tagDone: number | null }`
  - `export interface RateBase { reports: number; chunks: number; sum: number; tag: number; t: number }`
  - `export const NULL_RATE: MonitorRate`
  - `export function nextRate(base: RateBase | null, last: MonitorRate, s: RateSample, nowMs: number): { base: RateBase; rate: MonitorRate }`
  - `export function useMonitorRate(sample: RateSample | null): MonitorRate`

- [ ] **Step 1: 寫純函式失敗測試（鏡射 monitor.html rate() 邏輯）**

`frontend/src/features/monitor/rate.test.ts`：
```ts
import { NULL_RATE, nextRate, type RateSample } from './rate'

const s = (over: Partial<RateSample> = {}): RateSample => ({
  reports: 0,
  chunks: 0,
  sumDone: 0,
  tagDone: 0,
  ...over,
})

test('首次呼叫建立 base、回 NULL_RATE', () => {
  const r = nextRate(null, NULL_RATE, s({ reports: 100 }), 1000)
  expect(r.rate).toEqual(NULL_RATE)
  expect(r.base).toEqual({ reports: 100, chunks: 0, sum: 0, tag: 0, t: 1000 })
})

test('dt < 8 秒沿用上次速率', () => {
  const base = { reports: 100, chunks: 0, sum: 0, tag: 0, t: 1000 }
  const last = { rpm: 5, cps: 1, spm: 2, tpm: 3 }
  const r = nextRate(base, last, s({ reports: 200 }), 1000 + 5000)
  expect(r.rate).toEqual(last)
})

test('dt >= 8 秒計算每分鐘/每秒速率', () => {
  const base = { reports: 100, chunks: 0, sum: 0, tag: 0, t: 0 }
  // 60 秒後 reports +120 → rpm=120；chunks +600 → cps=10
  const r = nextRate(base, NULL_RATE, s({ reports: 220, chunks: 600, sumDone: 60, tagDone: 30 }), 60000)
  expect(r.rate.rpm).toBeCloseTo(120)
  expect(r.rate.cps).toBeCloseTo(10)
  expect(r.rate.spm).toBeCloseTo(60)
  expect(r.rate.tpm).toBeCloseTo(30)
})

test('sumDone/tagDone 為 null 時對應速率為 null', () => {
  const base = { reports: 0, chunks: 0, sum: 0, tag: 0, t: 0 }
  const r = nextRate(base, NULL_RATE, s({ sumDone: null, tagDone: null }), 60000)
  expect(r.rate.spm).toBeNull()
  expect(r.rate.tpm).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/monitor/rate.test.ts`
Expected: FAIL（`./rate` 不存在）。

- [ ] **Step 3: 建立 `frontend/src/features/monitor/rate.ts`**

```ts
export interface MonitorRate {
  rpm: number | null
  cps: number | null
  spm: number | null
  tpm: number | null
}
export interface RateSample {
  reports: number
  chunks: number
  sumDone: number | null
  tagDone: number | null
}
export interface RateBase {
  reports: number
  chunks: number
  sum: number
  tag: number
  t: number
}

export const NULL_RATE: MonitorRate = { rpm: null, cps: null, spm: null, tpm: null }

// 與 web/static/app/monitor.html rate() 等價：開頁以來平均；首次建 base，dt<8 沿用上次。
export function nextRate(
  base: RateBase | null,
  last: MonitorRate,
  s: RateSample,
  nowMs: number,
): { base: RateBase; rate: MonitorRate } {
  if (!base) {
    return {
      base: { reports: s.reports, chunks: s.chunks, sum: s.sumDone ?? 0, tag: s.tagDone ?? 0, t: nowMs },
      rate: NULL_RATE,
    }
  }
  const dt = (nowMs - base.t) / 1000
  if (dt < 8) return { base, rate: last }
  return {
    base,
    rate: {
      rpm: ((s.reports - base.reports) / dt) * 60,
      cps: (s.chunks - base.chunks) / dt,
      spm: s.sumDone == null ? null : ((s.sumDone - base.sum) / dt) * 60,
      tpm: s.tagDone == null ? null : ((s.tagDone - base.tag) / dt) * 60,
    },
  }
}
```

- [ ] **Step 4: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/monitor/rate.test.ts`
Expected: PASS（4 passed）。

- [ ] **Step 5: 建立 hook `frontend/src/features/monitor/useMonitorRate.ts`**

```ts
import { useRef } from 'react'
import { NULL_RATE, nextRate, type MonitorRate, type RateBase, type RateSample } from './rate'

/** 每次有新 sample 時更新；以 ref 保存 base/last，與舊頁開頁平均行為一致。 */
export function useMonitorRate(sample: RateSample | null): MonitorRate {
  const base = useRef<RateBase | null>(null)
  const last = useRef<MonitorRate>(NULL_RATE)
  const seen = useRef<RateSample | null>(null)

  if (sample && sample !== seen.current) {
    seen.current = sample
    const out = nextRate(base.current, last.current, sample, performance.now())
    base.current = out.base
    last.current = out.rate
  }
  return last.current
}
```

- [ ] **Step 6: 型別與 lint 檢查**

Run: `cd frontend && npm run typecheck && npm run lint`
Expected: 0 error。

- [ ] **Step 7: Commit**

```bash
git add frontend/src/features/monitor/rate.ts frontend/src/features/monitor/rate.test.ts frontend/src/features/monitor/useMonitorRate.ts
git commit -m "$(cat <<'EOF'
feat(frontend): 開頁平均速率 nextRate 純函式 + useMonitorRate hook

純函式鏡射舊 monitor rate()（首次建 base、dt<8 沿用上次、每分鐘/每秒換算），
hook 以 ref 保存 base/last。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 9: monitor 頁面（useTween + MonitorPage + RTL 測試）

**Files:**
- Create: `frontend/src/features/monitor/useTween.ts`, `frontend/src/features/monitor/useTween.test.tsx`, `frontend/src/features/monitor/MonitorPage.tsx`, `frontend/src/features/monitor/MonitorPage.test.tsx`

**Interfaces:**
- Consumes: `getProgress`/`ProgressResponse`（Task 6）、`rateText`/`ingestRateText`（Task 7）、`useMonitorRate`（Task 8）。
- Produces: `export function useTween(value: number | null): number | null`；`export default function MonitorPage()`。供 Task 11 掛路由。

- [ ] **Step 1: 寫 useTween 失敗測試（reduced-motion 直接回終值）**

`frontend/src/features/monitor/useTween.test.tsx`：
```tsx
import { renderHook } from '@testing-library/react'
import { useTween } from './useTween'

beforeEach(() => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: true })) as unknown as typeof matchMedia,
  )
})

test('reduced-motion 下直接回終值', () => {
  const { result } = renderHook(() => useTween(1234))
  expect(result.current).toBe(1234)
})

test('value 為 null 回 null', () => {
  const { result } = renderHook(() => useTween(null))
  expect(result.current).toBeNull()
})
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `cd frontend && npx vitest run src/features/monitor/useTween.test.tsx`
Expected: FAIL（`./useTween` 不存在）。

- [ ] **Step 3: 建立 `frontend/src/features/monitor/useTween.ts`**

```ts
import { useEffect, useRef, useState } from 'react'

/** 600ms 三次方緩動的數字動畫；尊重 reduced-motion（直接回終值）。 */
export function useTween(value: number | null): number | null {
  const reduce =
    typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
  const [shown, setShown] = useState<number | null>(value)
  const from = useRef<number | null>(value)

  useEffect(() => {
    if (value == null) {
      setShown(null)
      from.current = null
      return
    }
    const start = from.current ?? value
    if (reduce || start === value) {
      setShown(value)
      from.current = value
      return
    }
    const t0 = performance.now()
    let raf = 0
    const step = (t: number) => {
      const p = Math.min(1, (t - t0) / 600)
      const e = 1 - Math.pow(1 - p, 3)
      const cur = start + (value - start) * e
      setShown(cur)
      if (p < 1) raf = requestAnimationFrame(step)
      else from.current = value
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value, reduce])

  return shown
}
```

- [ ] **Step 4: 跑 useTween 測試確認通過**

Run: `cd frontend && npx vitest run src/features/monitor/useTween.test.tsx`
Expected: PASS。

- [ ] **Step 5: 建立 `frontend/src/features/monitor/MonitorPage.tsx`**

```tsx
import { useQuery } from '@tanstack/react-query'
import { Badge, Card, Group, Progress, SimpleGrid, Stack, Text, Title } from '@mantine/core'
import { getProgress } from '../../lib/api'
import { ingestRateText, rateText } from '../../lib/eta'
import { useMonitorRate } from './useMonitorRate'
import { useTween } from './useTween'

const nf = (n: number | null | undefined) =>
  n == null ? '—' : Math.round(Number(n)).toLocaleString('en-US')

function Tile({ label, value, suffix, sub }: { label: string; value: number | null; suffix?: string; sub?: string }) {
  const shown = useTween(value)
  return (
    <Card withBorder padding="md" radius="md">
      <Text size="xs" c="dimmed">{label}</Text>
      <Text fw={700} size="xl">
        {nf(shown)}
        {suffix ? <Text span size="sm" c="dimmed">{suffix}</Text> : null}
      </Text>
      {sub ? <Text size="xs" c="dimmed">{sub}</Text> : null}
    </Card>
  )
}

export default function MonitorPage() {
  const { data, isError } = useQuery({
    queryKey: ['progress'],
    queryFn: getProgress,
    refetchInterval: 2000,
    refetchIntervalInBackground: true,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
  })

  const rate = useMonitorRate(
    data
      ? {
          reports: data.db.reports,
          chunks: data.db.chunks,
          sumDone: data.summary?.done ?? null,
          tagDone: data.tagging?.done ?? null,
        }
      : null,
  )

  const tag = data?.tagging
  const sum = data?.summary
  const ing = data?.ingest

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <Title order={2}>研報導入監控</Title>
        <Badge color={isError ? 'red' : 'gold'} variant={isError ? 'light' : 'filled'}>
          {isError ? '重連中' : 'LIVE'}
        </Badge>
      </Group>

      <SimpleGrid cols={{ base: 2, sm: 4 }}>
        <Tile label="已導入報告" value={data?.db.reports ?? null} sub={`${data?.db.markets.length ?? 0} 個市場`} />
        <Tile label="總片段 CHUNKS" value={data?.db.chunks ?? null} sub="向量片段總數" />
        <Tile label="標註進度" value={tag ? tag.pct : null} suffix="%" sub={tag ? `已標註 ${nf(tag.done)} / ${nf(tag.total)}` : ''} />
        <Tile label="摘要進度" value={sum ? sum.pct : null} suffix="%" sub={sum ? `已生成 ${nf(sum.done)} / ${nf(sum.total)}` : ''} />
      </SimpleGrid>

      {tag ? (
        <Card withBorder padding="md" radius="md">
          <Group justify="space-between">
            <Text fw={600}>標註 TAGGING</Text>
            <Text size="sm" c="dimmed">未標 {nf(tag.fail)}</Text>
          </Group>
          <Progress value={tag.pct} color="gold" mt="xs" />
          <Text size="xs" c="dimmed" mt={4}>{rateText(tag.fail, rate.tpm, '標註')}</Text>
        </Card>
      ) : null}

      <Card withBorder padding="md" radius="md">
        <Group justify="space-between">
          <Text fw={600}>導入 INGEST</Text>
          <Text size="sm" c="dimmed">本輪導入 {nf(ing?.ingested ?? 0)} · 失敗 {nf(ing?.fail ?? 0)}</Text>
        </Group>
        <Text size="xs" c="dimmed" mt={4}>{ingestRateText(rate.rpm, rate.cps)}</Text>
      </Card>

      {sum ? (
        <Card withBorder padding="md" radius="md">
          <Group justify="space-between">
            <Text fw={600}>摘要 SUMMARY</Text>
            <Text size="sm" c="dimmed">未生成 {nf(sum.remaining)}</Text>
          </Group>
          <Progress value={sum.pct} color="gold" mt="xs" />
          <Text size="xs" c="dimmed" mt={4}>{rateText(sum.remaining, rate.spm, '摘要')}</Text>
        </Card>
      ) : null}

      <Card withBorder padding="md" radius="md">
        <Text fw={600} mb="xs">管線 PIPELINES</Text>
        <Group>
          {(['web', 'ingest', 'tag', 'summaries'] as const).map((k) => (
            <Badge key={k} color={data?.pipelines?.[k] ? 'green' : 'gray'} variant="light">
              {k} {data?.pipelines?.[k] ? '執行中' : '已停止'}
            </Badge>
          ))}
        </Group>
      </Card>

      <Card withBorder padding="md" radius="md">
        <Text fw={600} mb="xs">市場分佈 MARKETS</Text>
        <Stack gap={4}>
          {(data?.db.markets ?? []).map((m) => (
            <Group key={m.market} justify="space-between">
              <Text size="sm">{m.market}</Text>
              <Text size="sm" c="dimmed">{nf(m.count)}</Text>
            </Group>
          ))}
        </Stack>
      </Card>

      <Text size="xs" c="dimmed">更新於 {data?.ts ?? '—'} · 每 2 秒刷新 · 資料源 /api/progress</Text>
    </Stack>
  )
}
```

- [ ] **Step 6: 寫 MonitorPage RTL 失敗測試**

`frontend/src/features/monitor/MonitorPage.test.tsx`：
```tsx
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MantineProvider } from '@mantine/core'
import { render, screen } from '@testing-library/react'
import { afterEach, vi } from 'vitest'
import * as api from '../../lib/api'
import { theme } from '../../theme'
import MonitorPage from './MonitorPage'

afterEach(() => vi.restoreAllMocks())

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <MantineProvider theme={theme}>
      <QueryClientProvider client={qc}>
        <MonitorPage />
      </QueryClientProvider>
    </MantineProvider>,
  )
}

test('渲染進度數據與 LIVE 標記', async () => {
  vi.spyOn(api, 'getProgress').mockResolvedValue({
    ts: '14:00:00',
    db: { reports: 11401, chunks: 360000, markets: [{ market: 'TW', count: 9000 }] },
    summary: { done: 100, total: 200, remaining: 100, pct: 50 },
    tagging: { pct: 99, done: 11000, total: 11100, fail: 100 },
    ingest: { ingested: 3, fail: 0 },
    pipelines: { web: true, ingest: false, tag: false, summaries: true },
    orchestrator: undefined,
  })
  renderPage()
  expect(await screen.findByText('研報導入監控')).toBeInTheDocument()
  expect(await screen.findByText('LIVE')).toBeInTheDocument()
  expect(await screen.findByText(/標註 TAGGING/)).toBeInTheDocument()
  expect(await screen.findByText('TW')).toBeInTheDocument()
})
```

- [ ] **Step 7: 跑測試確認通過**

Run: `cd frontend && npx vitest run src/features/monitor/MonitorPage.test.tsx`
Expected: PASS。

- [ ] **Step 8: Commit**

```bash
git add frontend/src/features/monitor/useTween.ts frontend/src/features/monitor/useTween.test.tsx \
        frontend/src/features/monitor/MonitorPage.tsx frontend/src/features/monitor/MonitorPage.test.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): monitor 頁面（Query 2 秒輪詢 + 速率 + tween）

對位舊 monitor 版面（tiles/三面板/pipeline/市場分佈），useQuery 明確關閉
retry/focus/reconnect 維持舊節奏；含 useTween 數字動畫與 RTL 測試。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 10: 把 monitor 掛上 SPA 路由

**Files:**
- Modify: `frontend/src/App.tsx`（路由表加入 `/monitor`）

**Interfaces:**
- Consumes: `MonitorPage`（Task 9）。
- Produces: `/app/monitor` 渲染 monitor 頁。

- [ ] **Step 1: 在 `App.tsx` 匯入並加路由**

於 `frontend/src/App.tsx` 頂部加：
```tsx
import MonitorPage from './features/monitor/MonitorPage'
```
把路由陣列改為：
```tsx
export const router = createBrowserRouter(
  [
    { path: '/', element: <Home /> },
    { path: '/monitor', element: <Layout><MonitorPage /></Layout> },
    { path: '*', element: <NotFound /> },
  ],
  { basename: '/app' },
)
```

- [ ] **Step 2: build + 型別 + lint**

Run: `cd frontend && npm run build && npm run lint`
Expected: 0 error，`frontend/dist` 產出。

- [ ] **Step 3: 全套前端測試**

Run: `cd frontend && npm run test`
Expected: 全綠（eta/schemas/api/rate/theme/useTween/MonitorPage）。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "$(cat <<'EOF'
feat(frontend): 將 monitor 掛上 /app/monitor 路由

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 11: 端到端手動驗證（Playwright 平價劇本）

**Files:**
- Create: `frontend/e2e/monitor.spec.mjs`（或沿用 repo 既有 Playwright 慣例位置）
- Modify: `web/static/login.html`（補 `next` 隱藏欄位，若尚未具備）

**Interfaces:**
- Consumes: 登入流程、`/app/monitor`、`/api/progress`。
- Produces: 自動化平價驗證（輪詢、tiles、0 console error）。

- [ ] **Step 1: 補 `login.html` 的 `next` 透傳（若缺）**

確認 `web/static/login.html` 的 `<form method="post" action="/login">` 內含：
```html
<input type="hidden" name="next" id="next-field" />
<script>
  (() => {
    const n = new URLSearchParams(location.search).get('next')
    if (n) document.getElementById('next-field').value = n
  })()
</script>
```
若已存在等價機制則略過。

- [ ] **Step 2: build 並啟動服務**

Run（兩個終端，或背景）：
```bash
make spa-build
make serve
```
Expected: uvicorn 在 8097；`frontend/dist` 已是最新。

- [ ] **Step 3: 撰寫並執行 Playwright 平價劇本**

`frontend/e2e/monitor.spec.mjs`（憑證讀 repo 根 `.env` 的 `REPORT_MARK_ACCESS_USERNAME`/`_PASSWORD`）：
```js
import { test, expect } from '@playwright/test'
import fs from 'node:fs'

function creds() {
  const env = fs.readFileSync(new URL('../../.env', import.meta.url), 'utf8')
  const get = (k) => (env.match(new RegExp(`^${k}=(.*)$`, 'm')) || [])[1]?.trim()
  return { u: get('REPORT_MARK_ACCESS_USERNAME'), p: get('REPORT_MARK_ACCESS_PASSWORD') }
}

test('/app/monitor 平價：登入 → 輪詢 → 0 console error', async ({ page }) => {
  const errors = []
  page.on('console', (m) => m.type() === 'error' && errors.push(m.text()))

  const { u, p } = creds()
  await page.goto('http://localhost:8097/app/monitor')
  // 被導向 /login?next=/app/monitor
  await expect(page).toHaveURL(/\/login\?next=/)
  await page.fill('input[name="username"]', u)
  await page.fill('input[name="password"]', p)
  await page.click('button[type="submit"]')
  // 登入後返回 /app/monitor
  await expect(page).toHaveURL(/\/app\/monitor$/)
  await expect(page.getByText('研報導入監控')).toBeVisible()

  // 觀察 2 秒輪詢：~4 秒內至少 2 次 /api/progress
  let calls = 0
  page.on('requestfinished', (r) => r.url().includes('/api/progress') && calls++)
  await page.waitForTimeout(4500)
  expect(calls).toBeGreaterThanOrEqual(2)
  expect(errors).toEqual([])
})
```

Run: `cd frontend && npx playwright test e2e/monitor.spec.mjs`
Expected: PASS（登入返回、tiles 顯示、≥2 次輪詢、0 console error）。

> 若環境未裝 Playwright：`cd frontend && npm i -D @playwright/test && npx playwright install chromium`。

- [ ] **Step 4: Commit**

```bash
git add frontend/e2e/monitor.spec.mjs web/static/login.html
git commit -m "$(cat <<'EOF'
test(frontend): monitor /app 平價 Playwright 劇本 + login next 透傳

登入 return-to-origin 回 /app/monitor、驗證 2 秒輪詢與 0 console error。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 12: cutover — 舊 `/monitor` redirect 到 `/app/monitor`

**Files:**
- Modify: `web/server.py`（`@app.get("/monitor")` 改為 redirect）
- Test: `tests/test_spa_serving.py`（新增一案）

**Interfaces:**
- Consumes: Task 3 的 `/app/monitor`。
- Produces: `GET /monitor` → 302 至 `/app/monitor`。舊 `monitor.html` 保留檔案（未刪），僅不再由 `/monitor` 服務。

- [ ] **Step 1: 在 `tests/test_spa_serving.py` 加 redirect 測試**

```python
def test_legacy_monitor_redirects_to_spa():
    client = TestClient(app)
    resp = client.get("/monitor", cookies=_auth_cookies(), follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/app/monitor"
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest -q tests/test_spa_serving.py::test_legacy_monitor_redirects_to_spa`
Expected: FAIL（目前回 200 監控 HTML）。

- [ ] **Step 3: 改 `web/server.py` 的 `/monitor`**

把 `web/server.py:458-460` 的：
```python
@app.get("/monitor")
async def monitor():
    return _static_page("monitor.html")
```
改為：
```python
@app.get("/monitor")
async def monitor():
    # cutover：監控頁已遷至 SPA；舊 monitor.html 保留檔案，僅不再由此服務。
    return RedirectResponse("/app/monitor", status_code=307)
```

- [ ] **Step 4: 跑測試確認通過 + 全套後端測試**

Run: `uv run pytest -q tests/test_spa_serving.py`
Expected: PASS。

Run: `uv run pytest -q`
Expected: 全套後端測試綠燈（確認 cutover 未波及其他）。

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_spa_serving.py
git commit -m "$(cat <<'EOF'
feat(web): /monitor 導向 SPA /app/monitor（cutover）

monitor 切片達平價後，舊路由 307 轉址到 /app/monitor；monitor.html 保留待退役。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage：**
- §2 目標 1（SPA 骨架掛 /app）→ Task 1、5。✅
- §2 目標 2（FastAPI 服務 + 認證 + glue）→ Task 3、4。✅
- §2 目標 3（共用地基層：api/schema/theme/純函式/shell）→ Task 5、6、7。✅
- §2 目標 4（monitor 端到端 + 平價）→ Task 8、9、10、11。✅
- §2 目標 5（Vitest 骨架 + Playwright 平價）→ Task 1（vitest 設定）、9、11。✅
- §4.2 共存 /app、§4.3 catch-all + 資產 + next glue → Task 3、4。✅
- §4.5 Mantine 主題映射品牌 → Task 5。✅
- §6.2 query 行為（retry:false 等）→ Task 9（明列）。✅
- §6.3 平價標準 1–6 → Task 9（渲染/節奏）、Task 11（輪詢/0 error/登入返回）、Task 12（cutover redirect）。✅
- §7 錯誤處理（401 glue／retry:false／Zod 守門／staging 原子換版／404）→ Task 4、6、9、2；SPA 404 → Task 5。✅
- §8 測試（vitest 設定/jest-dom/eta 移植/Playwright）→ Task 1、7、11。✅
- §9 部署（spa-build staging 換版／systemd 不變）→ Task 2。✅
- §3 版本鎖定與相依約束 → Task 1 package.json + Global Constraints。✅

**2. Placeholder scan：** 無 TBD/TODO/「之後實作」等模糊用語；每個 code step 皆含完整可貼上的程式碼。✅

**3. Type consistency：** `MonitorRate`/`RateSample`/`RateBase`/`NULL_RATE`/`nextRate` 於 Task 8 定義並於 Task 9 `useMonitorRate` 消費；`ProgressResponse`/`progressSchema`/`getProgress`/`getJSON`/`ApiError` 於 Task 6 定義並於 Task 9 消費；`rateText`/`ingestRateText` 於 Task 7 定義並於 Task 9 消費；`theme` 於 Task 5 定義並於 Task 9 測試消費；`_safe_next` 於 Task 4 定義並於 Task 4 測試消費。命名一致。✅
