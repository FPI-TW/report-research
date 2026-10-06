"""管理後台的「診斷」快照（`GET /api/admin/diagnostics`）：這個 web 行程此刻的實際狀態。

出問題時管理員一頁看完，並把整份 JSON（診斷包）貼給維運。所以兩條鐵律：

1. **便宜**：不跑子行程（web 不一定能跑 git）、不打付費 API。DB 只送 `SELECT 1`、`SHOW server_version`
   與讀 `public.alembic_version`；R2 與 DeepSeek 只**被動**讀 healthz 上一次的結論
   （`web/routers/health.storage_snapshot`、`llm_health.cached_snapshot`，都不觸發探測）；維運代理問一次
   `list`（逾時壓到 `OPS_TIMEOUT`）。路由層整份 TTL 快取 30 秒。
2. **不含任何祕密**：設定值一律白名單（`config_section` 逐鍵列出，沒有「把 Settings 整份倒出來」）；
   祕密只回「有沒有設」的布林值；DB 連線字串只留 `host:port/db`（`schema_migrations.target_identity`）。
   白名單之外再加一層 `scrub`：整份回應的每個字串，凡出現名稱像祕密的環境變數之值、DB 密碼、
   或 `scheme://帳號:密碼@` 形態，一律換成 `[redacted]`——防的是狀態檔訊息、例外字串這類
   不是我們逐字組出來的文字。

每一段各自 try：某段失敗（DB 掛、代理連不上、/proc 讀不到）只讓那一段帶 `error`（**只記例外型別**，
不記訊息——訊息可能夾帶連線字串），整頁照樣 200。

## 版本資訊怎麼取得（為什麼讀 `.git` 檔而不是跑 git）

生產 web 跑在 systemd 底下，PATH 與身分都不保證有 git，也不該為了顯示版本 fork 子行程。這台機器的
主 checkout 就是部署目錄，所以直接讀 `.git/HEAD` → loose ref → `packed-refs`（worktree 的 `.git` 是
`gitdir:` 指標檔，refs 在 `commondir`）。讀兩次：

- **行程啟動時**（本模組 import 的時刻，web 啟動時就 import）：這才是「正在跑的程式」的版本。
- **此刻磁碟上**：兩者不同＝有人更新了程式卻還沒重啟 web（`restart_pending`）。這是最常見的
  「我明明部署了怎麼沒生效」。

沒有 `.git`（例如用 rsync 部署、不帶 .git 的環境）時回 `available=false`，不猜。前端沒有另外產生
build 資訊檔：`frontend/dist/index.html` 的修改時間＝最後一次 `make build-web` 的時間，入口 bundle
的檔名帶內容雜湊、可以分辨兩次 build 是否相同。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import re
import resource
import socket
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timezone
from functools import lru_cache
from importlib import metadata
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
SPA_INDEX = REPO_ROOT / "frontend" / "dist" / "index.html"
DEFAULT_SCHEMA_STATUS_FILE = REPO_ROOT / "data" / "schema_check.json"
SCHEMA_STATUS_FILE_ENV = "SCHEMA_CHECK_STATUS_FILE"  # 與 scripts/schema_baseline.py 的 STATUS_FILE_ENV 相同

DB_TIMEOUT = 3.0
OPS_TIMEOUT = 3.0
# 每日檢查（report-mark-schema-check.timer，每日 05:20）的狀態檔多舊算過期：一天＋餘裕。
SCHEMA_CHECK_STALE_HOURS = 36.0
MAX_STATUS_BYTES = 256 * 1024
_MAX_SMALL_FILE = 64 * 1024

# 顯示版本的套件（只讀 metadata，不 import——torch 這類 import 一次要好幾秒、好幾百 MB）。
PACKAGES = (
    "fastapi", "starlette", "uvicorn", "pydantic", "sqlalchemy", "asyncpg", "alembic", "httpx", "boto3",
    "pgvector", "torch", "transformers", "FlagEmbedding", "sentence-transformers", "pypdf", "pdfplumber",
)

REDACTED = "[redacted]"
# 名稱像祕密的環境變數：值一律不得出現在回應裡（scrub 的依據）。_URL 也算：連線字串、webhook、
# R2 endpoint（帶帳號 id）都可能夾帶憑證。
SENSITIVE_NAME = re.compile(r"KEY|SECRET|PASSWORD|PASSWD|TOKEN|WEBHOOK|CREDENTIAL|DSN|_URL$", re.IGNORECASE)
# 太短的值不拿來比對（`0`、`1` 這種會把整份回應打爛），真正的祕密不會這麼短。
_MIN_SECRET_LEN = 6
_URL_CREDENTIALS = re.compile(r"([A-Za-z][A-Za-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+@")
_SHA_RE = re.compile(r"^[0-9a-f]{40}([0-9a-f]{24})?$")
_ENTRY_ASSET_RE = re.compile(r"""(?:src|href)="/app/assets/([^"?#]+\.(?:js|css))\"""")

