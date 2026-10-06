"""Service Catalog：每台主機一份 TOML（`deploy/ops/services.prod.toml`、`deploy/ops/services.dev.toml`）。

代理只對 catalog 列出的服務動作；名稱不在 catalog 一律拒絕。格式：

    schema = 1
    environment = "production"                      # production | development
    socket_path = "/run/report-mark-ops/agent.sock" # 必須等於該環境的固定路徑

    [agent]
    allowed_users = ["kashionz"]   # 只接受這些使用者（SO_PEERCRED 的 uid）連線；也可寫 allowed_uids
    request_timeout = 15           # 整個請求的期限（秒）
    command_timeout = 8            # 單一子行程的期限（秒）
    max_response_bytes = 262144    # 一行回應的上限（logs 超過時從舊的那端截掉）
    max_log_lines = 1000
    max_log_age_hours = 168        # logs 的 since 最多往前多久
    max_concurrent = 4             # 同時處理的請求數；超過回 busy
    restart_delay = 1.5            # restart 先回應、等這麼多秒才執行（讓 web 來得及把 202 送出去）
    systemctl = "/usr/bin/systemctl"
    journalctl = "/usr/bin/journalctl"
    docker = "/usr/bin/docker"

    [[services]]
    name = "web"                   # API 與 UI 用的名稱：小寫英數與 -
    kind = "systemd"               # systemd | container
    unit = "report-mark-web.service"
    timer = "report-mark-sync.timer"   # 可省略；有 timer 的 oneshot 一併回報下次／上次觸發
    container = "..."              # kind = "container" 時用這個，不寫 unit
    tier = "critical"              # critical | important | supporting
    actions = ["status", "logs"]   # status | logs | restart | run（寫入類見下）
    group = "web"                  # execution group；有寫入類 action 時必填
    flock_files = ["/abs/path"]    # 寫入類執行前以非阻塞 flock 試探的鎖檔（被持有＝already_running）
    pid_files = ["/abs/path"]      # 寫入類執行前檢查的 PID 檔（記的行程還活著＝already_running）
    description = "..."

**寫入類 action（`restart`、`run`）的載入期規則**（代理執行前會再以 `ops_agent/actions.py` 硬擋一次）：
- 只給 `kind = "systemd"`；容器一律唯讀。
- `restart` 只給 `protocol.RESTARTABLE_UNITS`（v1 只有 Web）；`run` 不給這些常駐服務（它們用 restart）。
- 同一個服務不能同時有 `restart` 與 `run`；有寫入類就必須也有 `status`（前端要輪詢結果）與 `group`。
- unit 名稱命中 `protocol.FORBIDDEN_WRITE_TARGET`（PostgreSQL、nginx、cloudflared……）一律拒絕載入。
- `flock_files`／`pid_files` 只能寫在有 `group` 的服務上；同 group 的服務共用彼此的鎖檔（取聯集）。

**交叉拒絕**（`validate_binding` 與 `_check_environment_names`）：
- catalog 的 `socket_path` 必須是該 `environment` 的固定路徑；CLI 的 `--socket` 覆寫只允許 development
  （本機冒煙用臨時 socket），而且不能是其他環境的固定路徑。production 一律只綁自己的固定路徑。
- development catalog 只能列 `report-mark-dev-*` 的 unit 與 `report-mark-dev*` 的容器；production catalog
  不得列它們。於是 dev 代理即使被改了 catalog，也沒有辦法指到生產服務。

未知鍵一律拒絕載入（不是忽略）：拼錯 `actoins` 時默默套預設值，比起不了更危險。
"""

from __future__ import annotations

import pwd
import re
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from ops_agent.protocol import (
    CANONICAL_SOCKETS,
    ENVIRONMENTS,
    FORBIDDEN_WRITE_TARGET,
    KNOWN_ACTIONS,
    RESTARTABLE_UNITS,
    WRITE_ACTIONS,
)

try:  # 3.11+；系統 python 太舊時給清楚的訊息而不是 ImportError 堆疊
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 依執行環境而定
    tomllib = None

SCHEMA_VERSION = 1
KINDS = ("systemd", "container")
TIERS = ("critical", "important", "supporting")

_NAME = re.compile(r"[a-z][a-z0-9-]{0,39}")
# unit 名稱：不能以 - 開頭（避免被當成選項），只收 .service／.timer。
_UNIT = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:-]{0,200}\.service")
_TIMER = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._:-]{0,200}\.timer")
_CONTAINER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_DEV_UNIT_PREFIX = "report-mark-dev-"
_DEV_CONTAINER_PREFIX = "report-mark-dev"

