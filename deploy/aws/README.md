# AWS staging 基礎設施

此目錄只建立 `report-research-staging` 的 EC2、RDS PostgreSQL 與必要網路，並驗證 EC2 能透過 TLS
連上 RDS。它**不會** clone/build/run 專案、不套用 `db/schema.sql`、不搬資料，也不建立 Web、批次、
nginx、Cloudflare 或排程服務。

## CloudFormation 管理方式

本 staging 基礎設施由 AWS CloudFormation 以宣告式範本管理，stack 名稱為 `report-research-staging`。
`infrastructure.yaml` 描述 VPC、子網與路由、安全群組、EC2 與 Elastic IP、EC2 IAM role／instance profile，
以及 RDS subnet group、parameter group 和 PostgreSQL 執行個體。EC2 的範本 bootstrap 會安裝連線驗收所需工具；
這不代表應用程式已部署。

變更流程是提供範本與參數、建立 change set、檢視變更及 validation events，再於核准後執行，最後以 stack
outputs 作為資源 ID、RDS endpoint 與 managed secret ARN 的來源。既有 stack 更新須使用 `UPDATE` change set；
此文件後續章節列出初次 `CREATE` 與更新的命令和檢查步驟。

- `infrastructure.yaml`：CloudFormation 資源、參數、輸出及 EC2 基礎 bootstrap 的宣告。
- `parameters-staging.json`：初次 `CREATE` 使用的參數；檔案只有兩個覆寫值，不能直接當成既有 stack 的更新參數。
- `security.guard`：部署前在本機執行的 CloudFormation Guard 安全規則，不是 AWS 資源設定，也不會自行套用。
- `verify-rds-connectivity.sh`：透過 SSM 驗證 EC2 到 RDS 的 TLS 連線、設定與擴充功能；它會在資料庫執行
  `CREATE EXTENSION IF NOT EXISTS`，因此驗收包含資料庫副作用。

Cloudflare DNS 由 Cloudflare 控制台另行管理。應用程式、服務、`db/schema.sql` 與語料／狀態資料也在此 stack
之外部署或處理；修改這些部分不會因 CloudFormation stack 更新而自動完成。

## Staging 更新與驗收紀錄（2026-10-05）

本節是 2026-10-05 的操作紀錄；以下通過項目不代表所有上線待辦都已完成。

- EC2 checkout 已由 `be12fd3` fast-forward 到 main 的 `0f6fad8`（PR #290），工作目錄乾淨。
  此次差異沒有應用 Python、前端或相依套件修改，未重建前端或重啟 Web；同步腳本修正已落地。
  `deploy/nginx-origin.conf` 與 `/etc/nginx/sites-available/report-mark` 逐位元相同，nginx 驗證通過。
- Web、nginx、同步／健康／freshness timer 均為 active。本機 DB、R2、LLM 健康端點均通過；
  `SYNC_SOURCE=r2-inbox`，同步成功心跳已存在。
- 公開 HTTPS 登入、受保護 API 與 `/app/` 通過，session cookie 的 Secure、HttpOnly、SameSite=Lax
  均符合要求。匿名 API 回 401，公開 `/healthz` 回 200；R2／LLM 健康端點對外回 404。
- RDS 應用帳號為 `report_mark`，不是 PostgreSQL superuser 或 `rds_superuser` 成員；
  連線使用 `ssl=verify-full`、RDS CA bundle 與 TLS 1.3。RDS parameter group 為 `in-sync`，
  AWS API 顯示 `rds.force_ssl=1`；此參數不透過應用 SQL session 的 `SHOW` 取值。
- 真實問答經 Cloudflare／nginx 完成 SSE：收到 sources、538 個 token 事件、done 與一個心跳，
  約 28 秒完成。R2 inbox 有三個來源物件；選取既有 PDF 下載至暫存鏡像，大小、PDF 簽章、
  mtime 均符合，第二次 pull 正確略過。此檢查未上傳測試物件或觸發研報匯入。
- RDS 自動備份已透過 `ModifyDBInstance` 從一天提高到七天，修改前確認沒有 pending modifications。
  更新後為 available、pending 為空，`DbiResourceId` 仍是 `db-N6NICH6JJTKAXVW6LR7CYIDLUE`，
  deletion protection、private、storage encryption 均保留。EC2 與 RDS 未被替換。

### 尚未完成的 CloudFormation 與 Cloudflare 設定

