"""容器 entrypoint：一份映像、多個角色（`deploy/container/Dockerfile` 的 ENTRYPOINT）。

    web            uvicorn（:8097，單一 worker）；啟動前過 schema 閘門與必填設定檢查
    worker         上傳 worker 迴圈：每 UPLOAD_WORKER_INTERVAL_SECONDS 秒跑一輪 scripts/process_uploads.sh
    scheduler      週期工作（supercronic）；工作清單在 deploy/container/scheduler.py
    migrate        one-shot：全庫備份並驗證 → 逐字確認目標 → alembic upgrade head（空庫走 baseline 首次安裝）
    schema-version 只做一次 schema 版本比對並印出結論（唯讀；退出碼同 `make schema-version`）
    create-admin   scripts/create_admin.py（其餘參數照傳；互動輸入密碼要 `docker compose run`，它預設配 TTY）
    其他           原樣執行（例如 `bash`、`python scripts/xxx.py`），不過閘門

── schema 閘門（web／worker／scheduler／create-admin）────────────────────────────
只**檢查**、不變更：重用 `scripts/schema_baseline.py` 的 `check_version`（與 `make schema-version` 同一個判斷）。
一致才啟動；落後、超前、未接管、不明確一律拒起，並印出下一步要執行的指令。DB 還沒起來（連不上）時在
SCHEMA_GATE_WAIT_SECONDS 秒內重試——compose 的 depends_on 只保證 postgres 容器健康，不保證網路已通。
這個閘門只在容器 entrypoint：systemd 主機（`make serve`、各 unit）的行為不變。

為什麼不在啟動時自動 migrate：多個角色同時起來時誰先 upgrade 是競賽，而且 migration 前必須先有一份
驗證過的全庫備份、必須由人逐字確認目標（app/services/schema_migrations.py）。升級一律是明確的一步
`docker compose run --rm migrate`。

── migrate ────────────────────────────────────────────────────────────────
  一致（ok）        已在 head，不做任何事（不備份）。
  空庫（unversioned 且 research schema 沒有物件）
                    首次安裝：alembic upgrade head 從 baseline（0001）建起；空庫沒有東西可備份、免確認
                    （與 `make schema` 對空庫的規則相同）。
  落後（behind）    確認 REPORT_MARK_MIGRATE_CONFIRM 逐字等於 host:port/db（不符就在備份前拒絕，免得白做一次
                    全庫 dump）→ 全庫 `pg_dump -Fc` 到 MIGRATE_BACKUP_DIR 並驗證
                    （`schema_baseline.full_dump_preflight`：空間、時限、PGDMP 檔頭、`pg_restore -l`）→ upgrade
                    （db/migrations/env.py 的守門對同一個確認值再判一次）→ 讀回版本必須一致。備份沒通過就不升級。
  其他              超前、未接管的既有庫、不明確：拒絕（退出碼 2）並說明原因。

退出碼：0 成功／一致；1 落後（閘門）或備份 preflight 失敗；2 拒絕或無法判斷；3 DB 連不上；其餘照子行程。
"""
from __future__ import annotations

import asyncio
import os
import shlex
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

EXIT_OK, EXIT_BEHIND, EXIT_REFUSED, EXIT_DB_UNAVAILABLE = 0, 1, 2, 3

WEB_PORT_ENV = "WEB_PORT"
DEFAULT_WEB_PORT = 8097
GATE_WAIT_ENV = "SCHEMA_GATE_WAIT_SECONDS"
DEFAULT_GATE_WAIT = 120.0
GATE_INTERVAL = 3.0
WORKER_INTERVAL_ENV = "UPLOAD_WORKER_INTERVAL_SECONDS"
DEFAULT_WORKER_INTERVAL = 300.0  # 與 report-mark-upload.timer 的每 5 分鐘相同
MIGRATE_BACKUP_DIR_ENV = "MIGRATE_BACKUP_DIR"
MIGRATE_DUMP_TIMEOUT_ENV = "MIGRATE_DUMP_TIMEOUT_MIN"
CRONTAB_PATH = Path("/tmp/report-mark.crontab")

MIGRATE_COMMAND = "docker compose run --rm migrate"
GATED_ROLES = ("web", "worker", "scheduler", "create-admin")
ROLES = (*GATED_ROLES, "migrate", "schema-version")

