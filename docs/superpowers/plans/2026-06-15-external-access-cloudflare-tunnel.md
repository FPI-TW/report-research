# 對外存取（Cloudflare Tunnel + nginx）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 讓辦公室外的同事經 Cloudflare Tunnel 連入廷豐研報檢索網頁，並以 nginx Basic Auth 共用密碼設門檻。

**Architecture:** 新增 `deploy/` 目錄，內含 nginx + cloudflared 的 docker-compose。cloudflared 主動外連 Cloudflare 建立具名隧道，把 `research.<網域>` 轉給 compose 網路內的 nginx；nginx 做 Basic Auth + 安全標頭 + 限流後 proxy 到 host 上既有的 uvicorn `:8097`。應用程式碼一行不改。

**Tech Stack:** Docker Compose、nginx:alpine、cloudflare/cloudflared、httpd:alpine（產 htpasswd）、Makefile、Cloudflare Zero Trust Tunnel（token 式遠端管理）。

**重要前提（測試策略）：** 這是基礎建設，無 Python 單元測試。每個任務以「寫設定檔 → 用真實工具驗證（`nginx -t` / `docker compose config` / `curl` 驗 401-200）→ commit」取代 TDD。**Task 7 的 Cloudflare 儀表板建隧道與外網實測需要使用者的 token 與網域，無法由 agent 自動完成**，計畫已標明。

**前置假設：**
- WSL + Docker Desktop（`docker` 與 `docker compose` 可用，已驗證 compose v2）。
- 既有 uvicorn 可用 `make serve` 啟在 host `0.0.0.0:8097`（本機驗證 200 時需要）。
- 所有指令在 repo 根目錄 `/mnt/c/Users/User/Desktop/Project/report-mark` 執行。
- Makefile recipe 必須用 **Tab** 縮排。

---

## 檔案結構

| 檔案 | 職責 | 動作 |
|------|------|------|
| `deploy/nginx.conf` | 反向代理 + Basic Auth + 安全標頭 + 限流 + 真實 IP 還原 | Create |
| `deploy/docker-compose.yml` | nginx + cloudflared 兩服務，`restart: unless-stopped` | Create |
| `deploy/.env.example` | `TUNNEL_TOKEN` 範本 | Create |
| `deploy/secrets/.htpasswd` | Basic Auth 帳密雜湊（執行期由 `make edge-passwd` 產生，gitignore） | 由指令產生，不進版控 |
| `.gitignore` | 忽略 `deploy/.env` 與 `deploy/secrets/` | Modify |
| `Makefile` | 新增 `up-edge` / `down-edge` / `edge-logs` / `edge-passwd` 與變數 | Modify |
| `docs/EXTERNAL_ACCESS.md` | 繁中部署/維運逐步指南 | Create |

---

## Task 1：nginx 反向代理設定（`deploy/nginx.conf`）

**Files:**
- Create: `deploy/nginx.conf`

- [ ] **Step 1：建立 `deploy/nginx.conf`**

```nginx
# 廷豐研報 對外反向代理（經 Cloudflare Tunnel 進入）
# 路徑：cloudflared → nginx(:80, Basic Auth) → host uvicorn :8097
# 安全前提：nginx 不對 host 發佈任何埠，唯一入口是 cloudflared 隧道，
#          因此信任 CF-Connecting-IP 還原真實訪客 IP 是安全的。

# 限流區：以（還原後的）真實來源 IP 為 key
limit_req_zone $binary_remote_addr zone=edge:10m rate=10r/s;

server {
    listen 80;
    server_name _;

    # 還原 Cloudflare 帶來的真實訪客 IP
    real_ip_header CF-Connecting-IP;
    set_real_ip_from 0.0.0.0/0;

    # 唯讀檢索，無上傳；限制 body 大小
    client_max_body_size 1m;

    # 安全標頭（even on 401 也帶上）
    add_header X-Content-Type-Options nosniff always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header Referrer-Policy strict-origin-when-cross-origin always;

    # Basic Auth 共用密碼門檻
    auth_basic "tingfeng-research";
    auth_basic_user_file /etc/nginx/secrets/.htpasswd;

    location / {
        limit_req zone=edge burst=20 nodelay;

        proxy_pass http://host.docker.internal:8097;
        proxy_http_version 1.1;

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;

        # BGE-M3 查詢嵌入可能稍慢，放寬讀取逾時
        proxy_connect_timeout 5s;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
```

