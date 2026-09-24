# Repository Guidelines

Conventions for contributors and AI agents. Hard rules and the change-impact table live in `CLAUDE.md`; module-level invariants in `docs/ARCHITECTURE.md`. Reply to users in Traditional Chinese; no decorative emoji.

## Project Structure & Module Organization

- `app/services/`: extraction (`extract.py`, `extraction/`), tagging, chunking, embeddings, hybrid retrieval (`retrieval.py`, `retrieval_pipeline.py`), RAG answers (`answer.py`), and the read-only features `reading/`, `radar/`, `brief.py`. `app/config.py` is the only place for new knobs.
- `web/server.py` is the composition layer only; routes live in `web/routers/*.py` (11 routers, no `APIRouter(prefix=...)`), shared symbols go through `web/deps.py`.
- `frontend/`: React 19 + TypeScript + Vite, built to `frontend/dist`. `src/features/*` per page, `src/lib/*` for API boundaries (zod, SSE, reducers). `src/components/animate-ui/` is vendored and excluded from lint.
- `scripts/`: batch and ops jobs. Anything that spawns `claude -p` takes the flock in `scripts/_claude_lock.py` at its main entry (rc=75 on contention).
- `deploy/` is the single source of truth for systemd units, nginx and docker-compose; copy to `/etc` after editing, never edit the machine copy only.
- `db/schema.sql` is applied idempotently by `make schema`; there is no migration tool, so dropping a table needs its own script (`db/drop_deep_report_tables.sql` is the precedent, run by hand on existing databases). `db/expected_constraints.txt` is the CHECK-constraint golden list.
- `eval/` holds the offline evaluation harness and baselines, deliberately outside CI.
- `研報自動匯入/` is a read-only mirror; `data/` holds runtime artefacts. Neither is versioned.

## Build, Test, and Development Commands

- `uv sync` then `make setup` (deps + pgvector container + schema). `cp .env.example .env` and set the three `REPORT_MARK_*` auth values or the server refuses to start.
- `make serve` (port 8097, no reload, models warmed), `make serve-dev` (reload, `SKIP_WARMUP=1`, loopback only), `make serve-preview` (`DEV_NO_AUTH=1` on 8098). `make build-web` after any frontend change.
- `uv run pytest -q` (set `SKIP_SPA_TESTS=1` if `frontend/dist` is absent, otherwise those tests fail rather than skip), `uv run ruff check .`, `cd frontend && npm test`, `npm run typecheck`, `npm run lint`.
- Batch jobs: `make summaries / titles / takeaways / signals / brief`; ops: `make sync-once / db-backup / freshness / db-audit / llm-blocked`. Destructive targets (`reset-db`, `clean-data`, `ingest-lowio`) only when explicitly asked; ask before any TRUNCATE or DROP.

## Coding Style & Naming Conventions

- Python: ruff `E,F,I`, 120 columns, `E402` off (deliberate `sys.path.insert` and env loading before imports). No `ruff format`, black or mypy. Dataclass fields are appended with defaults; `rows.ChunkRow` and `store._meta_columns` are positionally aligned, so index fields by `ChunkRow._fields.index(...)`.
- Frontend: CSS Modules, TanStack Query, zod at API edges; `_`-prefixed identifiers are intentionally unused.
- Determinism boundary: Python decides (parsing, chunking, retrieval, anchoring, aggregation, windows); Claude only produces semantics. Derived features fail open.
- Reuse `hybrid_search` / `retrieval_pipeline`; never build a second retrieval path. Patch `retrieval_pipeline`, not `answer`, when stubbing retrieval.

## Testing Guidelines

