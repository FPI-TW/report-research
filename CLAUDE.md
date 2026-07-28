# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is (and is not)

廷豐智能研報 — a broker research-report platform: extract text from PDFs/docx, tag them with Claude, embed into pgvector, then serve **semantic search / RAG Q&A / downloadable deep-report PDFs**. Repo dir is `report-mark`; GitHub remote is `FPI-TW/report-research`.

- This is **NOT** the "FinDB" project described in `../CLAUDE.md` (the parent directory). That file's Alembic/normalizer/Source-API guidance does **not** apply here — this repo has no Alembic, no `app/api/`, no `NORMALIZER_MAP`. report-mark only *aligns market codes* to findb.
- **Answer user-facing questions in Traditional Chinese** (code, identifiers, paths, technical terms stay in their original language).
- Do not add decorative emoji to code, docs, commits, or output unless explicitly asked.

## Commands

```bash
uv sync                              # install deps (uv, Python 3.11+; torch is CPU-only)
make setup                           # one-shot: deps + start pgvector container + apply schema
make serve                           # uvicorn web.server:app on :8097 (loads repo-root .env)
make help                            # all Makefile targets

# Full-corpus pipeline (extract -> Claude tag -> embed/ingest)
uv run python scripts/extract_all.py
uv run python scripts/tag_all_cli.py --workers 8     # needs `claude` CLI on PATH
uv run python scripts/ingest_all.py                  # first run downloads BGE-M3 ~2-4GB
bash scripts/resume_corpus.sh                        # orchestrates tag+ingest in parallel, resumable

# Sample-prototype path (small-scale): make prep -> run workflows/tag_reports.workflow.js in Claude Code -> make ingest
make summaries                       # backfill 2-3 sentence summaries (Sonnet, idempotent)
make takeaways                       # reading-page takeaways (Sonnet, last 90d, idempotent) -> research.report_takeaway
make align                           # remap Chinese labels -> findb market codes (deterministic)

# Tests (unittest-style classes, run via pytest)
uv run pytest -q                                     # all (86 test files)
uv run pytest tests/test_answer.py                   # single file
uv run pytest -k retrieval                           # by keyword

# Frontend (React 19 + TS + Vite under frontend/; NOT web/static/)
cd frontend && npm test                              # vitest
cd frontend && npm run typecheck                     # tsc --noEmit
cd frontend && npm run lint                          # eslint
cd frontend && npm run build                         # 產出 frontend/dist（部署必跑）

make signals                                         # 訊號擷取 -> research.report_signal
make search Q="AI 伺服器散熱" MARKET=TW              # CLI semantic search
make stats                                           # market distribution + chunk count
```

**CI gates every PR** (`.github/workflows/ci.yml`): two required checks — 後端測試（`uv run pytest -q`）與前端測試（`npm run typecheck` + `npm test`）。main 有分支保護（strict + enforce_admins）。本機只跑 pytest 會在前端 job 上翻車。

Python 側**沒有 linter/formatter**（無 ruff/black/mypy/pre-commit）；**前端有** ESLint（`frontend/eslint.config.js`）＋ `tsc --noEmit`，且兩者都在 CI 內。其餘風格慣例見 `AGENTS.md`。

## Architecture (the parts that span multiple files)

**Two-layer split coupled by `file_hash`.** Python does everything deterministic (parse, extract, chunk, embed, store, retrieve); Claude does everything semantic (multi-dimensional tagging, summaries, Q&A, report writing). Every pipeline stage is keyed by a file's SHA256 `file_hash` and is **checkpoint-resumable** (skips already-done work). Preserve this when extending — e.g. `extract_all.py` -> `data/extracted/all.jsonl`, `tag_all_cli.py` -> `data/tags/<hash>.json`, `ingest_all.py` reads both and upserts. `ingest` gates on `is_research` + `market` before writing chunks.

**Hybrid retrieval** (`app/services/retrieval.py` + `store.py` + `textnorm.py` + `db/schema.sql`): dense recall (BGE-M3 1024-dim cosine via HNSW) plus lexical recall (`pg_trgm` LIKE over the generated `content_norm` column) are merged, deduped, then ranked by tiered fusion (phrase > all-terms > partial, then relevance band -> recency -> fused score). The DB's `content_norm` GENERATED expression must stay byte-for-byte equivalent to `textnorm.norm_for_match()`.

**RAG Q&A** (`app/services/answer.py`): embed query -> `hybrid_search` -> `build_context` (numbered sources) -> `llm.stream_completion` -> parse `[n]` citations -> write `qa_log`. Two pre-routers run first: **`scope_router.py`** (**five-way**, not a binary off-topic gate — `OFF_TOPIC`/`OVERVIEW`/`CORPUS_QA`/`TIME_SENSITIVE`/`ADVICE_RISK`; overview 判定為確定性優先、零 LLM 零向量，其次詞表安全前檢，最後 Haiku 四類分類，fail-open) and `overview.py` (enumeration/aggregate questions like "有哪些券商" go to pure-SQL facet aggregation, bypassing top-k). Multi-turn uses `conversation_id` grouped by `COALESCE(conversation_id, id)`.