SCHEMA_STATUSES = ("ok", "behind", "ahead", "unversioned", "ambiguous", "error")
WARMUP_STATES = ("skipped", "absent", "running", "done", "failed", "cancelled")


def _err(exc: BaseException) -> str:
    """錯誤只記例外型別：訊息可能夾帶連線字串、路徑或憑證。"""
    return type(exc).__name__


def _iso(ts: float | None) -> str | None:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds") if ts else None


def _read_small(path: Path, limit: int = _MAX_SMALL_FILE) -> str | None:
    try:
        with path.open("rb") as fh:
            raw = fh.read(limit + 1)
    except OSError:
        return None
    if len(raw) > limit:
        return None
    return raw.decode("utf-8", errors="replace")


# ── 祕密遮蔽 ─────────────────────────────────────────────────────────────


def secret_values(env: Mapping[str, str] | None = None, *, db_url: str | None = None) -> list[str]:
    """回應裡絕不能出現的字串：名稱像祕密的環境變數之值＋DB 密碼。長的排前面（先換長的，免得留下殘片）。"""
    env = os.environ if env is None else env
    values = {v.strip() for k, v in env.items() if SENSITIVE_NAME.search(k) and v and len(v.strip()) >= _MIN_SECRET_LEN}
    if db_url:
        try:
            from sqlalchemy.engine import make_url

            password = make_url(db_url).password
        except Exception:
            password = None
        if password and len(password) >= _MIN_SECRET_LEN:
            values.add(password)
    return sorted(values, key=len, reverse=True)