目前登入 profile 是 `PowerUserAccess-607063196781`，不是下方歷史命令使用的 `report-research`。
正常 UPDATE change set 除 EC2 Metadata 外，將 RDS `BackupRetentionPeriod` 的 1 → 7 標為
`Replacement=Conditional`，並列出 EC2 role 的 managed secret 參照更新；此計畫**未執行且已刪除**。
本機 cfn-lint、九項 Guard 規則與 AWS `validate-template` 已通過，正常計畫的 validation events 無錯誤。

為完成已授權的備份調整，先使用 RDS API 原地修改保留期，再建立比對實際狀態的 drift-aware change set。
AWS 官方說明指出，範本更新成與實際狀態相符時，可同步 drift 狀態而不修改該資源；但此次計畫在
讀取 EC2 role 時因 `iam:GetRole` 不足而失敗，未進入執行。
參考 [RDS 非零保留期更新語意](https://docs.aws.amazon.com/AmazonRDS/latest/APIReference/API_ModifyDBInstance.html)
與 [drift-aware change sets](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/drift-aware-change-sets.html)。

因此 **RDS 實際保留期為七天，但 CloudFormation 已部署範本仍記錄一天**；EC2 的 Metadata 修正亦尚未套用。
不要執行舊計畫，也不要把此次紀錄當成 stack 已完成更新。後續步驟：

1. 由 IAM 管理者將 [staging-state-read-policy.json](staging-state-read-policy.json) 的補充唯讀權限
   加入部署操作身分，不是加入 EC2 role。這份檔案只對現有 staging role／instance profile 開放讀取；
   不賦予 IAM 寫入或 PassRole。重新佈建 SSO permission set 後取得新 session。
2. 重新建立唯一命名的 UPDATE change set，保留六個參數，加入 `--deployment-mode REVERT_DRIFT`。
   核對完整 actual／previous／desired state、AMI resolved value 與 validation events；不得替換 EC2／RDS，
   也不得修改 IAM 權限。若仍缺其他權限或出現未預期項目，停止並處理原因，不執行原先的 Conditional 計畫。
3. 執行審查通過的計畫並等待 `stack-update-complete`，再確認已部署範本、實際保留期與物理 ID。

Cloudflare 的兩個待辦也尚未完成：

- HTTP 請求仍回 522，HTTPS 正常。只對此 hostname 新增 Single Redirect：Request URL 為
  `http://research.tingfong.com/*`、Target URL 為 `https://research.tingfong.com/${1}`、301、
  保留 query string。驗收 HTTP `/healthz?accept=290` 應回 301 且 Location 保留路徑與查詢字串。
  見 [Cloudflare Redirect Rules 操作](https://developers.cloudflare.com/rules/url-forwarding/single-redirects/create-dashboard/)。
- PDF 的 R2 presigned Range GET 回 206、簽章與 inline filename 正常，但沒有允許
  `https://research.tingfong.com` 的 CORS response header，自訂 PDF viewer 仍會被瀏覽器攔阻。
  應用 R2 憑證對 `GetBucketCors` 回 403 AccessDenied，不能修改 bucket 設定。
  在 Cloudflare R2 的目標 bucket → Settings → CORS Policy，將
  [r2-cors-research.json](../r2-cors-research.json) 的規則**加入現有規則**，保留其他部署的既有 origins。
  此檔案是可追加的單一網站規則，不能直接取代未知的整份 bucket policy。
  設定後重新取得 presigned URL，驗證 GET／OPTIONS 的 `Access-Control-Allow-Origin`、Range 與 exposed headers。
  見 [Cloudflare R2 CORS](https://developers.cloudflare.com/r2/buckets/cors/)。

此次帳號沒有 CloudTrail trail（`describe-trails` 為空），未另行建立 audit infrastructure。
上述 API／SSM 結果用於此次驗收；CloudFormation 更新與 Cloudflare 待辦仍須各自驗證完成。

## 固定邊界

- AWS account：`607063196781`
- Region：`ap-southeast-1`
- EC2：`c7i.xlarge`、Ubuntu 24.04 LTS amd64、100 GiB encrypted gp3、管理只走 SSM（無 SSH、無 key pair）
- 入站：只有 TCP/443，來源限定 Cloudflare IPv4 範圍（managed prefix list `CloudflareOriginPrefixList`）；
  不開 SSH、TCP/80、TCP/8097、RDS/5432 或 `0.0.0.0/0`
- 公網位址：由同一個 stack 管理並綁定 EC2 的 Elastic IP；`research.tingfong.com` 的橘雲 A record 指向它
- RDS：PostgreSQL 16、`db.m7g.large`、Single-AZ、100 GiB encrypted gp3、上限 500 GiB、自動備份保留 7 天
- VPC：`10.20.0.0/16`；EC2 在 public subnet，RDS 在跨兩 AZ 的 private subnet group
- Stack：`report-research-staging`；stack 與 RDS 均啟用刪除保護

EC2 instance role 可以讀取這一個 RDS managed master secret，因此具備執行破壞性 SQL 的能力。應用部署前必須
另建最小權限帳號，不能直接沿用 master credential。

## 部署狀態紀錄（2026-10-01）

以下保留 2026-10-01 在帳號 `607063196781`、新加坡 `ap-southeast-1` 完成 staging 建置與驗收時的觀察，
不是即時狀態；後續操作前須重新確認：

- Stack `report-research-staging`：`UPDATE_COMPLETE`，termination protection 已啟用。
- EC2 `i-030bdf624d877c58a`：`c7i.xlarge`、Ubuntu 24.04、SSM Online、無 key pair、零 ingress。
- Elastic IP `13.215.135.59`（allocation `eipalloc-002cdc2f5837c4553`）：已由 stack 建立並綁定同一台 EC2；
  權威值仍以 stack output `Ec2ElasticIpAddress` 為準。
- RDS `report-research-staging-postgres`：PostgreSQL 16.15、`db.m7g.large`、Single-AZ、private、
  deletion protection 已啟用。
- EC2 至 RDS 使用 RDS CA bundle 與 `sslmode=verify-full` 成功建立 TLS 1.3 連線；非 TLS 連線被拒絕。
- Parameter group 為 `in-sync` 且 `rds.force_ssl=1`；已安裝 `vector` 0.8.2 與 `pg_trgm` 1.6。
- 未部署 repo、應用服務、Docker workload、nginx、Cloudflare Tunnel 或排程工作。

Stack outputs 是資源 ID、endpoint 與 managed secret ARN 的權威來源；不要把 secret value 寫入文件或命令列。
外部 DNS 由 Cloudflare 管理（本 AWS 帳號沒有 Route 53 hosted zone）；建立 A record 前必須確認完整網域名稱，
並將目標設為 `Ec2ElasticIpAddress`。2026-10-01 當時 EC2 SG 為零 ingress，因此 DNS 可解析不代表 HTTP/HTTPS 可連線。

Cloudflare DNS 由帳號管理者手動設定，使用者指定來源為 `A research.tingfong.com -> 13.215.135.59`。
2026-10-02 公開測試已確認此 hostname 使用 Cloudflare 代理（橘雲）；以下紀錄取代原本先使用
DNS only（灰雲）的準備步驟。DNS 解析成功不能當成服務可用性驗收。

## 網域驗證紀錄（2026-10-02 16:41，Asia/Taipei）

本次僅做公開 DNS／HTTP／HTTPS 測試與本機文件核對，未變更 Cloudflare 或 AWS 設定：

| 檢查 | 結果 |
|---|---|
| tingfong.com NS | dante.ns.cloudflare.com、fish.ns.cloudflare.com |
| research.tingfong.com A | 1.1.1.1、8.8.8.8 與 Cloudflare 權威 DNS 均回覆 104.21.90.131、172.67.200.167（順序不固定） |
| research.tingfong.com AAAA | 2606:4700:3037::6815:5a83、2606:4700:3036::ac43:c8a7，屬 Cloudflare 代理節點 |
| 訪客至 Cloudflare HTTPS | TLS 1.3 成功；Google Trust Services 憑證驗證通過，SAN 的 *.tingfong.com 符合 hostname；有效期限為 2026-08-27 至 2026-11-25（UTC） |
| 公開 HTTP／HTTPS GET / | 兩者均回 HTTP 522，來源站連線逾時 |
| 指定 13.215.135.59 直連 | HTTP/80 與保留 hostname／SNI 的 HTTPS/443 均在 8 秒連線期限內逾時 |
| AWS 初次資源核對 | report-research profile 當時登入過期；重新登入後的即時結果見下節 |

結論：網域已公開解析、Cloudflare 訪客端 HTTPS 已成立，網站尚不可用。
橘雲會以 Cloudflare IP 回覆 DNS，因此公開查詢無法核實控制台背後的來源 A record 是否確為
13.215.135.59，也無法得知來源站的 SSL/TLS 模式與憑證狀態。
[Cloudflare 522 定義](https://developers.cloudflare.com/support/troubleshooting/http-status-codes/cloudflare-5xx-errors/error-522/)
為 Cloudflare 聯絡來源站逾時。既有範本的 EC2 零 ingress 與 2026-10-01 尚未部署應用的紀錄
可解釋本次現象；重新登入後已確認 EC2 仍為零 ingress、Web 尚未運行，詳見下節。

### AWS 登入後核對（2026-10-02）

使用者允許執行 aws login 後，已完成登入並確認帳號為 607063196781。本次瀏覽器授權為
PowerUserAccess，report-research profile 已由先前的 AdministratorAccess session 更新為該角色。
下列為 AWS API 與 SSM 唯讀檢查結果，未變更資源或服務設定：

- Stack 為 UPDATE_COMPLETE，termination protection 仍啟用。
- EC2 i-030bdf624d877c58a 為 running，instance／system status 均為 ok，SSM 為 Online。
- Stack output 的 Elastic IP 與實際綁定均為 13.215.135.59，對應同一台 EC2。
- EC2 實際套用 sg-047c7006962a34104，Ingress 為空；RDS SG 的 TCP/5432 仍僅允許來自該 EC2 SG。
- 16:48（Asia/Taipei）的 SSM 檢查顯示 80／443／8097／8098 均無 TCP listener；
  本機 http://127.0.0.1:8097/healthz 連線被拒絕。
- Docker 已安裝，但 docker ps -a 為空。主機 PATH 找不到 nginx／cloudflared。
- Web unit 已存在，User 為 ubuntu、WorkingDirectory 為 /home/ubuntu/report-mark，
  但 ActiveState 為 inactive、UnitFileState 為 disabled；多個監控／匯入 unit 亦已存在。
  此次未查證該應用目錄的相依、資料或環境設定是否完備。

目前已出現部分部署產物，不能再將 2026-10-01「尚未安裝 Docker／應用 unit」的觀察當成即時狀態。
公開入口與 Web 運行仍未就緒；關閉橘雲不會解除 EC2 入站限制，也不會啟動應用。

橘雲目前可保持開啟；先前指定 IP 的 HTTP／HTTPS 測試已繞過 Cloudflare，無須為了此次診斷改成灰雲。
若後續選 Tunnel，hostname 使用該 Tunnel 的代理 CNAME；若選直連 EIP，則先完成來源站 HTTPS 與
Cloudflare 到來源站的網路放行，再驗收代理流量。
若直連路徑使用 Cloudflare Origin CA，灰雲直連會失去瀏覽器信任；見
[Cloudflare Origin CA 的代理前提](https://developers.cloudflare.com/ssl/origin-configuration/origin-ca/)。

以下是 2026-10-02 核對時尚未執行的部署待辦；入口已於後續選定 A record＋橘雲（見「對外入口決定」），
應用與 AWS 即時狀態仍須以此次部署驗收為準：

1. Cloudflare 控制台的來源記錄及 SSL/TLS 模式仍待核對；AWS 帳號、stack outputs、Elastic IP
   綁定、EC2／SSM 與安全群組的唯讀核對已完成。
2. 完成應用部署：先盤點 /home/ubuntu/report-mark 的已有產物與設定，再補齊 RDS 最小權限
   應用帳號、schema、語料／必要狀態資料搬移，設定
   RDS verify-full、R2、DeepSeek、登入與 session 金鑰，安裝相依、建置前端、啟動 Web。
   先驗本機 healthz；R2／LLM 健康端點仍只允許本機直連。
3. 完成對外入口，須先選定其中一條路徑：
   - 延續現有 Cloudflare Tunnel 架構：在 EC2 部署 cloudflared 與適用於 Linux 的 nginx 上游設定，
     將 hostname 路由至 nginx，DNS 改為該 tunnel 的代理 CNAME，保留 EC2 零 ingress。
     Tunnel 透過出向連線提供入口，不依賴來源站的公網 80／443；見
     [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/)。
   - 沿用 A record 直連 Elastic IP：建立 nginx HTTPS listener 與符合 hostname 的來源站憑證，
     以 Cloudflare Full (strict) 連線，並透過審查後的 CloudFormation change set 調整 ingress。
     此路徑會改變目前零 ingress 的固定邊界，需同步修改範本、安全守門與文件，不能只在控制台開埠；
     不公開 SSH、8097 或 RDS/5432。見
     [Full (strict) 前提](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)。
4. 驗收 HTTPS 登入、session cookie、受保護 API、PDF／R2 CORS、問答 SSE，確認公開 healthz
   成功；同步安裝適用於 AWS 的監控、備份與排程，確認匯入資料來源後再切換正式流量。

兩條入口都需完成代理信任（REPORT_MARK_EDGE_SECRET 或精確的可信代理 CIDR），並確保代理
正確覆寫轉送標頭；現有 deploy/nginx.conf 是 Tunnel 內部模板，不能直接當成公網 TLS 入口。

### Tunnel 與 A record 的差異

比較下列路徑時尚未選定入口；後續已選定 A record＋橘雲（見「對外入口決定」）。
A record 是 DNS 記錄類型，橘雲／灰雲是代理狀態，兩者不可混為一談：
目前的 A record 開啟橘雲，因此訪客仍先經過 Cloudflare，再由 Cloudflare 連到 EIP；
Tunnel 的 hostname 則以代理 CNAME 指向 Tunnel，Cloudflare 透過 cloudflared 主動建立的連線到來源站。

| 面向 | Cloudflare Tunnel | A record＋橘雲 | A record＋灰雲 |
|---|---|---|---|
| 來源站入口 | EC2 主動建立出向加密連線，無須公開 Web 入站埠 | Cloudflare 連到 EIP 的 HTTPS listener | 瀏覽器直接連到 EIP 的 HTTPS listener |
| 來源 IP | Web 入口不依賴固定公網 IP；仍需要可用的出向網路 | 依賴 EIP，公開 DNS 回覆 Cloudflare IP | 依賴 EIP，公開 DNS 回覆來源 IP |
| 安全群組 | 可保留零 ingress | 需放行來源站 HTTPS；可限制只允許 Cloudflare IP 範圍 | 需允許實際訪客的 HTTPS 入站流量 |
| TLS 維護 | Cloudflare 提供訪客端 TLS；Tunnel 加密傳輸，同機本地上游可用 HTTP | Cloudflare 訪客端 TLS＋來源站憑證，建議 Full (strict) | 來源站需自行提供瀏覽器信任的憑證與續期 |
| 額外維運 | 維護 cloudflared、憑證／token、更新、自動重啟與 Tunnel 健康監控 | 維護來源站 TLS 與 Cloudflare 入站 IP 範圍 | 維護來源站 TLS、入口防護及限流 |
| 故障依賴 | Cloudflare、Tunnel connector、應用主機／服務 | Cloudflare、來源站入口、應用主機／服務 | 應用主機／服務；Cloudflare 若仍是 DNS provider，DNS 仍依賴它 |
| 移轉彈性 | 新主機可用同一 Tunnel 的 connector 接替，需保護 Tunnel 憑證 | 可改 DNS／來源設定接到其他入口供應商 | 可直接更換來源 IP 或 DNS provider |

Tunnel 與橘雲 A record 都能使用對應 Cloudflare 方案的 CDN／WAF 等功能；灰雲不經其 Web 代理。
Tunnel 可用於 Free 方案，但付費安全／負載平衡功能仍依方案計費，也不會消除 AWS 運算或出向網路費用。
若保留現有 EIP，選 Tunnel 不會自動釋放它或停止 IPv4 計費；本次未調整 EIP。

此專案的判斷：Tunnel 符合現有零 ingress 與既有 nginx＋cloudflared 設計；A record＋橘雲亦可安全部署，
但需新增來源站 TLS／入站限制。Tunnel 多個連線或 replicas 不能消除單台 EC2／應用的故障風險。
兩種代理入口的速度無固定優劣，應以問答首字延遲、SSE 連續性與實際流量驗收；
PDF 目前使用 R2 presigned URL，原檔下載走瀏覽器到 R2，不因 Web 入口選 Tunnel 而全改走 EC2。

參考：[Tunnel 原理與方案](https://developers.cloudflare.com/tunnel/)、
[Tunnel replicas](https://developers.cloudflare.com/tunnel/configuration/)、
[橘雲／灰雲](https://developers.cloudflare.com/dns/proxy-status/)、
[Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/)。

## 對外入口決定：A record＋橘雲直連（2026-10-02）

上節的兩條路徑選定 **A record＋橘雲**，不走 Tunnel。訪客 → Cloudflare（訪客端 TLS）→ EIP:443 →
EC2 上的 nginx（來源站 TLS）→ `127.0.0.1:8097`。

| 層 | 設定 | 管理位置 |
|---|---|---|
| Security Group | 只放行 `CloudflareOriginPrefixList` 的 TCP/443 | 本範本（`Ec2SecurityGroup`、`security.guard` 的 `ec2_inbound_is_only_https_from_cloudflare`） |
| 來源站憑證 | Cloudflare Origin CA，SAN `research.tingfong.com`，效期至 2041-09-28 | EC2 的 `/etc/ssl/report-mark/`（私鑰 0600 root），不進 repo |
| nginx | 非此網域的 TLS 握手一律拒絕；只信任 Cloudflare 範圍的 `CF-Connecting-IP` | 應用 repo 的 `deploy/nginx-origin.conf` |
| Cloudflare | SSL/TLS 模式 **Full (strict)**；**Always Use HTTPS** 開啟（來源站不聽 80） | Cloudflare 控制台 |

刻意的取捨與限制：

- **橘雲不能關**：Origin CA 憑證只有 Cloudflare 信任，改成灰雲時瀏覽器會報憑證錯誤。
- **SG 的 `GroupDescription` 刻意不改**（字面上仍寫 Ingress-free）：改它會替換整個 SG，連帶影響
  RDS SG 的來源參照。實際規則以 `SecurityGroupIngress` 與 Guard 規則為準。
- **只開 IPv4**：VPC 沒有 IPv6、網域沒有來源 AAAA record，Cloudflare 以 IPv4 回源。
- **prefix list 的 `MaxEntries` 是 20**：SG 規則引用 prefix list 時，以 `MaxEntries` 計入 SG 規則配額。
- **Cloudflare IP 範圍會變**：以 <https://www.cloudflare.com/ips-v4> 為準。有變動時，本範本的 prefix list
  與 nginx 的 `set_real_ip_from` 要一起改；只改 SG 的話，限流與登入失敗追蹤會算在 Cloudflare 節點頭上。
- 任何 Cloudflare 客戶都能把自己的網域指向這個 EIP、從 Cloudflare 範圍連進來，nginx 會以拒絕握手擋掉
  不是本網域的請求。要更嚴格可以另開 Cloudflare Authenticated Origin Pulls（mTLS），目前未啟用。

上線順序：先在 EC2 上確認 nginx 與 web 本機可用，再以下方「既有 stack 更新」流程建立 change set。
預期變更為新增 `CloudflareOriginPrefixList`（Add）與修改 `Ec2SecurityGroup`（Modify、
`Replacement=False`），外加 stack description 與 outputs。本範本也把 RDS 自動備份保留期從 1 天
提高為 7 天；若現有 stack 仍為 1 天，應另有 `Database` Modify、`Replacement=False`，且唯一屬性
變更是 `BackupRetentionPeriod`；若已是 7 天則無此項。修正 EC2 的設計註記也可能另有 `Ec2Instance`
Modify，此項必須僅修改 Metadata、`Replacement=False`，不得修改 ImageId 或其他 Properties。
出現其他項目就停止。最後在 Cloudflare 控制台
確認 Full (strict) 與 Always Use HTTPS，從外部打 `https://research.tingfong.com/healthz` 驗收。

## 前置與唯讀檢查

AWS CLI 使用獨立 profile，所有命令都明確指定 Region：

```bash
aws login --profile report-research
aws sts get-caller-identity --profile report-research --region ap-southeast-1
```

帳號不是 `607063196781` 時立即停止。接著確認 EC2 offering、Ubuntu AMI parameter、PostgreSQL 16
versions 與 RDS class 組合：

```bash
aws ec2 describe-instance-type-offerings \
  --profile report-research --region ap-southeast-1 \
  --location-type availability-zone \
  --filters Name=instance-type,Values=c7i.xlarge

aws ssm get-parameter \
  --profile report-research --region ap-southeast-1 \
  --name /aws/service/canonical/ubuntu/server/noble/stable/current/amd64/hvm/ebs-gp3/ami-id

aws rds describe-db-engine-versions \
  --profile report-research --region ap-southeast-1 \
  --engine postgres --query 'DBEngineVersions[?starts_with(EngineVersion, `16.`)].EngineVersion'

aws rds describe-orderable-db-instance-options \
  --profile report-research --region ap-southeast-1 \
  --engine postgres --engine-version <EXACT_16_MINOR> \
  --db-instance-class db.m7g.large --vpc
```

選用目標 Region 中最高且 `db.m7g.large` 可訂購的 PostgreSQL 16 minor。若 `c7i.xlarge` 只在第二個
stack AZ 供應，部署參數使用 `Ec2SubnetChoice=b`。

2026-10-01 的唯讀檢查結果為：`c7i.xlarge` 在 `ap-southeast-1a/b/c` 均可供應，Ubuntu AMI parameter
解析為 `ami-0c8eea4e9e5c38034`，而 PostgreSQL `16.15`、`db.m7g.large`、gp3、自動擴充在三個 AZ
均可訂購。因此 [`parameters-staging.json`](parameters-staging.json) 固定使用 `16.15` 與 subnet choice `a`；
實際建立前仍須重新執行上述唯讀檢查。

## 驗證與初次建立（CREATE）change set

先執行本機 schema lint、安全審查與 AWS template validation；`cfn-lint` 未安裝時必須先取得允許，且
只從 PyPI 安裝 `cfn-lint>=1,<2`。CloudFormation Guard 目前無法直接解析 template 使用的 YAML short-form
intrinsics，因此要先用 `cfn-lint` 的 parser 正規化成暫存 JSON。`CFN_LINT_PYTHON` 必須指向安裝
`cfn-lint` 的同一個 Python 環境；正規化檔只放 `/private/tmp`，不得加入 Git。

```bash
cfn-lint -r ap-southeast-1 -- deploy/aws/infrastructure.yaml

CFN_LINT_PYTHON=/path/to/cfn-lint-environment/bin/python
"$CFN_LINT_PYTHON" -c \
  "import json,sys; from cfnlint.decode import decode; json.dump(decode('deploy/aws/infrastructure.yaml')[0], sys.stdout)" \
  > /private/tmp/report-research-infrastructure.json

cfn-guard validate \
  --rules deploy/aws/security.guard \
  --data /private/tmp/report-research-infrastructure.json \
  --type CFNTemplate --show-summary all

aws cloudformation validate-template \
  --profile report-research --region ap-southeast-1 \
  --template-body file://deploy/aws/infrastructure.yaml
```

建立 change set 只產生計畫，不會建立或修改資源。初次建立參數使用已由唯讀檢查固定的檔案：

```bash
aws cloudformation create-change-set \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --change-set-name pre-deploy-validation-<TIMESTAMP> \
  --change-set-type CREATE \
  --template-body file://deploy/aws/infrastructure.yaml \
  --capabilities CAPABILITY_IAM \
  --parameters file://deploy/aws/parameters-staging.json \
  --tags \
    Key=Project,Value=report-research \
    Key=Environment,Value=staging
```

等待 change set 成功後，使用 `describe-change-set` 檢查所有變更，並以 `describe-events --change-set-name`
讀取 validation events。不得改用 legacy `describe-stack-events` 代替 pre-deployment validation。

只有在 change set 無 FAIL、警告已接受，且使用者明確批准後才能執行：

```bash
aws cloudformation execute-change-set \
  --profile report-research --region ap-southeast-1 \
  --change-set-name <CHANGE_SET_ARN>

aws cloudformation wait stack-create-complete \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging
```

不要使用 Express mode；RDS 與 EC2 必須完全 ready 才進入驗收。

## 既有 stack 更新（UPDATE）change set

既有 `report-research-staging` 必須使用 `UPDATE`，先完成上節的 lint、Guard 與 template validation，
並重新核對帳號與 stack 狀態。初次建立用的 `parameters-staging.json` 只含兩個參數，不可直接套用於更新；
下例逐一對範本現有六個參數指定 `UsePreviousValue=true`，避免省略參數而套入範本預設值，或重設既有機型、
AZ 選擇與 PostgreSQL 版本。不得同時指定該參數的 `ParameterValue`。
參考 [AWS create-change-set 參數規則](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/create-change-set.html)。

`LatestAmiId` 是 SSM parameter type：`UsePreviousValue=true` 保留的是 Parameter Store key，
CloudFormation 建立 change set 時仍會解析其當時最新值，並未固定原本的 AMI ID。先比對現有 stack 與
change set 的 `Parameters`（含 `ResolvedValue`），再檢查 `Ec2Instance.ImageId` 與 replacement；
若 AMI 變動造成未預期的 EC2 替換，立即停止，不執行此 change set。
參考 [AWS SSM parameter 更新語意](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/cloudformation-supplied-parameter-types.html)。

先唯讀取得現有狀態，再建立計畫（將 `UPDATE_CHANGE_SET_NAME` 的時間戳換成此次唯一值）：

```bash
aws cloudformation describe-stacks \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --output json

UPDATE_CHANGE_SET_NAME=staging-update-YYYYMMDD-HHMMSS
aws cloudformation create-change-set \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --change-set-name "$UPDATE_CHANGE_SET_NAME" \
  --change-set-type UPDATE \
  --template-body file://deploy/aws/infrastructure.yaml \
  --capabilities CAPABILITY_IAM \
  --parameters \
    ParameterKey=Environment,UsePreviousValue=true \
    ParameterKey=Ec2InstanceType,UsePreviousValue=true \
    ParameterKey=Ec2SubnetChoice,UsePreviousValue=true \
    ParameterKey=DbInstanceClass,UsePreviousValue=true \
    ParameterKey=DbEngineVersion,UsePreviousValue=true \
    ParameterKey=LatestAmiId,UsePreviousValue=true

aws cloudformation wait change-set-create-complete \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --change-set-name "$UPDATE_CHANGE_SET_NAME"

aws cloudformation describe-change-set \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --change-set-name "$UPDATE_CHANGE_SET_NAME" \
  --include-property-values --output json

aws cloudformation describe-events \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --change-set-name "$UPDATE_CHANGE_SET_NAME" \
  --output json
```

逐項檢查完整 `Changes`、`Action`、`Replacement` 與屬性前後值，並確認 validation events 無 FAIL；
遇到未預期的新增、刪除、替換（包括 `Replacement=Conditional`）或權限／網路放寬，立即停止。
wait 非零時先讀取 `describe-change-set` 的 `StatusReason` 與 validation events；無變更也不可繼續 execute。
參考 [AWS describe-change-set](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/describe-change-set.html)
與 [AWS describe-events](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/describe-events.html)。

只有計畫符合預期、警告已接受，且使用者明確批准該計畫後，才另行執行以下命令；不得把此區塊與前面的
計畫建立／檢視串成自動執行。更新完成要等待 `stack-update-complete`，再重新執行下節驗收與保護檢查：

```bash
aws cloudformation execute-change-set \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --change-set-name "$UPDATE_CHANGE_SET_NAME"

aws cloudformation wait stack-update-complete \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging
```

範本若增減參數，必須同步更新此清單；新參數沒有 previous value 時，要先明確選定並審查其值。
刻意調整既有參數時亦須另行審查，不可直接沿用此保留原值的更新範例。

## 驗收與保護

等待 EC2 在 SSM 中為 `Online`，再執行：

```bash
AWS_PROFILE=report-research AWS_REGION=ap-southeast-1 \
  bash deploy/aws/verify-rds-connectivity.sh
```

腳本會從 stack outputs 取得 instance ID、RDS endpoint 與 secret ARN，在 EC2 內安全取得 secret，使用
AWS RDS CA bundle 與 `sslmode=verify-full` 連線，建立 `vector`／`pg_trgm`，並驗證：

- database 為 `research`
- TLS 連線成立且 `rds.force_ssl=on`
- pgvector 版本至少 0.8
- EC2 沒有專案 checkout、Docker、nginx 或 cloudflared（只適用初次建立；應用部署後這項必然不成立）

所有驗收通過後啟用 stack termination protection：

```bash
aws cloudformation update-termination-protection \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --enable-termination-protection
```

最後再次確認 RDS `DeletionProtection=true`、`PubliclyAccessible=false`、`MultiAZ=false`，EC2 SG 的入站只有
`CloudflareOriginPrefixList` 的 TCP/443，
且 RDS SG 的 TCP/5432 唯一來源為 EC2 SG。資源驗收後保持運行並持續計費。

## 刪除與成本警告

Stack termination protection、RDS deletion protection 與 CloudFormation `Retain` 是三層獨立保護。
即使未來解除 stack 保護並刪除 stack，RDS 仍會脫離 CloudFormation 後繼續存在與計費。任何拆除都必須
先識別保留資源並另行取得明確批准。

2026-10-01 依 AWS Price List API、每月 730 小時計算的新加坡 On-Demand 基準如下：

| 項目 | 單價 | 每月 |
|---|---:|---:|
| EC2 `c7i.xlarge` | US$0.2058／小時 | US$150.23 |
| EC2 gp3 100 GB | US$0.096／GB-month | US$9.60 |
| RDS `db.m7g.large` Single-AZ | US$0.234／小時 | US$170.82 |
| RDS gp3 100 GB | US$0.138／GB-month | US$13.80 |
| 公有 IPv4 | US$0.005／小時 | US$3.65 |
| RDS managed secret | US$0.40／secret-month | US$0.40 |
| **合計** |  | **US$348.50／月** |

不含資料流量、Secrets Manager 少量 API request、超出 100 GB 的自動擴充、額外快照、日誌、監控與稅金；
正式建立前仍須重新核價。

Elastic IP 取代原本的 auto-assigned public IPv4 後，持續綁定並使用中的公有 IPv4 數量仍為一個，所以上表
US$3.65／月與合計 US$348.50／月不變。EIP 若解除綁定或 stack 外另行保留，仍會持續產生 public IPv4 費用；
不再使用時應由 CloudFormation stack 移除並釋放，避免孤兒資源持續計費。
