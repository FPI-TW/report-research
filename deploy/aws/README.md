# AWS 基礎設施（正式環境）

> 這套 EC2 與 RDS 是**正式環境**（`research.tingfong.com`）。stack、RDS、參數檔、標籤與 IAM 政策檔名裡的 staging 是建置當時的歷史命名，沿用不改；環境角色見 `AGENTS.md`「環境角色與資料安全」。

此目錄以 CloudFormation 管理 `report-research-staging` 這個 stack：VPC、子網與路由、安全群組、EC2 與 Elastic IP、EC2 IAM role／instance profile、RDS subnet group、parameter group 與 PostgreSQL 執行個體。它**不會** clone/build/run 專案、不套用 schema、不搬資料，也不建立 Web、批次、nginx 或排程服務；那些在 stack 之外部署（見「應用層」），改它們不會因 stack 更新而自動完成。Cloudflare DNS 由 Cloudflare 控制台管理（本 AWS 帳號沒有 Route 53 hosted zone）。

| 檔案 | 用途 |
|---|---|
| `infrastructure.yaml` | CloudFormation 資源、參數、輸出，以及 EC2 的基礎 bootstrap（只裝連線驗收工具與 RDS CA bundle，不裝應用） |
| `parameters-staging.json` | 初次 `CREATE` 用的參數；只有兩個覆寫值（`DbEngineVersion=16.15`、`Ec2SubnetChoice=a`），**不能**直接當成既有 stack 的更新參數 |
| `security.guard` | 部署前在本機執行的 CloudFormation Guard 規則；不是 AWS 資源設定，也不會自行套用 |
| `verify-rds-connectivity.sh` | 初次建立後，經 SSM 驗證 EC2 到 RDS 的 TLS 連線、設定與擴充功能；會在資料庫執行 `CREATE EXTENSION IF NOT EXISTS` |
| `staging-state-read-policy.json` | 給**部署操作身分**（不是 EC2 role）的補充唯讀 IAM 權限，建立 drift-aware change set 時要用（見「已知 drift 與待確認事項」） |
| `report-mark-sync.timer.d/offset.conf` | EC2 專屬的 sync timer drop-in（見「應用層」的研報導入） |

## 固定邊界

- AWS account：`607063196781`；Region：`ap-southeast-1`
- Stack：`report-research-staging`；stack termination protection 與 RDS deletion protection 均啟用
- EC2：`c7i.xlarge`（範本允許升到 `c7i.2xlarge`）、Ubuntu 24.04 LTS amd64、100 GiB encrypted gp3、IMDSv2、管理只走 SSM（無 SSH、無 key pair）
- 入站：只有 TCP/443，來源限定 Cloudflare IPv4 範圍（managed prefix list `CloudflareOriginPrefixList`）；不開 SSH、TCP/80、TCP/8097、RDS/5432 或 `0.0.0.0/0`
- 公網位址：同一個 stack 管理並綁定 EC2 的 Elastic IP；`research.tingfong.com` 的橘雲 A record 指向它（值以 stack output `Ec2ElasticIpAddress` 為準；2026-10-01 建立時是 `13.215.135.59`）
- RDS：PostgreSQL 16、`db.m7g.large`（範本允許升到 `db.m7g.xlarge`）、Single-AZ、private、100 GiB encrypted gp3、上限 500 GiB、自動備份保留 7 天、`rds.force_ssl=1`、資料庫名 `research`
- VPC：`10.20.0.0/16`；EC2 在 public subnet，RDS 在跨兩 AZ 的 private subnet group，沒有 NAT

Stack outputs 是資源 ID、RDS endpoint 與 managed secret ARN 的權威來源；不要把 secret value 寫入文件或命令列。

EC2 instance role 可以讀取這一個 RDS managed master secret（`report_admin`），因此具備執行破壞性 SQL 的能力。應用一律用另建的最小權限帳號 `report_mark`（不是 superuser，也不是 `rds_superuser` 成員），不沿用 master credential。

## 對外入口：A record＋橘雲直連

訪客 → Cloudflare（訪客端 TLS）→ EIP:443 → EC2 上的 nginx（來源站 TLS）→ `127.0.0.1:8097`。不走 Tunnel（辦公室主機那條才是 Tunnel，見 `docs/EXTERNAL_ACCESS.md`）。

