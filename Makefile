# 廷豐研報 運作流程編排
# 流程：deps/db/schema → sample → extract → worklist → [Claude 標註] → ingest → serve
# 詳見 docs/WORKFLOW.md

PORT ?= 8097
DB_CONTAINER ?= report-mark-postgres
DB_NAME ?= research
DB_PORT ?= 5436
Q ?= AI 伺服器散熱需求
MARKET ?=
EDGE_COMPOSE ?= deploy/docker-compose.yml
# eval-compare 的預設容忍值；BASE/CAND 刻意沒有預設，兩份結果檔必須由呼叫者指名
# （三套評測的形狀不同，猜錯就是拿 RAGAS 去比檢索）。
TOL ?= 0.03

# Docker 二進位自動偵測：可連到 daemon 的 docker 優先；否則若有 docker.exe（WSL+Docker Desktop）就用它；
# 都沒有時退回 docker，讓指令自己回報真正的 daemon 錯誤（而非 docker.exe: command not found）。
DOCKER := $(shell if docker info >/dev/null 2>&1; then echo docker; elif command -v docker.exe >/dev/null 2>&1; then echo docker.exe; else echo docker; fi)
COMPOSE := $(DOCKER) compose

.PHONY: help deps db schema setup sample extract worklist prep tag-info \
        ingest ingest-lowio restore-durability align serve search build-web \
        stats reset-db clean-data pipeline signals takeaways titles brief \
        eval-compare \
        up-edge down-edge edge-logs edge-reload \
        sync-once db-backup freshness

help:  ## 顯示可用指令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ───── 基礎建設 ─────
deps:  ## 安裝相依套件
	uv sync

db:  ## 起 pgvector 容器（已存在則啟動）
	$(DOCKER) start $(DB_CONTAINER) 2>/dev/null || \
	$(DOCKER) run -d --name $(DB_CONTAINER) \
	  --restart unless-stopped \
	  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=$(DB_NAME) \
	  -p $(DB_PORT):5432 -v report-mark-pgdata:/var/lib/postgresql/data \
	  pgvector/pgvector:pg16
	@# 既有容器補上重啟策略（--restart 只在 run 時生效；docker update 免重建）。
	@# 少了它，重開機後 nginx/cloudflared/web 都會自己回來、只有 DB 不會 →
	@# 站台開得起來、登入還會成功（登入路徑不碰 DB）、每個查詢 500。
	$(DOCKER) update --restart unless-stopped $(DB_CONTAINER) >/dev/null

schema: db  ## 套用 DB schema（vector 擴充 + 表 + HNSW 索引）
	@for i in $$(seq 1 30); do $(DOCKER) exec $(DB_CONTAINER) pg_isready -U postgres >/dev/null 2>&1 && break; sleep 1; done
	$(DOCKER) exec -i $(DB_CONTAINER) psql -U postgres -d $(DB_NAME) < db/schema.sql

setup: deps schema  ## 一次完成基礎建設（deps + db + schema）

# ───── 管線（Claude 標註前）─────
sample:  ## ① 分層抽樣 ~80 檔
	uv run python scripts/select_sample.py

extract:  ## ② 抽文字 + 檔名 metadata
	uv run python scripts/extract_batch.py

worklist:  ## ③ 產工作清單 + 分批（resume-aware）
	uv run python scripts/make_worklist.py

prep: sample extract worklist  ## ①②③ 一次跑完（停在 Claude 標註前）

tag-info:  ## ④ 印出 Claude 標註步驟說明
	@echo "④ 市場標註由 Claude Code 執行（非 make）："
	@echo "   在 Claude Code 中以 Workflow 工具執行 workflows/tag_reports.workflow.js"
	@echo "   產出 data/tags/<file_hash>.json，完成後執行：make ingest"

# ───── 管線（Claude 標註後）─────
ingest:  ## ⑤ 切塊 + BGE-M3 嵌入 + upsert pgvector（首次下載模型 ~2-4GB）
	uv run python scripts/run_ingest.py

ingest-lowio:  ## ⑤b 離線全量導入：關 fsync 降 I/O（僅限「沒對外服務」時用；自動還原）
	bash scripts/ingest_lowio.sh

restore-durability:  ## 還原 Postgres 耐久性設定（ingest-lowio 異常中斷時的保險）
	$(DOCKER) exec $(DB_CONTAINER) psql -U postgres -d $(DB_NAME) \
	  -c "ALTER SYSTEM RESET fsync;" \
	  -c "ALTER SYSTEM RESET full_page_writes;" \
	  -c "ALTER SYSTEM RESET synchronous_commit;" \
	  -c "SELECT pg_reload_conf();"

