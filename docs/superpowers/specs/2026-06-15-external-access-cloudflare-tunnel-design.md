# 對外存取：Cloudflare Tunnel + nginx 反向代理 設計

- 日期：2026-06-15
- 狀態：已通過設計、待寫實作計畫
- 目標：讓辦公室外的同事能從外網連進 廷豐研報 檢索網頁，並加上存取門檻。

## 背景與現況

- web 服務是 `uvicorn web.server:app` 直接跑在 WSL（埠 8097），**未容器化**、**無任何身份驗證**——任何能連到頁面的人都能讀全部研報。
- DB 是獨立 docker 容器（pgvector），非 compose。
- 機器在路由器 NAT 後面（LAN 192.168.1.128），**本機沒有公網 IP**。
- 既有 LAN 開放：Windows portproxy + 防火牆 inbound（埠 8097）。痛點：WSL 重開機換 IP → portproxy 失效要重設（見 `lan-deployment-8097` 記憶）。
- 研報為專屬內容，對外開放必須有門檻。

## 需求決策（已與使用者確認）

1. **開放對象**：只給同事/自己遠端用，**要存取門檻**（非對全網公開）。
2. **入口方式**：**Cloudflare Tunnel**（cloudflared 主動外連，免動路由器、免公網 IP）。
3. **網域**：已有網域掛在 Cloudflare，可用具名隧道。
4. **驗證方式**：**nginx Basic Auth 共用密碼**（非 Cloudflare Access）。
5. **執行方式**：nginx + cloudflared 以 **Docker 容器**執行（`restart: unless-stopped`）。

## 架構

```
瀏覽器 ──HTTPS──▶ Cloudflare 邊緣（TLS 終結、具名主機 research.<網域>）
            │
            ▼  cloudflared 主動外連的加密隧道（出向連線，不需 inbound 開埠）
        nginx（Basic Auth + 安全標頭 + 限流）
            │  proxy_pass http://host.docker.internal:8097
            ▼
        uvicorn web.server:app（127.0.0.1:8097，程式碼不動）
            ▼
        pgvector 容器（不動）
```

- 對外**唯一路徑**：Cloudflare → 隧道 → nginx(Basic Auth) → uvicorn。
- origin **不對公網開任何埠**（隧道為出向連線）。未過 Basic Auth 到不了應用。
- TLS 由 Cloudflare 邊緣處理；cloudflared 加密邊緣↔origin。
- cloudflared 為出向連線，**外網路徑與 LAN 的 portproxy/防火牆無關，亦不受 WSL 重開機換 IP 影響**。

## 元件職責（隔離邊界）

| 元件 | 做什麼 | 依賴 | 如何使用 |
|------|--------|------|---------|
| `cloudflared` 容器 | 建立到 Cloudflare 的具名隧道，把 `research.<網域>` 的請求轉給 compose 網路內的 `nginx:80` | `TUNNEL_TOKEN`（Cloudflare 隧道 token） | `docker compose up -d`；ingress 規則在 Cloudflare 儀表板設定 |
| `nginx` 容器 | 反向代理；Basic Auth 門檻；安全標頭；限流；轉發真實 IP | `.htpasswd`、可達 `host.docker.internal:8097` | 由 cloudflared 內部轉入，proxy 到 host uvicorn |
| uvicorn（既有，host 原生） | 應用本體，不變 | pgvector 容器 | 維持 `0.0.0.0:8097` |

## 新增檔案（全部新加，不碰 app/web 程式碼）

| 檔案 | 內容 | 進版控 |
|------|------|--------|
| `deploy/docker-compose.yml` | `nginx` + `cloudflared` 兩服務，皆 `restart: unless-stopped`；cloudflared 讀 `TUNNEL_TOKEN`；nginx 掛載 `nginx.conf` 與 `secrets/.htpasswd` | ✅ |
| `deploy/nginx.conf` | server 區塊：`auth_basic` + `auth_basic_user_file`；`proxy_pass http://host.docker.internal:8097`；轉發 `Host`/`X-Forwarded-For`/`X-Forwarded-Proto`；以 `CF-Connecting-IP` 還原真實 IP；安全標頭（X-Content-Type-Options、X-Frame-Options、Referrer-Policy）；`limit_req` 限流；`proxy_read_timeout` 放寬給 BGE-M3 查詢嵌入；透傳 `Cache-Control` | ✅ |
| `deploy/.env.example` | `TUNNEL_TOKEN=` 範本 + 註解 | ✅ |
| `deploy/.env` | 實際隧道 token（秘密） | ❌ gitignore |
| `deploy/secrets/.htpasswd` | Basic Auth 帳密雜湊（秘密） | ❌ gitignore |
| `docs/EXTERNAL_ACCESS.md` | 繁中逐步指南 | ✅ |

