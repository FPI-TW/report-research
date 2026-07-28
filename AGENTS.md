# Repository Guidelines

## Project Structure & Module Organization

`app/services/` contains extraction, filename parsing, tagging, chunking, embeddings, hybrid retrieval, RAG answers, deep report generation, PDF/Typst rendering, and DB access — plus the `radar/`（觀點雷達）and `reading/`（閱讀頁）sub-packages. `app/config.py` centralizes **all** tunables. `app/templates/` holds the Typst report templates and their registry.

`web/server.py` is now only the composition layer (app construction + auth middleware); routes live in `web/routers/*.py` and cross-module shared symbols go through `web/deps.py`. `web/static/` contains just `login.html`.

**The frontend is `frontend/` — React 19 + TypeScript + Vite**, built to `frontend/dist`. `scripts/` holds ingestion, tagging, evaluation, summary, signal/takeaway extraction, and maintenance jobs. `eval/` holds the RAGAS/report evaluation harness. `db/schema.sql` defines the `research` PostgreSQL/pgvector schema. `tests/` contains 86 Python test files; frontend tests are colocated as `*.test.ts(x)` under `frontend/src/`.

## Build, Test, and Development Commands

Always prefix shell commands with `rtk` when working from Codex.

- `rtk uv sync`: install Python dependencies from `pyproject.toml` and `uv.lock`.
- `rtk make setup`: install deps, start/apply the pgvector schema, and prepare local infrastructure.
- `rtk make serve`: run FastAPI on `http://localhost:8097`; code changes require restart.
- `rtk uv run pytest -q`: run the Python test suite (86 files).
- `rtk npm --prefix frontend test`: run the frontend suite (vitest).
- `rtk npm --prefix frontend run typecheck`: `tsc --noEmit`.
- `rtk npm --prefix frontend run build`: rebuild `frontend/dist` — **required for any frontend change to take effect**.
- `rtk uv run python scripts/eval_retrieval.py --help`: inspect retrieval evaluation.

**CI gates every PR** (`.github/workflows/ci.yml`): 後端（pytest）與前端（typecheck + vitest）兩個必要檢查，main 有分支保護。只跑 pytest 會在前端 job 上翻車。

> 舊文件曾寫 `node --test web/static/app/*.test.mjs`。該目錄已刪除，而且該指令在 bash 下會印出 `tests 0 / pass 0 / fail 0` 並 **exit 0** — 一份看起來全過、實際一個測試都沒跑的假綠。

## Coding Style & Naming Conventions

Use Python 3.11+ with explicit types where helpful, dataclasses for small value objects, and async SQLAlchemy sessions for DB work. Keep deterministic pipeline logic in Python; reserve Claude CLI calls for semantic labeling, summaries, Q&A, and generated reports. Frontend code lives in `frontend/src/` (React function components, CSS Modules, TanStack Query, zod at the API boundary); ESLint + `tsc --noEmit` are configured and enforced in CI.

**Tunables go in `app/config.py`**, not scattered `os.getenv` calls in service modules. The `REPORT_MARK_*` prefix applies **only** to the five auth/DB variables (see `.env.example`); all other keys (`ASK_*`, `REPORT_*`, `QA_*`, `FAITHFULNESS_*`, …) are unprefixed. All are loaded from repo-root `.env`.

## Testing Guidelines

Add or update `tests/test_*.py` for service behavior and API contracts. Prefer deterministic tests that mock LLM, embedding, filesystem, and DB boundaries unless the change is integration-level. For frontend code, add `*.test.ts(x)` beside the module under `frontend/src/`. Run the narrow test first, then `rtk uv run pytest -q` and `rtk npm --prefix frontend test`.

Two hard-won rules:

- **Test an API endpoint through HTTP, not by calling the handler object.** Calling `module.handler()` directly bypasses routing — that is how a decorator applied to the wrong function shipped a 422 to production with CI fully green.
- **When you add a parameter to a function that tests fake, update the fake's signature.** A stale fake raises `TypeError`, which a surrounding `except` swallows, and the code silently takes a different path. This has bitten this repo five times.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit style with scopes, often in Traditional Chinese, for example `feat(report): ...`, `fix(report): ...`, and `docs(plan): ...`. Before committing, inspect recent commit messages and the staged diff; do not use a plain English sentence. PRs should explain behavior changes, list validation commands, link issues or plans, and include screenshots for UI changes.

## Security & Agent-Specific Instructions

Answer user-facing questions in Traditional Chinese. Do not commit `.env`, generated data under `data/`, reports, screenshots, or local tool state. Auth is deny-by-default; keep login/session changes aligned with `web/auth.py` and `docs/EXTERNAL_ACCESS.md`. Treat `研報自動匯入/` as read-only source input.