- [ ] **Step 2：用 nginx 驗證設定語法**

Run（`--add-host` 讓 `host.docker.internal` 在語法測試時可解析；auth_basic_user_file 在請求期才讀取，不存在不影響 `-t`）：

```bash
docker run --rm --add-host host.docker.internal:127.0.0.1 \
  -v "$(pwd)/deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  nginx:alpine nginx -t
```

Expected：輸出包含 `syntax is ok` 與 `test is successful`，退出碼 0。

- [ ] **Step 3：Commit**

```bash
git add deploy/nginx.conf
git commit -m "feat(deploy): nginx reverse proxy config for external access"
```

---

## Task 2：docker-compose（`deploy/docker-compose.yml`）

**Files:**
- Create: `deploy/docker-compose.yml`

- [ ] **Step 1：建立 `deploy/docker-compose.yml`**

```yaml
# 廷豐研報 對外存取邊緣：nginx 反向代理 + cloudflared 隧道
# 用法：先 `make edge-passwd` 設定 Basic Auth，填好 deploy/.env 的 TUNNEL_TOKEN，再 `make up-edge`
services:
  nginx:
    image: nginx:alpine
    restart: unless-stopped
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/default.conf:ro
      - ./secrets:/etc/nginx/secrets:ro
    extra_hosts:
      - "host.docker.internal:host-gateway"
    # 不對 host 發佈任何埠；僅 compose 網路內供 cloudflared 取用

  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run
    environment:
      - TUNNEL_TOKEN=${TUNNEL_TOKEN}
    depends_on:
      - nginx
```

- [ ] **Step 2：驗證 compose 可解析**

Run（注入假 token 避免插值警告；`-q` 僅驗證、無輸出）：

```bash
TUNNEL_TOKEN=dummy docker compose -f deploy/docker-compose.yml config -q
```

Expected：無輸出，退出碼 0。

- [ ] **Step 3：Commit**

```bash
git add deploy/docker-compose.yml
git commit -m "feat(deploy): nginx + cloudflared compose for external access"
```

---

## Task 3：環境範本與 .gitignore

**Files:**
- Create: `deploy/.env.example`
- Modify: `.gitignore`

- [ ] **Step 1：建立 `deploy/.env.example`**

```bash
# Cloudflare Tunnel token（從 Zero Trust → Networks → Tunnels 取得）
# 複製本檔為 deploy/.env 並填入實際 token。
# deploy/.env 已被 gitignore，切勿提交真實 token。
TUNNEL_TOKEN=
```

- [ ] **Step 2：在 `.gitignore` 末尾新增秘密忽略規則**

把以下區塊加到 `.gitignore` 最後（在現有 `search-after.yml` 那行之後）：

```gitignore

# 對外存取邊緣的秘密（隧道 token / Basic Auth 帳密）
deploy/.env
deploy/secrets/
```

- [ ] **Step 3：驗證忽略規則生效**

Run：

```bash
mkdir -p deploy/secrets && touch deploy/.env deploy/secrets/.htpasswd
git check-ignore deploy/.env deploy/secrets/.htpasswd
```

Expected：輸出兩行 `deploy/.env` 與 `deploy/secrets/.htpasswd`（代表被忽略）。

- [ ] **Step 4：清掉剛才的測試空檔（避免殘留）**

Run：

```bash
rm -f deploy/.env deploy/secrets/.htpasswd
```

Expected：無輸出，退出碼 0。

- [ ] **Step 5：Commit**

```bash
git add deploy/.env.example .gitignore
git commit -m "chore(deploy): env template and gitignore for edge secrets"
```

---

## Task 4：Makefile 維運指令

**Files:**
- Modify: `Makefile`

- [ ] **Step 1：新增變數（在 `MARKET ?=` 那行之後）**

把：

```makefile
MARKET ?=
```

改成：

```makefile
MARKET ?=
EDGE_COMPOSE ?= deploy/docker-compose.yml
EDGE_USER ?= tingfeng
```

- [ ] **Step 2：把新 target 名稱加入 `.PHONY`**

把：

```makefile
.PHONY: help deps db schema setup sample extract worklist prep tag-info \
        ingest ingest-lowio restore-durability align normalize serve search \
        stats reset-db clean-data pipeline
```

改成：

```makefile
.PHONY: help deps db schema setup sample extract worklist prep tag-info \
        ingest ingest-lowio restore-durability align normalize serve search \
        stats reset-db clean-data pipeline \
        edge-passwd up-edge down-edge edge-logs
```