# 範本裡的佔位值：程式本身只擋空值，佔位字串會被當成真金鑰（AGENTS.md「指令」），容器在這裡多擋一層。
SESSION_SECRET_PLACEHOLDERS = frozenset({"請填入長隨機字串", "change-me", "changeme", "fixed-test-secret-change-me"})
SESSION_SECRET_MIN_LEN = 32


def log(msg: str) -> None:
    print(f"[entrypoint] {msg}", file=sys.stderr, flush=True)


# ───────────────────────── 必填設定 ─────────────────────────

def config_problems(role: str, env: Mapping[str, str]) -> list[str]:
    """啟動前必須成立的設定；回空清單＝放行。"""
    problems = []
    if not (env.get("REPORT_MARK_DB_URL") or "").strip():
        # 沒設時 app/services/db.py 退回 localhost:5436/research（辦公室主機的真實資料庫）；容器裡一律要明講。
        problems.append("REPORT_MARK_DB_URL 未設定（容器不使用程式內建的預設連線）")
    if role == "web":
        secret = (env.get("REPORT_MARK_SESSION_SECRET") or "").strip()
        if not secret or secret in SESSION_SECRET_PLACEHOLDERS or len(secret) < SESSION_SECRET_MIN_LEN:
            problems.append(
                f"REPORT_MARK_SESSION_SECRET 未設定、是範本佔位值，或短於 {SESSION_SECRET_MIN_LEN} 字元"
                "（產生方式：openssl rand -hex 32）"
            )
    return problems


# ───────────────────────── schema 閘門 ─────────────────────────

def gate_verdict(result) -> tuple[int, str]:
    """`schema_baseline.VersionResult` → (退出碼, 給操作者的訊息)。0＝放行。純函式。"""
    status = result.status
    if status == "ok":
        return EXIT_OK, f"schema 版本一致（revision {result.db_revision}）"
    if status == "behind":
        return EXIT_BEHIND, (f"{result.message}\n這份映像需要 revision {result.expected_head}；"
                             f"先備份並升級：REPORT_MARK_MIGRATE_CONFIRM=<host:port/db> {MIGRATE_COMMAND}")
    if status == "unversioned":
        return EXIT_REFUSED, (f"{result.message}\n空庫（首次安裝）請先執行：{MIGRATE_COMMAND}；"
                              "已有資料但未被 alembic 接管的既有庫要先走 schema-check＋stamp"
                              "（docs/CONTAINER_DEPLOYMENT.md「從主機部署遷入」）")
    if status == "ahead":
        return EXIT_REFUSED, (f"{result.message}\nDB 比這份映像新：改用與 DB 相符（或更新）的映像版本；"
                              "不要對 DB 做 downgrade")
    if status == "db_unavailable":
        return EXIT_DB_UNAVAILABLE, result.message
    return EXIT_REFUSED, result.message or f"schema 版本無法判斷（{status}）"


def wait_for_schema(check: Callable[[], Any], *, wait_s: float, interval: float = GATE_INTERVAL,
                    sleep: Callable[[float], None] = time.sleep,
                    clock: Callable[[], float] = time.monotonic) -> tuple[int, str, Any]:
    """跑版本比對；連不上就在 wait_s 秒內重試。回 (退出碼, 訊息, 最後一次的結果)。"""
    deadline = clock() + max(0.0, wait_s)
    while True:
        result = check()
        rc, msg = gate_verdict(result)
        if rc != EXIT_DB_UNAVAILABLE or clock() >= deadline:
            return rc, msg, result
        log(f"DB 還連不上，{interval:.0f} 秒後重試：{msg}")
        sleep(interval)


def _default_check() -> Any:
    from app.services.db import DATABASE_URL
    from scripts import schema_baseline as sb

    return asyncio.run(sb.check_version(DATABASE_URL))


def _gate_wait(env: Mapping[str, str]) -> float:
    try:
        return float(env.get(GATE_WAIT_ENV) or DEFAULT_GATE_WAIT)
    except ValueError:
        return DEFAULT_GATE_WAIT


def schema_gate(env: Mapping[str, str], *, check: Callable[[], Any] | None = None,
                sleep: Callable[[float], None] = time.sleep) -> int:
    rc, msg, _ = wait_for_schema(check or _default_check, wait_s=_gate_wait(env), sleep=sleep)
    log(("放行：" if rc == EXIT_OK else "拒絕啟動：") + msg)
    return rc


# ───────────────────────── migrate ─────────────────────────

