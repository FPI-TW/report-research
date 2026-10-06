#!/usr/bin/env python3
"""硬體用量取樣器（唯讀、零 LLM、不需要 .venv）。

存在理由：上雲選機型需要的是**分位數與尖峰**，而這個 repo 目前只有點狀實測
（註解裡的「rerank 50 對 ~34s」之類）與 `qa_log.latency_ms`——延遲不等於資源
用量，一次 34 秒的 rerank 到底吃掉幾顆核心、峰值多少 RSS，現況完全不可觀測。
`/api/progress` 的 runtime 區塊只回報「行程活著沒」，不是用量。

**刻意不 import `app.*`、不需要 `.venv`、不用 `uv run`**——理由同
`scripts/check_web_health.sh`：2026-08-18 中斷的根因正是 venv 損毀，相依 Python
環境的探針會跟被監控的東西一起死。本檔只用標準庫 ＋ /proc ＋ cgroup v2 檔案，
系統的 `/usr/bin/python3` 就跑得動。

**量測口徑（讀報告前先看懂這三條，否則數字會被誤讀）**

  1. CPU 一律以「核心數」計（`cpu_cores`），不是百分比。0.35 ＝ 平均吃掉
     0.35 顆核心。這是為了讓數字可以直接對到雲端機型的 vCPU，不必再乘上
     本機核心數換算——百分比在 20 核機器上量到的 5%，換到 2 vCPU 機型是 50%。
  2. 分元件用的是 **cgroup v2**，不是掃 `/proc/<pid>`：批次腳本會 fork 出
     `claude` CLI 與 BGE-M3 子行程，按 PID 掃一定會漏掉它們，而那正是尖峰的
     來源。cgroup 天然把子孫行程算進父 unit。
  3. **`user.slice` 也要量並且獨立記錄**。這台機器同時是開發機（Claude Code
     session、Chrome、MCP server 都在 user.slice），不切出來的話「全機 CPU」
     會把開發雜訊當成服務用量報上去，是這類量測最常見的高估來源。

**容器為什麼直接讀 cgroup 而不是 `docker stats`**：實測 `docker stats
--no-stream` 單次 2.1 秒，放進 20 秒的取樣迴圈等於一成的時間都在等 docker。
本機的容器 cgroup 就掛在 `/sys/fs/cgroup/docker/<id>/`，直接讀是微秒級。
docker CLI 只在「把 id 換成人看得懂的名字」與「查 DB 容量」時用，且各有
TTL 與逾時，掛掉就降級（記 id 當名字），不影響取樣本身。

輸出：JSONL，一天一檔 `data/metrics/resource-YYYYMMDD.jsonl`，三種 record：
  kind=meta      啟動時一筆（機器規格、取樣參數、元件清單）
  kind=sample    每 --interval 一筆（速率型指標，含前後兩次快照的差分）
  kind=capacity  每 --capacity-interval 一筆（df、DB 大小、各表列數）

**監控 spool（L3，`--observe-interval`，預設 60 秒）**：同一個常駐行程另外把 Host（CPU、記憶體、
磁碟、I/O PSI、load）、Service Catalog（`deploy/ops/services.prod.toml`）列出的容器狀態與 systemd
unit 狀態，以及批次 oneshot 的每次執行，寫成 spool JSONL（預設 `data/ops_spool/`，`OPS_SPOOL_DIR`
可覆寫）。**收集器不連 DB**：由 `scripts/load_observations.py`（report-mark-load-observations.timer）
冪等匯入 `research.service_observation`／`research.job_execution`，DB 掛掉期間的觀測事後補匯入。
spool 兩種檔、一天一檔、一行一筆：
  observations-YYYYMMDD.jsonl  {"v":1,"type":"observation","host","observed_at","scope","subject",
                                "metrics":{指標:數值},"states":{指標:狀態字串},"detail":{…}}
  jobs-YYYYMMDD.jsonl          {"v":1,"type":"job","host","unit","service","invocation_id","state",
                                "started_at","finished_at","result","exit_status","exec_main_code","observed_at"}
批次「跑過」的判準比照 `scripts/verify_oneshot_ran.sh`：`Result=success`／`ExecMainStatus=0` 不是證據，
必須有 InvocationID 與 ExecMainStartTimestamp；同一次 invocation 只在狀態改變時（running → finished）
多寫一行。每 2 分鐘的探針與 incident oneshot 不算批次（由 P5 消費，記下來只是雜訊）。

用法：
  python3 scripts/collect_resource_usage.py --once            # 印一筆到 stdout
  python3 scripts/collect_resource_usage.py                   # 常駐取樣（systemd 用這個）
  python3 scripts/collect_resource_usage.py --duration 3600   # 只跑一小時

退出碼：0＝正常收場（含 SIGTERM）；1＝參數或輸出路徑錯誤。
**刻意沒有「量到異常就非零退出」**：本檔是取樣器不是偵測器，門檻判定屬於
`scripts/analyze_resource_usage.py`，兩者不合流（同 P4／P5 的分工理由）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # 系統 python 3.11+ 才有；沒有時 catalog 讀不到，L3 的服務／容器觀測降級，取樣本身照常
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 依執行環境而定
    tomllib = None

REPO_ROOT = Path(__file__).resolve().parents[1]
CGROUP_ROOT = Path("/sys/fs/cgroup")
DEFAULT_CATALOG = REPO_ROOT / "deploy" / "ops" / "services.prod.toml"


def default_spool_dir() -> Path:
    """監控 spool 的落點：`OPS_SPOOL_DIR`，未設時是 repo 根的 `data/ops_spool/`。

    與 `scripts/incident_handler.sh`、`scripts/load_observations.py` 同一個預設；測試由
    `tests/conftest.py` 以賦值把它指到 os.devnull。
    """
    return Path(os.environ.get("OPS_SPOOL_DIR") or (REPO_ROOT / "data" / "ops_spool"))

# docker CLI 候選——與 scripts/_docker_bin.sh 同一份邏輯（該檔的註解解釋了為什麼
# 不能用 `command -v docker`：本機 /usr/bin/docker 存在但可能連不到 daemon）。
DOCKER_CANDIDATES = (
    "docker",
    "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe",
    "docker.exe",
)

# 取樣不需要精確到毫秒，但**必須是單調時鐘**：牆鐘會被 NTP 往回調，
# 差分除以負的 dt 會產生天文數字的速率，而那種離群值會污染整份分位數。
_now = time.monotonic


def _read(path: Path) -> str | None:
    """讀檔，任何 OSError 一律回 None。

    cgroup 檔會在 unit 結束的瞬間消失（oneshot 批次跑完就是這樣），
    所以「讀不到」是常態不是錯誤——取樣器不得因此中斷。
    """
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def _kv_file(path: Path) -> dict[str, int]:
    """解析 `key value\\n` 形式的 cgroup 檔（cpu.stat、memory.stat）。"""
    out: dict[str, int] = {}
    text = _read(path)
    if not text:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2:
            try:
                out[parts[0]] = int(parts[1])
            except ValueError:
                continue
    return out


def _int_file(path: Path) -> int | None:
    text = _read(path)
    if not text:
        return None
    text = text.strip()
    try:
        return int(text)
    except ValueError:
        return None


# ── 全機層級的來源 ────────────────────────────────────────────────────────


def proc_stat() -> dict[str, int] | None:
    """/proc/stat 第一行的累計 jiffies。

    `busy` 刻意**排除 iowait**：iowait 是「等 I/O 而閒置」，把它算進 CPU 用量
    會在 WSL 的 9p 上嚴重高估（本機 io PSI 常態 avg60≈5%）。iowait 另外獨立
    回報，因為它對雲端的意義完全不同——它指向 EBS 而不是 vCPU。
    """
    text = _read(Path("/proc/stat"))
    if not text:
        return None
    for line in text.splitlines():
        if not line.startswith("cpu "):
            continue
        f = [int(x) for x in line.split()[1:]]
        while len(f) < 8:
            f.append(0)
        user, nice, system, idle, iowait, irq, softirq, steal = f[:8]
        return {
            "busy": user + nice + system + irq + softirq + steal,
            "idle": idle,
            "iowait": iowait,
            "total": user + nice + system + idle + iowait + irq + softirq + steal,
        }
    return None


def meminfo() -> dict[str, int]:
    out: dict[str, int] = {}
    text = _read(Path("/proc/meminfo"))
    if not text:
        return out
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if not parts:
            continue
        try:
            out[key] = int(parts[0]) * 1024  # meminfo 單位是 kB
        except ValueError:
            continue
    return out


def pressure() -> dict[str, float]:
    """PSI——**這是三個指標裡最會被跳過、卻最該看的一個**。

    CPU 平均值看起來很閒、使用者卻覺得卡，多半是 io some 在高檔（等 I/O 的
    時間佔比）。上雲選 EBS 型別（gp3 vs io2）看的正是這個，不是平均吞吐量。
    """
    out: dict[str, float] = {}
    for res in ("cpu", "io", "memory"):
        text = _read(Path(f"/proc/pressure/{res}"))
        if not text:
            continue
        for line in text.splitlines():
            parts = line.split()
            if not parts:
                continue
            kind = parts[0]  # some / full
            for token in parts[1:]:
                key, _, val = token.partition("=")
                if key == "avg10":
                    try:
                        out[f"{res}_{kind}"] = float(val)
                    except ValueError:
                        pass
    return out


def diskstats() -> dict[str, int]:
    """實體磁碟的累計讀寫（扇區 → bytes）。loop/ram 裝置排除。"""
    total = {"r_bytes": 0, "w_bytes": 0, "r_ios": 0, "w_ios": 0}
    text = _read(Path("/proc/diskstats"))
    if not text:
        return total
    for line in text.splitlines():
        f = line.split()
        if len(f) < 10:
            continue
        name = f[2]
        if name.startswith(("loop", "ram", "zram", "dm-")):
            continue
        # 分割區與整顆磁碟會重複計數（sda 與 sda1）。不能用「名字結尾是不是數字」
        # 判斷——nvme0n1／vda 這類整顆磁碟本身就帶數字；改以「是不是 /sys/block 的
        # 直接成員」判斷，那正是「整顆磁碟」的定義。
        if not (Path("/sys/block") / name).exists():
            continue
        try:
            total["r_ios"] += int(f[3])
            total["r_bytes"] += int(f[5]) * 512
            total["w_ios"] += int(f[7])
            total["w_bytes"] += int(f[9]) * 512
        except ValueError:
            continue
    return total


def netdev() -> dict[str, int]:
    total = {"rx": 0, "tx": 0}
    text = _read(Path("/proc/net/dev"))
    if not text:
        return total
    for line in text.splitlines()[2:]:
        iface, _, rest = line.partition(":")
        iface = iface.strip()
        if iface in ("lo", "") or iface.startswith(("veth", "br-", "docker")):
            continue
        f = rest.split()
        if len(f) < 9:
            continue
        try:
            total["rx"] += int(f[0])
            total["tx"] += int(f[8])
        except ValueError:
            continue
    return total


# ── cgroup 層級 ──────────────────────────────────────────────────────────


def cgroup_snapshot(path: Path) -> dict[str, int] | None:
    """單一 cgroup 的累計值快照。cgroup 已消失時回 None。"""
    cpu = _kv_file(path / "cpu.stat")
    if "usage_usec" not in cpu:
        return None
    mem_stat = _kv_file(path / "memory.stat")
    io_r = io_w = io_rios = io_wios = 0
    io_text = _read(path / "io.stat") or ""
    for line in io_text.splitlines():
        for token in line.split()[1:]:
            key, _, val = token.partition("=")
            try:
                num = int(val)
            except ValueError:
                continue
            if key == "rbytes":
                io_r += num
            elif key == "wbytes":
                io_w += num
            elif key == "rios":
                io_rios += num
            elif key == "wios":
                io_wios += num
    return {
        "cpu_usec": cpu["usage_usec"],
        "mem": _int_file(path / "memory.current") or 0,
        "peak": _int_file(path / "memory.peak") or 0,
        "anon": mem_stat.get("anon", 0),
        "file": mem_stat.get("file", 0),
        "io_r": io_r,
        "io_w": io_w,
        "io_rios": io_rios,
        "io_wios": io_wios,
        "pids": _int_file(path / "pids.current") or 0,
    }


def discover_components(docker_names: dict[str, str]) -> dict[str, Path]:
    """每次取樣都重新探索，不是啟動時探一次。

    理由：批次 unit 是 oneshot，它的 cgroup 只在執行期間存在——而那幾分鐘
    正是全天的資源尖峰（sync 每 3 小時跑一次，內含 BGE-M3 嵌入與 claude CLI）。
    啟動時探一次的話，尖峰永遠不會被看到。

    回傳兩種項目，靠底線前綴區分，**分析端不得把兩者相加**：
      - 葉節點（無前綴）：`web`／`sync`／`report-mark-postgres` 等實際元件。
      - 頂層聚合（`_` 前綴）：cgroup 樹的第一層，合起來涵蓋全機。

    頂層聚合是刻意全掃而非白名單。**WSL 的關鍵事實**：從 WSL shell 啟動的
    行程（互動 shell、`claude` CLI、開發用的東西）落在 **`init.scope`**，
    不在 `user.slice`——只量 user.slice 會把開發負載算成「無人認領」，
    而無人認領的那一塊在報告裡看起來就像服務自己吃掉的。
    """
    comps: dict[str, Path] = {}
    system_slice = CGROUP_ROOT / "system.slice"
    for path in sorted(system_slice.glob("report-mark-*.service")):
        if not path.is_dir():
            continue
        name = path.name.removeprefix("report-mark-").removesuffix(".service")
        comps[name] = path
    docker_dir = CGROUP_ROOT / "docker"
    if docker_dir.is_dir():
        for path in sorted(docker_dir.iterdir()):
            if not path.is_dir() or not (path / "cpu.stat").exists():
                continue
            cid = path.name
            # 只認 64 位十六進位的 container id。`docker/buildx` 這種非容器的
            # 子群一併掃進來的話，報告裡會出現一列永遠是 0 的假元件。
            if len(cid) != 64 or not all(c in "0123456789abcdef" for c in cid):
                continue
            comps[docker_names.get(cid, f"container:{cid[:12]}")] = path
    for path in sorted(CGROUP_ROOT.iterdir()):
        if path.is_dir() and (path / "cpu.stat").exists():
            comps[f"_{path.name}"] = path
    return comps


# ── docker 名稱解析與 DB 容量 ────────────────────────────────────────────


def detect_docker_bin(explicit: str | None) -> str | None:
    if explicit:
        return explicit
    for cand in DOCKER_CANDIDATES:
        try:
            rc = subprocess.run(
                [cand, "info"], capture_output=True, timeout=20, check=False
            ).returncode
        except (OSError, subprocess.SubprocessError):
            continue
        if rc == 0:
            return cand
    return None


def docker_name_map(docker_bin: str | None) -> dict[str, str]:
    """完整 container id → 名稱。失敗回空 dict（呼叫端會退回短 id）。"""
    if not docker_bin:
        return {}
    try:
        proc = subprocess.run(
            [docker_bin, "ps", "--no-trunc", "--format", "{{.ID}}\t{{.Names}}"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    out: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        cid, _, name = line.partition("\t")
        cid, name = cid.strip(), name.strip()
        if cid and name:
            out[cid] = name
    return out


_DB_SQL = """
SELECT json_build_object(
  'database_bytes', pg_database_size(current_database()),
  'tables', (SELECT json_object_agg(c.relname, pg_total_relation_size(c.oid))
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             WHERE n.nspname = 'research' AND c.relkind = 'r'),
  'rows', json_build_object(
     'research_report', (SELECT count(*) FROM research.research_report),
     'report_chunk',    (SELECT count(*) FROM research.report_chunk),
     'qa_log',          (SELECT count(*) FROM research.qa_log)
  ))::text