| 層 | 設定 | 管理位置 |
|---|---|---|
| Security Group | 只放行 `CloudflareOriginPrefixList` 的 TCP/443 | 本範本（`Ec2SecurityGroup`、`security.guard` 的 `ec2_inbound_is_only_https_from_cloudflare`） |
| 來源站憑證 | Cloudflare Origin CA，SAN `research.tingfong.com`，效期至 2041-09-28 | EC2 的 `/etc/ssl/report-mark/`（`origin.pem`、`origin.key`；私鑰 0600 root），不進 repo |
| nginx | apt 安裝的 nginx；非此網域的 TLS 握手一律拒絕；只信任 Cloudflare 範圍的 `CF-Connecting-IP` | `deploy/nginx-origin.conf`，裝到 `/etc/nginx/sites-available/report-mark` |
| Cloudflare | SSL/TLS 模式 **Full (strict)**；HTTP 要在 Cloudflare 端轉成 HTTPS（來源站不聽 80） | Cloudflare 控制台 |

刻意的取捨與限制：

- **橘雲不能關**：Origin CA 憑證只有 Cloudflare 信任，改成灰雲時瀏覽器會報憑證錯誤。
- **`proxy_pass` 一定走 `127.0.0.1`**：App 靠對端 127.0.0.1 信任這個代理（`REPORT_MARK_TRUSTED_PROXY_CIDRS` 的預設值），這個入口不用邊緣共享祕密。
- **SG 的 `GroupDescription` 刻意不改**（字面上仍寫 Ingress-free）：改它會替換整個 SG，連帶影響 RDS SG 的來源參照。實際規則以 `SecurityGroupIngress` 與 Guard 規則為準。
- **只開 IPv4**：VPC 沒有 IPv6、網域沒有來源 AAAA record，Cloudflare 以 IPv4 回源。
- **prefix list 的 `MaxEntries` 是 20**：SG 規則引用 prefix list 時，以 `MaxEntries` 計入 SG 規則配額。
- **Cloudflare IP 範圍會變**：以 <https://www.cloudflare.com/ips-v4> 為準。有變動時，本範本的 prefix list 與 nginx 的 `set_real_ip_from` 要一起改；只改 SG 的話，限流與登入失敗追蹤會算在 Cloudflare 節點頭上。
- 任何 Cloudflare 客戶都能把自己的網域指向這個 EIP、從 Cloudflare 範圍連進來，nginx 會以拒絕握手擋掉不是本網域的請求。要更嚴格可以另開 Cloudflare Authenticated Origin Pulls（mTLS），目前未啟用。
- DNS 解析成功不能當成服務可用性驗收：橘雲回的是 Cloudflare IP，驗收一律從外部打 `https://research.tingfong.com/healthz`。

## 應用層（stack 之外）

部署目錄是 `/home/ubuntu/report-mark`，以 `ubuntu` 執行；主機上不用容器跑任何東西（DB 是 RDS、nginx 是 apt 的 systemd 服務）。部署與維運程序在 `docs/production_resilience.md`，與辦公室主機的差異整理在 `README.md`「非辦公室主機」一節與 `deploy/ops/services.staging.toml` 檔頭；主機現況與待確認事項記在 `docs/DEPLOYMENT_STATUS.md`。重建一台時依序要補的東西：

