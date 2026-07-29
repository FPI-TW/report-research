# Repository Guidelines

## Project Structure & Module Organization

`app/services/` contains extraction, filename parsing, tagging, chunking, embeddings, hybrid retrieval, RAG answers, deep report generation, PDF/Typst rendering, and DB access — plus the `radar/`（觀點雷達）and `reading/`（閱讀頁）sub-packages. `app/config.py` centralizes **most** tunables (with the exceptions listed under Coding Style). `app/templates/` holds the Typst report templates and their registry.

`web/server.py` is now only the composition layer (app construction + auth middleware); routes live in `web/routers/*.py` and cross-module shared symbols go through `web/deps.py`. `web/report_runs.py` is the background-run registry for deep reports: generation runs in its own `asyncio.Task`, events go into a replay buffer plus one queue per subscriber (tokens are deliberately not buffered — see the module docstring), and `POST /api/report` is merely a subscriber — a disconnect or refresh no longer aborts generation (`/api/report-runs`, `/api/report-runs/{run_id}/stream`, `/api/report-runs/{run_id}/cancel` cover polling, replay-then-live reconnect, and cancellation). **The registry is in-process state; a restart wipes it** — read its module docstring before touching report streaming. `web/auth.py` (fail-closed credentials/session) and `web/env_loader.py` (loads the repo-root `.env` at startup) sit in the same layer. `web/static/` contains just `login.html`.

**The frontend is `frontend/` — React 19 + TypeScript + Vite**, built to `frontend/dist`. `scripts/` holds ingestion, tagging, evaluation, summary, signal/takeaway extraction, and maintenance jobs. `eval/` holds the RAGAS/report evaluation harness. `deploy/` holds the deployment surface: `docker-compose.yml` + `nginx.conf` are the public edge (Cloudflare Tunnel + nginx, driven by `make up-edge` / `down-edge` / `edge-logs` / `edge-reload`), and `deploy/systemd/` holds `report-mark-web.service`, the NAS-sync `report-mark-sync.timer` / `.service`, the failure alert `report-mark-alert@.service`, and the drop-in that puts the `claude` CLI on the service `PATH`. **Change deployment settings here and then sync to the machine — never edit only the copy on the box.** `db/schema.sql` defines the `research` PostgreSQL/pgvector schema. `tests/` holds the Python test suite; frontend tests are colocated as `*.test.ts(x)` under `frontend/src/`.

## Build, Test, and Development Commands

Always prefix shell commands with `rtk` when working from Codex (a token-saving CLI proxy; Claude Code applies it automatically via hook, so use the bare command there).

- `rtk uv sync`: install Python dependencies from `pyproject.toml` and `uv.lock`.
- `rtk make setup`: install deps, start/apply the pgvector schema, and prepare local infrastructure.
- `rtk make serve`: run FastAPI on `http://localhost:8097`; code changes require restart.
- `rtk uv run pytest -q`: run the Python test suite.
- `rtk npm --prefix frontend test`: run the frontend suite (vitest).
- `rtk npm --prefix frontend run typecheck`: `tsc --noEmit`.
- `rtk npm --prefix frontend run build`: rebuild `frontend/dist` — **required for any frontend change to take effect**.
- `rtk uv run python scripts/eval_retrieval.py --help`: inspect retrieval evaluation.

**CI gates every PR** (`.github/workflows/ci.yml`): 後端（pytest）與前端（typecheck + vitest）兩個必要檢查，main 有分支保護。只跑 pytest 會在前端 job 上翻車。

> 舊文件曾寫 `node --test web/static/app/*.test.mjs`。該目錄已刪除，而且該指令在 bash 下會印出 `tests 0 / pass 0 / fail 0` 並 **exit 0** — 一份看起來全過、實際一個測試都沒跑的假綠。

## Coding Style & Naming Conventions

Use Python 3.11+ with explicit types where helpful, dataclasses for small value objects, and async SQLAlchemy sessions for DB work. Keep deterministic pipeline logic in Python; reserve Claude CLI calls for semantic labeling, summaries, Q&A, and generated reports. Frontend code lives in `frontend/src/` (React function components, CSS Modules, TanStack Query, zod at the API boundary). **On the frontend, CI gates only `tsc --noEmit` and vitest** — ESLint has a config (`frontend/eslint.config.js`) but no CI step, so a broken `npm --prefix frontend run lint` will not stop a merge. Run it yourself when touching the frontend.