"""


def db_capacity(docker_bin: str | None, container: str, db_name: str) -> dict | None:
    """DB 容量與列數。**這是磁碟選型與成長率的唯一來源**。

    容器內的 pgdata 不在本 distro 的 df 視野裡（Docker Desktop 的 volume 在
    另一個 VM），所以磁碟大小只能問 Postgres 自己，不能靠 df 推。
    """
    if not docker_bin:
        return None
    try:
        proc = subprocess.run(
            [docker_bin, "exec", container, "psql", "-U", "postgres", "-d", db_name, "-tAc", _DB_SQL],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return json.loads(proc.stdout.strip())
    except (ValueError, TypeError):
        return None


def filesystems(paths: list[str]) -> list[dict]:
    out = []
    seen: set[tuple] = set()
    for p in paths:
        try:
            st = os.statvfs(p)
        except OSError:
            continue
        key = (st.f_blocks, st.f_bsize, st.f_files)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "path": p,
                "size": st.f_blocks * st.f_frsize,
                "used": (st.f_blocks - st.f_bfree) * st.f_frsize,
                "avail": st.f_bavail * st.f_frsize,
            }
        )
    return out


# ── 取樣器本體 ──────────────────────────────────────────────────────────


class Sampler:
    """保存上一次的累計快照，把累計值換成速率。

    第一次取樣沒有前值，因此**刻意不輸出**——半個差分算出來的速率是錯的，
    而錯的第一筆會被分位數當成真實觀測。
    """

    def __init__(self, docker_bin: str | None, name_ttl: float = 300.0) -> None:
        self.docker_bin = docker_bin
        self.name_ttl = name_ttl
        self._names: dict[str, str] = docker_name_map(docker_bin)
        self._names_at = _now()
        self._prev_t: float | None = None
        self._prev_stat: dict[str, int] | None = None
        self._prev_disk: dict[str, int] | None = None
        self._prev_net: dict[str, int] | None = None
        self._prev_comp: dict[str, dict[str, int]] = {}
        self.ncpu = os.cpu_count() or 1

    @property
    def names(self) -> dict[str, str]:
        """已解析的 container id → 名稱（meta record 用）。"""
        return dict(self._names)

    def _refresh_names(self) -> None:
        if _now() - self._names_at < self.name_ttl:
            return
        fresh = docker_name_map(self.docker_bin)
        self._names_at = _now()
        if fresh:
            self._names = fresh

    def sample(self) -> dict | None:
        self._refresh_names()
        t = _now()
        stat = proc_stat()
        disk = diskstats()
        net = netdev()
        comps = discover_components(self._names)
        snaps = {name: cgroup_snapshot(path) for name, path in comps.items()}
        snaps = {k: v for k, v in snaps.items() if v}

        prev_t, self._prev_t = self._prev_t, t
        prev_stat, self._prev_stat = self._prev_stat, stat
        prev_disk, self._prev_disk = self._prev_disk, disk
        prev_net, self._prev_net = self._prev_net, net
        prev_comp, self._prev_comp = self._prev_comp, snaps

        if prev_t is None or t <= prev_t:
            return None
        dt = t - prev_t

        host: dict[str, object] = {"ncpu": self.ncpu, "dt": round(dt, 2)}
        if stat and prev_stat:
            d_total = stat["total"] - prev_stat["total"]
            if d_total > 0:
                host["cpu_cores"] = round((stat["busy"] - prev_stat["busy"]) / d_total * self.ncpu, 4)
                host["iowait_cores"] = round((stat["iowait"] - prev_stat["iowait"]) / d_total * self.ncpu, 4)
        mem = meminfo()
        if mem:
            host["mem_total"] = mem.get("MemTotal", 0)
            host["mem_avail"] = mem.get("MemAvailable", 0)
            host["mem_used"] = mem.get("MemTotal", 0) - mem.get("MemAvailable", 0)
            host["cached"] = mem.get("Cached", 0)
            host["swap_used"] = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
        try:
            host["load1"] = round(os.getloadavg()[0], 2)
        except OSError:
            pass
        host["psi"] = pressure()
        if prev_disk:
            host["disk_r"] = int((disk["r_bytes"] - prev_disk["r_bytes"]) / dt)
            host["disk_w"] = int((disk["w_bytes"] - prev_disk["w_bytes"]) / dt)
            host["disk_iops"] = int(
                (disk["r_ios"] - prev_disk["r_ios"] + disk["w_ios"] - prev_disk["w_ios"]) / dt
            )
        if prev_net:
            host["net_rx"] = int((net["rx"] - prev_net["rx"]) / dt)
            host["net_tx"] = int((net["tx"] - prev_net["tx"]) / dt)

        comp_out: dict[str, dict] = {}
        for name, snap in snaps.items():
            prev = prev_comp.get(name)
            entry: dict[str, object] = {
                "mem": snap["mem"],
                "anon": snap["anon"],
                "file": snap["file"],
                "peak": snap["peak"],
                "pids": snap["pids"],
            }
            if prev and snap["cpu_usec"] >= prev["cpu_usec"]:
                # cgroup 重建（unit 重啟）會讓累計值歸零，那時 snap < prev，
                # 上面的條件把那一筆的 CPU 留空而不是記成負值或暴衝。
                entry["cpu"] = round((snap["cpu_usec"] - prev["cpu_usec"]) / 1e6 / dt, 4)
                entry["io_r"] = int(max(0, snap["io_r"] - prev["io_r"]) / dt)
                entry["io_w"] = int(max(0, snap["io_w"] - prev["io_w"]) / dt)
                entry["io_iops"] = int(
                    max(0, snap["io_rios"] - prev["io_rios"] + snap["io_wios"] - prev["io_wios"]) / dt
                )
            comp_out[name] = entry

        return {
            "kind": "sample",
            "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            "host": host,
            "comp": comp_out,
        }


def capacity_record(args, docker_bin: str | None) -> dict:
    return {
        "kind": "capacity",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "fs": filesystems([str(REPO_ROOT), "/"]),
        "db": db_capacity(docker_bin, args.db_container, args.db_name),
    }


def meta_record(sampler: Sampler, args, docker_bin: str | None) -> dict:
    mem = meminfo()
    return {
        "kind": "meta",
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "host": os.uname().nodename,
        "kernel": os.uname().release,
        "ncpu": sampler.ncpu,
        "mem_total": mem.get("MemTotal", 0),
        "swap_total": mem.get("SwapTotal", 0),
        "interval": args.interval,
        "capacity_interval": args.capacity_interval,
        "docker_bin": docker_bin,
        "components": sorted(discover_components(sampler.names)),
    }


class Writer:
    """一天一檔的 JSONL 附加寫入，含保留期修剪。

    每筆都 flush：取樣器被 SIGKILL（WSL 被收掉）時，已寫的資料不能跟著消失
    ——那正是最需要被觀測的時刻。
    """

    def __init__(self, directory: Path, retention_days: int) -> None:
        self.dir = directory
        self.retention_days = retention_days
        self.dir.mkdir(parents=True, exist_ok=True)
        self._day: str | None = None
        self._fh = None

    def _path_for(self, day: str) -> Path:
        return self.dir / f"resource-{day}.jsonl"

    def _prune(self) -> None:
        if self.retention_days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        for path in self.dir.glob("resource-*.jsonl"):
            stem = path.stem.removeprefix("resource-")
            try:
                when = datetime.strptime(stem, "%Y%m%d")
            except ValueError:
                continue  # 名字不合格式的檔一律不動——修剪不得誤刪別人的東西
            if when < cutoff:
                path.unlink(missing_ok=True)

    def write(self, record: dict) -> None:
        day = datetime.now().strftime("%Y%m%d")
        if day != self._day:
            if self._fh:
                self._fh.close()
            self._fh = self._path_for(day).open("a", encoding="utf-8")
            self._day = day
            self._prune()
        self._fh.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()
            self._fh = None


# ── L3：監控 spool（Host／Container／Service 觀測與批次執行紀錄）──────────────
#
# 名單取自 Service Catalog（ops_agent 的同一份 TOML），不自己另列一份：catalog 是「這台主機上有哪些
# 服務、各屬哪個 tier」的唯一定義。讀不到 catalog（檔案不在、python 沒有 tomllib、格式壞了）時只降級
# 這一段（沒有服務／容器觀測），Host 觀測與硬體取樣照常——監控工具不得因為一份設定檔而整個停擺。

# 每 2 分鐘的探針與 incident oneshot：由 P5 消費、不是批次，記成 job 只會每天多出數千列雜訊。
JOB_EXCLUDE_SUFFIXES = ("-health.service", "-incident.service")
_INVOCATION_ID = re.compile(r"[0-9a-f]{32}")
_UNIX_TS = re.compile(r"@(\d{1,12})")
_METRIC = re.compile(r"[a-z][a-z0-9_]{0,63}")
_SYSTEMD_PROPS = (
    "Id", "LoadState", "ActiveState", "SubState", "Result", "UnitFileState", "NRestarts",
    "ExecMainCode", "ExecMainStatus", "ExecMainStartTimestamp", "ExecMainStartTimestampMonotonic",
    "ExecMainExitTimestamp", "ExecMainExitTimestampMonotonic", "InvocationID",
)
_EXEC_CODES = {"1": "exited", "2": "killed", "3": "dumped"}
_RUNNING_STATES = {"activating", "deactivating", "reloading", "refreshing"}
# 與 ops_agent/backends.py 的 DOCKER_INSPECT_FORMAT 同一個取法：只取 State 與 RestartCount，
# 不取 Config.Env（環境變數可能含祕密）與 State.Health.Log（健康檢查輸出可能帶任何東西）。
DOCKER_STATE_FORMAT = '{"name":{{json .Name}},"state":{{json .State}},"restart_count":{{json .RestartCount}}}'


def _iso_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_catalog_targets(path: Path) -> list[dict]:
    """catalog 的 `[[services]]` → [{name, kind, tier, unit, timer, container}]。讀不到回 []。"""
    if tomllib is None:
        print("collect_resource_usage: python 沒有 tomllib（需 3.11+），略過服務／容器觀測", file=sys.stderr)
        return []
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except (OSError, ValueError) as exc:
        print(f"collect_resource_usage: 讀不到 catalog {path}：{exc}，略過服務／容器觀測", file=sys.stderr)
        return []
    out = []
    for raw in data.get("services") or []:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("kind")
        name = raw.get("name")
        if kind not in ("systemd", "container") or not isinstance(name, str):
            continue
        out.append({
            "name": name,
            "kind": kind,
            "tier": raw.get("tier"),
            "unit": raw.get("unit") if kind == "systemd" else None,
            "timer": raw.get("timer") if kind == "systemd" else None,
            "container": raw.get("container") if kind == "container" else None,
        })
    return out


def parse_show_blocks(text: str) -> dict[str, dict[str, str]]:
    """`systemctl show` 多 unit 輸出（空行分段）→ {Id: {屬性: 值}}。以 Id 對應，不靠順序。"""
    out: dict[str, dict[str, str]] = {}
    for raw in text.strip("\n").split("\n\n"):
        props: dict[str, str] = {}
        for line in raw.splitlines():
            key, sep, value = line.partition("=")
            if sep:
                props[key] = value
        if props.get("Id"):
            out[props["Id"]] = props
    return out


def systemd_show(systemctl: str, units: list[str], timeout: float = 10.0) -> dict[str, dict[str, str]]:
    """一次 `systemctl show` 查全部 unit（`--timestamp=unix`：時間戳與時區、語系無關）。失敗回 {}。"""
    if not units:
        return {}
    try:
        proc = subprocess.run(
            [systemctl, "show", "--no-pager", "--timestamp=unix", "-p", ",".join(_SYSTEMD_PROPS), "--", *units],
            capture_output=True, text=True, timeout=timeout, check=False, env={**os.environ, "TZ": "UTC"},
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if proc.returncode != 0:
        return {}
    return parse_show_blocks(proc.stdout)


def unix_ts(value: str | None) -> datetime | None:
    m = _UNIX_TS.fullmatch(value or "")
    if not m:
        return None
    return datetime.fromtimestamp(int(m.group(1)), tz=timezone.utc)


def _intval(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def docker_states(docker_bin: str | None, names: list[str], timeout: float = 20.0) -> dict[str, dict] | None:
    """{容器名: {status, health, restart_count, exit_code, oom_killed, started_at}}。

    docker 不可用回 None（＝判不出來，不寫觀測）；容器不存在則不出現在結果裡（呼叫端記成 missing）。
    """
    if not docker_bin or not names:
        return None
    try:
        proc = subprocess.run(
            [docker_bin, "inspect", "--type", "container", "--format", DOCKER_STATE_FORMAT, "--", *names],
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    found: dict[str, dict] = {}
    for line in proc.stdout.splitlines():
        try:
            info = json.loads(line)
        except ValueError:
            continue
        if not isinstance(info, dict) or not isinstance(info.get("name"), str):
            continue
        st = info.get("state") if isinstance(info.get("state"), dict) else {}
        health = st.get("Health") if isinstance(st.get("Health"), dict) else {}
        started = st.get("StartedAt")
        found[info["name"].lstrip("/")] = {
            "status": st.get("Status") if isinstance(st.get("Status"), str) else "unknown",
            "health": health.get("Status") if isinstance(health.get("Status"), str) else None,
            "restart_count": info.get("restart_count") if isinstance(info.get("restart_count"), int) else None,
            "exit_code": st.get("ExitCode") if isinstance(st.get("ExitCode"), int) else None,
            "oom_killed": st.get("OOMKilled") if isinstance(st.get("OOMKilled"), bool) else None,
            "started_at": started if isinstance(started, str) and not started.startswith("0001-") else None,
        }
    if not found and proc.returncode != 0 and "No such" not in proc.stderr:
        return None  # daemon 連不上、逾時之類：判不出來
    return found


def pressure_full() -> dict[str, float]:
    """PSI 的 avg10 與 avg60（some／full）。既有的 `pressure()` 只取 avg10，給硬體取樣用，不動它。"""
    out: dict[str, float] = {}
    for res in ("cpu", "io", "memory"):
        text = _read(Path(f"/proc/pressure/{res}"))
        if not text:
            continue
        for line in text.splitlines():
            parts = line.split()
            if not parts or parts[0] not in ("some", "full"):
                continue
            for token in parts[1:]:
                key, _, val = token.partition("=")
                if key in ("avg10", "avg60"):
                    try:
                        out[f"psi_{res}_{parts[0]}_{key}"] = float(val)
                    except ValueError:
                        pass
    return out


def _clean_metrics(metrics: dict) -> dict[str, float]:
    return {
        k: round(float(v), 4) for k, v in metrics.items()
        if _METRIC.fullmatch(k) and isinstance(v, (int, float)) and not isinstance(v, bool)
    }


class Observer:
    """每 `--observe-interval` 產出一批 spool 紀錄（observation 與 job）。

    容器狀態有 TTL（`container_ttl`），做法與 `Sampler` 的名稱解析相同：docker CLI 慢（`docker.exe`
    經 WSL interop 秒級），不必每一輪都問；掛掉就降級（不寫容器觀測），不影響其他觀測。
    """

    def __init__(self, targets: list[dict], docker_bin: str | None, *, systemctl: str = "systemctl",
                 container_ttl: float = 60.0, host: str | None = None,
                 fs_paths: list[str] | None = None) -> None:
        self.targets = targets
        self.docker_bin = docker_bin
        self.systemctl = systemctl
        self.container_ttl = container_ttl
        self.host = host or os.uname().nodename
        self.fs_paths = fs_paths if fs_paths is not None else [str(REPO_ROOT), "/"]
        self._containers: dict[str, dict] | None = None
        self._containers_at: float | None = None
        # unit → (invocation_id, state, started_at ISO)：同一次 invocation 只在狀態改變時寫一行
        self._jobs: dict[str, tuple[str, str, str]] = {}

    def _container_states(self) -> dict[str, dict] | None:
        names = [t["container"] for t in self.targets if t["kind"] == "container" and t.get("container")]
        if not names:
            return None
        if self._containers_at is None or _now() - self._containers_at >= self.container_ttl:
            self._containers = docker_states(self.docker_bin, names)
            self._containers_at = _now()
        return self._containers

    def _obs(self, ts: str, scope: str, subject: str, metrics=None, states=None, detail=None) -> dict:
        rec: dict = {"v": 1, "type": "observation", "host": self.host, "observed_at": ts,
                     "scope": scope, "subject": subject}
        rec["metrics"] = _clean_metrics(metrics or {})
        rec["states"] = {k: str(v)[:64] for k, v in (states or {}).items() if _METRIC.fullmatch(k) and v}
        if detail:
            rec["detail"] = detail
        return rec

    def host_records(self, ts: str, sample: dict | None) -> list[dict]:
        host = (sample or {}).get("host") or {}
        ncpu = host.get("ncpu") or os.cpu_count() or 1
        metrics: dict[str, float] = {}
        if "cpu_cores" in host:
            metrics["cpu_pct"] = host["cpu_cores"] / ncpu * 100
        if "iowait_cores" in host:
            metrics["iowait_pct"] = host["iowait_cores"] / ncpu * 100
        mem = meminfo()
        total = mem.get("MemTotal", 0)
        if total:
            avail = mem.get("MemAvailable", 0)
            metrics["mem_used_pct"] = (total - avail) / total * 100
            metrics["mem_avail_bytes"] = avail
            metrics["swap_used_bytes"] = mem.get("SwapTotal", 0) - mem.get("SwapFree", 0)
        try:
            la = os.getloadavg()
            metrics.update(load1=la[0], load5=la[1], load15=la[2])
        except OSError:
            pass
        metrics.update(pressure_full())
        for src, dst in (("disk_r", "disk_read_bps"), ("disk_w", "disk_write_bps"), ("disk_iops", "disk_iops")):
            if src in host:
                metrics[dst] = host[src]
        out = [self._obs(ts, "host", "host", metrics)]
        for fs in filesystems(self.fs_paths):
            if fs["size"] <= 0:
                continue
            out.append(self._obs(ts, "host", f"fs:{fs['path']}", {
                "used_pct": fs["used"] / fs["size"] * 100, "avail_bytes": fs["avail"],
            }))
        return out

    def records(self, sample: dict | None = None) -> list[dict]:
        """一批紀錄：host ＋（catalog 有的話）容器、systemd unit 與 job。"""
        ts = _iso_now()
        out = self.host_records(ts, sample)
        comp = (sample or {}).get("comp") or {}

        states = self._container_states()
        if states is not None:
            for t in self.targets:
                if t["kind"] != "container" or not t.get("container"):
                    continue
                st = states.get(t["container"])
                usage = comp.get(t["container"]) or {}
                metrics = {"cpu_cores": usage.get("cpu"), "mem_bytes": usage.get("mem")}
                if st is None:
                    out.append(self._obs(ts, "container", t["name"], states={"status": "missing"},
                                         detail={"container": t["container"], "tier": t.get("tier")}))
                    continue
                metrics["restart_count"] = st.get("restart_count")
                out.append(self._obs(
                    ts, "container", t["name"], {k: v for k, v in metrics.items() if v is not None},
                    {"status": st.get("status"), "health": st.get("health")},
                    {"container": t["container"], "tier": t.get("tier"), "exit_code": st.get("exit_code"),
                     "oom_killed": st.get("oom_killed"), "started_at": st.get("started_at")},
                ))

        systemd = [t for t in self.targets if t["kind"] == "systemd" and t.get("unit")]
        show = systemd_show(self.systemctl, [t["unit"] for t in systemd]) if systemd else {}
        for t in systemd:
            props = show.get(t["unit"])
            if not props:
                continue
            usage = comp.get(t["unit"].removeprefix("report-mark-").removesuffix(".service")) or {}
            metrics = {"cpu_cores": usage.get("cpu"), "mem_bytes": usage.get("mem")}
            out.append(self._obs(
                ts, "service", t["name"], {k: v for k, v in metrics.items() if v is not None},
                {"active_state": props.get("ActiveState")},
                {"unit": t["unit"], "tier": t.get("tier"), "load_state": props.get("LoadState") or None,
                 "sub_state": props.get("SubState") or None, "result": props.get("Result") or None,
                 "unit_file_state": props.get("UnitFileState") or None,
                 "n_restarts": _intval(props.get("NRestarts")),
                 "exec_main_status": _intval(props.get("ExecMainStatus"))},
            ))
            if t.get("timer") and not t["unit"].endswith(JOB_EXCLUDE_SUFFIXES):
                out.extend(self.job_records(t, props, ts))
        return out

    def job_records(self, target: dict, props: dict[str, str], ts: str) -> list[dict]:
        """一個 oneshot 的執行紀錄（狀態沒變就不寫）。"""
        unit = target["unit"]
        inv = props.get("InvocationID", "")
        start_mono = _intval(props.get("ExecMainStartTimestampMonotonic")) or 0
        exit_mono = _intval(props.get("ExecMainExitTimestampMonotonic")) or 0
        started = unix_ts(props.get("ExecMainStartTimestamp"))
        # 「跑過」的必要條件（verify_oneshot_ran.sh）：有 InvocationID 與啟動時間戳。GC 過或從未執行的 unit
        # 兩者都是空的，Result=success 只是預設值——那不是一次執行，不寫。
        if not _INVOCATION_ID.fullmatch(inv) or start_mono <= 0 or started is None:
            return []
        active = props.get("ActiveState", "")
        if exit_mono >= start_mono and active not in _RUNNING_STATES:
            state = "finished"
        elif exit_mono == 0 or active in _RUNNING_STATES:
            state = "running"
        else:
            return []  # 完成時戳早於啟動：兩個欄位來自不同 invocation（讀數跨越邊界），下一輪再看
        out: list[dict] = []
        prev = self._jobs.get(unit)
        if prev and prev[0] != inv and prev[1] == "running":
            # 上一次記成 running 的 invocation 在兩次取樣之間結束、下一次又開始了：補一筆結束，
            # 結果不明（systemd 已經不保留它的屬性），結束時間以新一次的啟動為上界。
            out.append(self._job(target, prev[0], "finished", prev[2], started.isoformat(), None, None, None, ts))
        if prev and prev[0] == inv and prev[1] == state:
            return out
        finished = unix_ts(props.get("ExecMainExitTimestamp")) if state == "finished" else None
        if state == "finished" and finished is None:
            return out
        out.append(self._job(
            target, inv, state, started.isoformat(), finished.isoformat() if finished else None,
            (props.get("Result") or None) if state == "finished" else None,
            _intval(props.get("ExecMainStatus")) if state == "finished" else None,
            _EXEC_CODES.get(props.get("ExecMainCode", "")) if state == "finished" else None, ts,
        ))
        self._jobs[unit] = (inv, state, started.isoformat())
        return out

    def _job(self, target, inv, state, started, finished, result, status, code, ts) -> dict:
        return {"v": 1, "type": "job", "host": self.host, "unit": target["unit"], "service": target["name"],
                "invocation_id": inv, "state": state, "started_at": started, "finished_at": finished,
                "result": result, "exit_status": status, "exec_main_code": code, "observed_at": ts}


class SpoolWriter:
    """spool 的附加寫入：`<type>-YYYYMMDD.jsonl`，每筆一次寫完並 flush。

    **每筆重新開檔再關**（不持有 fd）：loader 刪掉已匯入的舊日檔時，這裡不會繼續寫進已被刪除的
    inode。保留期修剪是安全網：loader 正常時已匯入的舊檔早就被它刪掉；loader 長期停擺時，這裡把
    spool 的體積限制在 `retention_days` 天內（那之前的觀測只剩 journald／硬體取樣檔）。
    """

    KINDS = ("observations", "jobs")

    def __init__(self, directory: Path, retention_days: int = 14) -> None:
        self.dir = directory
        self.retention_days = retention_days
        self.dir.mkdir(parents=True, exist_ok=True)
        self._pruned_day: str | None = None

    def _prune(self) -> None:
        if self.retention_days <= 0:
            return
        cutoff = datetime.now() - timedelta(days=self.retention_days)
        for kind in self.KINDS:
            for path in self.dir.glob(f"{kind}-*.jsonl"):
                try:
                    when = datetime.strptime(path.stem.removeprefix(f"{kind}-"), "%Y%m%d")
                except ValueError:
                    continue  # 不是自己命名規則的檔一律不動
                if when < cutoff:
                    path.unlink(missing_ok=True)

    def write(self, records: list[dict]) -> int:
        day = datetime.now().strftime("%Y%m%d")
        if day != self._pruned_day:
            self._prune()
            self._pruned_day = day
        by_kind: dict[str, list[str]] = {}
        for rec in records:
            kind = "jobs" if rec.get("type") == "job" else "observations"
            by_kind.setdefault(kind, []).append(json.dumps(rec, ensure_ascii=False, separators=(",", ":")))
        for kind, lines in by_kind.items():
            with (self.dir / f"{kind}-{day}.jsonl").open("a", encoding="utf-8") as fh:
                fh.write("".join(line + "\n" for line in lines))
                fh.flush()
        return sum(len(v) for v in by_kind.values())


_STOP = False


def _on_signal(signum, frame) -> None:  # noqa: ARG001
    global _STOP
    _STOP = True


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="硬體用量取樣器（唯讀，不需 .venv）")
    p.add_argument("--interval", type=float, default=20.0, help="取樣間隔秒數（預設 20）")
    p.add_argument(
        "--capacity-interval",
        type=float,
        default=600.0,
        help="容量取樣間隔秒數（df/DB 大小，預設 600；設 0 關閉）",
    )
    p.add_argument("--duration", type=float, default=0.0, help="總時長秒數（0＝不限，systemd 用）")
    p.add_argument("--out-dir", default=str(REPO_ROOT / "data" / "metrics"), help="輸出目錄")
    p.add_argument("--retention-days", type=int, default=30, help="保留天數（0＝不修剪）")
    p.add_argument("--once", action="store_true", help="只取一筆並印到 stdout（不寫檔）")
    p.add_argument("--docker-bin", default=None, help="指定 docker 執行檔（預設自動偵測）")
    p.add_argument("--db-container", default="report-mark-postgres", help="Postgres 容器名")
    p.add_argument("--db-name", default="research", help="資料庫名")
    p.add_argument("--no-db", action="store_true", help="容量取樣不查 DB（不啟動 docker exec）")
    p.add_argument(
        "--observe-interval", type=float, default=60.0,
        help="監控 spool 的觀測間隔秒數（Host／容器／服務／批次執行，預設 60；設 0 關閉）",
    )
    p.add_argument("--spool-dir", default=None, help="監控 spool 目錄（預設 $OPS_SPOOL_DIR 或 data/ops_spool）")
    p.add_argument("--spool-retention-days", type=int, default=14, help="spool 保留天數安全網（0＝不修剪）")
    p.add_argument("--catalog", default=str(DEFAULT_CATALOG), help="Service Catalog（服務與容器名單）")
    p.add_argument("--container-ttl", type=float, default=60.0, help="容器狀態快取秒數（預設 60）")
    p.add_argument("--systemctl", default="systemctl", help="systemctl 執行檔")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.interval <= 0:
        print("--interval 必須大於 0", file=sys.stderr)
        return 1

    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)

    # 即使 --no-db 也要偵測：容器名稱解析同樣靠它，少了它報告裡會出現
    # `container:816434d4e5cb` 這種沒人看得懂的列。--no-db 只關掉 DB 查詢。
    docker_bin = detect_docker_bin(args.docker_bin)
    sampler = Sampler(docker_bin)
    observer = None
    if args.observe_interval > 0:
        observer = Observer(load_catalog_targets(Path(args.catalog)), docker_bin,
                            systemctl=args.systemctl, container_ttl=args.container_ttl)

    if args.once:
        sampler.sample()  # 暖機一筆，取得差分基準
        time.sleep(min(args.interval, 2.0))
        rec = sampler.sample()
        out = {"meta": meta_record(sampler, args, docker_bin), "sample": rec}
        if not args.no_db:
            out["capacity"] = capacity_record(args, docker_bin)
        if observer is not None:
            out["observations"] = observer.records(rec)
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    writer = Writer(Path(args.out_dir), args.retention_days)
    writer.write(meta_record(sampler, args, docker_bin))
    spool = None
    if observer is not None:
        try:
            spool = SpoolWriter(Path(args.spool_dir) if args.spool_dir else default_spool_dir(),
                                args.spool_retention_days)
        except OSError as exc:
            # spool 落點壞了只關掉 L3 這一段，硬體取樣照常（它的資料另有用途）
            print(f"collect_resource_usage: spool 目錄不可用（{exc}），停用監控觀測", file=sys.stderr)
    sampler.sample()  # 暖機
    last_sample: dict | None = None

    started = _now()
    next_sample = started + args.interval
    next_capacity = started if args.capacity_interval > 0 and not args.no_db else float("inf")
    # 第一批觀測等第一筆硬體取樣（CPU 百分比要差分）再寫
    next_observe = started + args.interval + 1 if spool is not None else float("inf")
    try:
        while not _STOP:
            now = _now()
            if args.duration > 0 and now - started >= args.duration:
                break
            due = min(next_sample, next_capacity, next_observe)
            if due > now:
                # 一次最多睡 1 秒，讓 SIGTERM 的收場延遲有上界（systemd 預設
                # TimeoutStopSec=90，但沒有理由讓一次停止等滿一個取樣間隔）。
                time.sleep(min(1.0, due - now))
                continue
            if now >= next_capacity:
                writer.write(capacity_record(args, docker_bin))
                next_capacity = now + args.capacity_interval
            if now >= next_observe:
                try:
                    spool.write(observer.records(last_sample))
                except OSError as exc:
                    print(f"collect_resource_usage: spool 寫入失敗：{exc}", file=sys.stderr)
                next_observe = now + args.observe_interval
            if now >= next_sample:
                rec = sampler.sample()
                if rec:
                    writer.write(rec)
                    last_sample = rec
                # 以「上次應該取樣的時間」推進而非 now，避免取樣成本累積成漂移；
                # 但若已落後超過一整個間隔（機器被凍住），直接對齊到現在，
                # 不補跑那些已經沒有差分基準的時點。
                next_sample += args.interval
                if next_sample < now:
                    next_sample = now + args.interval
    finally:
        writer.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