- **DB 連線**：repo 根 `.env` 與 `/etc/default/report-mark-sync` 兩處都設 `REPORT_MARK_DB_URL=postgresql+asyncpg://…@<RDS 端點>:5432/research?ssl=verify-full` 與 `PGSSLROOTCERT=/opt/report-research-infra/rds-global-bundle.pem`（bootstrap 已下載到這個路徑；批次不讀 repo 根 `.env`，漏設會靜默連回 `localhost:5436`）。
- **schema**：`REPORT_MARK_MIGRATE_CONFIRM=<host:port/db> uv run alembic upgrade head`。`make schema`、`make db-backup`、`make ingest-lowio` 走 `docker exec`，在 RDS 上不適用；migration 前的備份是 RDS 手動快照。
- **unit**：一律 `sudo deploy/install_units.sh --user ubuntu --root /home/ubuntu/report-mark <unit…>`（安裝時代換使用者、HOME 與 repo 路徑；只複製與 `daemon-reload`，不 enable），不要 `sudo cp`。這台跑的是 `deploy/ops/services.staging.toml` 列出的那些：常駐的 `report-mark-web`，以及 `sync`、`health`／`incident`、`edge-health`／`edge-incident`、`feed-health`／`feed-incident`、`freshness`、`audit`、`r2-reconcile`、`schema-check`、`db-snapshot`、`analytics-rollup`、`security-health`／`security-incident`、`security-retention` 各自的 timer。`/etc/default/report-mark-sync` 要設 `SCHEMA_CHECK_MODE=version`（app 帳號沒有 CREATEDB）。
- **這台不裝的 unit**：結構上不適用的有 `report-mark-backup`（落點是 NAS；這台只有 RDS 自動備份）、`report-mark-linebot-*`、`report-mark-metrics`（讀 docker cgroup）、`report-mark-container-*`（沒有容器）、`report-mark-backfill`、`report-mark-beacon`（發布端在辦公室主機）。`deploy/systemd/` 裡其餘沒列在 catalog 的 unit 目前也沒裝，完整清單與連帶後果（例如沒有監控匯入器時管理後台不列「主機」「排程工作」「事件」三頁）見 `README.md`「非辦公室主機」與 `docs/DEPLOYMENT_STATUS.md`。
- **維運代理**：`report-mark-ops-agent-staging.service`，程式與 catalog 裝在 root 擁有的 `/opt/report-mark-ops/`，web 的 `.env` 設 `OPS_AGENT_ENVIRONMENT=staging`；步驟見 `docs/production_resilience.md`「維運代理」的 EC2 一節。
- **LLM**：只有 DeepSeek（`LLM_PROVIDER=deepseek`）。金鑰與 R2 bucket 都和辦公室主機共用：在任一台手動多跑 LLM 批次或刪改 R2 物件，另一台也受影響。金鑰落點與輪替見 `docs/production_resilience.md`「DeepSeek 金鑰落點與輪替」。
- **來源監看**：這台的新研報全靠辦公室主機推上來，那台停機或來源段壞掉時這台自己的 sync 照樣 rc=0。web 的 `.env` 設 `HOST_BEACON_WATCH=office`（與辦公室主機的 `HOST_BEACON_NAME` 同名），並裝 `report-mark-feed-health`、`report-mark-feed-incident`；狀態與處置見 `docs/production_resilience.md`「來源主機信標」。

### 研報導入：R2 inbox 與同步錯開

EC2 碰不到 NAS，`/etc/default/report-mark-sync` 設 `SYNC_SOURCE=r2-inbox`，每輪以 `scripts/r2_inbox.py pull` 從 R2 的 `inbox/` 拉新研報；辦公室主機的 sync 設 `SYNC_INBOX_PUSH=1`，每輪 rsync 完把新檔推上去（沒設的話 EC2 每輪拉到 0 份、sync 照樣 rc=0，要靠來源主機信標才看得到）。辦公室主機整點觸發、rsync 完才推，所以 EC2 的同步錯開到每 3 小時的 :20（00:20、03:20…），drop-in 是 `deploy/aws/report-mark-sync.timer.d/offset.conf`：

```bash
sudo install -d -m 0755 /etc/systemd/system/report-mark-sync.timer.d
sudo install -m 0644 deploy/aws/report-mark-sync.timer.d/offset.conf /etc/systemd/system/report-mark-sync.timer.d/
sudo systemctl daemon-reload
systemctl list-timers report-mark-sync.timer   # NEXT 應落在 :20（被觸發的同步還在跑時 NEXT 暫為空白，跑完才排）
```

漏推的檔在辦公室主機用 `scripts/r2_inbox.py push --delta <清單>` 補推（清單是 `研報自動匯入/` 底下的相對路徑，一行一檔；批次不讀 repo 根 `.env`，R2 憑證從 `/etc/default/report-mark-sync` 帶入）。EC2 只拉本地沒有或大小不同的檔，多推不會重複匯入。

## 前置與唯讀檢查

AWS CLI 使用獨立 profile，所有命令都明確指定 Region（下文以 `report-research` 為 profile 名；換成你登入帳號 `607063196781` 所用的 profile）：

```bash
aws login --profile report-research
aws sts get-caller-identity --profile report-research --region ap-southeast-1
```

帳號不是 `607063196781` 時立即停止。初次建立前另外確認 EC2 offering、Ubuntu AMI parameter、PostgreSQL 16 versions 與 RDS class 組合：

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

選用目標 Region 中最高且 `db.m7g.large` 可訂購的 PostgreSQL 16 minor，寫進 [`parameters-staging.json`](parameters-staging.json)（目前固定 `16.15` 與 subnet choice `a`）。若 `c7i.xlarge` 只在第二個 stack AZ 供應，部署參數使用 `Ec2SubnetChoice=b`。

## 驗證與初次建立（CREATE）change set

