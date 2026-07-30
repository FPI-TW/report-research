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
> 安全限制：**非本機 localhost 的明文 HTTP 不會建立登入 session**；同事請走 `https://research.<你的網域>` 這條受保護入口。

### 3b) 信任反向代理來源 IP（外網必做）

App 以「連線來源 IP 是否為信任代理」決定要不要採信 nginx 帶來的 `X-Forwarded-Proto: https`。經 Cloudflare 進來的請求路徑是 `nginx → host.docker.internal:8097 → uvicorn`，**uvicorn 看到的來源 IP 是 Docker Desktop → WSL host 的閘道**（通常 `172.x.x.1`），**不在預設信任的 loopback 內**。若不設定，外網（即使走 HTTPS）登入會被擋並顯示「**此登入只接受 HTTPS 或本機 localhost，請改走受保護入口**」。

```bash
# 1) 先啟動 serve，從 uvicorn access log 找出 nginx 進來的來源 IP
make serve                       # log 在 data/serve_edge.log，找形如 "172.x.x.1:port - GET /login" 的來源
# 2) 在 repo 根 .env 設定（把 172.20.48.1 換成你實際看到的閘道 IP）
#    REPORT_MARK_TRUSTED_PROXY_CIDRS=127.0.0.1/32,::1/128,172.20.48.1/32
# 3) 重啟 serve 生效（auth 在啟動時讀此設定，不重啟不生效）
```

> ⚠️ 此閘道 IP 可能隨 WSL／Windows 重開機變動；重開機後若外網又出現該訊息，重查 IP 更新此設定再重啟 serve。
> 驗證：從 nginx 容器走真實代理路徑打一次假帳密 —
> `docker compose -f deploy/docker-compose.yml exec nginx wget -S --post-data='username=x&password=y' -O /dev/null http://127.0.0.1/login`
> 導向 `/login?error=1` ＝信任已通（僅帳密錯）；`/login?error=insecure` ＝仍未信任，回頭檢查上面的 CIDR。

### 3c) 邊緣共享祕密（建議做，做了就不再受 IP 漂移影響）

上一節那個閘道 IP **會隨 WSL／Windows 重開機變動**，而它一變，外網就是全體登入被擋。共享祕密把「是不是我家的反向代理」從「來源 IP 對不對」換成「有沒有帶對 header」，重開機不影響。

- 邊緣：`deploy/nginx.conf` 內已有 `proxy_set_header X-Edge-Secret "${EDGE_SECRET}";`，值來自 `deploy/.env` 的 `EDGE_SECRET`（該檔是 **envsubst 模板**，由 compose 掛到 `/etc/nginx/templates/`，容器啟動時渲染）。
- App：`web/auth.py` 讀 repo 根 `.env` 的 `REPORT_MARK_EDGE_SECRET`，以常數時間比對。

```bash
# 1) 產生一組祕密
python -c "import secrets; print(secrets.token_urlsafe(32))"
# 2) 兩邊各填一次（值必須逐字相同）
#    deploy/.env      EDGE_SECRET=<剛剛那串>
#    repo 根 .env      REPORT_MARK_EDGE_SECRET=<同一串>
# 3) 兩邊各自生效
make edge-reload                                  # nginx（模板只在容器啟動時渲染）
sudo systemctl restart report-mark-web.service    # App（auth 在啟動時讀設定）
```

> **兩者是 OR，不是取代**：祕密 header 相符 **或** 來源 IP 落在 `REPORT_MARK_TRUSTED_PROXY_CIDRS` 內，都算可信代理。這是刻意的——改 nginx 與改 App 之間必然有時間差，只認其一的話那段窗口會把所有外網使用者擋在門外。等祕密穩定運作之後，CIDR 那行留著也無妨（它本來就只信任私網位址）。
>
> 祕密外流的後果是「可被當成可信代理」：能自稱 HTTPS、能指定 `X-Real-IP`（污染限流 key）。它**不等於**登入憑證，但仍請當機密看待（`deploy/.env` 與 repo 根 `.env` 皆已 gitignore），要輪替就兩邊同時換。

### 3d) 若日後啟用 Cloudflare Access：`/healthz` 必須設例外

Cloudflare Access（Zero Trust 的身分驗證層）不在本 repo 內，是儀表板設定。若要開，**務必為 `/healthz` 建一條 Bypass policy**（Application 的 path 設 `/healthz`，Action 選 Bypass / Service Auth）。

理由：`/healthz` 存在的**全部**目的是讓外部監控分辨「站台正常」與「DB 掛了」（健康 200、DB 不可用 503）。Access 會把未帶身分的請求攔成 302 導向登入頁——那時外部探測拿到的是 302，與「DB 掛了」「路由不存在」「App 沒起來」完全無從分辨，等於把這支端點的唯一價值抵銷掉。詳見 `docs/production_resilience.md`。

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