def scrub(value: Any, secrets: Iterable[str]) -> Any:
    """遞迴把字串裡的祕密值與 URL 帳密換成 `[redacted]`（dict 的鍵也掃）。白名單之外的第二道防線。"""
    secrets = list(secrets)

    def _s(text: str) -> str:
        text = _URL_CREDENTIALS.sub(rf"\1{REDACTED}@", text)
        for secret in secrets:
            if secret in text:
                text = text.replace(secret, REDACTED)
        return text

    def _walk(v: Any) -> Any:
        if isinstance(v, str):
            return _s(v)
        if isinstance(v, Mapping):
            return {(_s(k) if isinstance(k, str) else k): _walk(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_walk(x) for x in v]
        return v

    return _walk(value)


# ── 版本 ─────────────────────────────────────────────────────────────────


def _git_dirs(repo_root: Path) -> tuple[Path, Path] | None:
    """(gitdir, commondir)。`.git` 是目錄＝一般 checkout；是檔案＝worktree 的 `gitdir:` 指標。"""
    dot = repo_root / ".git"
    if dot.is_dir():
        gitdir = dot
    elif dot.is_file():
        content = (_read_small(dot) or "").strip()
        if not content.startswith("gitdir:"):
            return None
        gitdir = Path(content[len("gitdir:"):].strip())
        if not gitdir.is_absolute():
            gitdir = (repo_root / gitdir).resolve()
    else:
        return None
    common = (_read_small(gitdir / "commondir") or "").strip()
    commondir = (gitdir / common).resolve() if common else gitdir
    return gitdir, commondir


def _resolve_ref(ref: str, gitdir: Path, commondir: Path) -> str | None:
    if ".." in ref or not ref.startswith("refs/"):
        return None
    for base in (gitdir, commondir):
        sha = (_read_small(base / ref) or "").strip()
        if _SHA_RE.match(sha):
            return sha
    packed = _read_small(commondir / "packed-refs", limit=8 * 1024 * 1024) or ""
    for line in packed.splitlines():
        parts = line.strip().split(" ", 1)
        if len(parts) == 2 and parts[1] == ref and _SHA_RE.match(parts[0]):
            return parts[0]
    return None


def read_git_head(repo_root: Path = REPO_ROOT) -> dict:
    """{available, reason, commit, branch}。只讀檔案、不跑 git；任何意外都回 available=false。"""
    try:
        dirs = _git_dirs(repo_root)
        if dirs is None:
            return {"available": False, "reason": "no_git_dir", "commit": None, "branch": None}
        gitdir, commondir = dirs
        head = (_read_small(gitdir / "HEAD") or "").strip()
        if head.startswith("ref:"):
            ref = head[len("ref:"):].strip()
            branch = ref.removeprefix("refs/heads/")
            sha = _resolve_ref(ref, gitdir, commondir)
        else:
            branch, sha = None, head if _SHA_RE.match(head) else None
        if sha is None:
            return {"available": False, "reason": "unresolved_head", "commit": None, "branch": branch}
        return {"available": True, "reason": None, "commit": sha, "branch": branch}
    except Exception as exc:  # 診斷不得因為讀版本而失敗
        return {"available": False, "reason": _err(exc), "commit": None, "branch": None}


def _process_start_time() -> float:
    """行程啟動的 epoch 秒（/proc/self/stat 的 starttime＋開機時刻）；讀不到退回本模組 import 的時刻。"""
    try:
        stat = Path("/proc/self/stat").read_text()
        fields = stat[stat.rindex(")") + 2:].split()
        start_ticks = int(fields[19])  # 第 22 欄（扣掉前兩欄）
        btime = next(int(line.split()[1]) for line in Path("/proc/stat").read_text().splitlines()
                     if line.startswith("btime "))
        return btime + start_ticks / os.sysconf("SC_CLK_TCK")
    except Exception:
        return time.time()


PROCESS_STARTED_AT = _process_start_time()
GIT_AT_START = read_git_head()


def git_section(repo_root: Path = REPO_ROOT) -> dict:
    now = read_git_head(repo_root)
    start = GIT_AT_START
    restart_pending = None
    if start["available"] and now["available"]:
        restart_pending = start["commit"] != now["commit"]
    return {
        "available": bool(start["available"] or now["available"]),
        "reason": start["reason"] if not start["available"] else None,
        "commit_at_start": start["commit"],
        "branch_at_start": start["branch"],
        "commit_on_disk": now["commit"],
        "branch_on_disk": now["branch"],
        "restart_pending": restart_pending,
    }


def frontend_section(index: Path = SPA_INDEX) -> dict:
    try:
        st = index.stat()
    except OSError:
        return {"available": False, "built_at": None, "entry_assets": []}
    html = _read_small(index, limit=256 * 1024) or ""
    assets = sorted(set(_ENTRY_ASSET_RE.findall(html)))
    return {"available": True, "built_at": _iso(st.st_mtime), "entry_assets": assets}


def package_versions(names: Iterable[str] = PACKAGES) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for name in names:
        try:
            out[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            out[name] = None
    return out


def versions_section() -> dict:
    return {
        "git": git_section(),
        "frontend": frontend_section(),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "packages": package_versions(),
    }


# ── schema ───────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def code_revisions() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(baseline→head 的 revision 鏈, head 清單)。只讀 db/migrations/，不連 DB；行程內快取（程式不重啟不會變）。"""
    from app.services import schema_migrations as sm

    script = sm._script_directory()
    heads = tuple(script.get_heads())
    chain = tuple(r.revision for r in sm.revision_chain(heads[0])) if len(heads) == 1 else ()
    return chain, heads


def compare_revisions(
    db_revisions: list[str] | None, *, chain: Iterable[str], heads: Iterable[str],
) -> tuple[str, list[str]]:
    """(status, pending)。判定與 `scripts/schema_baseline.py` 的 `compare_versions` 相同（web 不 import scripts，
    `tests/test_admin_diagnostics_api.py` 逐例比對兩邊）。"""
    chain, heads = list(chain), list(heads)
    if len(heads) != 1:
        return "ambiguous", []
    if not db_revisions:
        return "unversioned", []
    if len(db_revisions) > 1:
        return "ambiguous", []
    rev, head = db_revisions[0], heads[0]
    if rev == head:
        return "ok", []
    if rev in chain:
        return "behind", chain[chain.index(rev) + 1:]
    return "ahead", []


def schema_status_path() -> Path:
    return Path(os.environ.get(SCHEMA_STATUS_FILE_ENV) or DEFAULT_SCHEMA_STATUS_FILE)


def schema_daily_check(now: datetime | None = None) -> dict:
    """每日 schema 檢查狀態檔（`scripts/schema_baseline.py scheduled` 寫）的摘要。沒有檔案不是錯誤。"""
    now = now or datetime.now(timezone.utc)
    base = {
        "available": False, "unavailable_reason": None, "checked_at": None, "age_hours": None, "stale": False,
        "mode": None, "exit_code": None, "alert": None, "problems": [], "message": None,
        "version_status": None, "drift_status": None, "drift_count": None,
    }
    path = schema_status_path()
    try:
        size = path.stat().st_size
    except OSError:
        return {**base, "unavailable_reason": "missing"}
    if size > MAX_STATUS_BYTES:
        return {**base, "unavailable_reason": "too_large"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {**base, "unavailable_reason": "invalid"}
    if not isinstance(data, dict):
        return {**base, "unavailable_reason": "invalid"}

    def _s(v: Any, limit: int = 2000) -> str | None:
        return v[:limit] if isinstance(v, str) else None

    def _i(v: Any) -> int | None:
        return v if isinstance(v, int) and not isinstance(v, bool) else None

    checked_at, age = _s(data.get("checked_at")), None
    if checked_at:
        try:
            parsed = datetime.fromisoformat(checked_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            age = round((now - parsed).total_seconds() / 3600, 2)
        except ValueError:
            pass
    version = data.get("version") if isinstance(data.get("version"), dict) else {}
    drift = data.get("drift") if isinstance(data.get("drift"), dict) else {}
    problems = data.get("problems") if isinstance(data.get("problems"), list) else []
    return {
        **base,
        "available": True,
        "checked_at": checked_at,
        "age_hours": age,
        "stale": age is None or age > SCHEMA_CHECK_STALE_HOURS,
        "mode": _s(data.get("mode"), 32),
        "exit_code": _i(data.get("exit_code")),
        "alert": data.get("alert") if isinstance(data.get("alert"), bool) else None,
        "problems": [p[:64] for p in problems if isinstance(p, str)][:20],
        "message": _s(data.get("message")),
        "version_status": _s(version.get("status"), 32),
        "drift_status": _s(drift.get("status"), 32),
        "drift_count": _i(drift.get("drift_count")),
    }


# ── 執行環境 ──────────────────────────────────────────────────────────────


def _proc_status() -> dict[str, int]:
    out: dict[str, int] = {}
    text = _read_small(Path("/proc/self/status")) or ""
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if key in ("VmRSS", "VmHWM") and parts:
            out[key] = int(parts[0]) * 1024  # kB
        elif key == "Threads" and parts:
            out[key] = int(parts[0])
    return out


def runtime_section(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    proc = _proc_status()
    peak = proc.get("VmHWM")
    if peak is None:
        try:
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024  # Linux：kB
        except Exception:
            peak = None
    local = datetime.now().astimezone()
    return {
        "pid": os.getpid(),
        "hostname": socket.gethostname(),
        "started_at": _iso(PROCESS_STARTED_AT),
        "uptime_s": round(max(0.0, now - PROCESS_STARTED_AT), 1),
        "rss_bytes": proc.get("VmRSS"),
        "peak_rss_bytes": peak,
        "threads": proc.get("Threads"),
        "timezone": {
            "tz_env": os.environ.get("TZ") or None,
            "name": local.tzname() or "",
            "utc_offset": local.strftime("%z"),
        },
        "python_executable": sys.executable,
    }


def _flag(name: str) -> bool:
    return os.environ.get(name) == "1"


def _present(*names: str) -> bool:
    return all((os.environ.get(n) or "").strip() for n in names)


def config_section() -> dict:
    """**白名單**：只有這裡逐鍵列出的值會出現。祕密只給「有沒有設」。新增鍵前先想：它可能含帳密嗎？"""
    from app.config import get_settings
    from app.services import db, llm_models
    from app.services.schema_migrations import target_identity

    s = get_settings()
    return {
        "db_target": target_identity(db.DATABASE_URL),
        "object_storage_mode": s.object_storage_mode,
        "llm_provider": s.llm_provider,
        "models": dict(llm_models.resolve_all(llm_models.ONLINE_TASKS)),
        "extractor": s.extractor,
        "log_level": s.log_level,
        "ops_agent_environment": s.ops_agent_environment,
        "ops_agent_socket": s.ops_agent_socket,
        "flags": {
            "ask_enable_web": s.ask_enable_web,
            "ask_rerank_enabled": s.ask_rerank_enabled,
            "qa_agentic_enabled": s.qa_agentic_enabled,
            "ask_faithfulness_enabled": s.ask_faithfulness_enabled,
            "trusted_data_enabled": s.trusted_data_enabled,
            "skip_warmup": _flag("SKIP_WARMUP"),
            "dev_no_auth": _flag("DEV_NO_AUTH"),
        },
        "secrets_present": {
            "deepseek_api_key": _present("DEEPSEEK_API_KEY"),
            "session_secret": _present("REPORT_MARK_SESSION_SECRET"),
            "edge_secret": _present("REPORT_MARK_EDGE_SECRET"),
            "r2_credentials": _present("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"),
            "alert_webhook": _present("REPORT_MARK_ALERT_WEBHOOK"),
        },
        "limits": {
            "db_pool_size": s.db_pool_size,
            "db_max_overflow": s.db_max_overflow,
            "db_pool_timeout_s": s.db_pool_timeout,
            "db_statement_timeout_ms": s.db_statement_timeout_ms,
            "db_idle_tx_timeout_ms": s.db_idle_tx_timeout_ms,
            "embed_max_concurrency": s.embed_max_concurrency,
            "ask_faithfulness_sample_rate": s.ask_faithfulness_sample_rate,
            "ask_faithfulness_max_inflight": s.ask_faithfulness_max_inflight,
        },
    }


def models_section(warmup_task: asyncio.Task | None) -> dict:
    """嵌入／rerank 模型是否已載入、暖機 task 的狀態。被動：只看模組狀態，不觸發載入。"""
    embed = sys.modules.get("app.services.embed")
    rerank = sys.modules.get("app.services.rerank")
    warmup_error = None
    if _flag("SKIP_WARMUP"):
        warmup = "skipped"
    elif warmup_task is None:
        warmup = "absent"
    elif not warmup_task.done():
        warmup = "running"
    elif warmup_task.cancelled():
        warmup = "cancelled"
    elif warmup_task.exception() is not None:
        warmup, warmup_error = "failed", _err(warmup_task.exception())
    else:
        warmup = "done"
    return {
        "embed_model": getattr(embed, "MODEL_NAME", None),
        "embed_loaded": getattr(embed, "_model", None) is not None,
        "rerank_loaded": getattr(rerank, "_model", None) is not None,
        "rerank_load_failed": bool(getattr(rerank, "_load_failed", False)),
        "warmup": warmup,
        "warmup_error": warmup_error,
    }


def pool_section(engine) -> dict:
    from app.config import get_settings

    s = get_settings()
    pool = engine.pool
    checked_out, checked_in = int(pool.checkedout()), int(pool.checkedin())
    # 不回 SQLAlchemy 的 overflow()：連線還沒開滿 pool_size 時它是負數，看了只會誤會。
    return {
        "pool_class": type(pool).__name__,
        "size": s.db_pool_size,
        "max_overflow": s.db_max_overflow,
        "checked_out": checked_out,
        "checked_in": checked_in,
        "open_connections": checked_out + checked_in,
    }


def gates_section(gates: Iterable[Any]) -> list[dict]:
    out = []
    for g in gates:
        free = getattr(getattr(g, "_sem", None), "_value", None)
        out.append({
            "name": str(g.name),
            "capacity": int(g.capacity),
            "in_use": int(g.capacity - free) if isinstance(free, int) else None,
            "waiting": int(g.waiting),
            "max_queue": int(g.max_queue),
        })
    return out


# ── 連通性 ────────────────────────────────────────────────────────────────


async def probe_db(session_factory) -> dict:
    """一個 session：`SELECT 1` 延遲、伺服器版本、`public.alembic_version` 的各列（表不存在為 None）。"""
    from sqlalchemy import text

    async with asyncio.timeout(DB_TIMEOUT):
        async with session_factory() as session:
            started = time.perf_counter()
            await session.execute(text("SELECT 1"))
            latency = (time.perf_counter() - started) * 1000
            version = (await session.execute(text("SHOW server_version"))).scalar()
            has_table = (await session.execute(
                text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))).scalar()
            revisions = None
            if has_table:
                rows = (await session.execute(text("SELECT version_num FROM public.alembic_version ORDER BY 1"))).all()
                revisions = [str(r[0]) for r in rows]
    return {"latency_ms": round(latency, 1), "server_version": str(version) if version else None,
            "revisions": revisions}


async def probe_ops_agent(client_factory: Callable[[], Any]) -> dict:
    client = client_factory()
    client.timeout = min(client.timeout, OPS_TIMEOUT)
    started = time.perf_counter()
    result = await client.request("list")
    items = result.get("items") if isinstance(result, dict) else None
    return {"latency_ms": round((time.perf_counter() - started) * 1000, 1),
            "services": len(items) if isinstance(items, list) else None}


async def _guard(coro, label: str) -> tuple[dict | None, str | None]:
    try:
        return await coro, None
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.info("診斷：%s 失敗：%s", label, _err(exc))
        return None, _err(exc)


def _sync_guard(fn: Callable[[], Any], fallback: dict, label: str) -> Any:
    try:
        return fn()
    except Exception as exc:
        logger.info("診斷：%s 失敗：%s", label, _err(exc), exc_info=True)
        return {**fallback, "error": _err(exc)}


async def snapshot(
    *,
    session_factory,
    engine,
    warmup_task: asyncio.Task | None,
    gates: Iterable[Any],
    storage_snapshot: Callable[[], dict],
    llm_snapshot: Callable[[], dict],
    ops_client_factory: Callable[[], Any],
    now: datetime | None = None,
) -> dict:
    """整份診斷（已 scrub）。永不拋例外：失敗的段落帶 `error`（例外型別）。"""
    from app.services import db

    now = now or datetime.now(timezone.utc)
    (db_probe, db_error), (ops_probe, ops_error) = await asyncio.gather(
        _guard(probe_db(session_factory), "DB"),
        _guard(probe_ops_agent(ops_client_factory), "維運代理"),
    )

    schema: dict = {"status": "error", "db_revisions": [], "code_heads": [], "pending": [], "error": None}
    try:
        chain, heads = await asyncio.to_thread(code_revisions)
        schema["code_heads"] = list(heads)
        if db_error is not None:
            schema["error"] = db_error
        else:
            revs = db_probe["revisions"]
            schema["db_revisions"] = list(revs or [])
            schema["status"], schema["pending"] = compare_revisions(revs, chain=chain, heads=heads)
    except Exception as exc:
        schema["error"] = _err(exc)
    schema["daily_check"] = _sync_guard(lambda: schema_daily_check(now), {
        "available": False, "unavailable_reason": "error", "checked_at": None, "age_hours": None, "stale": False,
        "mode": None, "exit_code": None, "alert": None, "problems": [], "message": None,
        "version_status": None, "drift_status": None, "drift_count": None,
    }, "每日 schema 檢查狀態檔")

    storage = _sync_guard(lambda: {**storage_snapshot(), "error": None},
                          {"state": "unknown", "consecutive_failures": 0, "last_probe_age_s": None,
                           "last_probe_ok": None}, "R2 快取狀態")
    llm = _sync_guard(lambda: {**llm_snapshot(), "error": None},
                      {"state": "unknown", "key_configured": False, "ask_uses_http": False,
                       "consecutive_failures": 0, "last_check_age_s": None, "quota_latched": False}, "LLM 快取狀態")

    result = {
        "generated_at": now.isoformat(timespec="seconds"),
        "versions": _sync_guard(lambda: {**versions_section(), "error": None}, {
            "git": None, "frontend": None, "python": platform.python_version(), "platform": None, "packages": {},
        }, "版本"),
        "schema_info": schema,
        "runtime": _sync_guard(lambda: {**runtime_section(), "error": None}, {}, "執行環境"),
        "config": _sync_guard(lambda: {**config_section(), "error": None}, {}, "設定"),
        "models": _sync_guard(lambda: {**models_section(warmup_task), "error": None}, {}, "模型"),
        "db_pool": _sync_guard(lambda: {**pool_section(engine), "error": None}, {}, "連線池"),
        "gates": _sync_guard(lambda: gates_section(gates), {}, "併發閘"),
        "checks": {
            "db": {"ok": db_error is None, "latency_ms": (db_probe or {}).get("latency_ms"),
                   "server_version": (db_probe or {}).get("server_version"), "error": db_error},
            "storage": storage,
            "llm": llm,
            "ops_agent": {"ok": ops_error is None, "latency_ms": (ops_probe or {}).get("latency_ms"),
                          "services": (ops_probe or {}).get("services"), "error": ops_error},
        },
    }
    if isinstance(result["gates"], dict):  # 併發閘那段失敗：清單形狀不變，錯誤另放
        result["gates_error"] = result["gates"].get("error")
        result["gates"] = []
    else:
        result["gates_error"] = None
    return scrub(result, secret_values(db_url=db.DATABASE_URL))