- Async tests use `unittest.IsolatedAsyncioTestCase`; pytest-asyncio is intentionally absent.
- Tests never hit the network, load models, touch the DB or the real filesystem; fake LLM, embeddings, DB and files. When you add a parameter to a function, update its fakes: a stale fake raises `TypeError` that an outer `except` swallows silently.
- Test endpoints through HTTP (`TestClient`), never by calling handler objects. Router helper functions go above every `@router.*` decorator.
- Never write the repo-root `.env` from tests; `tests/conftest.py` snapshots and restores it and fails the session if it changed. Use `tempfile`.
- Contract tests that turn red when you change something elsewhere: `tests/test_docs_contract.py` (paths in living docs exist; every route is documented in `README.md` or `docs/WORKFLOW.md`), `tests/test_schema_constraints.py`, `tests/test_content_norm_equivalence.py`, `tests/test_sse_event_contract.py` with `tests/fixtures/sse_events.json`, `tests/test_deploy_units.py`, `tests/test_env_loading.py`, `tests/test_logging_setup.py`, `tests/test_dev_mode.py`, `tests/test_sql_index_hygiene.py`, `tests/test_secret_scan_config.py`, `tests/test_dev_ergonomics.py`, `tests/test_claude_lock.py`, `tests/test_pre_split_guards.py`, `tests/test_spa_serving.py`. Fix the code or the doc, do not widen allowlists.
- The extraction-layer CJK tests (`tests/test_extraction_layout.py` CjkTests, which render a Chinese test PDF with weasyprint) skip locally without fonts; CI installs the fonts and sets `REPORT_MARK_REQUIRE_CJK=1` so they cannot skip there. `REPORT_MARK_WRITE_CONSTRAINTS=1` regenerates the constraint golden list and is only for deliberate schema changes.
- Retrieval or generation quality changes: run the relevant `eval/` harness before and after and compare with `make eval-compare BASE=... CAND=...`; the exit code is the verdict (0 ok, 1 regression, 2 incomparable, 3 unclassified metric).

## Commit & Pull Request Guidelines

- Conventional Commits with a Traditional-Chinese scope: `feat(報告): ...`, `fix(閱讀頁): ...`, `docs(維運): ...`. Title under about 70 characters, body explains what and why.
- Stage explicitly with `git add <path>`; never `git add -A` or `.` (shared working tree, others have WIP). Run ruff and the relevant tests before committing; do not bypass hooks.
- The four CI jobs are required checks named by their Chinese `name` in `.github/workflows/ci.yml`; renaming a job requires updating branch protection in GitHub settings.
- Documentation drift is enforced: renaming or deleting a file, or adding an endpoint, turns `tests/test_docs_contract.py` red. Update `README.md`, `docs/WORKFLOW.md`, `docs/ARCHITECTURE.md` or `CLAUDE.md` accordingly.

## Security & Configuration Tips

- Auth is deny-by-default and fail-closed. Sessions are HMAC cookies with a 7-day sliding and 30-day absolute limit; changing the password, `REPORT_MARK_SESSION_SECRET` or `REPORT_MARK_SESSION_EPOCH` logs everyone out by design.
- External access requires `REPORT_MARK_EDGE_SECRET` or `REPORT_MARK_TRUSTED_PROXY_CIDRS`; the secret must be byte-identical in the repo-root `.env` and `deploy/.env`. See `docs/EXTERNAL_ACCESS.md`.
- `DEV_NO_AUTH` and `SKIP_WARMUP` are read only from the process environment and must never be written to an env file.
- Object storage: non-`local` modes fail closed on any missing `R2_*` value; credentials live in the repo-root `.env` and `/etc/default/report-mark-sync`, byte-identical. Presigned links carry `filename` and expire within an hour.
- The `claude` CLI must be on PATH; systemd gets it from `deploy/systemd/report-mark-web.service.d/path.conf`. `/healthz` probes only the DB and cannot see a missing CLI; `scripts/check_web_health.sh` rc=5 can. Likewise R2: `/healthz` never probes it; the probe reads loopback-only `/healthz/storage` and exits 6 when the bucket or credentials are unusable. The DeepSeek account (balance below the CNY floor, 402, 401, unreachable) is read from loopback-only `/healthz/llm` and exits 7; the probe checks all three, reports every reason on one line, and exits with the first of 5 → 6 → 7.
- Secrets never enter argv (webhook URLs are fed to curl via stdin); gitleaks runs over full history in CI and `.gitleaks.toml` is guarded by `tests/test_secret_scan_config.py`.