每次變更（CREATE 與 UPDATE 都一樣）先執行本機 schema lint、安全審查與 AWS template validation；`cfn-lint` 未安裝時必須先取得允許，且只從 PyPI 安裝 `cfn-lint>=1,<2`。CloudFormation Guard 無法直接解析 template 使用的 YAML short-form intrinsics，因此要先用 `cfn-lint` 的 parser 正規化成暫存 JSON。`CFN_LINT_PYTHON` 必須指向安裝 `cfn-lint` 的同一個 Python 環境；正規化檔只放暫存目錄，不得加入 Git。

```bash
cfn-lint -r ap-southeast-1 -- deploy/aws/infrastructure.yaml

CFN_LINT_PYTHON=/path/to/cfn-lint-environment/bin/python
CFN_JSON="${TMPDIR:-/tmp}/report-research-infrastructure.json"
"$CFN_LINT_PYTHON" -c \
  "import json,sys; from cfnlint.decode import decode; json.dump(decode('deploy/aws/infrastructure.yaml')[0], sys.stdout)" \
  > "$CFN_JSON"

cfn-guard validate \
  --rules deploy/aws/security.guard \
  --data "$CFN_JSON" \
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

等待 change set 成功後，使用 `describe-change-set` 檢查所有變更，並以 `describe-events --change-set-name` 讀取 validation events。不得改用 legacy `describe-stack-events` 代替 pre-deployment validation。

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

既有 `report-research-staging` 必須使用 `UPDATE`，先完成上節的 lint、Guard 與 template validation，並重新核對帳號與 stack 狀態。初次建立用的 `parameters-staging.json` 只含兩個參數，不可直接套用於更新；下例逐一對範本現有六個參數指定 `UsePreviousValue=true`，避免省略參數而套入範本預設值，或重設既有機型、AZ 選擇與 PostgreSQL 版本。不得同時指定該參數的 `ParameterValue`。
參考 [AWS create-change-set 參數規則](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/create-change-set.html)。

`LatestAmiId` 是 SSM parameter type：`UsePreviousValue=true` 保留的是 Parameter Store key，CloudFormation 建立 change set 時仍會解析其當時最新值，並未固定原本的 AMI ID。先比對現有 stack 與 change set 的 `Parameters`（含 `ResolvedValue`），再檢查 `Ec2Instance.ImageId` 與 replacement；若 AMI 變動造成未預期的 EC2 替換，立即停止，不執行此 change set。
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

逐項檢查完整 `Changes`、`Action`、`Replacement` 與屬性前後值，並確認 validation events 無 FAIL；遇到未預期的新增、刪除、替換（包括 `Replacement=Conditional`）或權限／網路放寬，立即停止。wait 非零時先讀取 `describe-change-set` 的 `StatusReason` 與 validation events；無變更也不可繼續 execute。
參考 [AWS describe-change-set](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/describe-change-set.html)
與 [AWS describe-events](https://docs.aws.amazon.com/cli/latest/reference/cloudformation/describe-events.html)。

只有計畫符合預期、警告已接受，且使用者明確批准該計畫後，才另行執行以下命令；不得把此區塊與前面的計畫建立／檢視串成自動執行。更新完成要等待 `stack-update-complete`，再重新執行下節的保護檢查：

```bash
aws cloudformation execute-change-set \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --change-set-name "$UPDATE_CHANGE_SET_NAME"

