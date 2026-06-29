# Repository Guidelines

## Project Structure & Module Organization

`app/services/` contains extraction, filename parsing, tagging, chunking, embeddings, hybrid retrieval, RAG answers, deep report generation, PDF rendering, and DB access. `web/server.py` is the FastAPI composition layer for auth, search, browsing, Q&A, conversations, generated reports, and static pages. `web/static/` is a vanilla ES module frontend. `scripts/` holds ingestion, tagging, evaluation, summary, and maintenance jobs. `db/schema.sql` defines the `research` PostgreSQL/pgvector schema. `tests/` contains Python tests; frontend tests live beside modules as `*.test.mjs`.

## Build, Test, and Development Commands

Always prefix shell commands with `rtk` when working from Codex.

- `rtk uv sync`: install Python dependencies from `pyproject.toml` and `uv.lock`.
- `rtk make setup`: install deps, start/apply the pgvector schema, and prepare local infrastructure.
- `rtk make serve`: run FastAPI on `http://localhost:8097`; code changes require restart.
- `rtk uv run pytest -q`: run the Python test suite.
- `rtk node --test web/static/app/*.test.mjs`: run frontend module tests.
- `rtk uv run python scripts/eval_retrieval.py --help`: inspect retrieval evaluation.

## Coding Style & Naming Conventions

Use Python 3.11+ with explicit types where helpful, dataclasses for small value objects, and async SQLAlchemy sessions for DB work. Keep deterministic pipeline logic in Python; reserve Claude CLI calls for semantic labeling, summaries, Q&A, and generated reports. Frontend code should stay as focused ES modules under `web/static/app/`, using existing helpers. Environment variables use `REPORT_MARK_*` names and are loaded from `.env`.

## Testing Guidelines

Add or update `tests/test_*.py` for service behavior and API contracts. Prefer deterministic tests that mock LLM, embedding, filesystem, and DB boundaries unless the change is integration-level. For frontend utilities, add `*.test.mjs` next to the module. Run the narrow test first, then `rtk uv run pytest -q`; run Node tests when touching `web/static/app/`.

## Commit & Pull Request Guidelines

Recent history uses Conventional Commit style with scopes, often in Traditional Chinese, for example `feat(report): ...`, `fix(report): ...`, and `docs(plan): ...`. Before committing, inspect recent commit messages and the staged diff; do not use a plain English sentence. PRs should explain behavior changes, list validation commands, link issues or plans, and include screenshots for UI changes.

## Security & Agent-Specific Instructions

Answer user-facing questions in Traditional Chinese. Do not commit `.env`, generated data under `data/`, reports, screenshots, or local tool state. Auth is deny-by-default; keep login/session changes aligned with `web/auth.py` and `docs/EXTERNAL_ACCESS.md`. Treat `研報自動匯入/` as read-only source input.