@dataclass
class MigrateOps:
    """migrate 實際碰 DB 與外部程式的動作（測試以假物件替換）。"""

    identity: Callable[[], str]
    check: Callable[[], Any]
    research_has_objects: Callable[[], bool]
    database_size: Callable[[], int]
    dump: Callable[[Path, int, float], Any]  # (目錄, 庫大小, 時限秒) → schema_baseline.DumpResult
    upgrade: Callable[[], int]


def _real_ops() -> MigrateOps:
    from sqlalchemy import text

    from app.services import schema_migrations as sm
    from app.services.db import DATABASE_URL
    from scripts import schema_baseline as sb

    async def _has_objects() -> bool:
        eng = sb._engine(DATABASE_URL)
        try:
            async with eng.connect() as conn:
                return bool((await conn.execute(text(sm.RESEARCH_HAS_OBJECTS_SQL))).scalar())
        finally:
            await eng.dispose()

    def _upgrade() -> int:
        # 走 alembic CLI（子行程）：db/migrations/env.py 由 CLI 取得指令名，守門（空庫免確認、有資料要逐字
        # 確認）照常在那裡再判一次。
        return subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=REPO_ROOT).returncode

    return MigrateOps(
        identity=lambda: sm.target_identity(DATABASE_URL),
        check=_default_check,
        research_has_objects=lambda: asyncio.run(_has_objects()),
        database_size=lambda: asyncio.run(sb.database_size(DATABASE_URL)),
        dump=lambda d, size, timeout: sb.full_dump_preflight(DATABASE_URL, d, db_size=size, container=None,
                                                             timeout_s=timeout),
        upgrade=_upgrade,
    )


def _confirmed(env: Mapping[str, str], identity: str) -> bool:
    return (env.get("REPORT_MARK_MIGRATE_CONFIRM") or "").strip() == identity


def run_migrate(env: Mapping[str, str], ops: MigrateOps | None = None, *,
                sleep: Callable[[float], None] = time.sleep) -> int:
    ops = ops or _real_ops()
    _rc, msg, result = wait_for_schema(ops.check, wait_s=_gate_wait(env), sleep=sleep)
    identity = ops.identity()
    status = getattr(result, "status", None)
    if status == "ok":
        log(f"{identity} 已在 head（revision {result.db_revision}），不需要 migration")
        return EXIT_OK
    if status == "db_unavailable":
        log(f"拒絕：{msg}")
        return EXIT_DB_UNAVAILABLE

    if status == "unversioned":
        if ops.research_has_objects():
            log(f"拒絕：{identity} 已有 research schema 的物件，但沒有被 alembic 接管。既有庫不能直接 upgrade——"
                "先走 schema-check（必須零 drift）＋ schema-stamp-baseline（docs/CONTAINER_DEPLOYMENT.md）")
            return EXIT_REFUSED
        log(f"{identity} 是空庫：首次安裝，從 baseline 建到 head（空庫沒有資料可備份）")
        return _upgrade_and_verify(ops)

    if status != "behind":
        log(f"拒絕：{msg}")
        return EXIT_REFUSED

    pending = ", ".join(getattr(result, "pending", []) or [])
    log(f"{identity} 落後：DB 在 {result.db_revision}，映像期待 {result.expected_head}（待套用 {pending}）")
    if not _confirmed(env, identity):
        log(f"拒絕：對已有資料的庫做 migration 必須逐字確認目標。確認無誤後重跑："
            f"REPORT_MARK_MIGRATE_CONFIRM={identity} {MIGRATE_COMMAND}"
            "（寫在指令前或 .env 都可以）")
        return EXIT_REFUSED

    backup_dir = Path(env.get(MIGRATE_BACKUP_DIR_ENV) or Path(env.get("REPORT_MARK_BACKUP_DIR") or "/app/backups")
                      / "migrate")
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log(f"拒絕：無法建立備份目錄 {backup_dir}（{exc}）")
        return EXIT_BEHIND
    try:
        timeout_s = float(env.get(MIGRATE_DUMP_TIMEOUT_ENV) or 90) * 60
    except ValueError:
        timeout_s = 90 * 60
    log(f"全庫備份到 {backup_dir}（pg_dump -Fc＋pg_restore -l 驗證）")
    dump = ops.dump(backup_dir, ops.database_size(), timeout_s)
    log(dump.message)
    if not dump.ok:
        log("拒絕 migration：全庫備份 preflight 未通過")
        return EXIT_BEHIND
    return _upgrade_and_verify(ops)