align:  ## 把中文標籤重映射為 findb 代碼（一次性、冪等）
	uv run python scripts/align_findb_markets.py

# ───── Claude CLI 批次（互斥）─────
# 下面三支與 tag_all_cli.py／sync_new_reports.py 共五支都 spawn claude CLI，併發互搶
# 會讓擷取被大量誤標 rejected（不是資料壞、也不是模型壞，是 CLI 被搶）。互斥由
# scripts/_claude_lock.py 的 flock 跨進程鎖強制，不再只靠這行註解：撞車時後啟動者
# 會印出持有者（腳本名／pid／起始時間）並以 rc=75 結束，不會產出壞資料。
# 排程（report-mark-sync.timer，每 3 小時）也走同一把鎖，所以手動開跑前不必再去
# 確認 timer 有沒有在跑——真撞上就是不跑，不是跑壞。
summaries:  ## 為缺摘要的報告生成 2-3 句中文摘要（Sonnet，冪等可續傳，補 summary IS NULL）
	uv run python scripts/generate_summaries.py

# 勿與 make summaries / signals / takeaways 同時跑：多批次併發搶 claude CLI 會大量誤判失敗。
titles:  ## 產生顯示標題取代檔名（Sonnet，冪等可續傳，補 title IS NULL；新→舊優先）
	uv run python scripts/generate_titles.py

signals:  ## 觀點雷達訊號擷取（子集先行，冪等可續傳；先 make schema）→ research.report_signal
	uv run python scripts/extract_signals.py

takeaways:  ## 閱讀頁重點摘錄擷取（近 90 天，冪等可續傳；先 make schema）→ research.report_takeaway
	uv run python scripts/extract_takeaways.py

brief:  ## 每日簡報（一天一列；當日已有或未到 --after-hour 即 no-op）→ research.report_brief
	uv run python scripts/generate_brief.py

# ───── 檢索 ─────
# 刻意**不**讓 serve 相依 build-web：serve 是生產 systemd 的 ExecStart，讓它跑
# `npm ci` 等於把一次 npm registry 不通變成「站台起不來」。前端改動要生效請自己
# 先跑 `make build-web`（或 cd frontend && npm run build）再 restart。
build-web:  ## 建置 SPA → frontend/dist（前端改動後必跑；npm run build 內含 tsc）
	cd frontend && npm ci && npm run build

serve:  ## 啟動查詢網頁（BGE-M3 常駐）→ http://localhost:$(PORT)
	uv run uvicorn web.server:app --host 0.0.0.0 --port $(PORT)

# 開發用：--reload ＋ 跳過模型暖機。生產一律用上面那個 target。
# 為什麼需要它：`serve` 沒有 --reload（那是對的），而 BGE-M3 ＋ reranker 冷載入
# 合計約一分鐘，所以改一行 Python 就要再等一次——那正是「直接在生產機上改檔然後
# 懶得重啟」的溫床（docs/production_resilience.md 已記錄過一次 unit 漂移）。
# SKIP_WARMUP=1 讓首個查詢 lazy 載入：慢但可用，改路由／改文案時完全不需要模型。
# --reload 會開子行程，assert_single_worker() 刻意不用 parent_process() 偵測，
# 所以這裡不會誤判成多 worker。
serve-dev:  ## 開發用啟動（--reload ＋ 跳過模型暖機；勿用於生產）
	SKIP_WARMUP=1 uv run uvicorn web.server:app --host 127.0.0.1 --port $(PORT) --reload

search:  ## CLI 檢索（用法：make search Q="查詢" MARKET=TW）
	uv run python scripts/search.py "$(Q)" $(if $(MARKET),--market $(MARKET),)

stats:  ## 看 DB 市場分佈與筆數
	@$(DOCKER) exec $(DB_CONTAINER) psql -U postgres -d $(DB_NAME) \
	  -c "select market, count(*) reports from research.research_report group by market order by 2 desc;" \
	  -c "select count(*) chunks from research.report_chunk;"

# ───── 離線評測 ─────
# 刻意**不**接進 CI：跑一輪 RAGAS 會 spawn claude CLI，與每 3 小時的
# report-mark-sync.timer 搶同一個 CLI（那把 flock 刻意不含 llm.py，而 eval 走 llm.py）。
# 這是本機／手動工具：改檢索或生成品質時前後各跑一次，再用 eval-compare 比。
eval-compare:  ## 比較兩份評測結果（BASE=… CAND=… [TOL=0.03]；劣化即非零退出）
	@test -n "$(BASE)" && test -n "$(CAND)" || { \
	  echo "用法：make eval-compare BASE=<基準線.json> CAND=<待比較.json> [TOL=0.03]"; \
	  echo "  兩份必須是同一套評測的產物（RAGAS／研報／檢索三套不能互比）"; \
	  echo "  例：make eval-compare BASE=eval/before.json CAND=eval/after.json"; \
	  exit 2; }
	uv run python scripts/eval_compare.py --baseline "$(BASE)" --candidate "$(CAND)" --tolerance $(TOL)

