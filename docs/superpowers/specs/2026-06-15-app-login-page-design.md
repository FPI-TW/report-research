# 設計:廷豐研報 App 內建登入頁

- 日期:2026-06-15
- 狀態:已核可(待寫實作計畫)
- 範圍:`web/` 應用層認證 + `deploy/` 邊緣設定調整

## 背景與現況

目前的存取模型有兩條路徑、兩種(不一致的)防護:

- **`web/server.py`(uvicorn :8097)本身完全無認證**,直接以 FileResponse 提供 HTML 與 `/api/*`。
- **LAN**(`http://192.168.1.128:8097`,經 portproxy)— 無任何密碼門檻。
- **外網**(Cloudflare Tunnel → nginx → uvicorn)— 以 nginx **Basic Auth**(單一共用密碼的瀏覽器原生彈窗)把關。

問題:外網是醜的瀏覽器 Basic Auth 彈窗;LAN 則毫無防護。兩條路徑的認證不一致,且彈窗無品牌感。

## 目標

1. 以 App 內建、品牌化的登入頁取代瀏覽器 Basic Auth 彈窗。
2. **LAN 與外網走同一個登入頁**,認證邏輯統一在 App 層。
3. 單一**共用帳號 + 共用密碼**(非多人各自帳密)。
4. 登入後維持 **7 天滑動到期** 的 session。
5. 移除 nginx Basic Auth,讓 App 的 session 成為唯一門檻。

## 非目標(YAGNI)

- 不做多人帳號 / 角色權限 / 使用者管理。
- 不做存取稽核記錄、密碼自助重設、OAuth/SSO。
- 不改既有檢索 / 標籤 / 監控等業務邏輯。

## 已定案的決策(來自 brainstorming)

| 議題 | 結論 |
|------|------|
| 登入目的 | App 內建登入頁,**取代** nginx Basic Auth 彈窗 |
| nginx Basic Auth | **移除**;App 登入(session cookie)為唯一門檻 |
| Session 長度 | 中效,約 **7 天滑動到期** |
| 憑證 | 單一**共用帳號 + 共用密碼**(登入頁兩欄) |
| Session 機制 | stdlib `hmac` 簽章 cookie(不新增相依套件) |
| 路由保護 | deny-by-default 中介層(白名單只有 `/login`) |

## 架構與資料流

```
瀏覽器 ──▶ AuthMiddleware（deny-by-default）
              │  cookie 有效？（hmac 簽章 + 未過期）
              ├─ 是 ─▶ 正常路由 → 回應時刷新 cookie（7 天滑動到期）
              └─ 否 ─▶  /api/*  回 401 JSON
                        其他    302 轉址到 /login
GET  /login  ─▶ 已登入則 302 轉 /；否則回自包式登入頁
POST /login  ─▶ 驗帳號+密碼(constant-time)
                  成功：reset 失敗計數 → set cookie → 303 轉 /
                  失敗：record 失敗 → 302 轉 /login?error=1
POST /logout ─▶ 清除 cookie → 302 轉 /login
```

外網與 LAN 共用同一個 `/login`。nginx 僅保留反向代理、安全標頭、限流(移除 `auth_basic`)。

## 元件與職責

| 檔案 | 職責 |
|------|------|
| `web/auth.py`(新) | 認證核心,純函式、易測:`issue_token` / `verify_token`(hmac 簽章 + 過期檢查)、`set_session_cookie` / `clear_session_cookie`、`check_credentials`(帳號與密碼皆 `hmac.compare_digest`)、每 IP 失敗次數限流(in-memory 鎖定窗) |
| `web/server.py` | 掛 `AuthMiddleware`;新增 `GET /login`、`POST /login`、`POST /logout` 路由 |
| `web/static/login.html`(新) | 自包式品牌登入頁:帳號欄 + 密碼欄 + 送出鈕 + 錯誤訊息區;**內嵌樣式**(不依賴 `/static`),沿用品牌色,無 emoji |
| `web/static/index.html`(等頁) | header 加一個「登出」連結(POST /logout) |
| `deploy/nginx.conf` | 移除 `auth_basic` / `auth_basic_user_file` 兩行 |
| `deploy/docker-compose.yml` | 移除 `.htpasswd` 的 volume 掛載 |
| `Makefile` | 移除 `edge-passwd` 等 `.htpasswd` 相關目標與 `up-edge` 的 `.htpasswd` 前置檢查 |
| `docs/EXTERNAL_ACCESS.md` | 更新:改述為 App 登入,移除 `.htpasswd` 設定段落 |
| `tests/test_auth.py`(新) | 認證行為測試(見下) |

> 模組邊界:`web/auth.py` 不依賴 FastAPI 路由,僅輸入字串/時間、輸出 token/bool,可獨立單元測試。`web/server.py` 只負責把 HTTP 形狀接到 `auth.py`。

## Session 機制(stdlib hmac 簽章 cookie)