- [ ] **Step 3：新增「對外存取」區段（加在 `stats` target 之後、`# ───── 維運 ─────` 之前）**

> 注意：以下 recipe 行必須是 **Tab** 縮排，不是空格。

```makefile
# ───── 對外存取（Cloudflare Tunnel + nginx）─────
edge-passwd:  ## 設定/更換對外 Basic Auth 共用密碼（互動輸入兩次）
	@mkdir -p deploy/secrets
	docker run --rm -it -v "$(CURDIR)/deploy/secrets:/secrets" httpd:alpine \
	  htpasswd -B -c /secrets/.htpasswd $(EDGE_USER)

up-edge:  ## 啟動對外邊緣（nginx + cloudflared）
	@test -f deploy/secrets/.htpasswd || { echo "缺少 deploy/secrets/.htpasswd，請先執行 make edge-passwd"; exit 1; }
	@test -f deploy/.env || { echo "缺少 deploy/.env，請複製 deploy/.env.example 並填入 TUNNEL_TOKEN"; exit 1; }
	docker compose -f $(EDGE_COMPOSE) up -d

down-edge:  ## 關閉對外邊緣
	docker compose -f $(EDGE_COMPOSE) down

edge-logs:  ## 跟看對外邊緣日誌
	docker compose -f $(EDGE_COMPOSE) logs -f --tail=100
```

- [ ] **Step 4：驗證 Makefile 解析正常且新 target 出現在 help**

Run：

```bash
make help
```

Expected：輸出列表中可見 `edge-passwd`、`up-edge`、`down-edge`、`edge-logs` 四個 target 與其說明；無 `*** missing separator` 之類 Makefile 解析錯誤。

- [ ] **Step 5：驗證 up-edge 守門（缺秘密時應擋下）**

Run（此時尚未產生 htpasswd / .env，應被守門擋下）：

```bash
make up-edge; echo "exit=$?"
```

Expected：印出「缺少 deploy/secrets/.htpasswd，請先執行 make edge-passwd」，且 `exit=1`（不會真的啟動容器）。

- [ ] **Step 6：Commit**

```bash
git add Makefile
git commit -m "feat(deploy): make targets for edge (up/down/logs/passwd)"
```

---

## Task 5：本機端到端驗證 Basic Auth + 反向代理

> 此任務不改檔案，純驗證 nginx + htpasswd 串接正確。用臨時 htpasswd 與臨時發佈埠，驗完即清。真正的共用密碼由使用者部署時以 `make edge-passwd` 設定。

**Files:** 無（僅執行驗證）

- [ ] **Step 1：產生臨時測試用 htpasswd**

Run：

```bash
mkdir -p deploy/secrets
docker run --rm -v "$(pwd)/deploy/secrets:/secrets" httpd:alpine \
  htpasswd -Bbc /secrets/.htpasswd testuser testpass
```

Expected：`deploy/secrets/.htpasswd` 產生，內容為 `testuser:` 開頭的 bcrypt 雜湊（`$2y$...`）。

- [ ] **Step 2：（可選但建議）啟動 host uvicorn 以驗 200**

Run（背景啟動既有檢索服務，使上游可達；若不便啟動可略過，Step 4 會以 502 代表「已過 Basic Auth」）：

```bash
make serve &
sleep 8
```

Expected：uvicorn 在 `0.0.0.0:8097` 監聽（BGE-M3 載入需數秒）。

- [ ] **Step 3：用實際 nginx.conf 啟一個臨時容器（發佈 18088）**

Run：

```bash
docker run -d --name edge-nginx-test -p 18088:80 \
  --add-host host.docker.internal:host-gateway \
  -v "$(pwd)/deploy/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v "$(pwd)/deploy/secrets:/etc/nginx/secrets:ro" \
  nginx:alpine
sleep 2
```

Expected：容器啟動，無錯誤。

- [ ] **Step 4：驗證 401（無帳密）與通過（正確帳密）**

Run：

```bash
echo "no-cred:  $(curl -s -o /dev/null -w '%{http_code}' http://localhost:18088/)"
echo "bad-cred: $(curl -s -o /dev/null -w '%{http_code}' -u testuser:wrong http://localhost:18088/)"
echo "ok-cred:  $(curl -s -o /dev/null -w '%{http_code}' -u testuser:testpass http://localhost:18088/)"
```

