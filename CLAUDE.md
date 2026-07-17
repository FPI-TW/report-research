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
uv run pytest -q                                     # all
uv run pytest tests/test_answer.py                   # single file
uv run pytest -k retrieval                           # by keyword
node --test web/static/app/*.test.mjs                # frontend ES-module tests

make search Q="AI 伺服器散熱" MARKET=TW              # CLI semantic search
make stats                                           # market distribution + chunk count
```

There is **no linter/formatter configured in-repo** (no ruff/black/mypy/pre-commit). Style conventions live in `AGENTS.md`.

## Architecture (the parts that span multiple files)

**Two-layer split coupled by `file_hash`.** Python does everything deterministic (parse, extract, chunk, embed, store, retrieve); Claude does everything semantic (multi-dimensional tagging, summaries, Q&A, report writing). Every pipeline stage is keyed by a file's SHA256 `file_hash` and is **checkpoint-resumable** (skips already-done work). Preserve this when extending — e.g. `extract_all.py` -> `data/extracted/all.jsonl`, `tag_all_cli.py` -> `data/tags/<hash>.json`, `ingest_all.py` reads both and upserts. `ingest` gates on `is_research` + `market` before writing chunks.

**Hybrid retrieval** (`app/services/retrieval.py` + `store.py` + `textnorm.py` + `db/schema.sql`): dense recall (BGE-M3 1024-dim cosine via HNSW) plus lexical recall (`pg_trgm` LIKE over the generated `content_norm` column) are merged, deduped, then ranked by tiered fusion (phrase > all-terms > partial, then relevance band -> recency -> fused score). The DB's `content_norm` GENERATED expression must stay byte-for-byte equivalent to `textnorm.norm_for_match()`.

**RAG Q&A** (`app/services/answer.py`): embed query -> `hybrid_search` -> `build_context` (numbered sources) -> `llm.stream_completion` -> parse `[n]` citations -> write `qa_log`. Two pre-routers run first: `intent.py` (Haiku off-topic gate, fail-open) and `overview.py` (enumeration/aggregate questions like "有哪些券商" go to pure-SQL facet aggregation, bypassing top-k). Multi-turn uses `conversation_id` grouped by `COALESCE(conversation_id, id)`.

**Deep report** (`app/services/report.py` -> `pdf.py` -> `chart.py`): deeper retrieval -> long-form Claude stream (may emit ```kpi / ```chart blocks) -> WeasyPrint renders branded PDF -> persisted in `report_doc` (markdown is the source of truth; PDF is rebuildable). `report_gate.py` decides whether to offer a report after a Q&A turn.

**Reading page** (`app/services/reading/` -> `/api/reading/*`, SPA route `/app/report/:hash`, keyed by `file_hash`): one report's text plus everything the corpus knows about it, at a shareable URL. Same split as everywhere else — Claude only ever emits semantics (`scripts/extract_takeaways.py` asks for claim + verbatim quote, never offsets), Python does the anchoring (`anchor.py` `locate_quote`/`locate_chunk`) and the reads (`queries.py`, pure SQL). Takeaways are batch-produced into `report_takeaway`, so **serving the page costs zero LLM calls**. The canonical text is `clean_extracted(full_text)`: the anchoring baseline, the excerpt fed to the LLM, and the text the API returns must all be that same string — `text_sha256` guards the invariant (sha at extraction time vs sha of the current canonical text; a mismatch degrades a takeaway to non-jumpable rather than jumping to the wrong place).

**LLM integration** (`app/services/llm.py`): shells out to the **`claude` CLI** (not the SDK) as `claude -p --model <m> --setting-sources '' --output-format stream-json`, parsing `text_delta` events. `--setting-sources ''` is deliberate (excludes user/project settings and SessionStart hooks). Adds `--allowedTools WebSearch` when web is enabled — note the CLI sometimes withholds streamed text and returns the full answer only in the final `result` event, so there is a result/assistant fallback. Retries only on API 529; fail-open on timeout if text already streamed, else raises `LLMUnavailableError`.

**Web composition** (`web/server.py`): single FastAPI app mounting search/browse, `/api/ask` and `/api/report` (both SSE), conversations/history, feedback, monitor, and static pages. Auth is deny-by-default middleware (`web/auth.py`): one shared username/password (env), HMAC-signed `tf_session` cookie (7-day sliding), per-IP login rate limit, localhost-HTTP exception. `/api/ask` is capped at 3 concurrent; `/api/report` is serialized (`REPORT_SEMAPHORE`).

**Schema** (`db/schema.sql`, applied via `make schema` — **no migration tool**): tables `research_report`, `report_chunk` (`vector(1024)` + HNSW + trgm GIN), `qa_log`, `report_doc`, `report_signal`, `report_takeaway` (one row = one report x one takeaway: claim + verbatim quote + anchored `quote_start`/`quote_end` + `text_sha256`; `UNIQUE(report_id, ordinal)`, FK CASCADE), all in the `research` schema. It is idempotent: new columns are added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`.

## Project-specific gotchas

- **`make serve` has no `--reload`.** Python changes need a restart. In production the web runs as systemd `report-mark-web.service` (enabled, auto-restart) — restart with `sudo systemctl restart report-mark-web.service`. Static assets (`web/static/**`) are served via `_NoCacheStatic` and take effect immediately without restart.
- **Auth is fail-closed.** The app refuses to start if `REPORT_MARK_ACCESS_USERNAME`/`_PASSWORD` are unset. External access (Cloudflare Tunnel + nginx) additionally requires `REPORT_MARK_TRUSTED_PROXY_CIDRS`, or login is rejected as non-HTTPS.
- **The `claude` CLI must be on PATH** for tagging, summaries, Q&A, and reports. Under systemd this needs an explicit PATH (drop-in), since the service environment differs from a login shell.
- **The PDF render path is fragile.** `pdf.py` `inject_kpi`/`inject_charts` must `isinstance`-guard every shape before `.get()` — an exception there propagates out of `render_report_pdf` and aborts the entire report (no PDF, no persistence, and a permanent 500 on later rebuild). Always defend against malformed LLM JSON shapes.
- **`research_report.full_text` holds the *uncleaned* extraction.** `ingest_all.py` writes `full_text=raw_text` but chunks `chunk_text(clean_extracted(raw_text))`, so `full_text` still carries the CJK inter-character spaces PDF extraction leaves behind (「台 積 電」). **Any UI displaying the full text must render `clean_extracted(full_text)`** — `clean_extracted`, not `clean_text` (the latter collapses all newlines into spaces; it only suits search snippets).
- **`report_chunk.content` is not a substring of `full_text`.** `chunk.py`'s overlap merge (`f"{tail}{cur}"`) copies the previous chunk's last `CHUNK_OVERLAP` (80) chars into the next chunk's head, so a naive `full_text.find(chunk.content)` **fails ~99% of the time** (measured over 400 samples: 3/300 raw, 168/400 normalized, 400/400 normalized + head-drop). Worse, anchoring against raw `full_text` does *not* return None — it returns a plausible-looking Anchor in the wrong coordinate system. Always locate through `app/services/reading/anchor.py`.
- **Never run `extract_takeaways.py` and `extract_signals.py` concurrently.** Multiple batches fighting over the `claude` CLI get their extractions mass-marked `rejected` — the data isn't bad and the model isn't broken, the CLI is contended. Run one batch at a time.
- **New `ChunkRow` fields land mid-tuple, and consumers may hardcode indices.** `store._meta_columns` and `rows.ChunkRow` align *positionally*; inserting `file_hash` moved `content` from 14 to 15 and silently repointed `scripts/eval_retrieval.py`'s `_RID, _CONTENT = 1, 14` at `chunk_index` — no exception, just garbage eval scores. Derive positions via `ChunkRow._fields.index(...)`; never write the number.
- **`研報自動匯入/` is read-only source input** (mirrored from a NAS share every 3h by `report-mark-sync.timer`). Never write to it.
- **Shared working directory.** Other users may have uncommitted WIP in this tree. Stage explicit paths (`git add <file>`), never `git add -A`/`.`; verify scope with `git diff --staged --stat` before committing.
- **Config from env, `REPORT_MARK_*` names.** Tunables read via `os.getenv(...)` with in-code defaults (`ASK_*` in `answer.py`, `REPORT_*` in `report.py`, intent in `intent.py`). Loaded from repo-root `.env` by `web/env_loader.py` at `make serve` start.
- **Market codes align to findb:** `TW US HK CN FX WTX MACRO GLOBAL CRYPTO` (bond -> MACRO, commodity -> GLOBAL). Mapping in `app/services/tagging.py`; re-map existing rows deterministically with `make align` (no Claude rerun).
- Commits follow Conventional Commits with Traditional-Chinese scopes (e.g. `feat(report): ...`, `docs: ...`); inspect `git log` and the staged diff before composing.

## Where to look

- `README.md` — comprehensive developer guide (architecture diagrams, full API table, env reference, deployment).
- `docs/WORKFLOW.md` — authoritative end-to-end pipeline, stage I/O, full tag vocabulary, Web API contract.
- `AGENTS.md` — contributor conventions (structure, style, testing, commits, security).
- `docs/ROADMAP.md` — Phase 1 (RAG/reports) shipped; daily brief and Phase 2/3 (signals, findb, MCP/REST) not yet implemented.