def _upgrade_and_verify(ops: MigrateOps) -> int:
    rc = ops.upgrade()
    if rc != 0:
        log(f"alembic upgrade head 失敗（rc={rc}）；交易已回滾，DB 維持原版本（備份仍在）")
        return rc
    after = ops.check()
    vrc, vmsg = gate_verdict(after)
    if vrc != EXIT_OK:
        log(f"upgrade 之後版本仍不一致：{vmsg}")
        return EXIT_REFUSED
    log(f"完成：{vmsg}。接著 docker compose up -d")
    return EXIT_OK


# ───────────────────────── 角色 ─────────────────────────

def web_command(env: Mapping[str, str]) -> list[str]:
    port = str(env.get(WEB_PORT_ENV) or DEFAULT_WEB_PORT)
    # 單一行程是硬性前提（web/concurrency.py 偵測到多 worker 會拒絕啟動）。
    return ["uvicorn", "web.server:app", "--host", "0.0.0.0", "--port", port]


def scheduler_env(env: Mapping[str, str]) -> dict[str, str]:
    """排程角色的額外環境：每日備份（scripts/db_backup.sh）在容器裡直接連 DB，不經 docker exec。"""
    from scripts import schema_baseline as sb

    out = {"DB_BACKUP_VIA": "direct"}
    url = (env.get("REPORT_MARK_DB_URL") or "").strip()
    if url:
        out.update(sb.libpq_env(url))
    return out


class _Stop:
    requested = False


def run_worker(env: Mapping[str, str], *, runner=subprocess.run, sleep: Callable[[float], None] = time.sleep,
               rounds: int | None = None) -> int:
    """上傳 worker 迴圈。每輪的鎖、退出碼與下游段都在 scripts/process_uploads.sh，這裡只負責定時呼叫。"""
    try:
        interval = max(10.0, float(env.get(WORKER_INTERVAL_ENV) or DEFAULT_WORKER_INTERVAL))
    except ValueError:
        interval = DEFAULT_WORKER_INTERVAL

    def _stop(signum, _frame):
        _Stop.requested = True
        log(f"收到訊號 {signum}，這一輪結束後停止")

    if rounds is None:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    done = 0
    while not _Stop.requested and (rounds is None or done < rounds):
        rc = runner(["bash", str(REPO_ROOT / "scripts" / "process_uploads.sh")], cwd=REPO_ROOT).returncode
        log(f"上傳 worker 一輪結束 rc={rc}")
        done += 1
        if rounds is not None and done >= rounds:
            break
        waited = 0.0
        while waited < interval and not _Stop.requested:
            step = min(5.0, interval - waited)
            sleep(step)
            waited += step
    return EXIT_OK


def main(argv: list[str], env: Mapping[str, str] | None = None, *, execvpe=os.execvpe,
         gate: Callable[[Mapping[str, str]], int] | None = None) -> int:
    env = dict(os.environ if env is None else env)
    args = argv[1:] or ["web"]
    role, rest = args[0], args[1:]
    if role not in ROLES:
        log(f"執行：{shlex.join(args)}（不過 schema 閘門）")
        execvpe(args[0], args, env)
        return EXIT_REFUSED  # execvpe 不會回來；測試替身才會走到這裡

    problems = config_problems(role, env)
    if problems:
        for p in problems:
            log(f"拒絕啟動：{p}")
        return EXIT_REFUSED

    if role == "migrate":
        return run_migrate(env)
    if role == "schema-version":
        rc, msg, _ = wait_for_schema(_default_check, wait_s=0)
        print(msg)
        return rc

    rc = (gate or schema_gate)(env)
    if rc != EXIT_OK:
        return rc

    if role == "web":
        cmd = web_command(env)
        execvpe(cmd[0], cmd, env)
    elif role == "worker":
        return run_worker(env)
    elif role == "scheduler":
        from deploy.container import scheduler

        try:
            jobs = scheduler.selected_jobs(env)
        except scheduler.SchedulerConfigError as exc:
            log(f"拒絕啟動：{exc}")
            return EXIT_REFUSED
        crontab = scheduler.render_crontab(jobs)
        CRONTAB_PATH.write_text(crontab, encoding="utf-8")
        log("排程工作：\n" + crontab)
        env.update(scheduler_env(env))
        cmd = ["supercronic", "-passthrough-logs", str(CRONTAB_PATH)]
        execvpe(cmd[0], cmd, env)
    elif role == "create-admin":
        cmd = [sys.executable, str(REPO_ROOT / "scripts" / "create_admin.py"), *rest]
        execvpe(cmd[0], cmd, env)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main(sys.argv))