Expected：
- `no-cred: 401`（Basic Auth 門檻生效）
- `bad-cred: 401`（錯密碼被擋）
- `ok-cred: 200`（若 Step 2 有啟 uvicorn）或 `502`（未啟 uvicorn）。**只要不是 401，即代表已通過 Basic Auth 進到上游。**

- [ ] **Step 5：清理臨時容器、測試 htpasswd、背景 uvicorn**

Run：

```bash
docker rm -f edge-nginx-test
rm -f deploy/secrets/.htpasswd
# 若 Step 2 有啟 uvicorn：
pkill -f "uvicorn web.server:app" 2>/dev/null || true
```

Expected：容器移除；測試用 htpasswd 刪除（`deploy/secrets/` 為 gitignore，本就不會進版控）。

- [ ] **Step 6：（無檔案異動，免 commit）**

確認 `git status` 沒有新增/異動被追蹤的檔案（`deploy/secrets/` 應被忽略）。

---

## Task 6：部署/維運文件（`docs/EXTERNAL_ACCESS.md`）

**Files:**
- Create: `docs/EXTERNAL_ACCESS.md`

- [ ] **Step 1：建立 `docs/EXTERNAL_ACCESS.md`**

```markdown
# 對外存取：Cloudflare Tunnel + nginx

讓辦公室外的同事從外網連入廷豐研報檢索網頁，並以共用密碼（Basic Auth）設門檻。

## 架構

\`\`\`
瀏覽器 ──HTTPS──▶ Cloudflare 邊緣（TLS、research.<你的網域>）
            │
            ▼  cloudflared 主動外連的加密隧道（出向連線，免動路由器/防火牆）
        nginx（Basic Auth + 安全標頭 + 限流）
            │  proxy_pass host.docker.internal:8097
            ▼
        uvicorn web.server:app（既有，:8097）
\`\`\`

對外唯一路徑是 Cloudflare → 隧道 → nginx(Basic Auth) → uvicorn。origin 不對公網開任何埠；外網路徑與既有 LAN portproxy/防火牆互不影響，也不受 WSL 重開機換 IP 影響。

## 前置需求

- 已有網域掛在 Cloudflare（nameserver 指向 Cloudflare）。
- 本機可用 Docker Desktop（`docker` 與 `docker compose`）。
- 既有檢索服務可啟動（`make serve`，監聽 :8097）。

## 一次性設定

### 1) 在 Cloudflare 建立具名隧道並取得 token

1. 進 Cloudflare **Zero Trust → Networks → Tunnels → Create a tunnel**。
2. 選 **Cloudflared** 連接器，命名（例如 `tingfeng-research`）。
3. 建立後頁面會顯示安裝指令，其中 \`--token\` 後那串就是 **TUNNEL_TOKEN**，複製起來。
4. 在該隧道的 **Public Hostname** 新增一筆：
   - Subdomain：\`research\`（或你想要的）
   - Domain：選你的網域
   - Service：\`HTTP\` → \`nginx:80\`
   （Service 用 \`nginx:80\` 是因為 cloudflared 與 nginx 在同一個 compose 網路，可用服務名互通。）

### 2) 填入 token

\`\`\`bash
cp deploy/.env.example deploy/.env
# 編輯 deploy/.env，把 TUNNEL_TOKEN= 後面貼上剛才複製的 token
\`\`\`

### 3) 設定 Basic Auth 共用密碼

\`\`\`bash
make edge-passwd          # 互動輸入密碼兩次（帳號預設 tingfeng）
# 想自訂帳號：make edge-passwd EDGE_USER=yourname
\`\`\`

### 4) 啟動邊緣

\`\`\`bash
make up-edge              # 啟動 nginx + cloudflared
make edge-logs            # 觀察隧道是否連上（看到 "Registered tunnel connection" 即成功）
\`\`\`

> 提醒：請確保既有檢索服務 \`make serve\` 也在執行，否則 nginx 會回 502。

### 5) 外網實測

從**非辦公室網路**（例如手機關 Wi-Fi 用行動網路）開 \`https://research.<你的網域>\`：
- 應先跳 Basic Auth 帳密視窗；
- 輸入正確帳密後可正常檢索研報。

## 日常維運

| 動作 | 指令 |
|------|------|
| 啟動邊緣 | \`make up-edge\` |
| 關閉邊緣 | \`make down-edge\` |
| 看日誌 | \`make edge-logs\` |
| 換共用密碼 | \`make edge-passwd\`（改完 \`make down-edge && make up-edge\` 或 \`docker compose -f deploy/docker-compose.yml restart nginx\`） |

重開機後：兩容器為 \`restart: unless-stopped\` 會自動回復；只要 \`make serve\` 也在跑，外網即恢復，**無需重設 portproxy**。

## 安全備註

- Basic Auth 為**共用密碼**，透過 Cloudflare TLS 加密傳輸（非明文）。請定期 \`make edge-passwd\` 輪替。
- 建議 Cloudflare 端開「Always Use HTTPS」、TLS 模式至少 Full。
- \`deploy/.env\`（token）與 \`deploy/secrets/.htpasswd\`（帳密）皆已 gitignore，切勿提交。

## 疑難排解

| 症狀 | 可能原因 / 處置 |
|------|----------------|
| 外網開站一直 502 | host uvicorn 沒在跑 → \`make serve\`；或 \`host.docker.internal\` 不通（確認 compose 的 \`extra_hosts: host-gateway\` 存在） |
| 一直跳帳密、輸入正確仍進不去 | htpasswd 沒設或帳號不符 → 重跑 \`make edge-passwd\`，並 \`docker compose -f deploy/docker-compose.yml restart nginx\` |
| \`make edge-logs\` 看不到 tunnel 連線 | token 錯/沒填 → 檢查 \`deploy/.env\`；Cloudflare 儀表板確認隧道狀態為 HEALTHY |
| 外網打不開但 LAN 正常 | Cloudflare Public Hostname 的 Service 是否設成 \`http://nginx:80\`；DNS 記錄是否由隧道自動建立 |
```

