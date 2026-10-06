"""寫入類操作（`restart`、`run`）：硬性限制、execution group 互斥、鎖檔試探、argv 組裝。

**三層限制，缺一層也不會放行不該放行的東西**：
1. catalog 載入期（`catalog._check_write_actions`）：容器不給寫入、restart 只給 Web、命中
   `protocol.FORBIDDEN_WRITE_TARGET` 的 unit（PostgreSQL、nginx、cloudflared……）拒絕載入。
2. 代理執行前（這裡的 `check_write_allowed`）：同一組規則再擋一次。catalog 被誤設、或有人繞過載入直接給
   `Catalog` 物件時，PostgreSQL 的 restart 也在這裡被拒，runner 一次都不會被呼叫。
3. 作業系統（`deploy/polkit/50-report-mark-ops.rules`）：代理的使用者只能對列出的 unit 做 start／restart。

**互斥（不排隊，衝突直接 `already_running`）**：
- 代理行程內：同一個 group 從「接受請求」到「systemctl 回來」之間標記為忙碌（`Agent._busy_groups`），
  擋住兩個幾乎同時到的請求。
- 主機狀態：`systemctl show` 查同 group 全部 unit。其他 unit 的 ActiveState 是 activating／active／
  reloading／deactivating，或有排隊中的 job，即衝突；被操作的 unit 本身，`run` 同樣規則（oneshot 跑完才是
  inactive），`restart` 只看轉換中狀態與排隊 job（Web 平常就是 active，不能因此擋掉 restart）。
- 鎖檔：手動在主機上跑的批次（`make summaries`、`uv run python scripts/...`）不經 systemd、也不經 Web，
  unit 狀態看不到它們，所以 **不另建 DB 鎖**，而是直接試探批次自己的鎖：
  - `flock_files`：`scripts/_claude_lock.py` 的 `data/.claude_cli.lock`。以 `LOCK_SH|LOCK_NB` 試探、
    取得立刻放掉；被持有＝衝突。試探的那一瞬間（微秒級）若正好有批次要取鎖，批次會 rc=75（「不跑」
    不是「跑壞」，下一輪補上）——這是非阻塞試探無法完全避免的窗口。
  - `pid_files`：`scripts/sync_new_reports.sh` 的 `data/.sync_new_reports.lock` 是 PID 檔（不是 flock），
    比照 sync 自己的判斷：記的 PID 還活著（`kill -0` 成功或 EPERM）＝衝突。
  - **檔案不存在**：所在目錄存在＝從沒有人取過鎖＝沒被持有；目錄不存在或看不到＝`lock_unavailable`
    （路徑設錯、或代理的 sandbox 沒有綁進那個目錄）。無法確認就不執行。

**執行**：一律 `systemctl --no-ask-password <verb> --no-block -- <catalog 的 unit>`，argv 沒有任何一段來自請求。
`run` 同步執行（`--no-block` 只把 job 排進 systemd 就回來）；`restart` 由 server 先把回應送出、
`restart_delay` 秒後才執行——重啟 Web 會中斷這次請求本身，先送回應前端才拿得到 202。
前端輪詢 status，`systemd.invocation_id` 換成新的值就是新的一輪起來了。
"""

from __future__ import annotations

import errno
import fcntl
import json
import os

from ops_agent.backends import clean_line, parse_ts
from ops_agent.catalog import Catalog, Service
from ops_agent.protocol import FORBIDDEN_WRITE_TARGET, RESTARTABLE_UNITS, WRITE_ACTIONS, ProtocolError

CHECK_PROPS = ("Id", "LoadState", "ActiveState", "SubState", "Job", "InvocationID", "ActiveEnterTimestamp",
               "ExecMainStartTimestamp")
# 其他 unit（以及 run 的對象本身）處於這些狀態＝有工作在跑或正在轉換。
BUSY_STATES = frozenset({"activating", "active", "reloading", "deactivating", "refreshing"})
# restart 的對象本身只看轉換中狀態：Web 平常就是 active。
TRANSITION_STATES = frozenset({"activating", "reloading", "deactivating", "refreshing"})
CMD_MAX_BYTES = 64 * 1024
_HOLDER_MAX_BYTES = 4096