- `.gitignore` 補上 `deploy/.env` 與 `deploy/secrets/`。
- Makefile 加 target：`up-edge`、`down-edge`、`edge-logs`、`edge-passwd`（產生/更換 Basic Auth 密碼）。`edge-passwd` 以一次性 `httpd:alpine` 容器跑 `htpasswd -Bc`（bcrypt）產出 `deploy/secrets/.htpasswd`，不依賴 host 是否裝有 htpasswd。

## nginx.conf 要點

- `upstream` 指向 `host.docker.internal:8097`（Docker Desktop 下可解析到 host）。
- 全站 `auth_basic "..."` + `auth_basic_user_file /etc/nginx/secrets/.htpasswd`。
- `limit_req_zone` 以 `$binary_remote_addr`（取自 `CF-Connecting-IP`）建區，套在 `location /`，緩衝 burst，鈍化 Basic Auth 暴力。
- `proxy_set_header` 帶 `Host`、`X-Real-IP`、`X-Forwarded-For`、`X-Forwarded-Proto https`。
- `real_ip_header CF-Connecting-IP` + `set_real_ip_from`（Cloudflare/cloudflared 來源）讓日誌記真實 IP。
- `client_max_body_size` 設小（唯讀檢索，無上傳）。
- `proxy_read_timeout` 放寬（如 60s）以容查詢時的嵌入運算。
- 安全標頭：`X-Content-Type-Options nosniff`、`X-Frame-Options SAMEORIGIN`、`Referrer-Policy strict-origin-when-cross-origin`。
- 透傳應用設定的 `Cache-Control: no-cache`（不額外快取 HTML/靜態）。

## docker-compose.yml 要點

- service `nginx`：官方 `nginx:alpine`；掛載 `./nginx.conf:/etc/nginx/conf.d/default.conf:ro`、`./secrets:/etc/nginx/secrets:ro`；`extra_hosts: ["host.docker.internal:host-gateway"]`（保險）；`restart: unless-stopped`；**不對 host 發佈埠**（僅 compose 網路內被 cloudflared 取用）。
- service `cloudflared`：官方 `cloudflare/cloudflared:latest`；`command: tunnel --no-autoupdate run`；`environment: TUNNEL_TOKEN=${TUNNEL_TOKEN}`；`restart: unless-stopped`；`depends_on: nginx`。
- ingress（`research.<網域>` → `http://nginx:80`）在 Cloudflare 儀表板的隧道設定中配置（token 式遠端管理隧道）。

## 安全姿態

- origin 無對公網開埠；唯一入口經 Cloudflare。
- Basic Auth 為**共用密碼**：以 `make edge-passwd` 產生/輪替；密碼經 Cloudflare TLS 加密傳輸，不走明文。
- nginx 限流緩衝暴力嘗試。
- token 與 htpasswd 皆為秘密、不進版控。
- 建議 Cloudflare 端：開「Always Use HTTPS」、TLS 模式至少 Full。

## 明確不動

- 應用程式碼（`web/server.py` 等）一行不改。
- 既有 LAN 的 portproxy / 防火牆 / 8097 維持原樣。
- pgvector DB 容器不動。

## docs/EXTERNAL_ACCESS.md 涵蓋步驟

1. 在 Cloudflare Zero Trust → Networks → Tunnels 建立具名隧道，取得 `TUNNEL_TOKEN`。
2. 在隧道的 Public Hostname 設 `research.<網域>` → Service `http://nginx:80`。
3. 把 token 填入 `deploy/.env`（複製自 `.env.example`）。
4. `make edge-passwd` 設定 Basic Auth 帳密。
5. `make up-edge` 啟動 nginx + cloudflared。
6. 從外網裝置開 `https://research.<網域>`，輸入 Basic Auth 帳密驗證。
7. 換密碼 / 看日誌 / 關閉的指令。
8. 疑難排解（隧道狀態、`host.docker.internal` 不通、502 等）。

## 驗收標準

- 從外網（非辦公室網路、非同 LAN）裝置開 `https://research.<網域>`：先被 Basic Auth 擋，輸入正確帳密後可正常檢索研報。
- 不帶/帶錯帳密 → 401。
- `docker compose -f deploy/docker-compose.yml restart` 或機器重開機後，兩容器自動回復（`restart: unless-stopped`），外網路徑自動恢復、無需手動重設 portproxy。
- LAN 8097 既有存取不受影響。
- token / htpasswd 不在 git 追蹤內。
