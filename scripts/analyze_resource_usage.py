#!/usr/bin/env python3
"""把取樣器的 JSONL 轉成「可以拿去選機型」的分位數報告（唯讀、零 LLM）。

與 `scripts/collect_resource_usage.py` 的分工同 P4／P5：那支只記錄事實、不做
任何判定；門檻、分位數、成長率外推與選型建議全部在這裡。分開的理由是取樣器
必須極度不容易壞（它跑在常駐 unit 裡），而判定邏輯會反覆修改。

**三條刻意的統計口徑，讀數字前先看懂**

  1. 「服務合計」是**每一筆樣本先加總、再取分位數**，不是「各元件的 p95 相加」。
     後者是這類報告最常見的錯誤：web 的尖峰與 postgres 的尖峰多半不同時發生，
     把兩個 p95 相加會系統性高估，而高估的結果是每個月多付一台機器的錢。
  2. **非服務負載一律扣掉。** 這台機器同時是開發機（Claude Code、Chrome、MCP
     server 都在 `init.scope`——WSL 下不是 `user.slice`），實測閒置時全機 CPU
     常態就有 1 顆核心以上，那與上雲後要跑的東西完全無關。
  3. **批次元件的分位數只取「該 unit 活著的樣本」**。sync 每 3 小時跑一次，
     把 99% 的閒置樣本算進去的話 p95 會是 0——而那幾分鐘正是全天尖峰。
     報表另外標出活躍樣本佔比，讓讀者自己判斷這個尖峰有多常發生。

用法：
  python3 scripts/analyze_resource_usage.py                   # 讀 data/metrics/ 全部
  python3 scripts/analyze_resource_usage.py --since 24h
  python3 scripts/analyze_resource_usage.py --json

退出碼：0＝完成；2＝找不到可用樣本（與 check_batch_freshness.py 同慣例：
「量測工具本身沒資料」與「量到問題」是不同的處置，不可共用退出碼）。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DIR = REPO_ROOT / "data" / "metrics"

GIB = 1024**3
MIB = 1024**2

# 常見雲端規格階梯。**取整到這裡是刻意的**：實測值算出 3.4 vCPU 沒有任何機型
# 對得上，報一個買不到的數字等於沒有結論。
VCPU_STEPS = (2, 4, 8, 16, 32, 64)
RAM_STEPS_GIB = (2, 4, 8, 16, 32, 64, 128)
DISK_STEPS_GB = (20, 50, 100, 200, 300, 500, 1000, 2000)

# 批次型元件（oneshot unit）。壓測窗期若撞上它們，「邊際成本」會把批次的 CPU
# 算到請求頭上——2026-08-28 首次壓測就正好撞上 12:00 的 sync，web 與 sync 各吃
# 約 10 核、把 20 核機器打滿。**這種污染必須被偵測並標示，不能只出現在數字裡。**
BATCH_COMPONENTS = ("sync", "backup", "audit", "freshness")

# headroom 係數。分開列而不是寫成一個「安全係數」：三者的理由不同，
# 未來要調整時該調哪一個必須看得出來。
CPU_HEADROOM = 1.5   # 尖峰之上還要留給突發與 GC；低於 1.3 在 p99 上會排隊
RAM_HEADROOM = 1.35  # 常駐模型的記憶體是階梯式成長（載入即到位），餘裕可以小
DISK_HEADROOM = 1.3  # 磁碟擴充有 downtime，餘裕給大一點


def _w(s: str) -> int:
    """顯示寬度（CJK 算 2）——表格對齊用。"""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def pct(values: list[float], q: float) -> float:
    """線性內插分位數（與 numpy 預設一致）。空序列回 0.0。"""
    if not values:
        return 0.0
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return xs[int(pos)]
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def quantiles(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "p50": pct(values, 0.50),
        "p90": pct(values, 0.90),
        "p95": pct(values, 0.95),
        "p99": pct(values, 0.99),
        "max": max(values) if values else 0.0,
        "mean": sum(values) / len(values) if values else 0.0,
    }


def round_up_to(value: float, steps: tuple[int, ...]) -> int:
    for s in steps:
        if value <= s:
            return s
    return steps[-1]


def parse_since(text: str | None) -> datetime | None:
    """`24h`／`7d`／`2026-08-28` 三種寫法。"""
    if not text:
        return None
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([hd])", text.strip())
    if m:
        amount = float(m.group(1))
        delta = timedelta(hours=amount) if m.group(2) == "h" else timedelta(days=amount)
        return datetime.now().astimezone() - delta
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise SystemExit(f"--since 格式無法解析：{text}（可用 24h／7d／ISO 日期）") from exc
    return dt if dt.tzinfo else dt.astimezone()


def load_records(paths: list[Path], since: datetime | None):
    meta: dict | None = None
    samples: list[dict] = []
    caps: list[dict] = []
    bad = 0
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                # 取樣器被砍在半行的情況（SIGKILL）。**跳過而不是中止**：
                # 那半行的前面全是有效資料，為了一行壞掉丟掉一整天不合理。
                bad += 1
                continue
            ts_text = rec.get("ts")
            try:
                ts = datetime.fromisoformat(ts_text) if ts_text else None
            except ValueError:
                bad += 1
                continue
            if ts is None:
                continue
            if since and ts < since:
                continue
            rec["_ts"] = ts
            kind = rec.get("kind")
            if kind == "sample":
                samples.append(rec)
            elif kind == "capacity":
                caps.append(rec)
            elif kind == "meta" and meta is None:
                meta = rec
    samples.sort(key=lambda r: r["_ts"])
    caps.sort(key=lambda r: r["_ts"])
    return meta, samples, caps, bad


def is_aggregate(name: str) -> bool:
    return name.startswith("_")


def build_report(meta, samples, caps, bad) -> dict:
    first, last = samples[0]["_ts"], samples[-1]["_ts"]
    span_s = max(1.0, (last - first).total_seconds())
    interval = float((meta or {}).get("interval") or 20.0)
    expected = span_s / interval + 1
    coverage = min(1.0, len(samples) / expected) if expected > 0 else 0.0

    ncpu = int((meta or {}).get("ncpu") or samples[-1]["host"].get("ncpu") or 1)
    mem_total = int((meta or {}).get("mem_total") or samples[-1]["host"].get("mem_total") or 0)

    leaf_names: set[str] = set()
    for s in samples:
        leaf_names.update(n for n in s.get("comp", {}) if not is_aggregate(n))

    host_cpu: list[float] = []
    host_iowait: list[float] = []
    host_mem_used: list[float] = []
    host_swap: list[float] = []
    host_io_bps: list[float] = []
    host_iops: list[float] = []
    host_net_tx: list[float] = []
    psi_io: list[float] = []
    psi_cpu: list[float] = []
    svc_cpu: list[float] = []
    online_cpu: list[float] = []
    batch_cpu: list[float] = []
    online_anon: list[float] = []
    batch_active = 0
    svc_anon: list[float] = []
    svc_mem: list[float] = []
    nonsvc_cpu: list[float] = []
    per_comp: dict[str, dict[str, list[float]]] = {
        n: {"cpu": [], "mem": [], "anon": [], "io": [], "peak": []} for n in leaf_names
    }
    agg_cpu: dict[str, list[float]] = {}

    for s in samples:
        h = s.get("host", {})
        if "cpu_cores" in h:
            host_cpu.append(float(h["cpu_cores"]))
        if "iowait_cores" in h:
            host_iowait.append(float(h["iowait_cores"]))
        if "mem_used" in h:
            host_mem_used.append(float(h["mem_used"]))
        if "swap_used" in h:
            host_swap.append(float(h["swap_used"]))
        if "disk_r" in h and "disk_w" in h:
            host_io_bps.append(float(h["disk_r"]) + float(h["disk_w"]))
        if "disk_iops" in h:
            host_iops.append(float(h["disk_iops"]))
        if "net_tx" in h:
            host_net_tx.append(float(h["net_tx"]))
        psi = h.get("psi") or {}
        if "io_some" in psi:
            psi_io.append(float(psi["io_some"]))
        if "cpu_some" in psi:
            psi_cpu.append(float(psi["cpu_some"]))

        comp = s.get("comp", {})
        cpu_sum = anon_sum = mem_sum = 0.0
        for name, c in comp.items():
            if is_aggregate(name):
                agg_cpu.setdefault(name, []).append(float(c.get("cpu") or 0.0))
                continue
            bucket = per_comp.setdefault(name, {"cpu": [], "mem": [], "anon": [], "io": [], "peak": []})
            if "cpu" in c:
                bucket["cpu"].append(float(c["cpu"]))
                cpu_sum += float(c["cpu"])
            bucket["mem"].append(float(c.get("mem") or 0))
            bucket["anon"].append(float(c.get("anon") or 0))
            bucket["peak"].append(float(c.get("peak") or 0))
            bucket["io"].append(float(c.get("io_r") or 0) + float(c.get("io_w") or 0))
            anon_sum += float(c.get("anon") or 0)
            mem_sum += float(c.get("mem") or 0)
        # 線上與批次分開：雲端架構本來就會把它們放在不同機器上（既有評估簡報的
        # 雙路徑正是這個切法），混在一起算出來的 vCPU 是兩者尖峰疊加的產物，
        # 對任何一邊都不是正確答案。
        online = sum(
            float(c.get("cpu") or 0.0)
            for n, c in comp.items()
            if not is_aggregate(n) and n not in BATCH_COMPONENTS
        )
        batch = sum(
            float(c.get("cpu") or 0.0)
            for n, c in comp.items()
            if not is_aggregate(n) and n in BATCH_COMPONENTS
        )
        online_cpu.append(online)
        batch_cpu.append(batch)
        online_anon.append(
            sum(
                float(c.get("anon") or 0.0)
                for n, c in comp.items()
                if not is_aggregate(n) and n not in BATCH_COMPONENTS
            )
        )
        if batch > 0.1:
            batch_active += 1
        svc_cpu.append(cpu_sum)
        svc_anon.append(anon_sum)
        svc_mem.append(mem_sum)
        if "cpu_cores" in h:
            nonsvc_cpu.append(max(0.0, float(h["cpu_cores"]) - cpu_sum))

    comp_stats = {}
    for name, b in per_comp.items():
        # 活躍樣本＝該元件本輪有耗用 CPU 或有常駐記憶體。批次 unit 的 cgroup
        # 只在執行期間存在，所以樣本數本身就是「活了多久」的證據。
        active_cpu = [v for v in b["cpu"] if v > 0.001]
        comp_stats[name] = {
            "samples": len(b["cpu"]),
            "active_samples": len(active_cpu),
            "presence": len(b["cpu"]) / len(samples) if samples else 0.0,
            "cpu": quantiles(b["cpu"]),
            "cpu_active": quantiles(active_cpu),
            "mem": quantiles(b["mem"]),
            "anon": quantiles(b["anon"]),
            "io": quantiles(b["io"]),
            "peak_max": max(b["peak"]) if b["peak"] else 0.0,
        }

    growth = None
    if len(caps) >= 2:
        a, b2 = caps[0], caps[-1]
        hours = max(1e-6, (b2["_ts"] - a["_ts"]).total_seconds() / 3600.0)
        da, db = a.get("db") or {}, b2.get("db") or {}
        if da and db:
            d_bytes = float(db.get("database_bytes", 0)) - float(da.get("database_bytes", 0))
            rows_a, rows_b = da.get("rows") or {}, db.get("rows") or {}
            growth = {
                "hours": hours,
                "db_bytes_start": da.get("database_bytes", 0),
                "db_bytes_end": db.get("database_bytes", 0),
                "db_bytes_per_day": d_bytes / hours * 24,
                "tables_end": db.get("tables") or {},
                "rows_delta": {k: rows_b.get(k, 0) - rows_a.get(k, 0) for k in rows_b},
                "rows_end": rows_b,
                # 窗期太短時外推是雜訊放大器：一次 sync 的 28 篇會被乘成
                # 「每天 672 篇」。門檻標在這裡，由呈現層決定要不要印。
                "reliable": hours >= 6.0,
            }
    fs_end = (caps[-1].get("fs") if caps else None) or []

    svc_cpu_q = quantiles(svc_cpu)
    svc_anon_q = quantiles(svc_anon)
    svc_mem_q = quantiles(svc_mem)
    host_iops_q = quantiles(host_iops)

    # ── 選型（規則寫在常數與這段，報表會把算式一起印出來）────────────
    vcpu_need = svc_cpu_q["p99"] * CPU_HEADROOM
    ram_need = (svc_anon_q["max"] * RAM_HEADROOM + 1 * GIB) / GIB
    db_bytes_end = (growth or {}).get("db_bytes_end") or 0
    disk_12m = (db_bytes_end + (growth or {}).get("db_bytes_per_day", 0) * 365) / GIB * DISK_HEADROOM

    online_cpu_q = quantiles(online_cpu)
    batch_cpu_q = quantiles(batch_cpu)
    online_anon_q = quantiles(online_anon)
    sizing = {
        "online_cpu": online_cpu_q,
        "batch_cpu": batch_cpu_q,
        "online_vcpu_pick": round_up_to(max(online_cpu_q["p99"] * CPU_HEADROOM, 2), VCPU_STEPS),
        "batch_vcpu_pick": round_up_to(max(batch_cpu_q["p99"] * CPU_HEADROOM, 2), VCPU_STEPS),
        "online_ram_pick_gib": round_up_to(
            max(online_anon_q["max"] * RAM_HEADROOM / GIB + 1, 2), RAM_STEPS_GIB
        ),
        "batch_active_share": batch_active / len(samples) if samples else 0.0,
        "vcpu_measured_p99": svc_cpu_q["p99"],
        "vcpu_measured_max": svc_cpu_q["max"],
        "vcpu_need": vcpu_need,
        "vcpu_pick": round_up_to(max(vcpu_need, 2), VCPU_STEPS),
        "ram_anon_max_gib": svc_anon_q["max"] / GIB,
        "ram_with_cache_max_gib": svc_mem_q["max"] / GIB,
        "ram_need_gib": ram_need,
        "ram_pick_gib": round_up_to(max(ram_need, 2), RAM_STEPS_GIB),
        "disk_now_gib": db_bytes_end / GIB,
        "disk_12m_gib": disk_12m,
        "disk_pick_gb": round_up_to(max(disk_12m, 20), DISK_STEPS_GB),
        "iops_p99": host_iops_q["p99"],
        "iops_pick": max(3000, int(host_iops_q["p99"] * 2)),
        "egress_gib_per_month": quantiles(host_net_tx)["mean"] * 86400 * 30 / GIB,
        "growth_reliable": bool(growth and growth.get("reliable")),
    }

    return {
        "window": {
            "start": first.isoformat(timespec="seconds"),
            "end": last.isoformat(timespec="seconds"),
            "hours": span_s / 3600.0,
            "samples": len(samples),
            "coverage": coverage,
            "interval": interval,
            "bad_lines": bad,
            "capacity_samples": len(caps),
        },
        "machine": {
            "host": (meta or {}).get("host"),
            "kernel": (meta or {}).get("kernel"),
            "ncpu": ncpu,
            "mem_total": mem_total,
        },
        "host": {
            "cpu": quantiles(host_cpu),
            "iowait": quantiles(host_iowait),
            "mem_used": quantiles(host_mem_used),
            "swap_used": quantiles(host_swap),
            "io_bps": quantiles(host_io_bps),
            "iops": host_iops_q,
            "net_tx": quantiles(host_net_tx),
            "psi_io_some": quantiles(psi_io),
            "psi_cpu_some": quantiles(psi_cpu),
        },
        "service": {"cpu": svc_cpu_q, "anon": svc_anon_q, "mem": svc_mem_q},
        "nonservice": {"cpu": quantiles(nonsvc_cpu)},
        "aggregates": {k: quantiles(v) for k, v in agg_cpu.items()},
        "components": comp_stats,
        "growth": growth,
        "filesystems": fs_end,
        "sizing": sizing,
    }


def build_bench_section(bench: dict, samples: list[dict], baseline_seconds: float = 600.0) -> dict:
    """把一次壓測換算成「每條請求的邊際硬體成本」。

    **邊際而不是總量**：窗期內量到的 CPU 包含服務閒置時本來就有的那一份（心跳、
    連線池、cloudflared），不扣掉的話請求數越少、單條成本被高估得越誇張。
    基線取壓測開始前 10 分鐘的中位數——用中位數不用平均，是因為那段可能剛好卡到
    一次 sync 批次，平均會被單一尖峰整個帶走。

    核心秒（core-seconds）是這裡的主單位：`Σ(cpu_i × dt_i)`。它可加、可除以請求數，
    而「峰值核心數」不行——兩條請求各自 2 核的尖峰若不重疊，加總不是 4 核。
    決定 vCPU 要看峰值，決定「一台機器一小時能服務幾條」要看核心秒，兩個問題不同。
    """
    start = datetime.fromisoformat(bench["started"])
    end = datetime.fromisoformat(bench["ended"])
    window = [s for s in samples if start <= s["_ts"] <= end]
    base = [s for s in samples if start - timedelta(seconds=baseline_seconds) <= s["_ts"] < start]

    def svc_cpu(sample: dict) -> float:
        return sum(
            float(c.get("cpu") or 0.0)
            for n, c in sample.get("comp", {}).items()
            if not is_aggregate(n)
        )

    def svc_anon(sample: dict) -> float:
        return sum(
            float(c.get("anon") or 0.0)
            for n, c in sample.get("comp", {}).items()
            if not is_aggregate(n)
        )

    def comp_cpu_seconds(rows: list[dict]) -> dict[str, float]:
        out: dict[str, float] = {}
        for sample in rows:
            dt = float(sample.get("host", {}).get("dt") or 0.0)
            for n, c in sample.get("comp", {}).items():
                if is_aggregate(n) or "cpu" not in c:
                    continue
                out[n] = out.get(n, 0.0) + float(c["cpu"]) * dt
        return out

    window_seconds = sum(float(s.get("host", {}).get("dt") or 0.0) for s in window)
    total_core_s = sum(svc_cpu(s) * float(s.get("host", {}).get("dt") or 0.0) for s in window)
    base_cores = pct([svc_cpu(s) for s in base], 0.5) if base else 0.0
    base_anon = pct([svc_anon(s) for s in base], 0.5) if base else 0.0
    marginal_core_s = max(0.0, total_core_s - base_cores * window_seconds)
    n_ok = int((bench.get("summary") or {}).get("ok") or 0)

    per_comp = comp_cpu_seconds(window)
    base_per_comp = comp_cpu_seconds(base)
    base_span = sum(float(s.get("host", {}).get("dt") or 0.0) for s in base) or 1.0
    marginal_per_comp = {
        n: max(0.0, v - base_per_comp.get(n, 0.0) / base_span * window_seconds)
        for n, v in per_comp.items()
    }

    contaminated_by = {}
    for name in BATCH_COMPONENTS:
        cores = per_comp.get(name, 0.0) / window_seconds if window_seconds else 0.0
        if cores > 0.1:
            contaminated_by[name] = cores

    per_req_by_comp = (
        {n: v / n_ok for n, v in marginal_per_comp.items()} if n_ok else {}
    )

    return {
        "endpoint": bench.get("endpoint"),
        "concurrency": bench.get("concurrency"),
        "summary": bench.get("summary") or {},
        "window_seconds": window_seconds,
        "window_samples": len(window),
        "baseline_samples": len(base),
        "baseline_cores": base_cores,
        "total_core_seconds": total_core_s,
        "marginal_core_seconds": marginal_core_s,
        "core_seconds_per_request": marginal_core_s / n_ok if n_ok else 0.0,
        "marginal_core_seconds_by_component": dict(
            sorted(marginal_per_comp.items(), key=lambda kv: -kv[1])
        ),
        "core_seconds_per_request_by_component": dict(
            sorted(per_req_by_comp.items(), key=lambda kv: -kv[1])
        ),
        "contaminated_by": contaminated_by,
        "peak_service_cores": max((svc_cpu(s) for s in window), default=0.0),
        "peak_service_anon_gib": max((svc_anon(s) for s in window), default=0.0) / GIB,
        "baseline_anon_gib": base_anon / GIB,
        # 樣本太少時每個數字都是單點觀測，呈現層據此加警語（20 秒一筆，一條
        # 60 秒的問答只會落在 3 筆樣本上）。
        "reliable": len(window) >= 5 and len(base) >= 3 and not contaminated_by,
    }


# ── 呈現 ────────────────────────────────────────────────────────────────


WIDTH = 78
_QS = ("p50", "p90", "p95", "p99", "max")


def _head(title: str) -> str:
    """章節標題補到固定寬度。CJK 佔兩格，用 _w 而不是 len——用 len 的話
    每個中文標題都會多畫一半的線，表格看起來像沒對齊。"""
    prefix = f"── {title} "
    return prefix + "─" * max(4, WIDTH - _w(prefix))


def _table(rows: list[tuple[str, list[str], str]]) -> list[str]:
    """rows = [(標籤, [五個分位數字串], 行尾附註)]；欄寬依實際內容決定。"""
    label_w = max([_w(r[0]) for r in rows] + [_w("")])
    num_w = max([max((len(c) for c in r[1]), default=0) for r in rows] + [len(q) for q in _QS]) + 2
    out = [_pad("", label_w) + "".join(q.rjust(num_w) for q in _QS)]
    for label, cells, note in rows:
        line = _pad(label, label_w) + "".join(c.rjust(num_w) for c in cells)
        if note:
            line += "   " + note
        out.append(line.rstrip())
    return out


def _cells(q: dict, scale: float = 1.0, fmt: str = "{:.2f}") -> list[str]:
    return [fmt.format(q[k] / scale) for k in _QS]


def render_text(r: dict) -> str:
    out: list[str] = []
    w = r["window"]
    m = r["machine"]
    hours = w["hours"]
    span = f"{hours:.1f} 小時" if hours >= 1 else f"{hours * 60:.0f} 分鐘"
    out.append("═" * WIDTH)
    out.append("廷豐智能研報 — 硬體用量實測報告")
    out.append("═" * WIDTH)
    out.append(
        f"觀測窗期  {w['start'][:19].replace('T', ' ')} → {w['end'][:19].replace('T', ' ')}（{span}）"
    )
    out.append(
        f"樣本      {w['samples']} 筆／間隔 {w['interval']:.0f}s／涵蓋率 {w['coverage'] * 100:.1f}%"
        f"｜容量樣本 {w['capacity_samples']} 筆"
        + (f"｜壞行 {w['bad_lines']}" if w["bad_lines"] else "")
    )
    out.append(
        f"機器      {m['host']}｜{m['ncpu']} vCPU｜{m['mem_total'] / GIB:.1f} GiB RAM｜{m['kernel']}"
    )

    # ── CPU ──
    rows: list[tuple[str, list[str], str]] = [
        ("全機", _cells(r["host"]["cpu"]), ""),
        ("　服務合計", _cells(r["service"]["cpu"]), ""),
    ]
    for name, c in sorted(r["components"].items(), key=lambda kv: -kv[1]["cpu"]["p99"]):
        presence = c["presence"]
        # 在場率低＝批次型 unit（cgroup 只在執行期間存在）。它的分位數是
        # **該 unit 活著的那段時間**的分位數，不是全窗期——不標出來會被誤讀成
        # 「這東西整天都吃這麼多」。
        note = "" if presence > 0.95 else f"← 批次；分位數僅涵蓋其執行中的 {presence * 100:.1f}% 樣本"
        rows.append((f"　　{name}", _cells(c["cpu"]), note))
    rows.append(("　線上路徑合計（web＋DB＋邊緣）", _cells(r["sizing"]["online_cpu"]), ""))
    rows.append(("　批次路徑合計（sync 等）", _cells(r["sizing"]["batch_cpu"]), ""))
    rows.append(("　非服務（開發／系統）", _cells(r["nonservice"]["cpu"]), ""))
    rows.append(("　　其中 iowait", _cells(r["host"]["iowait"]), ""))
    out.append("")
    out.append(_head("CPU（單位：核心數，非百分比）"))
    out.extend(_table(rows))

    # ── 記憶體 ──
    rows = [
        ("全機已用（不含快取）", _cells(r["host"]["mem_used"], GIB), ""),
        ("　服務合計 含頁快取", _cells(r["service"]["mem"], GIB), ""),
        ("　服務合計 常駐 anon", _cells(r["service"]["anon"], GIB), ""),
    ]
    for name, c in sorted(r["components"].items(), key=lambda kv: -kv[1]["anon"]["max"]):
        rows.append(
            (f"　　{name} anon", _cells(c["anon"], GIB), f"cgroup 歷史峰值 {c['peak_max'] / GIB:.2f}")
        )
    rows.append(("swap 已用", _cells(r["host"]["swap_used"], GIB), ""))
    out.append("")
    out.append(_head("記憶體（GiB）"))
    out.extend(_table(rows))
    out.append(
        "註：anon＝真正的常駐記憶體；Postgres 的 shared_buffers 記在頁快取那一欄，"
        "所以它的 anon 看起來極小是正常的。"
    )

    # ── I/O ──
    out.append("")
    out.append(_head("磁碟 I/O 與壓力（全機口徑，含開發負載）"))
    out.extend(
        _table(
            [
                ("讀寫 MB/s", _cells(r["host"]["io_bps"], MIB), ""),
                ("IOPS", _cells(r["host"]["iops"], 1.0, "{:.0f}"), ""),
                ("PSI io.some %（等 I/O 佔比）", _cells(r["host"]["psi_io_some"]), ""),
                ("PSI cpu.some %", _cells(r["host"]["psi_cpu_some"]), ""),
                ("對外送出 net_tx MB/s", _cells(r["host"]["net_tx"], MIB), ""),
            ]
        )
    )

    # ── 容量 ──
    g = r.get("growth")
    out.append("")
    out.append(_head("容量與成長"))
    if g:
        out.append(
            f"DB 大小    {g['db_bytes_start'] / GIB:.2f} GiB → {g['db_bytes_end'] / GIB:.2f} GiB"
            f"（窗期 {g['hours']:.1f}h）"
        )
        if g["reliable"]:
            per_day = g["db_bytes_per_day"]
            out.append(
                f"DB 成長    {per_day / MIB:.0f} MiB／日 ⇒ {per_day * 30 / GIB:.2f} GiB／月"
                f" ⇒ {per_day * 365 / GIB:.1f} GiB／年"
            )
        else:
            out.append("DB 成長    窗期不足 6 小時，刻意不外推（一次 sync 的增量會被乘成假的日增量）")
        rows_end = g["rows_end"]
        delta = g["rows_delta"]
        out.append(
            "列數       "
            + "｜".join(f"{k} {rows_end.get(k, 0):,}（{delta.get(k, 0):+,}）" for k in sorted(rows_end))
        )
        top = sorted((g["tables_end"] or {}).items(), key=lambda kv: -kv[1])[:3]
        out.append("最大表     " + "｜".join(f"{k} {v / GIB:.2f} GiB" for k, v in top))
    else:
        out.append("（容量樣本不足兩筆，無法算成長率）")
    for fs in r["filesystems"]:
        out.append(
            f"檔案系統   {fs['path']}：已用 {fs['used'] / GIB:.0f} GiB／可用 {fs['avail'] / GIB:.0f} GiB"
        )

    # ── 選型 ──
    s_ = r["sizing"]
    out.append("")
    out.append(_head("上雲選型（由上面的實測值推導，算式一併列出）"))
    if s_["batch_active_share"] > 0.2:
        out.append(
            f"⚠ 本窗期有 {s_['batch_active_share'] * 100:.0f}% 的樣本正在跑批次，"
            "**不是典型的一天**。下面「服務合計」那條的分位數是線上與批次尖峰疊加的產物，"
            "對任何一邊都不是正確答案——請看分路徑那兩條。"
        )
    out.append(
        f"線上   p99 {s_['online_cpu']['p99']:.2f} 核（max {s_['online_cpu']['max']:.2f}）"
        f" × {CPU_HEADROOM} ⇒ 取 {s_['online_vcpu_pick']} vCPU"
        f"｜RAM ⇒ {s_['online_ram_pick_gib']} GiB"
    )
    out.append(
        f"批次   p99 {s_['batch_cpu']['p99']:.2f} 核（max {s_['batch_cpu']['max']:.2f}）"
        f" × {CPU_HEADROOM} ⇒ 取 {s_['batch_vcpu_pick']} vCPU"
        "（獨立 worker；與線上同機時兩者互相拖慢，實測問答延遲 70–108s → 184s）"
    )
    out.append(
        f"合計   服務合計 p99 {s_['vcpu_measured_p99']:.2f} 核 × {CPU_HEADROOM}"
        f" = {s_['vcpu_need']:.2f} ⇒ {s_['vcpu_pick']} vCPU（**只有單機不分離時才看這條**）"
    )
    out.append(
        f"RAM    服務常駐 anon 峰值 {s_['ram_anon_max_gib']:.2f} GiB × {RAM_HEADROOM}"
        f" ＋ 1 GiB 系統 = {s_['ram_need_gib']:.2f} ⇒ 取 {s_['ram_pick_gib']} GiB"
    )
    out.append(
        f"       （含頁快取的實際佔用峰值 {s_['ram_with_cache_max_gib']:.2f} GiB；"
        "DB 若上 RDS，記憶體要另外照 working set 給，不能只看 anon）"
    )
    if s_["growth_reliable"]:
        out.append(
            f"磁碟   DB 現況 {s_['disk_now_gib']:.1f} GiB ＋ 12 個月成長，× {DISK_HEADROOM}"
            f" = {s_['disk_12m_gib']:.0f} GiB ⇒ 取 {s_['disk_pick_gb']} GB"
        )
    else:
        out.append(
            f"磁碟   DB 現況 {s_['disk_now_gib']:.1f} GiB；**成長率窗期不足，未外推**"
            "——磁碟結論要等跨過至少一輪 sync（3 小時）再看"
        )
    out.append(f"IOPS   全機 p99 {s_['iops_p99']:.0f} × 2，下限 3000 ⇒ 取 {s_['iops_pick']}")
    out.append(
        "⚠ vCPU 這一條要小心：`EMBED_TORCH_THREADS=0`（預設）代表 torch 不綁執行緒、"
        "會吃滿可用核心，所以**量到的峰值核心數是「供給」不是「需求」**——同一份工作在 "
        "4 vCPU 的機器上會用 4 核跑久一點，不會失敗。真正可移植的量是下面的核心秒。"
    )
    out.append(
        f"對外流量  全機平均 net_tx 外推 ≈ {s_['egress_gib_per_month']:.1f} GiB／月"
        "（含開發流量，是上限不是服務用量）"
    )

    b = r.get("bench")
    if b:
        out.append("")
        out.append(_head(f"受控負載：/api/{b['endpoint']}（併發 {b['concurrency']}）"))
        bs = b["summary"]
        out.append(
            f"請求      {bs.get('ok', 0)}/{bs.get('n', 0)} 成功｜排隊 {bs.get('queued', 0)}｜"
            f"延遲 p50 {bs.get('total_p50_s', 0)}s／p95 {bs.get('total_p95_s', 0)}s"
        )
        out.append(
            f"窗期      {b['window_seconds']:.0f} 秒／{b['window_samples']} 筆樣本"
            f"（基線取前 10 分鐘 {b['baseline_samples']} 筆，服務閒置 {b['baseline_cores']:.3f} 核）"
        )
        out.append(
            f"核心秒    窗期總計 {b['total_core_seconds']:.1f}，扣掉閒置後的邊際 "
            f"{b['marginal_core_seconds']:.1f}"
        )
        out.append(
            f"**單條成本 {b['core_seconds_per_request']:.1f} 核心秒／請求**；"
            f"窗期內服務尖峰 {b['peak_service_cores']:.2f} 核"
        )
        out.append(
            f"記憶體    窗期服務 anon 峰值 {b['peak_service_anon_gib']:.2f} GiB"
            f"（基線 {b['baseline_anon_gib']:.2f} GiB，增量 "
            f"{b['peak_service_anon_gib'] - b['baseline_anon_gib']:.2f} GiB）"
        )
        by = b.get("core_seconds_per_request_by_component") or {}
        if by:
            out.append(
                "分元件    "
                + "｜".join(f"{n} {v:.1f}" for n, v in list(by.items())[:5])
                + "（核心秒／請求；`web` 這一份＝BGE-M3 嵌入 ＋ cross-encoder rerank ＋ "
                "LLM 串流（PR-M 前是 claude CLI 父程序），是問答真正的本機成本）"
            )
        # 吞吐量換算：核心秒是可加的，所以「一台機器一小時能服務幾條」可以直接除。
        # 利用率取 0.7——排隊理論上把 CPU 推到 100% 會讓 p95 延遲爆炸，這個折扣
        # 是容量規劃的常規做法，不是保守的裝飾。
        clean_cs = (
            (b.get("core_seconds_per_request_by_component") or {}).get("web")
            if b.get("contaminated_by")
            else b["core_seconds_per_request"]
        )
        if clean_cs:
            picks = [2, 4, 8, 16]
            per_hour = "｜".join(
                f"{v} vCPU {v * 3600 * 0.7 / clean_cs:.0f} 條" for v in picks
            )
            out.append(
                f"吞吐量    以 {clean_cs:.1f} 核心秒／條、利用率 0.7 換算每小時可服務：{per_hour}"
            )
        if b.get("contaminated_by"):
            detail = "｜".join(f"{n} 平均 {v:.1f} 核" for n, v in b["contaminated_by"].items())
            out.append(
                f"⚠ **窗期被批次污染**（{detail}）：合計的「單條成本」把批次的 CPU 算進去了，"
                "不可採信。請改看下面分元件那一行的 `web`——那一份才是問答自己的成本；"
                "要乾淨的合計值請在批次不跑的時段重跑一次。"
            )
        if b["window_samples"] < 5 or b["baseline_samples"] < 3:
            out.append(
                "⚠ 樣本不足（窗期 <5 筆或基線 <3 筆）：以上是單點觀測，不要當成分位數用。"
                "拉長 --repeat 或把取樣間隔調小再跑一次。"
            )
    out.append("")
    out.append(_head("讀這份報告的三個前提"))
    out.append("1. 「服務合計」是每筆樣本先加總再取分位數，不是各元件 p95 相加（後者系統性高估）。")
    out.append("2. 非服務負載已扣除；這台機器同時是開發機，閒置時全機 CPU 常態就有 1 核以上。")
    out.append("3. 這是**現況負載**下的用量。窗期內若沒有真實問答，尖峰就沒有被觀測到——")
    out.append("   對照下面實際發生的請求數判斷樣本代表性，必要時先跑受控負載再重新分析。")
    if g and g.get("rows_delta"):
        d = g["rows_delta"]
        out.append(
            f"   窗期內實際發生：問答 {d.get('qa_log', 0)} 次｜"
            f"新入庫研報 {d.get('research_report', 0)} 篇"
        )
    return "\n".join(out)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="硬體用量分析（唯讀）")
    p.add_argument("--dir", default=str(DEFAULT_DIR), help="JSONL 目錄（預設 data/metrics）")
    p.add_argument("--file", action="append", default=[], help="指定檔案（可重複；給了就不掃目錄）")
    p.add_argument("--since", default=None, help="只看這之後：24h／7d／ISO 日期")
    p.add_argument("--until", default=None, help="只看這之前：ISO 日期時間")
    p.add_argument("--bench", default=None, help="壓測結果檔；自動框出那段窗期並算單條成本")
    p.add_argument("--json", action="store_true", help="輸出 JSON（供其他工具消費）")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    since = parse_since(args.since)
    paths = [Path(f) for f in args.file] or sorted(Path(args.dir).glob("resource-*.jsonl"))
    if not paths:
        print(f"找不到取樣檔（{args.dir}/resource-*.jsonl）。先跑 collect_resource_usage.py。", file=sys.stderr)
        return 2
    bench = None
    if args.bench:
        try:
            bench = json.loads(Path(args.bench).read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"讀不到壓測結果檔：{exc}", file=sys.stderr)
            return 2
        # 壓測模式一律先載入全部樣本：基線取自壓測**開始之前**那段，
        # 用 --since 濾掉的話基線會是空的，邊際成本就退化成總量。
        since = None

    meta, samples, caps, bad = load_records(paths, since)
    until = parse_since(args.until) if args.until else None
    if bench:
        window_start = datetime.fromisoformat(bench["started"])
        window_end = datetime.fromisoformat(bench["ended"])
        bench_section = build_bench_section(bench, samples)
        focus = [s for s in samples if window_start <= s["_ts"] <= window_end]
        caps = [c for c in caps if window_start <= c["_ts"] <= window_end]
    else:
        bench_section = None
        focus = [s for s in samples if until is None or s["_ts"] <= until]
        caps = [c for c in caps if until is None or c["_ts"] <= until]

    if len(focus) < 2:
        print(f"樣本不足（{len(focus)} 筆）：至少要兩筆才有差分可算。", file=sys.stderr)
        return 2
    report = build_report(meta, focus, caps, bad)
    if bench_section:
        report["bench"] = bench_section
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