- 登入為**共用帳密**,App 以 hmac 簽章 session cookie 維持登入(7 天滑動到期、**30 天絕對上限**),並對登入失敗做每 IP 限流。請定期更換 `.env` 的密碼。
- 建議 Cloudflare 端開「Always Use HTTPS」、TLS 模式至少 Full。
- `deploy/.env`（隧道 token）與 repo 根 `.env`（登入帳密 / session 金鑰）皆已 gitignore，切勿提交。

### 撤銷 session

session token 的簽章訊息含「帳密指紋」與「登出 epoch」，所以改任一項就等於全員失效（都需重啟 App 生效）。

| 想做的事 | 做法 | 影響 |
|---|---|---|
| 換密碼並踢掉所有人 | 改 `REPORT_MARK_ACCESS_PASSWORD` | 全員登出（**不再像以前那樣「換了密碼舊 cookie 照樣能用」**）|
| 不換密碼、只踢掉所有人 | `REPORT_MARK_SESSION_EPOCH` 填一個新值（慣例填日期） | 全員登出 |
| 什麼都不做 | — | 任一 session 最長活 30 天（`web/auth.py` 的 `MAX_ABSOLUTE_TTL`）；滑動續期只推遲到期時間，不會延長這條上限 |

### 登入稽核

登入的四種結果都會寫進 journald：成功 `INFO`，失敗／限流鎖定／非 HTTPS 遭拒 `WARNING`。**一律不記密碼（連長度都不記）**，帳號只記「相符與否」。

```bash
sudo journalctl -u report-mark-web.service | grep -E "登入成功|登入失敗|登入遭"
```

> ⚠️ 把 `LOG_LEVEL` 調成 `WARNING` 只會留下攻擊面那一半（失敗／鎖定／遭拒），失去「誰在何時登入」。要完整稽核就別調高。

## 疑難排解

| 症狀 | 可能原因 / 處置 |
|------|----------------|
| 外網開站一直 502 | host uvicorn 沒在跑 → `make serve`；或 `host.docker.internal` 不通（確認 compose 的 `extra_hosts: host-gateway` 存在） |
| 內網直接打 `http://<LAN-IP>:8097` 一直回登入頁 | 這是刻意的：非 localhost 的明文 HTTP 不接受登入 session → 請改走 Cloudflare HTTPS 網址；只有本機開發可用 `http://localhost:8097` |
| 外網（HTTPS）登入顯示「只接受 HTTPS 或本機 localhost」 | `REPORT_MARK_TRUSTED_PROXY_CIDRS` 未含 nginx 進來的來源 IP（Docker→WSL 閘道，~`172.x.x.1`）→ App 不採信 `X-Forwarded-Proto: https`。**先看日誌**：`journalctl -u report-mark-web.service \| grep 登入遭拒` 會直接印出 `peer=<實際對端>`，把它加進 CIDR（見 3b）或改用共享祕密（見 3c，一勞永逸）後重啟 |
| 設了 `EDGE_SECRET` 仍被當成不可信 | 兩邊值不一致，或 nginx 沒重新渲染模板（`nginx -s reload` 不會重新代換，要 `make edge-reload` 重啟容器）。驗證：`docker compose -f deploy/docker-compose.yml exec nginx cat /etc/nginx/conf.d/default.conf \| grep X-Edge-Secret` 應看到**實際祕密值**而非字面的 `${EDGE_SECRET}` |
| 所有人突然被登出 | 預期行為：改了 `REPORT_MARK_ACCESS_PASSWORD` / `REPORT_MARK_SESSION_SECRET` / `REPORT_MARK_SESSION_EPOCH`，或該 session 已達 30 天絕對上限。重新登入即可 |
| 一直回登入頁、輸入正確仍進不去 | session cookie 沒被接受(瀏覽器擋第三方/封鎖 cookie),或 `REPORT_MARK_SESSION_SECRET` 每次重啟都變(請在 `.env` 固定一組) |
| App 啟動即報錯退出 | 未設 `REPORT_MARK_ACCESS_USERNAME` / `_ACCESS_PASSWORD`(fail-closed)→ 補進 `.env` 再 `make serve` |
| 改了程式卻沒生效（看到新 UI 卻無登入頁 / 登出按 404）| `make serve` 無 `--reload`：靜態 HTML 即時生效，但路由/中介層在**啟動時**載入；舊 uvicorn 程序還在跑 → `pkill -f "uvicorn web.server"` 後重啟 `make serve` |
| `make edge-logs` 看不到 tunnel 連線 | token 錯/沒填 → 檢查 `deploy/.env`；Cloudflare 儀表板確認隧道狀態為 HEALTHY |
| 外網打不開但 LAN 正常 | Cloudflare Public Hostname 的 Service 是否設成 `http://nginx:80`；DNS 記錄是否由隧道自動建立 |