# ───── 對外存取（Cloudflare Tunnel + nginx）─────
up-edge:  ## 啟動對外邊緣（nginx + cloudflared）
	@test -f deploy/.env || { echo "缺少 deploy/.env，請複製 deploy/.env.example 並填入 TUNNEL_TOKEN"; exit 1; }
	@grep -qE '^TUNNEL_TOKEN=[^[:space:]]' deploy/.env || { echo "deploy/.env 的 TUNNEL_TOKEN 是空的，請填入 Cloudflare 隧道 token"; exit 1; }
	$(COMPOSE) -f $(EDGE_COMPOSE) up -d

down-edge:  ## 關閉對外邊緣
	$(COMPOSE) -f $(EDGE_COMPOSE) down

edge-logs:  ## 跟看對外邊緣日誌
	$(COMPOSE) -f $(EDGE_COMPOSE) logs -f --tail=100

# 刻意用 `up -d --force-recreate` 而不是 `restart`，兩種失效模式各對應其中一半：
#   - `restart` 會重跑 entrypoint（於是用**新模板**重新渲染），但**不會套用 compose
#     的 `environment:` 變更**——2026-07-31 就是這樣炸的：#160 同時加了模板裡的
#     `${EDGE_SECRET}` 與 compose 的 `EDGE_SECRET=`，而長跑的容器環境裡沒有那個變數，
#     於是首次重啟時 envsubst 代換不掉 → `[emerg] unknown "edge_secret" variable` → 502。
#   - 單純 `up -d` 會套用 environment，但 nginx.conf 是 bind mount，改它不會改變容器
#     設定雜湊，compose 會判定無需重建而**靜默 no-op**，新設定根本沒生效。
# 只有重建同時滿足兩者。
edge-reload:  ## 重新套用邊緣設定（重建 nginx 容器）
	$(COMPOSE) -f $(EDGE_COMPOSE) up -d --force-recreate nginx

# ───── 維運 ─────
pipeline: prep tag-info  ## 跑 ①②③ 並提示 Claude 標註步驟

reset-db:  ## 清空 canonical 與向量表（保留 schema）
	$(DOCKER) exec $(DB_CONTAINER) psql -U postgres -d $(DB_NAME) \
	  -c "TRUNCATE research.report_chunk, research.research_report CASCADE;"

clean-data:  ## 刪除中繼產物（抽樣/抽文字/工作清單/tag）
	rm -rf data/extracted data/tags data/*.json

sync-once:  ## 手動跑一次 NAS→本地同步 + 增量匯入（drvfs + rsync）
	bash scripts/sync_new_reports.sh

# 只備「重建不回來」的七張表（qa_log / report_doc / rendition / takeaway / signal /
# run / section）。落點在 NAS，掛載不可用時刻意失敗而非寫本地——與 pgdata 同一塊
# 磁碟的備份等於沒有備份。平時由 report-mark-backup.timer 每日跑。
db-backup:  ## 備份不可重建的 DB 表（pg_dump -Fc → NAS，保留 7 日 + 4 週）
	bash scripts/db_backup.sh

# 派生資產（摘要／摘錄／訊號）停更本來沒有任何訊號會亮：sync 殼把那幾段設成
# best-effort（失敗只 log、不 exit），所以連續失敗永遠不會讓 unit 變紅 ⇒ OnFailure
# 一次都不觸發。純 SQL、零 LLM、零寫入，rc 0＝新鮮／1＝停更／2＝查不到（DB 不可用，
# 處置不同故刻意分流）。平時由 report-mark-freshness.timer 每日 08:30 跑。
freshness:  ## 管線與批次停更偵測（純 SQL、零 LLM；rc 0 PASS／1 資產停更／2 DB 查不到／3 管線停跑）
	uv run python scripts/check_batch_freshness.py

# 與 freshness 分工：那支量「批次有沒有在前進」，這支量「已產出的資料有沒有互相
# 矛盾」——兩個不同的問題，同一種失效型態（壞掉了但沒人會回報）。
# 兩者都不修東西：處置需要人決定（孤兒該刪還是補回連結？重複 chunk 刪哪一列？）。
# 幾條是 57 萬列全表掃描，腳本內走 relax_statement_timeout，別在對外服務尖峰跑。
db-audit:  ## 資料完整性稽核（唯讀；rc 0 乾淨／1 有發現／2 DB 不可用）
	uv run python scripts/db_audit.py