_TOP_KEYS = frozenset({"schema", "environment", "socket_path", "agent", "services"})
_AGENT_KEYS = frozenset({
    "allowed_users", "allowed_uids", "request_timeout", "command_timeout", "max_response_bytes",
    "max_log_lines", "max_log_age_hours", "max_concurrent", "restart_delay", "systemctl", "journalctl", "docker",
})
_SERVICE_KEYS = frozenset({"name", "kind", "unit", "timer", "container", "tier", "actions", "group", "flock_files",
                           "pid_files", "description"})
_LOCK_PATH = re.compile(r"/[A-Za-z0-9._@/-]{1,255}")
MAX_LOCK_FILES = 8


class CatalogError(ValueError):
    pass


@dataclass(frozen=True)
class Service:
    name: str
    kind: str
    tier: str
    actions: tuple[str, ...]
    unit: str | None = None
    timer: str | None = None
    container: str | None = None
    description: str = ""
    group: str | None = None
    flock_files: tuple[str, ...] = ()
    pid_files: tuple[str, ...] = ()

    @property
    def target(self) -> str:
        return self.unit if self.kind == "systemd" else self.container  # type: ignore[return-value]

    def public(self) -> dict:
        # 鎖檔路徑刻意不回給 web：那是主機的檔案配置，管理頁用不到。
        return {"name": self.name, "kind": self.kind, "tier": self.tier, "target": self.target,
                "timer": self.timer, "actions": list(self.actions), "group": self.group,
                "description": self.description}


@dataclass(frozen=True)
class AgentConfig:
    allowed_uids: frozenset[int]
    request_timeout: float = 15.0
    command_timeout: float = 8.0
    max_response_bytes: int = 256 * 1024
    max_log_lines: int = 1000
    max_log_age: timedelta = timedelta(hours=168)
    max_concurrent: int = 4
    restart_delay: float = 1.5
    systemctl: str = "/usr/bin/systemctl"
    journalctl: str = "/usr/bin/journalctl"
    docker: str = "/usr/bin/docker"


@dataclass(frozen=True)
class Catalog:
    environment: str
    socket_path: str
    agent: AgentConfig
    services: tuple[Service, ...] = field(default_factory=tuple)

    def get(self, name: str) -> Service | None:
        for svc in self.services:
            if svc.name == name:
                return svc
        return None

    def group_members(self, group: str) -> tuple[Service, ...]:
        return tuple(s for s in self.services if s.group == group)