- [ ] **Step 2：驗證文件存在且非空**

Run：

```bash
test -s docs/EXTERNAL_ACCESS.md && echo "OK doc exists" && wc -l docs/EXTERNAL_ACCESS.md
```

Expected：輸出 `OK doc exists` 與行數。

- [ ] **Step 3：Commit**

```bash
git add docs/EXTERNAL_ACCESS.md
git commit -m "docs: external access guide (Cloudflare Tunnel + nginx)"
```

---

## Task 7：Cloudflare 儀表板設定 + 外網實測（**使用者手動**）

> 此任務需要使用者的 Cloudflare 帳號、網域與 token，agent 無法自動完成。照 `docs/EXTERNAL_ACCESS.md` 的「一次性設定」執行。

- [ ] **Step 1：** 依 `docs/EXTERNAL_ACCESS.md` §1 在 Cloudflare 建立具名隧道，取得 `TUNNEL_TOKEN`，並設 Public Hostname `research.<網域>` → `http://nginx:80`。
- [ ] **Step 2：** `cp deploy/.env.example deploy/.env` 並填入 token。
- [ ] **Step 3：** `make edge-passwd` 設定共用密碼。
- [ ] **Step 4：** 確保 `make serve` 在跑，然後 `make up-edge`。
- [ ] **Step 5：** `make edge-logs` 確認看到 `Registered tunnel connection`。
- [ ] **Step 6（驗收）：** 從非辦公室網路裝置開 `https://research.<網域>`：
  - 無帳密 → 跳 Basic Auth 視窗 / 401；
  - 正確帳密 → 可正常檢索研報；
  - LAN 8097 既有存取不受影響。

---

## Self-Review（撰寫者已核）

- **Spec 覆蓋：** nginx 反向代理→T1；compose（含 restart）→T2；env 範本與 gitignore→T3；Makefile 維運指令→T4；Basic Auth 本機驗證→T5；EXTERNAL_ACCESS.md 步驟→T6；Cloudflare 隧道建立與外網驗收→T7。spec 各項皆有對應任務。
- **Placeholder 掃描：** 無 TBD/TODO；所有設定檔、指令、預期輸出皆具體列出。`research.<網域>` 為使用者實際網域之代入位，非缺漏。
- **型別/名稱一致：** 服務名 `nginx`/`cloudflared`、檔案路徑 `deploy/...`、Makefile target `edge-passwd`/`up-edge`/`down-edge`/`edge-logs`、變數 `EDGE_COMPOSE`/`EDGE_USER`、htpasswd 路徑 `/etc/nginx/secrets/.htpasswd` 全文一致。
- **可建置順序：** 每個任務獨立可 commit；T1–T4 建立檔案、T5 純驗證、T6 文件、T7 手動。樹在任一 commit 後皆不破。
