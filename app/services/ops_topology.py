"""服務依賴圖的判讀（`GET /api/admin/ops/dependencies`）：純函式，不連 DB、不問代理、不碰檔案。

輸入是維運代理 `list` 的結果（`ops_agent/server.py`）：每個服務的狀態與 `depends_on`，以及 catalog 的
`externals`。**依賴關係只有一個真相來源——Service Catalog**（`deploy/ops/services.*.toml`，載入期已驗證
引用存在、無自我依賴、無環）；這裡只負責「判讀」，不另寫一份依賴。

每個節點的 `health`（給依賴圖上色與傳播用的粗分類；細節仍看服務頁的 systemd／docker 原始屬性）：

- 服務：`failed`、`not_found` → down；常駐服務（沒有 timer、不是 oneshot）的 `idle` 也是 down（停著就是壞了，
  與 `/api/status` 的 critical 判讀一致），有 timer 或 oneshot 的 `idle`／`transitioning` 是 ok（待命、執行中）；
  常駐服務的 `transitioning` 是 degraded；查不到（`unknown`）就是 unknown。
- 外部依賴：沒有 probe → unknown（未監控）。有 probe 時看那個探針 unit **最後一次結束**的退出碼：落在
  `down_exit_codes` → down、`degraded_exit_codes` → degraded、`ok_exit_codes` → ok；其他退出碼一律 unknown——
  探針的退出碼有優先序（例如 web 探針 8 會蓋住 6），被蓋住的那一項判斷不出來，不能當成正常。探針沒結果、
  正在跑、結果比 `PROBE_STALE` 舊（timer 停了），也都是 unknown。

傳播只看 down：某節點（直接或間接）依賴的節點有 down，它就是 `affected`，`impacted_by` 列出那些 down 的上游；
degraded 與 unknown 不傳播（不確定的狀態不擴大成一片紅）。`root_causes` 是自己 down、但上游沒有 down 的節點——
修它們才可能讓下游恢復。

防禦：代理版本較舊（沒有 depends_on／externals）時就是沒有邊的圖；指向不存在節點的邊丟掉；萬一有環
（catalog 已擋，這裡不信任輸入）以 visited 集合保證終止，環上的節點層級照環外最深的算。
"""

from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta

HEALTH_STATES = ("ok", "degraded", "down", "unknown")
PROBE_STALE = timedelta(minutes=30)  # 探針每 2 分鐘跑一次；超過這麼久沒有新結果＝timer 停了或探針卡住
_TIER_RANK = {"critical": 0, "important": 1, "supporting": 2}


def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo is not None else None


def _scheduled(item: dict) -> bool:
    """有 timer 或 Type=oneshot：平常就是 idle，跑完就停，不是「沒在跑」。"""
    systemd = item.get("systemd") or {}
    return bool(item.get("timer")) or systemd.get("type") == "oneshot"


def service_health(item: dict) -> tuple[str, str]:
    summary = item.get("summary")
    resident = item.get("kind") == "container" or not _scheduled(item)
    if summary == "failed":
        return "down", "最後一次執行失敗" if not resident else "服務失敗"
    if summary == "not_found":
        return "down", "主機上找不到這個 unit／容器"
    if summary == "running":
        return "ok", "執行中"
    if summary == "idle":
        return ("down", "常駐服務沒有在跑") if resident else ("ok", "排程待命")
    if summary == "transitioning":
        return ("degraded", "啟動或停止中") if resident else ("ok", "執行中")
    return "unknown", item.get("error") or "狀態查不到"