def load_catalog(path: str | Path, *, resolve_user=None) -> Catalog:
    if tomllib is None:
        raise CatalogError("需要 Python 3.11+（tomllib）")
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except OSError as exc:
        raise CatalogError(f"讀不到 catalog {path}：{exc.strerror}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise CatalogError(f"catalog {path} 不是合法 TOML：{exc}") from exc
    return parse_catalog(data, resolve_user=resolve_user)


def _uid_of(username: str) -> int:
    try:
        return pwd.getpwnam(username).pw_uid
    except KeyError as exc:
        raise CatalogError(f"allowed_users 的使用者不存在：{username}") from exc


def _num(table: dict, key: str, default, *, lo, hi, integer: bool):
    value = table.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or (integer and not isinstance(value, int)):
        raise CatalogError(f"agent.{key} 必須是{'整數' if integer else '數字'}")
    if not lo <= value <= hi:
        raise CatalogError(f"agent.{key} 必須介於 {lo}..{hi}")
    return value


def _abs_bin(table: dict, key: str, default: str) -> str:
    value = table.get(key, default)
    if not isinstance(value, str) or not value.startswith("/") or "\n" in value:
        raise CatalogError(f"agent.{key} 必須是絕對路徑")
    return value


def _parse_agent(table, resolve_user) -> AgentConfig:
    if not isinstance(table, dict):
        raise CatalogError("缺 [agent] 區段")
    extra = set(table) - _AGENT_KEYS
    if extra:
        raise CatalogError(f"[agent] 有不認得的鍵：{sorted(extra)}")
    users = table.get("allowed_users", [])
    uids = table.get("allowed_uids", [])
    if not isinstance(users, list) or not all(isinstance(u, str) and u for u in users):
        raise CatalogError("agent.allowed_users 必須是使用者名稱清單")
    if not isinstance(uids, list) or not all(isinstance(u, int) and not isinstance(u, bool) and u >= 0
                                             for u in uids):
        raise CatalogError("agent.allowed_uids 必須是非負整數清單")
    resolved = {(resolve_user or _uid_of)(u) for u in users} | set(uids)
    if not resolved:
        raise CatalogError("agent.allowed_users／allowed_uids 至少要一個（不允許「誰都能連」）")
    if 0 in resolved:
        raise CatalogError("不允許 root（uid 0）當呼叫端：web 不該以 root 執行")
    return AgentConfig(
        allowed_uids=frozenset(resolved),
        request_timeout=float(_num(table, "request_timeout", 15, lo=1, hi=120, integer=False)),
        command_timeout=float(_num(table, "command_timeout", 8, lo=1, hi=60, integer=False)),
        max_response_bytes=_num(table, "max_response_bytes", 256 * 1024, lo=16 * 1024, hi=4 * 1024 * 1024,
                                integer=True),
        max_log_lines=_num(table, "max_log_lines", 1000, lo=1, hi=5000, integer=True),
        max_log_age=timedelta(hours=_num(table, "max_log_age_hours", 168, lo=1, hi=24 * 31, integer=True)),
        max_concurrent=_num(table, "max_concurrent", 4, lo=1, hi=32, integer=True),
        restart_delay=float(_num(table, "restart_delay", 1.5, lo=0, hi=10, integer=False)),
        systemctl=_abs_bin(table, "systemctl", "/usr/bin/systemctl"),
        journalctl=_abs_bin(table, "journalctl", "/usr/bin/journalctl"),
        docker=_abs_bin(table, "docker", "/usr/bin/docker"),
    )


def _parse_service(i: int, raw) -> Service:
    where = f"services[{i}]"
    if not isinstance(raw, dict):
        raise CatalogError(f"{where} 必須是表格")
    extra = set(raw) - _SERVICE_KEYS
    if extra:
        raise CatalogError(f"{where} 有不認得的鍵：{sorted(extra)}")
    name = raw.get("name")
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise CatalogError(f"{where}.name 必須是小寫英數與 -（1–40 字元、英文字母開頭）：{name!r}")
    where = f"服務 {name}"
    kind = raw.get("kind")
    if kind not in KINDS:
        raise CatalogError(f"{where}：kind 必須是 {'／'.join(KINDS)}")
    tier = raw.get("tier")
    if tier not in TIERS:
        raise CatalogError(f"{where}：tier 必須是 {'／'.join(TIERS)}")
    actions = raw.get("actions")
    if not isinstance(actions, list) or not actions or not all(isinstance(a, str) for a in actions):
        raise CatalogError(f"{where}：actions 必須是非空字串清單")
    unknown = [a for a in actions if a not in KNOWN_ACTIONS]
    if unknown:
        raise CatalogError(f"{where}：不支援的 action {unknown}（只有 {list(KNOWN_ACTIONS)}）")
    if len(set(actions)) != len(actions):
        raise CatalogError(f"{where}：actions 有重複")
    group = raw.get("group")
    if group is not None and (not isinstance(group, str) or not _NAME.fullmatch(group)):
        raise CatalogError(f"{where}：group 必須是小寫英數與 -（1–40 字元、英文字母開頭）：{group!r}")
    flock_files = _lock_files(where, raw, "flock_files", group)
    pid_files = _lock_files(where, raw, "pid_files", group)
    description = raw.get("description", "")
    if not isinstance(description, str) or len(description) > 200:
        raise CatalogError(f"{where}：description 必須是 ≤200 字元的字串")
    unit, timer, container = raw.get("unit"), raw.get("timer"), raw.get("container")
    if kind == "systemd":
        if container is not None:
            raise CatalogError(f"{where}：systemd 服務不寫 container")
        if not isinstance(unit, str) or not _UNIT.fullmatch(unit):
            raise CatalogError(f"{where}：unit 必須是 *.service 名稱：{unit!r}")
        if timer is not None and (not isinstance(timer, str) or not _TIMER.fullmatch(timer)):
            raise CatalogError(f"{where}：timer 必須是 *.timer 名稱：{timer!r}")
    else:
        if unit is not None or timer is not None:
            raise CatalogError(f"{where}：container 服務不寫 unit／timer")
        if not isinstance(container, str) or not _CONTAINER.fullmatch(container):
            raise CatalogError(f"{where}：container 名稱不合法：{container!r}")
    _check_write_actions(where, kind, unit, actions, group)
    return Service(name=name, kind=kind, tier=tier, actions=tuple(actions), unit=unit, timer=timer,
                   container=container, description=description, group=group, flock_files=flock_files,
                   pid_files=pid_files)


def _lock_files(where: str, raw: dict, key: str, group: str | None) -> tuple[str, ...]:
    value = raw.get(key)
    if value is None:
        return ()
    if group is None:
        raise CatalogError(f"{where}：{key} 只能寫在有 group 的服務上")
    if not isinstance(value, list) or not value or len(value) > MAX_LOCK_FILES:
        raise CatalogError(f"{where}：{key} 必須是 1–{MAX_LOCK_FILES} 個絕對路徑")
    for path in value:
        if (not isinstance(path, str) or not _LOCK_PATH.fullmatch(path)
                or any(part in ("", ".", "..") for part in path[1:].split("/"))):
            raise CatalogError(f"{where}：{key} 的路徑不合法（要正規化的絕對路徑）：{path!r}")
    if len(set(value)) != len(value):
        raise CatalogError(f"{where}：{key} 有重複")
    return tuple(value)


def _check_write_actions(where: str, kind: str, unit: str | None, actions: list[str], group: str | None) -> None:
    writes = [a for a in actions if a in WRITE_ACTIONS]
    if not writes:
        return
    if kind != "systemd":
        raise CatalogError(f"{where}：容器服務不允許 {writes}（容器一律唯讀）")
    if len(writes) > 1:
        raise CatalogError(f"{where}：同一個服務不能同時有 restart 與 run")
    if "status" not in actions:
        raise CatalogError(f"{where}：有寫入類 action 就必須也有 status（前端要輪詢結果）")
    if group is None:
        raise CatalogError(f"{where}：有寫入類 action 就必須宣告 group（execution group）")
    if FORBIDDEN_WRITE_TARGET.search(unit or ""):
        raise CatalogError(f"{where}：{unit} 永遠不允許寫入類 action（PostgreSQL／邊緣／代理自身等）")
    if writes == ["restart"] and unit not in RESTARTABLE_UNITS:
        raise CatalogError(f"{where}：restart 只開放 {sorted(RESTARTABLE_UNITS)}，不含 {unit}")
    if writes == ["run"] and unit in RESTARTABLE_UNITS:
        raise CatalogError(f"{where}：常駐服務 {unit} 不用 run（用 restart）")


def _check_environment_names(environment: str, services: tuple[Service, ...]) -> None:
    for svc in services:
        targets = [t for t in (svc.unit, svc.timer) if t] if svc.kind == "systemd" else [svc.container]
        for target in targets:
            is_dev = (target.startswith(_DEV_UNIT_PREFIX) if svc.kind == "systemd"
                      else target.startswith(_DEV_CONTAINER_PREFIX))
            if environment == "development" and not is_dev:
                raise CatalogError(
                    f"development catalog 只能列 {_DEV_UNIT_PREFIX}* unit 與 {_DEV_CONTAINER_PREFIX}* 容器："
                    f"服務 {svc.name} 指向 {target}")
            if environment == "production" and is_dev:
                raise CatalogError(f"production catalog 不得列開發用的 {target}（服務 {svc.name}）")


def parse_catalog(data: dict, *, resolve_user=None) -> Catalog:
    if not isinstance(data, dict):
        raise CatalogError("catalog 必須是 TOML 表格")
    extra = set(data) - _TOP_KEYS
    if extra:
        raise CatalogError(f"catalog 有不認得的鍵：{sorted(extra)}")
    if data.get("schema") != SCHEMA_VERSION:
        raise CatalogError(f"schema 必須是 {SCHEMA_VERSION}")
    environment = data.get("environment")
    if environment not in ENVIRONMENTS:
        raise CatalogError(f"environment 必須是 {'／'.join(ENVIRONMENTS)}")
    socket_path = data.get("socket_path")
    if socket_path != CANONICAL_SOCKETS[environment]:
        raise CatalogError(
            f"{environment} catalog 的 socket_path 必須是 {CANONICAL_SOCKETS[environment]}，"
            f"不是 {socket_path!r}")
    agent = _parse_agent(data.get("agent"), resolve_user)
    raw_services = data.get("services")
    if not isinstance(raw_services, list) or not raw_services:
        raise CatalogError("至少要列一個 [[services]]")
    services = tuple(_parse_service(i, s) for i, s in enumerate(raw_services))
    names = [s.name for s in services]
    if len(set(names)) != len(names):
        raise CatalogError(f"服務名稱重複：{sorted({n for n in names if names.count(n) > 1})}")
    targets = [t for s in services for t in (s.unit, s.timer, s.container) if t]
    if len(set(targets)) != len(targets):
        raise CatalogError("同一個 unit／timer／容器不可出現兩次")
    _check_environment_names(environment, services)
    return Catalog(environment=environment, socket_path=socket_path, agent=agent, services=services)


def validate_binding(catalog: Catalog, socket_path: str) -> None:
    """代理要綁的 socket 與 catalog 的環境必須一致，否則拒絕啟動。"""
    path = str(socket_path)
    own = CANONICAL_SOCKETS[catalog.environment]
    for env, canonical in CANONICAL_SOCKETS.items():
        if env != catalog.environment and path == canonical:
            raise CatalogError(f"拒絕以 {env} 的 socket（{canonical}）載入 {catalog.environment} catalog")
    if catalog.environment == "production" and path != own:
        raise CatalogError(f"production 代理只能綁 {own}（不接受 --socket 覆寫）")
    if not path.startswith("/"):
        raise CatalogError("socket 路徑必須是絕對路徑")