The Q&A path also runs: cross-encoder **rerank** (`rerank.py`, M2)、**agentic 多輪補查** (`agentic_qa.py` + `query_planner.py`, M5)、**受信任時效資料** (`trusted_market_data.py`, M4a — registry 為空即安全婉拒)、**證據帳本** (`evidence.py`, M4b)、**忠實度抽查** (`faithfulness.py`, M8c，done 後非同步、僅落庫)、**追問建議** (`followups.py`)、**輸出語言** (`locale.py`, M10 — fail-open 到 zh-Hant)。

**Deep report** (`app/services/report.py` -> `report_writer.py` -> `typst_render.py` / `pdf.py` -> `chart.py`): deeper retrieval -> long-form Claude stream (may emit ```kpi / ```chart blocks) -> branded PDF -> persisted in `report_doc` (markdown is the source of truth; PDF is rebuildable). `report_gate.py` decides whether to offer a report after a Q&A turn.

**渲染是雙軌，Typst 為主**（M9a）：`REPORT_RENDERER` 預設 `typst`（`app/config.py`），`report.render_report_pdf` 分派，Typst 失敗才 fail-open 回退 WeasyPrint（`pdf.py`）。模板在 `app/templates/*.typ`，由 `manifest.py` registry 管理（M9b：ib-classic / broker-modern / privatebank-dark），換模板重出走 `report_rendition` 不可變表、零 LLM。

**生成預設為逐節**（M7，`REPORT_SECTIONED_ENABLED=1`）：大綱→逐節檢索與草稿→單次組裝，狀態機落 `report_run`/`report_section`。預算前瞻（`REPORT_DRAFT_BUDGET`）在逾時逼近時只砍動態分析子節、保住五章骨架，仍交付。

**Reading page** (`app/services/reading/` -> `/api/reading/*`, SPA route `/app/report/:hash`, keyed by `file_hash`): one report's text plus everything the corpus knows about it, at a shareable URL. Same split as everywhere else — Claude only ever emits semantics (`scripts/extract_takeaways.py` asks for claim + verbatim quote, never offsets), Python does the anchoring (`anchor.py` `locate_quote`/`locate_chunk`) and the reads (`queries.py`, pure SQL). Takeaways are batch-produced into `report_takeaway`, so **serving the page costs zero LLM calls**. The canonical text is `clean_extracted(full_text)`: the anchoring baseline, the excerpt fed to the LLM, and the text the API returns must all be that same string — `text_sha256` guards the invariant (sha at extraction time vs sha of the current canonical text; a mismatch degrades a takeaway to non-jumpable rather than jumping to the wrong place).

**LLM integration** (`app/services/llm.py`): shells out to the **`claude` CLI** (not the SDK) as `claude -p --model <m> --setting-sources '' --output-format stream-json`, parsing `text_delta` events. `--setting-sources ''` is deliberate (excludes user/project settings and SessionStart hooks). Adds `--allowedTools WebSearch` when web is enabled — note the CLI sometimes withholds streamed text and returns the full answer only in the final `result` event, so there is a result/assistant fallback. Retries only on API 529; fail-open on timeout if text already streamed, else raises `LLMUnavailableError`.

**Web composition** (`web/server.py` 已收斂為**組合層**): 路由拆在 `web/routers/`（`ask`/`report`/`search`/`qa_history`/`monitor`/`radar`/`reading`/`report_file`/`health`/`auth_pages`/`spa`），跨組共用符號一律經 **`web/deps.py`** 存取（測試 patch 的單一位置）。Auth 仍是 deny-by-default middleware（留在 `server.py`）：one shared username/password (env), HMAC-signed `tf_session` cookie (7-day sliding), per-IP login rate limit, localhost-HTTP exception。**免登入白名單為 `/login` 與 `/healthz`，另加前綴 `/app/assets/`**。`/api/ask` 併發上限 3（`routers/ask.py`）；`/api/report` 序列化（`routers/report.py` 的 `REPORT_SEMAPHORE`）——**這兩個常數已不在 `server.py`**。

**在 router 檔新增輔助函式時，一律放在所有 `@router.*` 裝飾器之上**：夾在裝飾器與 handler 之間會讓裝飾器套到輔助函式，端點對正常請求回 422（2026-07-28 實際事故；直接呼叫函式物件的測試看不到，端點契約要走 HTTP 層測試）。

**Schema** (`db/schema.sql`, applied via `make schema` — **no migration tool**): tables `research_report`, `report_chunk` (`vector(1024)` + HNSW + trgm GIN), `qa_log`, `report_doc`, `report_signal`, `report_run` / `report_section`（M7 逐節狀態機）, `report_rendition`（M9b 不可變渲染產物）, `report_takeaway` (one row = one report x one takeaway: claim + verbatim quote + anchored `quote_start`/`quote_end` + `text_sha256`; `UNIQUE(report_id, ordinal)`, FK CASCADE), all in the `research` schema. It is idempotent: new columns are added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

## Project-specific gotchas