**New tunables go in `app/config.py`** (a frozen dataclass over `os.getenv`, ~80 fields — not pydantic-settings), not scattered `os.getenv` calls in service modules. That is the rule, not yet the whole truth: `SSE_HEARTBEAT_INTERVAL` (`web/deps.py`), `REPORT_SEMAPHORE` / `REPORT_MAX_QUEUE` (`web/routers/report.py`), `ASK_MAX_QUEUE` (`web/routers/ask.py`), `REPORT_RUN_RETENTION_SECONDS` (`web/report_runs.py`), `ASK_FOLLOWUP_MODEL` / `ASK_FOLLOWUP_TIMEOUT` (`app/services/followups.py`), `REPORT_MARK_RERANK_WORKERS` / `REPORT_MARK_RERANK_TIMEOUT` (`app/services/retrieval_pipeline.py`), `REPORT_MARK_DB_URL` (`app/services/db.py`), `REPORT_MARK_MAX_TRACKED_FAIL_IPS` (`web/auth.py`) and `EVAL_JUDGE_MODEL` (`eval/judge.py`) are still read in place — grep those eight files too when a key is not in `config.py`.

The `REPORT_MARK_*` prefix is likewise a **rule**: it belongs to the five auth/DB variables (see `.env.example`) and new tunables never take it. **Five** other live keys predate the rule and still carry it — `REPORT_MARK_MAX_TRACKED_FAIL_IPS` (`web/auth.py`), the two `REPORT_MARK_RERANK_*` above, plus `REPORT_MARK_ROOT` / `REPORT_MARK_ALERT_WEBHOOK` in `deploy/systemd/`; they work, so do not "fix" the naming. Everything else (`ASK_*`, `REPORT_*`, `QA_*`, `FAITHFULNESS_*`, …) is unprefixed. All are loaded from repo-root `.env`.

## Testing Guidelines

Add or update `tests/test_*.py` for service behavior and API contracts. Prefer deterministic tests that mock LLM, embedding, filesystem, and DB boundaries unless the change is integration-level. For frontend code, add `*.test.ts(x)` beside the module under `frontend/src/`. Run the narrow test first, then `rtk uv run pytest -q` and `rtk npm --prefix frontend test`.

Three hard-won rules:

- **Test an API endpoint through HTTP, not by calling the handler object.** Calling `module.handler()` directly bypasses routing — that is how a decorator applied to the wrong function shipped a 422 to production with CI fully green.
- **When you add a parameter to a function that tests fake, update the fake's signature.** A stale fake raises `TypeError`, which a surrounding `except` swallows, and the code silently takes a different path. This has bitten this repo five times.
- **A test must never write to the repo-root `.env`.** On this machine the repo root *is* the deployment directory (systemd's `WorkingDirectory`, and `web/server.py` resolves `.env` from the module's own path, not from cwd). On 2026-07-29 a test verified dotenv loading by overwriting that file and restoring it in `finally`; the restore never ran, and production's `REPORT_MARK_ACCESS_*` and `REPORT_MARK_SESSION_SECRET` were replaced by test values checked into the repo — a known signing secret means session cookies can simply be forged, no login required. `finally` survives an exception, not a killed process (timeout, OOM, cancelled CI). **The general rule: any test that can only be verified by mutating a real deployment file is trading production for coverage.** Feed `load_env_file()` a throwaway file from `tempfile.TemporaryDirectory()` instead, and assert wiring and load order statically via AST. Two guards are already in place and will catch you: the AST scan in `tests/test_env_loading.py`, and the session-scoped autouse fixture in `tests/conftest.py` that compares `.env` at teardown, restores it, and fails the run. **They are the second line of defence, not a licence — the first is to not touch the real file at all.**

`tests/test_docs_contract.py` keeps the docs honest mechanically: it asserts that every backtick-quoted path in the five "current-state" docs resolves to a real file, and that the routes declared in `web/routers/*.py` and the API tables in `README.md` / `docs/WORKFLOW.md` agree **in both directions**. When it goes red after a rename or a new endpoint, **fix the doc — do not widen the allowlist**; that list is per-document and exists only for deliberately-absent names (planned files, "this file does not exist" counter-examples). Write endpoint paths verbatim, including parameter names and the `:path` converter.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit style with scopes, often in Traditional Chinese, for example `feat(report): ...`, `fix(report): ...`, and `docs(plan): ...`. Before committing, inspect recent commit messages and the staged diff; do not use a plain English sentence. PRs should explain behavior changes, list validation commands, link issues or plans, and include screenshots for UI changes.

## Security & Agent-Specific Instructions

Answer user-facing questions in Traditional Chinese. Do not commit `.env`, generated data under `data/`, reports, screenshots, or local tool state. Auth is deny-by-default; keep login/session changes aligned with `web/auth.py` and `docs/EXTERNAL_ACCESS.md`. Treat `研報自動匯入/` as read-only source input.