aws cloudformation wait stack-update-complete \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging
```

範本若增減參數，必須同步更新此清單；新參數沒有 previous value 時，要先明確選定並審查其值。刻意調整既有參數時亦須另行審查，不可直接沿用此保留原值的更新範例。

## 已知 drift 與待確認事項

以下是 2026-10-05 留下、之後沒有在 repo 這側重新查證的狀態；動 stack 或 Cloudflare 之前先核對。

- **RDS 備份保留期的 drift**：實際值是 7 天（2026-10-05 以 RDS `ModifyDBInstance` 原地調整，未替換執行個體），repo 的範本也是 7，但當時**已部署的範本仍記錄 1 天**。一般的 UPDATE change set 會把 `Database` 的 `BackupRetentionPeriod` 1 → 7 標成 `Replacement=Conditional`，照上節的規則要停止、不執行。正確做法是建立 drift-aware change set：在上節的 `create-change-set` 加 `--deployment-mode REVERT_DRIFT`，核對完整 actual／previous／desired state；預期只有 `Database` 的保留期對齊與 `Ec2Instance` 的 Metadata 修改（`Replacement=False`，不得動 `ImageId` 或其他 Properties），不得替換 EC2／RDS，也不得修改 IAM 權限。
  參考 [drift-aware change sets](https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/drift-aware-change-sets.html)。
- **drift-aware change set 需要讀 IAM**：`PowerUserAccess` 缺 `iam:GetRole`，計畫會在讀取 EC2 role 時失敗。先由 IAM 管理者把 [staging-state-read-policy.json](staging-state-read-policy.json) 加到部署操作身分（只開放讀取現有的 EC2 role／instance profile，不含 IAM 寫入或 PassRole），重新佈建 SSO permission set 後取得新 session。
- **HTTP → HTTPS 轉址**：2026-10-05 驗收時 `http://research.tingfong.com/` 回 522（來源站不聽 80）。要在 Cloudflare 開 Always Use HTTPS，或只對此 hostname 加 Single Redirect（`http://research.tingfong.com/*` → `https://research.tingfong.com/${1}`、301、保留 query string）。
- **R2 bucket 的 CORS**：PDF 檢視器以 presigned URL 直接向 R2 發 Range GET，bucket 沒有允許 `https://research.tingfong.com` 的 CORS 規則時瀏覽器會攔下（伺服器端零錯誤）。應用的 R2 憑證讀不到也改不了 bucket 設定：到 Cloudflare R2 的 bucket → Settings → CORS Policy，把 [r2-cors-research.json](../r2-cors-research.json) 的規則**加入現有規則**（這個檔是可追加的單一網站規則，不能拿來取代整份 policy，bucket 與辦公室主機共用）。設定後重新取得 presigned URL，驗證 GET／OPTIONS 的 `Access-Control-Allow-Origin`、Range 與 exposed headers。
- 這個帳號沒有 CloudTrail trail（2026-10-05 `describe-trails` 為空）。

## 驗收與保護

`verify-rds-connectivity.sh` 是**初次建立**的驗收：等 EC2 在 SSM 中為 `Online` 再執行。

```bash
AWS_PROFILE=report-research AWS_REGION=ap-southeast-1 \
  bash deploy/aws/verify-rds-connectivity.sh
```

腳本從 stack outputs 取得 instance ID、RDS endpoint 與 secret ARN，在 EC2 內取得 secret，使用 AWS RDS CA bundle 與 `sslmode=verify-full` 連線，建立 `vector`／`pg_trgm`，並驗證：

- parameter group 為 `in-sync` 且 `rds.force_ssl=1`，非 TLS 連線被拒絕
- 連線的 database 為 `research`、走 TLS
- pgvector 版本至少 0.8
- 主機還是乾淨的：`/home/ubuntu/report-research` 不存在，找不到 `docker`、`nginx`、`cloudflared`

最後一項在應用部署後必然不成立（現在這台有 nginx 與 docker 指令），所以在既有主機上執行會以失敗收場，而前面的 `CREATE EXTENSION` 已經跑過；既有主機要驗連線，改用應用自己的 `/healthz`。

所有驗收通過後啟用 stack termination protection：

```bash
aws cloudformation update-termination-protection \
  --profile report-research --region ap-southeast-1 \
  --stack-name report-research-staging \
  --enable-termination-protection
```

每次建立或更新後確認 RDS `DeletionProtection=true`、`PubliclyAccessible=false`、`MultiAZ=false`，EC2 SG 的入站只有 `CloudflareOriginPrefixList` 的 TCP/443，且 RDS SG 的 TCP/5432 唯一來源為 EC2 SG。

## 刪除與成本警告

Stack termination protection、RDS deletion protection 與 CloudFormation `Retain` 是三層獨立保護。即使未來解除 stack 保護並刪除 stack，RDS 仍會脫離 CloudFormation 後繼續存在與計費。任何拆除都必須先識別保留資源並另行取得明確批准。

2026-10-01 依 AWS Price List API、每月 730 小時計算的新加坡 On-Demand 基準如下：

| 項目 | 單價 | 每月 |
|---|---:|---:|
| EC2 `c7i.xlarge` | US$0.2058／小時 | US$150.23 |
| EC2 gp3 100 GB | US$0.096／GB-month | US$9.60 |
| RDS `db.m7g.large` Single-AZ | US$0.234／小時 | US$170.82 |
| RDS gp3 100 GB | US$0.138／GB-month | US$13.80 |
| 公有 IPv4（Elastic IP） | US$0.005／小時 | US$3.65 |
| RDS managed secret | US$0.40／secret-month | US$0.40 |
| **合計** |  | **US$348.50／月** |

不含資料流量、Secrets Manager 少量 API request、超出 100 GB 的自動擴充、額外快照、日誌、監控與稅金。Elastic IP 解除綁定或留在 stack 外仍會持續計費；不再使用時由 CloudFormation stack 移除並釋放。
