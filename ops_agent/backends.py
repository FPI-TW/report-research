"""systemd 與 docker 的查詢：參數組裝、輸出解析、回應裡只留白名單欄位。

**status 用 `systemctl show -p ...`，不用 `is-active`**：2026-08-18 的中斷期間 `is-active` 全程是
active（見 `deploy/systemd/report-mark-health.service` 的註解），而 oneshot 的 `Result=success`
可能是上一次、甚至從未執行過的預設值（`scripts/verify_oneshot_ran.sh`）。所以這裡回報原始屬性與
時間戳（`ExecMainStartTimestamp` 等），`summary` 只是給清單頁排序用的粗分類，判讀交給人。

**回應裡沒有環境變數**：`systemctl show` 只取 `SYSTEMD_PROPS`／`TIMER_PROPS`（不含 `Environment`），
`docker inspect` 用 `--format` 只取 State、RestartCount、Config.Image（不含 `Config.Env`，也不含
`State.Health.Log`——健康檢查的輸出可能帶任何東西）。日誌內容另經 `redact()` 遮掉形似祕密的片段，
那是第二道防線，不是許可證：服務本來就不該把祕密寫進日誌。

批次查詢：清單頁一次 `systemctl show`（全部 unit 與 timer）＋一次 `docker inspect`（全部容器），
不是每個服務各開一個子行程。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from ops_agent.catalog import Catalog, Service
from ops_agent.protocol import ProtocolError

SYSTEMD_PROPS = (
    "Id", "Description", "LoadState", "ActiveState", "SubState", "Result", "Type", "UnitFileState",
    "ExecMainCode", "ExecMainStatus", "MainPID", "NRestarts", "ExecMainStartTimestamp",
    "ExecMainExitTimestamp", "ActiveEnterTimestamp", "StateChangeTimestamp", "InvocationID",
)
TIMER_PROPS = ("Id", "LoadState", "ActiveState", "NextElapseUSecRealtime", "LastTriggerUSec")
_ALL_PROPS = ",".join(dict.fromkeys(SYSTEMD_PROPS + TIMER_PROPS))

DOCKER_INSPECT_FORMAT = (
    '{"name":{{json .Name}},"state":{{json .State}},'
    '"restart_count":{{json .RestartCount}},"image":{{json .Config.Image}}}'
)
_CONTAINER_STATE_KEYS = ("Status", "Running", "Paused", "Restarting", "OOMKilled", "Dead", "ExitCode",
                         "Error", "StartedAt", "FinishedAt")

STATUS_MAX_BYTES = 1024 * 1024
MAX_LINE_CHARS = 2000
_EXEC_CODES = {"1": "exited", "2": "killed", "3": "dumped"}
_TRANSITIONING = {"activating", "deactivating", "reloading", "refreshing"}

_UNIX_TS = re.compile(r"@(\d{1,12})")
_UTC_TS = re.compile(r"(?:[A-Za-z]{3} )?(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})(?:\.\d+)? UTC")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_REDACTIONS = (
    # KEY=value／KEY: value，鍵名含 SECRET、TOKEN、PASSWORD 等
    (re.compile(r"(?i)\b([A-Z0-9_.-]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|ACCESS_KEY|PRIVATE_KEY|"
                r"WEBHOOK)[A-Z0-9_.-]*)(\s*[=:]\s*)(\"[^\"]*\"|'[^']*'|\S+)"), r"\1\2<redacted>"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 <redacted>"),
    # scheme://user:password@host
    (re.compile(r"([a-z][a-z0-9+.-]*://[^:/\s@]+:)[^@\s]+@"), r"\1<redacted>@"),
    (re.compile(r"\bsk-[A-Za-z0-9]{16,}"), "sk-<redacted>"),
    (re.compile(r"https://hooks\.slack\.com/\S+"), "https://hooks.slack.com/<redacted>"),
)


def redact(line: str) -> str:
    for pattern, repl in _REDACTIONS:
        line = pattern.sub(repl, line)
    return line


def clean_line(line: str) -> str:
    line = _CONTROL.sub("", _ANSI.sub("", line)).replace("\t", "    ")
    if len(line) > MAX_LINE_CHARS:
        line = line[:MAX_LINE_CHARS] + "…"
    return redact(line)


def iso_utc(epoch: float | int) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def parse_ts(value: str | None) -> str | None:
    """`@1791191596`（--timestamp=unix）或 `Tue 2026-10-06 04:00:00 UTC`（TZ=UTC 下的 timer 屬性）→ ISO。"""
    if not value or value in ("n/a", "0"):
        return None
    m = _UNIX_TS.fullmatch(value)
    if m:
        return iso_utc(int(m.group(1)))
    m = _UTC_TS.fullmatch(value)
    if m:
        return f"{m.group(1)}T{m.group(2)}Z"
    return None


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_show(text: str) -> list[dict[str, str]]:
    blocks = []
    for raw in text.strip("\n").split("\n\n"):
        props: dict[str, str] = {}
        for line in raw.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key] = value
        blocks.append(props)
    return blocks


def systemd_state(props: dict[str, str]) -> dict:
    return {
        "load_state": props.get("LoadState") or None,
        "active_state": props.get("ActiveState") or None,
        "sub_state": props.get("SubState") or None,
        "result": props.get("Result") or None,
        "type": props.get("Type") or None,
        "unit_file_state": props.get("UnitFileState") or None,
        "exec_main_code": _EXEC_CODES.get(props.get("ExecMainCode", "")),
        "exec_main_status": _int(props.get("ExecMainStatus")),
        "main_pid": _int(props.get("MainPID")) or None,
        "n_restarts": _int(props.get("NRestarts")),
        "exec_main_start_at": parse_ts(props.get("ExecMainStartTimestamp")),
        "exec_main_exit_at": parse_ts(props.get("ExecMainExitTimestamp")),
        "active_enter_at": parse_ts(props.get("ActiveEnterTimestamp")),
        "state_change_at": parse_ts(props.get("StateChangeTimestamp")),
        # 每次啟動一個新值：restart／run 之後前端輪詢到它換掉，就知道新的一輪起來了。
        "invocation_id": props.get("InvocationID") or None,
    }


def timer_state(unit: str, props: dict[str, str]) -> dict:
    return {
        "unit": unit,
        "load_state": props.get("LoadState") or None,
        "active_state": props.get("ActiveState") or None,
        "next_elapse_at": parse_ts(props.get("NextElapseUSecRealtime")),
        "last_trigger_at": parse_ts(props.get("LastTriggerUSec")),
    }


def systemd_summary(state: dict) -> str:
    if state["load_state"] == "not-found":
        return "not_found"
    active = state["active_state"]
    if active == "failed":
        return "failed"
    if active == "active":
        return "running"
    if active in _TRANSITIONING:
        return "transitioning"
    if active == "inactive":
        return "idle"
    return "unknown"


def container_state(info: dict) -> dict:
    raw = info.get("state") if isinstance(info.get("state"), dict) else {}
    state = {k: raw.get(k) for k in _CONTAINER_STATE_KEYS}
    health = raw.get("Health") if isinstance(raw.get("Health"), dict) else {}

    def _time(v):
        return None if not isinstance(v, str) or v.startswith("0001-01-01") else v

    error = state["Error"] if isinstance(state["Error"], str) and state["Error"] else None
    return {
        "status": state["Status"] if isinstance(state["Status"], str) else None,
        "running": state["Running"] if isinstance(state["Running"], bool) else None,
        "paused": state["Paused"] if isinstance(state["Paused"], bool) else None,
        "restarting": state["Restarting"] if isinstance(state["Restarting"], bool) else None,
        "oom_killed": state["OOMKilled"] if isinstance(state["OOMKilled"], bool) else None,
        "dead": state["Dead"] if isinstance(state["Dead"], bool) else None,
        "exit_code": state["ExitCode"] if isinstance(state["ExitCode"], int) else None,
        "error": clean_line(error)[:300] if error else None,
        "started_at": _time(state["StartedAt"]),
        "finished_at": _time(state["FinishedAt"]),
        "health": health.get("Status") if isinstance(health.get("Status"), str) else None,
        "failing_streak": health.get("FailingStreak") if isinstance(health.get("FailingStreak"), int) else None,
        "restart_count": info.get("restart_count") if isinstance(info.get("restart_count"), int) else None,
        "image": info.get("image") if isinstance(info.get("image"), str) else None,
    }


def container_summary(state: dict) -> str:
    status = state["status"]
    if status == "running":
        if state["health"] == "unhealthy":
            return "failed"
        if state["health"] == "starting":
            return "transitioning"
        return "running"
    if status == "restarting":
        return "transitioning"
    if status in ("created", "paused"):
        return "idle"
    if status in ("exited", "dead"):
        if status == "dead" or state["oom_killed"] or (state["exit_code"] or 0) != 0:
            return "failed"
        return "idle"
    return "unknown"


def _base(svc: Service) -> dict:
    return {**svc.public(), "summary": "unknown", "error": None,
            "systemd": None, "container": None, "timer_state": None}


def _first_line(data: bytes, limit: int = 200) -> str:
    text = data.decode("utf-8", "replace").strip().splitlines()
    return clean_line(text[0])[:limit] if text else ""


async def query_status(catalog: Catalog, runner, services: list[Service]) -> list[dict]:
    """批次查詢 status。單一服務查不到不讓整批失敗：那一列 summary=unknown、error 寫原因。"""
    cfg = catalog.agent
    results = {svc.name: _base(svc) for svc in services}

    systemd = [s for s in services if s.kind == "systemd"]
    if systemd:
        units: list[str] = []
        for s in systemd:
            units.append(s.unit)
            if s.timer:
                units.append(s.timer)
        argv = [cfg.systemctl, "show", "--no-pager", "--timestamp=unix", "-p", _ALL_PROPS, "--", *units]
        res = await runner.run(argv, timeout=cfg.command_timeout, max_bytes=STATUS_MAX_BYTES, env={"TZ": "UTC"})
        error = None
        if res.timed_out:
            error = "systemctl show 逾時"
        elif res.returncode != 0 or res.truncated:
            error = f"systemctl show 失敗（rc={res.returncode}）：{_first_line(res.stderr)}"
        else:
            blocks = _parse_show(res.stdout.decode("utf-8", "replace"))
            if len(blocks) != len(units):
                error = f"systemctl show 輸出無法對應（{len(blocks)} 段／{len(units)} 個 unit）"
            else:
                by_unit = dict(zip(units, blocks))
                for s in systemd:
                    row = results[s.name]
                    row["systemd"] = systemd_state(by_unit[s.unit])
                    row["summary"] = systemd_summary(row["systemd"])
                    if s.timer:
                        row["timer_state"] = timer_state(s.timer, by_unit[s.timer])
        if error:
            for s in systemd:
                results[s.name]["error"] = error

    containers = [s for s in services if s.kind == "container"]
    if containers:
        names = [s.container for s in containers]
        argv = [cfg.docker, "inspect", "--type", "container", "--format", DOCKER_INSPECT_FORMAT, "--", *names]
        res = await runner.run(argv, timeout=cfg.command_timeout, max_bytes=STATUS_MAX_BYTES)
        if res.timed_out:
            for s in containers:
                results[s.name]["error"] = "docker inspect 逾時"
        else:
            found: dict[str, dict] = {}
            for line in res.stdout.decode("utf-8", "replace").splitlines():
                try:
                    info = json.loads(line)
                except ValueError:
                    continue
                if isinstance(info, dict) and isinstance(info.get("name"), str):
                    found[info["name"].lstrip("/")] = info
            stderr = res.stderr.decode("utf-8", "replace")
            for s in containers:
                row = results[s.name]
                if s.container in found:
                    row["container"] = container_state(found[s.container])
                    row["summary"] = container_summary(row["container"])
                elif "No such" in stderr and s.container in stderr:
                    row["summary"] = "not_found"
                else:
                    row["error"] = f"docker inspect 失敗（rc={res.returncode}）：{_first_line(res.stderr)}"
    return [results[s.name] for s in services]


def fit_entries(entries: list[str], budget: int) -> tuple[list[str], bool]:
    """從舊的那端丟行，直到 JSON 編碼後的總量 ≤ budget。回傳（留下的行, 是否丟過）。"""
    sizes = [len(json.dumps(e, ensure_ascii=False).encode("utf-8")) + 1 for e in entries]
    total = sum(sizes)
    start = 0
    while start < len(entries) and total > budget:
        total -= sizes[start]
        start += 1
    return entries[start:], start > 0


async def query_logs(catalog: Catalog, runner, svc: Service, *, since: datetime, lines: int,
                     budget: int) -> dict:
    cfg = catalog.agent
    epoch = int(since.timestamp())
    raw_cap = min(max(budget * 4, 256 * 1024), 8 * 1024 * 1024)
    if svc.kind == "systemd":
        argv = [cfg.journalctl, "--no-pager", "--quiet", "--output=short-iso", "--lines", str(lines),
                "--since", f"@{epoch}", "--unit", svc.unit]
        res = await runner.run(argv, timeout=cfg.command_timeout, max_bytes=raw_cap, keep_tail=True)
        what = "journalctl"
    else:
        argv = [cfg.docker, "logs", "--timestamps", "--since", str(epoch), "--tail", str(lines), "--", svc.container]
        res = await runner.run(argv, timeout=cfg.command_timeout, max_bytes=raw_cap, merge_stderr=True,
                               keep_tail=True)
        what = "docker logs"
    if res.timed_out:
        raise ProtocolError("command_timeout", f"{what} 逾時（{cfg.command_timeout:g} 秒）")
    if res.returncode != 0:
        detail = _first_line(res.stderr or res.stdout)
        raise ProtocolError("command_failed", f"{what} 失敗（rc={res.returncode}）{('：' + detail) if detail else ''}")
    text = res.stdout.decode("utf-8", "replace").splitlines()
    if res.truncated and text:
        text = text[1:]  # 從中間切開的第一行不完整
    entries = [clean_line(t) for t in text][-lines:]
    entries, dropped = fit_entries(entries, budget)
    return {**svc.public(), "since": iso_utc(epoch), "lines": lines, "truncated": bool(res.truncated or dropped),
            "entries": entries}
