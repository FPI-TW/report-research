# 對外存取：Cloudflare Tunnel + nginx

讓辦公室外的同事從外網連入廷豐研報檢索網頁。登入由 App 內建登入頁處理（共用帳密），邊緣 nginx 只做反向代理與限流。

## 架構

```
瀏覽器 ──HTTPS──▶ Cloudflare 邊緣（TLS、research.<你的網域>）
            │
            ▼  cloudflared 主動外連的加密隧道（出向連線，免動路由器/防火牆）
        nginx（安全標頭 + 限流）
            │  proxy_pass host.docker.internal:8097
            ▼
        uvicorn web.server:app（既有，:8097）
```

對外唯一路徑是 Cloudflare → 隧道 → nginx → uvicorn（App 登入把關）。origin 不對公網開任何埠；外網路徑與既有 LAN portproxy/防火牆互不影響，也不受 WSL 重開機換 IP 影響。

## 前置需求

- 已有網域掛在 Cloudflare（nameserver 指向 Cloudflare）。
- 本機可用 Docker Desktop（`docker` 與 `docker compose`）。
- 既有檢索服務可啟動（`make serve`，監聽 :8097）。

## 一次性設定

### 1) 在 Cloudflare 建立具名隧道並取得 token

1. 進 Cloudflare **Zero Trust → Networks → Tunnels → Create a tunnel**。
2. 選 **Cloudflared** 連接器，命名（例如 `tingfeng-research`）。
3. 建立後頁面會顯示安裝指令，其中 `--token` 後那串就是 **TUNNEL_TOKEN**，複製起來。
4. 在該隧道的 **Public Hostname** 新增一筆：
   - Subdomain：`research`（或你想要的）
   - Domain：選你的網域
   - Service：`HTTP` → `nginx:80`

   （Service 用 `nginx:80` 是因為 cloudflared 與 nginx 在同一個 compose 網路，可用服務名互通。）

### 2) 填入 token

```bash
cp deploy/.env.example deploy/.env
# 編輯 deploy/.env，把 TUNNEL_TOKEN= 後面貼上剛才複製的 token
```

### 3) 設定 App 登入帳密

登入由 App 處理,憑證來自環境變數(複製 repo 根 `.env.example` 為 `.env`):

```bash
cp .env.example .env
# 編輯 .env：
#   REPORT_MARK_ACCESS_USERNAME / REPORT_MARK_ACCESS_PASSWORD  共用帳密
#   REPORT_MARK_SESSION_SECRET  固定長隨機字串(未設則重啟登出所有人)
#   產生 secret：python -c "import secrets; print(secrets.token_hex(32))"
```

> `make serve` 會自動載入 `.env`。未設帳密時 App 會 fail-closed 拒絕啟動。

### 4) 啟動邊緣

```bash
make up-edge              # 啟動 nginx + cloudflared
make edge-logs            # 觀察隧道是否連上（看到 "Registered tunnel connection" 即成功）
```

> 提醒：請確保既有檢索服務 `make serve` 也在執行，否則 nginx 會回 502。

### 5) 外網實測

從**非辦公室網路**（例如手機關 Wi-Fi 用行動網路）開 `https://research.<你的網域>`：

- 應看到廷豐研報的登入頁；
- 輸入正確帳密後導向首頁,可正常檢索研報。

## 日常維運

| 動作 | 指令 |
|------|------|
| 啟動邊緣 | `make up-edge` |
| 關閉邊緣 | `make down-edge` |
| 看日誌 | `make edge-logs` |
| 換登入帳密 | 編輯 `.env` 的帳密 → 重啟 `make serve` |
| 重啟 nginx | `make edge-reload` |

重開機後：邊緣的兩個容器（nginx + cloudflared）為 `restart: unless-stopped`，Docker 會自動拉起，**無需重設 portproxy**。但檢索服務 `make serve` 是 host 上的原生 uvicorn 程序、**不是容器，不會自動復活**——重開機後必須手動重跑 `make serve`，否則外網會一直回 502。若想免手動，可考慮把 `make serve` 掛到 process manager（如 WSL 的 systemd、或開機腳本）常駐。

## 安全備註

- 登入為**共用帳密**,App 以 hmac 簽章 session cookie 維持登入(7 天滑動到期),並對登入失敗做每 IP 限流。請定期更換 `.env` 的密碼。
- 建議 Cloudflare 端開「Always Use HTTPS」、TLS 模式至少 Full。
- `deploy/.env`（隧道 token）與 repo 根 `.env`（登入帳密 / session 金鑰）皆已 gitignore，切勿提交。

## 疑難排解

| 症狀 | 可能原因 / 處置 |
|------|----------------|
| 外網開站一直 502 | host uvicorn 沒在跑 → `make serve`；或 `host.docker.internal` 不通（確認 compose 的 `extra_hosts: host-gateway` 存在） |
| 一直回登入頁、輸入正確仍進不去 | session cookie 沒被接受(瀏覽器擋第三方/封鎖 cookie),或 `REPORT_MARK_SESSION_SECRET` 每次重啟都變(請在 `.env` 固定一組) |
| App 啟動即報錯退出 | 未設 `REPORT_MARK_ACCESS_USERNAME` / `_ACCESS_PASSWORD`(fail-closed)→ 補進 `.env` 再 `make serve` |
| 改了程式卻沒生效（看到新 UI 卻無登入頁 / 登出按 404）| `make serve` 無 `--reload`：靜態 HTML 即時生效，但路由/中介層在**啟動時**載入；舊 uvicorn 程序還在跑 → `pkill -f "uvicorn web.server"` 後重啟 `make serve` |
| `make edge-logs` 看不到 tunnel 連線 | token 錯/沒填 → 檢查 `deploy/.env`；Cloudflare 儀表板確認隧道狀態為 HEALTHY |
| 外網打不開但 LAN 正常 | Cloudflare Public Hostname 的 Service 是否設成 `http://nginx:80`；DNS 記錄是否由隧道自動建立 |