class LockProbeError(Exception):
    pass


def check_write_allowed(svc: Service, action: str) -> None:
    """代理端的硬性限制：與 catalog 載入期同一組規則，catalog 怎麼寫都放不開。"""
    if action not in WRITE_ACTIONS:  # pragma: no cover - 呼叫端只會傳寫入類
        raise ProtocolError("action_not_allowed", f"{action} 不是寫入類操作")
    if svc.kind != "systemd" or not svc.unit:
        raise ProtocolError("action_not_allowed", f"服務 {svc.name} 是容器，一律唯讀")
    if FORBIDDEN_WRITE_TARGET.search(svc.unit):
        raise ProtocolError("action_not_allowed", f"服務 {svc.name}（{svc.unit}）永遠不允許 {action}")
    if action == "restart" and svc.unit not in RESTARTABLE_UNITS:
        raise ProtocolError("action_not_allowed", f"restart 只開放 Web；服務 {svc.name} 不允許")
    if action == "run" and svc.unit in RESTARTABLE_UNITS:
        raise ProtocolError("action_not_allowed", f"服務 {svc.name} 是常駐服務，不用 run")
    if not svc.group:
        raise ProtocolError("action_not_allowed", f"服務 {svc.name} 沒有 execution group，不允許 {action}")


def group_units(catalog: Catalog, svc: Service) -> list[str]:
    """被操作的 unit 排第一，接著同 group 的其他 systemd unit（timer 不算：等待中的 timer 本來就是 active）。"""
    units = [svc.unit]
    for other in catalog.group_members(svc.group):
        if other.kind == "systemd" and other.unit and other.unit not in units:
            units.append(other.unit)
    return units


def group_locks(catalog: Catalog, svc: Service) -> tuple[list[str], list[str]]:
    flocks: list[str] = []
    pids: list[str] = []
    for member in (svc, *catalog.group_members(svc.group)):
        flocks += [p for p in member.flock_files if p not in flocks]
        pids += [p for p in member.pid_files if p not in pids]
    return flocks, pids


def _parse_blocks(text: str) -> list[dict[str, str]]:
    blocks = []
    for raw in text.strip("\n").split("\n\n"):
        props: dict[str, str] = {}
        for line in raw.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key] = value
        blocks.append(props)
    return blocks


def _job_pending(props: dict[str, str]) -> bool:
    job = props.get("Job", "").strip()
    return bool(job) and job != "0"


async def check_units(catalog: Catalog, runner, svc: Service, action: str) -> dict:
    """查同 group 的 unit 狀態；衝突拋 `already_running`，查不到一律拒絕（無法確認就不執行）。

    回傳被操作 unit 目前的 InvocationID 等，讓前端輪詢時分得出新舊兩輪。
    """
    cfg = catalog.agent
    units = group_units(catalog, svc)
    argv = [cfg.systemctl, "show", "--no-pager", "--timestamp=unix", "-p", ",".join(CHECK_PROPS), "--", *units]
    res = await runner.run(argv, timeout=cfg.command_timeout, max_bytes=CMD_MAX_BYTES, env={"TZ": "UTC"})
    if res.timed_out:
        raise ProtocolError("command_timeout", "systemctl show 逾時，無法確認是否有工作在跑")
    if res.returncode != 0 or res.truncated:
        raise ProtocolError("command_failed", f"systemctl show 失敗（rc={res.returncode}），無法確認是否有工作在跑")
    blocks = _parse_blocks(res.stdout.decode("utf-8", "replace"))
    if len(blocks) != len(units):
        raise ProtocolError("command_failed", f"systemctl show 輸出無法對應（{len(blocks)} 段／{len(units)} 個 unit）")
    by_unit = dict(zip(units, blocks))
    target = by_unit[svc.unit]
    if target.get("LoadState") != "loaded":
        raise ProtocolError("command_failed", f"{svc.unit} 的 LoadState 是 {target.get('LoadState') or '未知'}，不執行")
    for unit, props in by_unit.items():
        state = props.get("ActiveState", "")
        if unit == svc.unit and action == "restart":
            busy = state in TRANSITION_STATES
        else:
            busy = state in BUSY_STATES
        if busy or _job_pending(props):
            what = "有排隊中的 job" if _job_pending(props) and not busy else f"狀態是 {state}"
            raise ProtocolError("already_running", f"{unit} {what}（execution group {svc.group}），不排隊，請稍後再試")
    return {
        "previous_invocation_id": target.get("InvocationID") or None,
        "previous_active_enter_at": parse_ts(target.get("ActiveEnterTimestamp")),
        "previous_exec_main_start_at": parse_ts(target.get("ExecMainStartTimestamp")),
    }