- Token 格式:`<exp_ts>.<hmac_sha256(secret, exp_ts)>`(base64url 編碼),`exp_ts` 為到期 Unix 秒數。
- 驗證:拆解 → `hmac.compare_digest` 比對簽章 → 檢查 `exp_ts > now`。
- **滑動到期**:每個通過認證的回應,middleware 以 `now + 7d` 重新簽發 cookie 並 `Set-Cookie`,故 7 天內持續使用會自動延展;閒置滿 7 天才失效。
- Cookie 名稱:`tf_session`;屬性 `HttpOnly`、`SameSite=Lax`、`Path=/`、`Max-Age=7d`、`Secure=False`。
  - `Secure=False` 的理由:LAN 走 HTTP,設 `Secure` 會導致 LAN 收不到 cookie;外網經 Cloudflare 仍是 HTTPS 加密傳輸,LAN 屬內網可接受。

## 憑證與設定(沿用 `os.environ` 慣例)

| 環境變數 | 說明 | 未設定時 |
|----------|------|----------|
| `REPORT_MARK_ACCESS_USERNAME` | 共用帳號 | **啟動即報錯**(fail-closed) |
| `REPORT_MARK_ACCESS_PASSWORD` | 共用密碼 | **啟動即報錯**(fail-closed) |
| `REPORT_MARK_SESSION_SECRET` | cookie 簽章金鑰 | 隨機產生 + 記 warning(僅供開發;正式須固定,否則重啟登出所有人) |

- 提供方式:gitignored 的 repo 根 `.env`(由 `make serve` 載入)或手動 `export`。實作計畫敲定確切載入方式並提供 `.env.example` 範例。
- 絕不在程式或範例中留預設帳密。

## 安全要點(因為這是唯一門檻)

- 帳號與密碼皆以 `hmac.compare_digest` 比對(constant-time,避免時序側信道)。
- 帳密任一錯誤 → 回相同訊息「帳號或密碼錯誤」(不洩漏哪一項錯)。
- **登入失敗每 IP 限流**:超過門檻(如 5 次/窗)鎖定一段時間(如 60 秒),成功即重置。LAN 會繞過 nginx 限流,故 App 層自備防爆破。
- 路由白名單僅 `/login`(GET+POST);登入頁自包樣式,**不開放任何 `/static`**,避免 `/static/index.html` 之類繞過 route 門檻直接讀到頁面。
- deny-by-default:未列入白名單的新路由自動受保護。

## 路由保護白名單

| 路徑 | 是否需登入 |
|------|-----------|
| `GET /login`、`POST /login` | 否(白名單) |
| 其他全部(`/`、`/monitor`、`/help`、`/api/*`、`/static/*`、`/api/report/*/file` 等) | 是 |
| `POST /logout` | 是(本就須先登入) |

## 邊緣(nginx)變更

- `deploy/nginx.conf`:移除 `auth_basic "tingfeng-research";` 與 `auth_basic_user_file ...;`。保留 `real_ip`、安全標頭、`limit_req`。
- `Cookie` 標頭:nginx 預設即會轉發,無需額外設定;原 `proxy_set_header Authorization "";` 可保留(無害,本案不使用 Authorization)。
- `deploy/docker-compose.yml`:移除掛載 `.htpasswd` 的 volume。
- `Makefile`:移除 `edge-passwd` 目標與 `up-edge` 對 `.htpasswd` 的前置檢查。
- `docs/EXTERNAL_ACCESS.md`:更新架構圖與步驟,移除「產生 .htpasswd」段落,改述登入由 App 處理。

## 測試(`tests/test_auth.py`,FastAPI `TestClient`)

1. 未帶 cookie 取 `/` → 302 轉 `/login`。
2. 未帶 cookie 取 `/api/stats` → 401 JSON。
3. `GET /login` → 200,內含帳號與密碼欄位。
4. `POST /login` 帳密正確 → 取得 `tf_session` cookie;帶該 cookie 取 `/` → 200。
5. `POST /login` 帳號對密碼錯(或反之) → 未取得有效 cookie,訊息為通用錯誤。
6. 連續失敗達門檻 → 後續嘗試被限流鎖定。
7. cookie 被竄改(改 exp 或簽章) → 視為未登入。
8. `POST /logout` → 清除 cookie;再取 `/` → 302 轉 `/login`。
9. 過期 token(`exp_ts < now`) → 視為未登入。

測試需設 `REPORT_MARK_ACCESS_USERNAME` / `REPORT_MARK_ACCESS_PASSWORD` / `REPORT_MARK_SESSION_SECRET`。

## 影響與相容性

- 既有 API 與前端不需改動回應格式;僅新增認證閘門與登入頁。
- 部署者需在啟動前設定三個環境變數,否則 App 拒絕啟動(刻意 fail-closed)。
- 外網同事改為看到 App 登入頁而非瀏覽器彈窗;LAN 同事首次需登入。