- **`make serve` has no `--reload`.** Python changes need a restart. In production the web runs as systemd `report-mark-web.service` (enabled, auto-restart) — restart with `sudo systemctl restart report-mark-web.service`. **前端改動需要 `cd frontend && npm run build`**：SPA 由 `frontend/dist` 提供，資產走 `_ImmutableStatic`（`Cache-Control: immutable`，一年）。`web/static/` 現在只剩 `login.html`（走 `_NoCacheStatic`，即時生效）。`frontend/dist` 不存在時 SPA 直接回 **503**。
- **Auth is fail-closed.** The app refuses to start if `REPORT_MARK_ACCESS_USERNAME`/`_PASSWORD` are unset. External access (Cloudflare Tunnel + nginx) additionally requires `REPORT_MARK_TRUSTED_PROXY_CIDRS`, or login is rejected as non-HTTPS.
- **The `claude` CLI must be on PATH** for tagging, summaries, Q&A, and reports. Under systemd this needs an explicit PATH (drop-in), since the service environment differs from a login shell.
- **The PDF render path is fragile.** `pdf.py` `inject_kpi`/`inject_charts` must `isinstance`-guard every shape before `.get()` — an exception there propagates out of `render_report_pdf` and aborts the entire report (no PDF, no persistence, and a permanent 500 on later rebuild). Always defend against malformed LLM JSON shapes.
- **`research_report.full_text` holds the *uncleaned* extraction.** `ingest_all.py` writes `full_text=raw_text` but chunks `chunk_text(clean_extracted(raw_text))`, so `full_text` still carries the CJK inter-character spaces PDF extraction leaves behind (「台 積 電」). **Any UI displaying the full text must render `clean_extracted(full_text)`** — `clean_extracted`, not `clean_text` (the latter collapses all newlines into spaces; it only suits search snippets).
- **`report_chunk.content` is not a substring of `full_text`.** `chunk.py`'s overlap merge (`f"{tail}{cur}"`) copies the previous chunk's last `CHUNK_OVERLAP` (80) chars into the next chunk's head, so a naive `full_text.find(chunk.content)` **fails ~99% of the time** (measured over 400 samples: 3/300 raw, 168/400 normalized, 400/400 normalized + head-drop). Worse, anchoring against raw `full_text` does *not* return None — it returns a plausible-looking Anchor in the wrong coordinate system. Always locate through `app/services/reading/anchor.py`.
- **Never run `extract_takeaways.py` and `extract_signals.py` concurrently.** Multiple batches fighting over the `claude` CLI get their extractions mass-marked `rejected` — the data isn't bad and the model isn't broken, the CLI is contended. Run one batch at a time.
- **New `ChunkRow` fields land mid-tuple, and consumers may hardcode indices.** `store._meta_columns` and `rows.ChunkRow` align *positionally*; inserting `file_hash` moved `content` from 14 to 15 and silently repointed `scripts/eval_retrieval.py`'s `_RID, _CONTENT = 1, 14` at `chunk_index` — no exception, just garbage eval scores. Derive positions via `ChunkRow._fields.index(...)`; never write the number.
- **`研報自動匯入/` is read-only source input** (mirrored from a NAS share every 3h by `report-mark-sync.timer`). Never write to it.
- **Shared working directory.** Other users may have uncommitted WIP in this tree. Stage explicit paths (`git add <file>`), never `git add -A`/`.`; verify scope with `git diff --staged --stat` before committing.
- **Config 已集中到 `app/config.py`**（frozen dataclass + `os.getenv`，**非 pydantic-settings**）。約 80 個鍵：`ASK_*`／`REPORT_*`／`QA_*`／`FAITHFULNESS_*`／`RERANK_MODEL`／`TRUSTED_DATA_ENABLED` 等。各服務模組保留原常數名但改由 `get_settings()` 取值（如 `REPORT_RENDERER = _S.report_renderer`）。**新增旋鈕加在 `app/config.py`，不要在服務模組裡寫 `os.getenv`**。
- **`REPORT_MARK_*` 前綴只適用於 auth/DB 那五個變數**（見 `.env.example`），其餘旋鈕一律不加前綴。由 `web/env_loader.py` 於 `make serve` 啟動時從 repo-root `.env` 載入。
- **Market codes align to findb:** `TW US HK CN FX WTX MACRO GLOBAL CRYPTO` (bond -> MACRO, commodity -> GLOBAL). Mapping in `app/services/tagging.py`; re-map existing rows deterministically with `make align` (no Claude rerun).
- Commits follow Conventional Commits with Traditional-Chinese scopes (e.g. `feat(report): ...`, `docs: ...`); inspect `git log` and the staged diff before composing.

## Where to look

- `README.md` — comprehensive developer guide (architecture diagrams, full API table, env reference, deployment).
- `docs/WORKFLOW.md` — authoritative end-to-end pipeline, stage I/O, full tag vocabulary, Web API contract.
- `AGENTS.md` — contributor conventions (structure, style, testing, commits, security).
- `docs/ROADMAP.md` — Phase 1 (RAG/reports) shipped; daily brief and Phase 2/3 (signals, findb, MCP/REST) not yet implemented.