def _parent_visible(path: str) -> bool:
    return os.path.isdir(os.path.dirname(path))


def _holder(fd: int) -> str:
    """`_claude_lock.py` 寫在鎖檔裡的 {script, pid}；只是診斷，讀不到就空字串。"""
    try:
        raw = os.pread(fd, _HOLDER_MAX_BYTES, 0)
        obj = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return ""
    if not isinstance(obj, dict):
        return ""
    script = obj.get("script") if isinstance(obj.get("script"), str) else "未知批次"
    pid = obj.get("pid") if isinstance(obj.get("pid"), int) else "?"
    return f"（{clean_line(script)[:64]}，pid={pid}）"


def probe_flock(path: str) -> str | None:
    """被持有回說明字串、沒被持有回 None；無法試探拋 LockProbeError。"""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOCTTY | os.O_NONBLOCK)
    except FileNotFoundError:
        if _parent_visible(path):
            return None
        raise LockProbeError(f"看不到鎖檔所在的目錄 {os.path.dirname(path)}") from None
    except OSError as exc:
        raise LockProbeError(f"打不開鎖檔 {path}：{exc.strerror}") from exc
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EACCES, errno.EWOULDBLOCK):
                return f"批次鎖 {os.path.basename(path)} 被持有{_holder(fd)}"
            raise LockProbeError(f"無法試探鎖檔 {path}：{exc.strerror}") from exc
        fcntl.flock(fd, fcntl.LOCK_UN)
        return None
    finally:
        os.close(fd)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # 行程在，只是不屬於代理的使用者
    except OSError:
        return False
    return True


def probe_pidfile(path: str) -> str | None:
    """比照 sync 自己的 `kill -0 "$(cat "$LOCK")"`：記的 PID 還活著＝被持有。內容不是 PID＝沒被持有。"""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(64)
    except FileNotFoundError:
        if _parent_visible(path):
            return None
        raise LockProbeError(f"看不到 PID 檔所在的目錄 {os.path.dirname(path)}") from None
    except OSError as exc:
        raise LockProbeError(f"讀不到 PID 檔 {path}：{exc.strerror}") from exc
    text = raw.decode("ascii", "replace").strip()
    if not text.isdigit() or int(text) <= 0:
        return None
    pid = int(text)
    return f"{os.path.basename(path)} 記的行程 pid={pid} 還在執行" if _pid_alive(pid) else None


def check_locks(catalog: Catalog, svc: Service, *, probe_flock_fn=probe_flock, probe_pid_fn=probe_pidfile) -> None:
    flocks, pids = group_locks(catalog, svc)
    try:
        for path in flocks:
            held = probe_flock_fn(path)
            if held:
                raise ProtocolError("already_running", f"{held}：有批次在跑（execution group {svc.group}），不排隊")
        for path in pids:
            held = probe_pid_fn(path)
            if held:
                raise ProtocolError("already_running", f"{held}：有批次在跑（execution group {svc.group}），不排隊")
    except LockProbeError as exc:
        raise ProtocolError("lock_unavailable", f"{exc}；無法確認批次是否在跑，不執行") from exc


def command_argv(catalog: Catalog, svc: Service, action: str) -> list[str]:
    verb = {"restart": "restart", "run": "start"}[action]
    return [catalog.agent.systemctl, "--no-ask-password", verb, "--no-block", "--", svc.unit]