def external_health(ext: dict, probe_item: dict | None, checked_at: datetime | None) -> tuple[str, str, str | None]:
    """回傳（health, 說明, 探針結果的時間）。"""
    probe = ext.get("probe")
    if not probe:
        return "unknown", "未監控（沒有探針）", None
    if probe_item is None:
        return "unknown", f"探針 {probe} 不在服務清單", None
    if probe_item.get("summary") == "not_found":
        return "unknown", f"探針 {probe} 未安裝", None
    systemd = probe_item.get("systemd") or {}
    exited_at = systemd.get("exec_main_exit_at")
    if probe_item.get("summary") == "transitioning" or systemd.get("active_state") in ("activating", "reloading"):
        return "unknown", f"探針 {probe} 正在執行，等下一次結果", exited_at
    code = systemd.get("exec_main_status")
    if systemd.get("exec_main_code") != "exited" or not isinstance(code, int):
        return "unknown", f"探針 {probe} 還沒有結果（或被中止）", exited_at
    exit_ts = _parse_ts(exited_at)
    if checked_at is not None and (exit_ts is None or checked_at - exit_ts > PROBE_STALE):
        minutes = int(PROBE_STALE.total_seconds() // 60)
        return "unknown", f"探針 {probe} 超過 {minutes} 分鐘沒有新結果", exited_at
    if code in (ext.get("down_exit_codes") or []):
        return "down", f"探針 {probe} 退出碼 {code}", exited_at
    if code in (ext.get("degraded_exit_codes") or []):
        return "degraded", f"探針 {probe} 退出碼 {code}", exited_at
    if code in (ext.get("ok_exit_codes") or [0]):
        return "ok", f"探針 {probe} 退出碼 {code}", exited_at
    return "unknown", f"探針 {probe} 退出碼 {code}：可能被其他狀況蓋住，判斷不出這一項", exited_at


def _layers(deps: dict[str, list[str]]) -> dict[str, int]:
    """0＝不依賴任何節點；其餘＝1＋最深的依賴。Kahn 拓樸排序；環上的節點（不該有）照環外最深的算。"""
    pending = {n: len(ds) for n, ds in deps.items()}
    dependents: dict[str, list[str]] = {n: [] for n in deps}
    for n, ds in deps.items():
        for d in ds:
            dependents[d].append(n)
    layer = dict.fromkeys(deps, 0)
    queue = deque(n for n, c in pending.items() if c == 0)
    done: set[str] = set()
    while queue:
        n = queue.popleft()
        done.add(n)
        for m in dependents[n]:
            layer[m] = max(layer[m], layer[n] + 1)
            pending[m] -= 1
            if pending[m] == 0:
                queue.append(m)
    for n in deps:
        if n not in done:
            layer[n] = max([layer[d] + 1 for d in deps[n] if d in done] or [0])
    return layer


def _upstream(name: str, deps: dict[str, list[str]]) -> set[str]:
    seen: set[str] = set()
    stack = list(deps.get(name, ()))
    while stack:
        n = stack.pop()
        if n in seen or n == name:
            continue
        seen.add(n)
        stack.extend(deps.get(n, ()))
    return seen


def build_topology(listing: dict) -> dict:
    """代理 `list` 的結果 → 依賴圖（節點、邊、down／root_causes／affected）。"""
    checked_at = _parse_ts(listing.get("checked_at"))
    items = [i for i in listing.get("items") or [] if isinstance(i, dict) and i.get("name")]
    externals = [e for e in listing.get("externals") or [] if isinstance(e, dict) and e.get("name")]
    by_item = {i["name"]: i for i in items}

    nodes: dict[str, dict] = {}
    for item in items:
        health, reason = service_health(item)
        nodes[item["name"]] = {
            "name": item["name"], "kind": item.get("kind"), "tier": item.get("tier"), "target": item.get("target"),
            "description": item.get("description") or "", "summary": item.get("summary"), "health": health,
            "health_reason": reason, "probe": None, "observed_at": None,
            "depends_on": list(item.get("depends_on") or []),
        }
    for ext in externals:
        if ext["name"] in nodes:
            continue  # catalog 擋了同名；不信任輸入，先到的服務為準
        health, reason, observed = external_health(ext, by_item.get(ext.get("probe") or ""), checked_at)
        nodes[ext["name"]] = {
            "name": ext["name"], "kind": "external", "tier": ext.get("tier"), "target": None,
            "description": ext.get("description") or "", "summary": None, "health": health, "health_reason": reason,
            "probe": ext.get("probe"), "observed_at": observed, "depends_on": list(ext.get("depends_on") or []),
        }

    # 只留指向存在節點、不指向自己的邊（去重、保留 catalog 的順序）。
    deps: dict[str, list[str]] = {}
    for name, node in nodes.items():
        kept: list[str] = []
        for d in node["depends_on"]:
            if d in nodes and d != name and d not in kept:
                kept.append(d)
        node["depends_on"] = kept
        deps[name] = kept
    dependents: dict[str, list[str]] = {n: [] for n in nodes}
    for name, ds in deps.items():
        for d in ds:
            dependents[d].append(name)

    layer = _layers(deps)
    down = {n for n, node in nodes.items() if node["health"] == "down"}
    upstream = {n: _upstream(n, deps) for n in nodes}
    for name, node in nodes.items():
        impacted_by = sorted(upstream[name] & down)
        node["dependents"] = sorted(dependents[name])
        node["layer"] = layer[name]
        node["affected"] = bool(impacted_by)
        node["impacted_by"] = impacted_by

    order = sorted(nodes, key=lambda n: (nodes[n]["layer"], _TIER_RANK.get(nodes[n]["tier"], 9), n))
    edges = [{"dependent": n, "dependency": d, "broken": d in down or nodes[d]["affected"]}
             for n in order for d in deps[n]]
    return {
        "environment": listing.get("environment"),
        "host": listing.get("host"),
        "checked_at": listing.get("checked_at"),
        "nodes": [nodes[n] for n in order],
        "edges": edges,
        "down": sorted(down),
        "root_causes": sorted(n for n in down if not (upstream[n] & down)),
        "affected": sorted(n for n in nodes if nodes[n]["affected"]),
    }
