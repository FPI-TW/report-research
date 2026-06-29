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

# Docker 二進位自動偵測：可連到 daemon 的 docker 優先；否則若有 docker.exe（WSL+Docker Desktop）就用它；
# 都沒有時退回 docker，讓指令自己回報真正的 daemon 錯誤（而非 docker.exe: command not found）。
DOCKER := $(shell if docker info >/dev/null 2>&1; then echo docker; elif command -v docker.exe >/dev/null 2>&1; then echo docker.exe; else echo docker; fi)
COMPOSE := $(DOCKER) compose

.PHONY: help deps db schema setup sample extract worklist prep tag-info \
        ingest ingest-lowio restore-durability align normalize serve search \
        stats reset-db clean-data pipeline \
        up-edge down-edge edge-logs edge-reload \
        sync-once \
        spa-dev spa-build spa-test

help:  ## 顯示可用指令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ───── 基礎建設 ─────
deps:  ## 安裝相依套件
	uv sync

db:  ## 起 pgvector 容器（已存在則啟動）
	$(DOCKER) start $(DB_CONTAINER) 2>/dev/null || \
	$(DOCKER) run -d --name $(DB_CONTAINER) \
	  -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=$(DB_NAME) \
	  -p $(DB_PORT):5432 -v report-mark-pgdata:/var/lib/postgresql/data \
	  pgvector/pgvector:pg16

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

normalize:  ## 一次性清理 chunk content（CJK 空白）+ ANALYZE（冪等）
	uv run python scripts/normalize_chunks.py

summaries:  ## 為缺摘要的報告生成 2-3 句中文摘要（Sonnet，冪等可續傳，補 summary IS NULL）
	uv run python scripts/generate_summaries.py

# ───── 檢索 ─────
serve:  ## 啟動查詢網頁（BGE-M3 常駐）→ http://localhost:$(PORT)
	uv run uvicorn web.server:app --host 0.0.0.0 --port $(PORT)

search:  ## CLI 檢索（用法：make search Q="查詢" MARKET=TW）
	uv run python scripts/search.py "$(Q)" $(if $(MARKET),--market $(MARKET),)

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

stats:  ## 看 DB 市場分佈與筆數
	@$(DOCKER) exec $(DB_CONTAINER) psql -U postgres -d $(DB_NAME) \
	  -c "select market, count(*) reports from research.research_report group by market order by 2 desc;" \
	  -c "select count(*) chunks from research.report_chunk;"

# ───── 對外存取（Cloudflare Tunnel + nginx）─────
up-edge:  ## 啟動對外邊緣（nginx + cloudflared）
	@test -f deploy/.env || { echo "缺少 deploy/.env，請複製 deploy/.env.example 並填入 TUNNEL_TOKEN"; exit 1; }
	@grep -qE '^TUNNEL_TOKEN=[^[:space:]]' deploy/.env || { echo "deploy/.env 的 TUNNEL_TOKEN 是空的，請填入 Cloudflare 隧道 token"; exit 1; }
	$(COMPOSE) -f $(EDGE_COMPOSE) up -d

down-edge:  ## 關閉對外邊緣
	$(COMPOSE) -f $(EDGE_COMPOSE) down

edge-logs:  ## 跟看對外邊緣日誌
	$(COMPOSE) -f $(EDGE_COMPOSE) logs -f --tail=100

edge-reload:  ## 重啟 nginx（更新設定後使用）
	$(COMPOSE) -f $(EDGE_COMPOSE) restart nginx

# ───── 維運 ─────
pipeline: prep tag-info  ## 跑 ①②③ 並提示 Claude 標註步驟

reset-db:  ## 清空 canonical 與向量表（保留 schema）
	$(DOCKER) exec $(DB_CONTAINER) psql -U postgres -d $(DB_NAME) \
	  -c "TRUNCATE research.report_chunk, research.research_report CASCADE;"

clean-data:  ## 刪除中繼產物（抽樣/抽文字/工作清單/tag）
	rm -rf data/extracted data/tags data/*.json

sync-once:  ## 手動跑一次 NAS→本地同步 + 增量匯入（drvfs + rsync）
	bash scripts/sync_new_reports.sh
