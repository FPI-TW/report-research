"""DeepSeek 切換後的批次產出觀測（遷移 PR-16，第四版「緊急直接切換」）：零 LLM、唯讀、一次性。

claude CLI 已於 2026-09-23 失效，批次被迫直接切到 DeepSeek，**切換前閘門取消、改成切換後觀測**。
這支把「切換前 N 天的 Claude 產出」與「切換後的 DeepSeek 產出」擺在一起，依計畫第四版 §判準的
**方向**取 CI 端點、標出是否落在 D-N／D-A 容差內。**只輸出判讀，不做任何切換、不改任何設定**：
發現劣化時能做的只有修 prompt 或換 `deepseek-v4-pro`（沒有 Claude 可以退回），那是人的決定。

建議在切換後第 7 天、第 14 天各跑一次（用法見 `docs/WORKFLOW.md`「DeepSeek 切換後觀測」）。

## 零 LLM、唯讀

- 不 import 任何 LLM 呼叫層（`llm`／`llm_http`／`_claude_cli`／`_claude_lock`／`eval.judge`／問答服務層），
  只取 `llm_models` 的常數與白名單判斷。`tests/test_observe_switch.py` 以 AST 釘住，並確認
  `tests/test_llm_env_loading.py` 的入口掃描不會把它當成 LLM 入口（所以不需要列進 `NON_LLM_ENTRIES`）。
- DB 一律經 `SessionFactory`，單一交易、第一句 `SET TRANSACTION READ ONLY`，接著
  `relax_statement_timeout`（`SET LOCAL`），最後 rollback。不寫任何表。
- 檔案只讀：`data/llm_usage.jsonl`（`--usage-log`，不存在就略過）與 `data/tags/<hash>.json`
  （`--tags-dir`）。輸出只到 stdout 或 `--out`，`--out` 不接受 repo 根 `data/` 底下的路徑。
- 不取 `scripts/_claude_lock.py` 的 flock（不呼叫 LLM、不寫表），也不進 LOCKED_SCRIPTS。

## 怎麼分群（產出模型的判斷依據）

窗期：Claude 群＝`[switch_at − before_days, switch_at)`；DeepSeek 群＝`[switch_at, until)`。
依據 D-C（CLI 永久放棄）：**切換後寫入的東西只可能來自 DeepSeek**，所以時間就是主要依據，
能拿到明確的模型紀錄時以紀錄為準：

- 摘錄：`raw_payload.model`（PR-15 起才有；沒有這個鍵依 `created_at` 判）。摘錄是 DELETE＋INSERT，
  `created_at` 就是寫入時間，所以不看用量紀錄（一次切換後的 rejected 不會重寫舊列）。
- 訊號：`raw_payload.model`；沒有鍵時看用量紀錄（切換後有成功呼叫＝DeepSeek 重寫過），再退回 `created_at`。
  限制：upsert 不更新 `created_at`，PR-15 之前、又沒有用量紀錄時，切換後重擷取的舊列會被算成 Claude。
- 標題、摘要：用量紀錄（`task`＋`file_hash` 切換後的最後一次成功呼叫），沒有就依研報 `created_at`。
  限制：`research_report` 沒有標題／摘要的更新時間，切換前入庫、切換後才補的列，沒有用量紀錄時會被算成 Claude。
- 標註：用量紀錄，沒有就依入庫時間。入庫的取 `research_report.created_at`（回填
  `backfill_extraction.py` 會刷新 `extraction_log.updated_at`）；非研報沒有報告列，取 `extraction_log.updated_at`。

用量紀錄 `data/llm_usage.jsonl` 由 PR-12 起的批次寫入（`scripts/_claude_cli.py`），只取 `kind` 為 null
（傳輸成功）且帶 `file_hash` 的列，模型取 `model_resp`，沒有就取 `model_req`。檔案不存在時只用時間判，
報告會註明。缺值率（應補未補、摘錄未產出）不看模型，看**入庫批次**：Claude 批次＝窗期內入庫的研報，
DeepSeek 批次＝切換後入庫、且早於 `until − grace_hours` 的研報（太新的還沒輪到下游批次）。

## 指標與判讀（計畫第四版 §判準、D-N、D-A）

| 指標 | 判準 | 取哪一端 |
|---|---|---|
| 摘錄：任一方式錨定成功率（**主**，D-A） | 差值（DeepSeek − Claude）≥ −5pp | 差值的 CI 下界 |
| 摘錄產出率、訊號非 rejected 率、標題／摘要填補率（「解析成功率」，D-N） | 差值 ≥ −2pp | 差值的 CI 下界 |
| 標註：`skip_non_research` 比例、market=None 比例（批次 B：≤ 過去 30 天） | 差值 ≤ 0 | 差值的 CI 上界 |
| 摘錄 exact 錨定率、每篇條數、殘留簡體率、長度、stance／market／is_research 分布、跳過名單 | 觀測值，不判 | — |

判讀三態：CI 那一端落在容差內＝「通過」；整條 CI 都在容差外＝「劣化」；CI 跨過容差＝「未定」（樣本不足
以下結論，另列點估計是否在容差內）。任一群沒有樣本＝「樣本不足」。

- 摘錄錨定**重算**：對 `clean_extracted(full_text)` 跑 `app/services/reading/anchor.locate_quote`（與
  `scripts/extract_takeaways.build_rows` 同一條路徑，也是 9/24 探測的算法），不讀存的 `anchor_method`、
  不寫回。分母是有 quote 的條目；存的 `text_sha256` 跟現在的正典文字對不上（全文在擷取後被回填改過）
  的研報不計入錨定率，另列篇數。CI 是以研報為單位的 bootstrap（同一篇的條目彼此相關，逐條算會低估變異）。
- 其他比例用 Wilson 區間，差值用 Newcombe（Wilson 混合）區間，都是 95%。
- 摘錄 rejected **不寫列**（`extract_takeaways.py`），所以「rejected 比例」以入庫批次為母體算「未產出率」
  （＝rejected＋失敗＋還沒跑）；DeepSeek 側另列跳過名單裡的 takeaway 筆數。
- 殘留簡體：`zh_hant.count_simplified(text) > 0` 的比例（與探測報告相同）。
- 跳過名單：`research.llm_task_failure` 裡 `first_at ≥ switch_at` 的列（換 model 時 `first_at` 會重設），
  依 task／reason 計數，並以 `llm_failures.should_skip` 算已經被跳過的篇數。

## 用法

    uv run python eval/observe_switch.py --switch-at 2026-09-25T10:00 --dry-run   # 只印查詢
    uv run python eval/observe_switch.py --switch-at 2026-09-25T10:00             # markdown 到 stdout
    uv run python eval/observe_switch.py --switch-at 2026-09-25T10:00 --json --out /tmp/observe-d7.json

`--switch-at` 沒帶時區時視為台北時間。退出碼：0＝報告已產出（不論判讀結果）；2＝參數錯誤。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import random
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sqlalchemy import text  # noqa: E402

from app.services.db import SessionFactory, relax_statement_timeout  # noqa: E402
from app.services.llm_failures import FailureRecord, should_skip  # noqa: E402
from app.services.llm_models import (  # noqa: E402
    TASK_SIGNAL,
    TASK_SUMMARY,
    TASK_TAG,
    TASK_TAKEAWAY,
    TASK_TITLE,
    is_http_model,
)
from app.services.reading.anchor import locate_quote  # noqa: E402
from app.services.signal_extract import STANCE_CONSTRUCTIVENESS, THESIS_DIMENSIONS  # noqa: E402
from app.services.tagging import load_tag  # noqa: E402
from app.services.textnorm import clean_extracted  # noqa: E402
from app.services.zh_hant import count_simplified  # noqa: E402

TAIPEI = ZoneInfo("Asia/Taipei")
CLAUDE = "claude"
DEEPSEEK = "deepseek"
GROUPS = (CLAUDE, DEEPSEEK)
Z95 = 1.959963984540054

# 計畫第四版 D-N／D-A 的容差（比例，不是百分點）
TOL_PARSE = 0.02   # 解析成功率（摘錄產出、訊號非 rejected、標題／摘要填補）
TOL_ANCHOR = 0.05  # 摘錄任一方式錨定成功率
HIGHER = "higher"  # 越高越好：差值 ≥ −容差，看 CI 下界
LOWER = "lower"    # 越低越好：差值 ≤ 容差，看 CI 上界

VERDICT_PASS = "通過"
VERDICT_FAIL = "劣化"
VERDICT_UNSURE = "未定（CI 跨過容差）"
VERDICT_NO_DATA = "樣本不足"

RC_OK = 0
RC_CONFIG = 2

DEFAULT_USAGE_LOG = ROOT / "data" / "llm_usage.jsonl"
DEFAULT_TAGS_DIR = ROOT / "data" / "tags"
FORBIDDEN_OUT_DIR = ROOT / "data"

# ── 查詢（--dry-run 原樣印出；測試以這些常數比對假 session 收到的 SQL）──────────────────

READ_ONLY_SQL = "SET TRANSACTION READ ONLY"

FAILURE_TABLE_READY_SQL = "SELECT to_regclass('research.llm_task_failure') IS NOT NULL"

# 摘錄是 DELETE＋INSERT（scripts/extract_takeaways.py），created_at 就是寫入時間，所以窗期直接套在列上。
TAKEAWAY_SQL = """
SELECT t.report_id::text AS report_id, r.file_hash, t.quote, t.extraction_status, t.text_sha256,
       t.created_at, t.raw_payload->>'model' AS model
FROM research.report_takeaway t
JOIN research.research_report r ON r.id = t.report_id
WHERE t.created_at >= :before_start AND t.created_at < :until
""".strip()

FULL_TEXT_SQL = """
SELECT id::text AS report_id, full_text
FROM research.research_report
WHERE id = ANY(CAST(:ids AS uuid[]))
""".strip()

# 訊號是 upsert、不更新 created_at：切換後重擷取的舊列靠 raw_payload.model 或用量紀錄撈進來。
SIGNAL_SQL = """
SELECT s.report_id::text AS report_id, r.file_hash, s.extraction_status, s.thesis_dimensions,
       s.created_at, s.raw_payload->>'model' AS model
FROM research.report_signal s
JOIN research.research_report r ON r.id = s.report_id
WHERE s.extraction_status <> 'pending'
  AND (s.created_at >= :before_start
       OR (s.raw_payload->>'model') IS NOT NULL
       OR r.file_hash = ANY(CAST(:usage_hashes AS text[])))
""".strip()

# 母體與 generate_titles／generate_summaries／extract_takeaways 的工作集一致：有全文、非「明確非研報」。
REPORT_SQL = """
SELECT r.id::text AS report_id, r.file_hash, r.created_at, r.title, r.summary,
       EXISTS (SELECT 1 FROM research.report_takeaway t WHERE t.report_id = r.id) AS has_takeaway
FROM research.research_report r
WHERE r.full_text IS NOT NULL AND r.is_research IS NOT FALSE
  AND ((r.created_at >= :before_start AND r.created_at < :until)
       OR r.file_hash = ANY(CAST(:usage_hashes AS text[])))
""".strip()

# 入庫的取報告 created_at（回填會刷新 extraction_log.updated_at）；非研報沒有報告列，取 updated_at。
TAG_SQL = """
SELECT l.file_hash, l.stopped_at, COALESCE(r.created_at, l.updated_at) AS at, r.market, r.is_research
FROM research.extraction_log l
LEFT JOIN research.research_report r ON r.file_hash = l.file_hash
WHERE l.stopped_at IN ('ingested', 'not_research')
  AND COALESCE(r.created_at, l.updated_at) >= :before_start
  AND COALESCE(r.created_at, l.updated_at) < :until
""".strip()

SKIP_LIST_SQL = """
SELECT file_hash, task, reason, model, fail_count
FROM research.llm_task_failure
WHERE first_at >= :switch_at
""".strip()


# ── 窗期與分群 ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Window:
    switch_at: datetime
    before_start: datetime
    until: datetime
    grace: timedelta = timedelta(hours=6)

    def cohort(self, ts: Optional[datetime]) -> Optional[str]:
        """入庫批次（缺值率用）：DeepSeek 批次扣掉最近 grace 小時（下游批次還沒輪到）。"""
        if ts is None:
            return None
        if self.before_start <= ts < self.switch_at:
            return CLAUDE
        if self.switch_at <= ts < self.until - self.grace:
            return DEEPSEEK
        return None


@dataclass(frozen=True)
class UsageHit:
    ts: datetime
    model: str


def model_group(model: Optional[str]) -> str:
    """模型名稱 → 群。白名單或 deepseek- 開頭是 DeepSeek；其餘（含缺值）是 Claude。"""
    if model and (is_http_model(model) or model.startswith("deepseek")):
        return DEEPSEEK
    return CLAUDE


def attribute(*, model_key: Optional[str], ts: Optional[datetime], window: Window,
              usage: Optional[UsageHit] = None) -> Optional[str]:
    """一筆產出屬於哪一群；不在窗期內回 None。

    依序：明確的 `raw_payload.model` → 切換後的成功用量紀錄 → 時間（切換前 Claude、切換後 DeepSeek，
    依據 D-C：CLI 已永久失效，切換後的寫入只可能來自 DeepSeek）。
    """
    if ts is not None and ts >= window.until:
        return None
    if model_key:
        if model_group(model_key) == DEEPSEEK:
            return DEEPSEEK
        return CLAUDE if ts is not None and window.before_start <= ts < window.until else None
    if usage is not None and usage.ts >= window.switch_at:
        return model_group(usage.model)
    if ts is None or ts < window.before_start or ts >= window.until:
        return None
    return CLAUDE if ts < window.switch_at else DEEPSEEK


def _aware(ts) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, str):
        ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def load_usage(path: Optional[Path], tasks: Iterable[str]) -> tuple[dict[tuple[str, str], UsageHit], dict]:
    """讀用量紀錄：每個 (task, file_hash) 留最後一次成功呼叫。檔案不存在回空索引（只讀、壞行略過）。"""
    wanted = set(tasks)
    index: dict[tuple[str, str], UsageHit] = {}
    stats = {"path": str(path) if path else None, "exists": False, "lines": 0, "used": 0, "bad_lines": 0}
    if path is None or not path.exists():
        return index, stats
    stats["exists"] = True
    with path.open(encoding="utf-8") as f:
        for line in f:
            stats["lines"] += 1
            try:
                row = json.loads(line)
                task, fh, kind = row.get("task"), row.get("file_hash"), row.get("kind")
                model = row.get("model_resp") or row.get("model_req")
                ts = _aware(row.get("ts"))
            except (ValueError, AttributeError, TypeError):
                stats["bad_lines"] += 1
                continue
            if task not in wanted or not fh or kind is not None or not model or ts is None:
                continue
            stats["used"] += 1
            prev = index.get((task, fh))
            if prev is None or ts >= prev.ts:
                index[(task, fh)] = UsageHit(ts, model)
    return index, stats


def usage_hashes(index: dict[tuple[str, str], UsageHit], tasks: Iterable[str], since: datetime) -> list[str]:
    wanted = set(tasks)
    return sorted({fh for (task, fh), hit in index.items() if task in wanted and hit.ts >= since})


# ── 統計 ─────────────────────────────────────────────────────────────────────


def wilson(k: int, n: int, z: float = Z95) -> Optional[tuple[float, float]]:
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def newcombe(k1: int, n1: int, k0: int, n0: int, z: float = Z95) -> Optional[tuple[float, float, float]]:
    """p1 − p0 的點估計與 Newcombe（Wilson 混合）區間。1＝DeepSeek、0＝Claude。"""
    w1, w0 = wilson(k1, n1, z), wilson(k0, n0, z)
    if w1 is None or w0 is None:
        return None
    p1, p0 = k1 / n1, k0 / n0
    d = p1 - p0
    lo = d - math.sqrt((p1 - w1[0]) ** 2 + (w0[1] - p0) ** 2)
    hi = d + math.sqrt((w1[1] - p1) ** 2 + (p0 - w0[0]) ** 2)
    return d, lo, hi


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def _ratio(pairs: Sequence[tuple[int, int]]) -> Optional[float]:
    n = sum(b for _, b in pairs)
    return sum(a for a, _ in pairs) / n if n else None


def cluster_bootstrap_diff(ds: Sequence[tuple[int, int]], cl: Sequence[tuple[int, int]], *,
                           n_boot: int, seed: int) -> Optional[tuple[float, float, float]]:
    """(命中, 分母) 以研報為單位重抽，回 (差值點估計, 2.5%, 97.5%)。任一邊沒有分母回 None。"""
    point_ds, point_cl = _ratio(ds), _ratio(cl)
    if point_ds is None or point_cl is None:
        return None
    rng = random.Random(seed)
    diffs = []
    for _ in range(n_boot):
        a = _ratio([ds[rng.randrange(len(ds))] for _ in ds])
        b = _ratio([cl[rng.randrange(len(cl))] for _ in cl])
        if a is not None and b is not None:
            diffs.append(a - b)
    if not diffs:
        return None
    diffs.sort()
    return point_ds - point_cl, _percentile(diffs, 0.025), _percentile(diffs, 0.975)


def cluster_bootstrap_ci(pairs: Sequence[tuple[int, int]], *, n_boot: int, seed: int) -> Optional[tuple[float, float]]:
    if _ratio(pairs) is None:
        return None
    rng = random.Random(seed)
    vals = sorted(v for v in (_ratio([pairs[rng.randrange(len(pairs))] for _ in pairs]) for _ in range(n_boot))
                  if v is not None)
    return (_percentile(vals, 0.025), _percentile(vals, 0.975)) if vals else None


def mean_diff_bootstrap(ds: Sequence[float], cl: Sequence[float], *, n_boot: int,
                        seed: int) -> Optional[tuple[float, float, float]]:
    if not ds or not cl:
        return None
    rng = random.Random(seed)
    diffs = sorted(
        sum(ds[rng.randrange(len(ds))] for _ in ds) / len(ds) - sum(cl[rng.randrange(len(cl))] for _ in cl) / len(cl)
        for _ in range(n_boot)
    )
    return sum(ds) / len(ds) - sum(cl) / len(cl), _percentile(diffs, 0.025), _percentile(diffs, 0.975)


def judge(diff: Optional[tuple[float, float, float]], *, direction: str, margin: float) -> dict:
    """依 §判準的方向取端點：越高越好看下界、越低越好看上界。只判讀、不做任何動作。"""
    end = "差值 CI 下界" if direction == HIGHER else "差值 CI 上界"
    bound = -margin if direction == HIGHER else margin
    out = {"direction": direction, "margin": margin, "bound": bound, "end": end}
    if diff is None:
        return {**out, "verdict": VERDICT_NO_DATA, "end_value": None, "point_within": None}
    d, lo, hi = diff
    if direction == HIGHER:
        verdict = VERDICT_PASS if lo >= bound else (VERDICT_FAIL if hi < bound else VERDICT_UNSURE)
        return {**out, "verdict": verdict, "end_value": lo, "point_within": d >= bound}
    verdict = VERDICT_PASS if hi <= bound else (VERDICT_FAIL if lo > bound else VERDICT_UNSURE)
    return {**out, "verdict": verdict, "end_value": hi, "point_within": d <= bound}


def prop(k: int, n: int) -> dict:
    ci = wilson(k, n)
    return {"k": k, "n": n, "rate": (k / n) if n else None, "ci": list(ci) if ci else None}


def _diff_dict(diff: Optional[tuple[float, float, float]]) -> Optional[dict]:
    return None if diff is None else {"point": diff[0], "ci": [diff[1], diff[2]]}


def _lengths(vals: Sequence[int]) -> Optional[dict]:
    if not vals:
        return None
    s = sorted(vals)
    return {"n": len(s), "p10": _percentile(s, 0.1), "p50": _percentile(s, 0.5), "p90": _percentile(s, 0.9),
            "mean": sum(s) / len(s)}


# ── 分析（純函式，測試直接餵假列）──────────────────────────────────────────────


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def analyze_takeaways(rows: Sequence[dict], texts: dict[str, Optional[str]], reports: Sequence[dict],
                      window: Window, *, n_boot: int, seed: int) -> dict:
    per_report: dict[str, dict] = {}
    for r in rows:
        g = attribute(model_key=r.get("model"), ts=_aware(r.get("created_at")), window=window)
        if g is None:
            continue
        rep = per_report.setdefault(r["report_id"], {"groups": set(), "rows": []})
        rep["groups"].add(g)
        rep["rows"].append(r)

    groups = {g: {"reports": 0, "items": [], "pairs_any": [], "pairs_exact": [], "sha_mismatch_reports": 0,
                  "stored_status": Counter()} for g in GROUPS}
    mixed = 0
    for rid, rep in per_report.items():
        if len(rep["groups"]) != 1:
            mixed += 1  # 同一篇的列分屬兩群（不該發生：摘錄整篇重寫），不計
            continue
        g = next(iter(rep["groups"]))
        agg = groups[g]
        agg["reports"] += 1
        agg["items"].append(len(rep["rows"]))
        for r in rep["rows"]:
            agg["stored_status"][r.get("extraction_status") or "?"] += 1
        canonical = clean_extracted(texts.get(rid) or "")
        if any(r.get("text_sha256") != _sha256(canonical) for r in rep["rows"]):
            agg["sha_mismatch_reports"] += 1
            continue
        quotes = [r["quote"] for r in rep["rows"] if r.get("quote")]
        if not quotes:
            continue
        anchors = [locate_quote(canonical, q) for q in quotes]
        agg["pairs_any"].append((sum(1 for a in anchors if a is not None), len(quotes)))
        agg["pairs_exact"].append((sum(1 for a in anchors if a is not None and a.method == "exact"), len(quotes)))

    out: dict = {"mixed_reports": mixed, "groups": {}}
    for g, agg in groups.items():
        k_any = sum(a for a, _ in agg["pairs_any"])
        k_exact = sum(a for a, _ in agg["pairs_exact"])
        n_q = sum(b for _, b in agg["pairs_any"])
        out["groups"][g] = {
            "reports": agg["reports"],
            "sha_mismatch_reports": agg["sha_mismatch_reports"],
            "items_per_report": (sum(agg["items"]) / len(agg["items"])) if agg["items"] else None,
            "quotes": n_q,
            "anchor_any": {"k": k_any, "n": n_q, "rate": (k_any / n_q) if n_q else None,
                           "ci": _list(cluster_bootstrap_ci(agg["pairs_any"], n_boot=n_boot, seed=seed))},
            "anchor_exact": {"k": k_exact, "n": n_q, "rate": (k_exact / n_q) if n_q else None,
                             "ci": _list(cluster_bootstrap_ci(agg["pairs_exact"], n_boot=n_boot, seed=seed))},
            "stored_status": dict(agg["stored_status"]),
        }
    any_diff = cluster_bootstrap_diff(groups[DEEPSEEK]["pairs_any"], groups[CLAUDE]["pairs_any"],
                                      n_boot=n_boot, seed=seed)
    exact_diff = cluster_bootstrap_diff(groups[DEEPSEEK]["pairs_exact"], groups[CLAUDE]["pairs_exact"],
                                        n_boot=n_boot, seed=seed)
    out["anchor_any_diff"] = _diff_dict(any_diff)
    out["anchor_any_judgement"] = judge(any_diff, direction=HIGHER, margin=TOL_ANCHOR)
    out["anchor_exact_diff"] = _diff_dict(exact_diff)
    out["items_diff"] = _diff_dict(mean_diff_bootstrap(
        [float(x) for x in groups[DEEPSEEK]["items"]], [float(x) for x in groups[CLAUDE]["items"]],
        n_boot=n_boot, seed=seed))

    # 產出率（1 − 未產出率）：以入庫批次為母體。rejected 不寫列，所以「沒有任何摘錄列」＝rejected＋失敗＋未跑。
    produced = {g: [0, 0] for g in GROUPS}
    for rep in reports:
        c = window.cohort(_aware(rep.get("created_at")))
        if c is None:
            continue
        produced[c][1] += 1
        produced[c][0] += 1 if rep.get("has_takeaway") else 0
    out["produced"] = {g: prop(*produced[g]) for g in GROUPS}
    pdiff = newcombe(produced[DEEPSEEK][0], produced[DEEPSEEK][1], produced[CLAUDE][0], produced[CLAUDE][1])
    out["produced_diff"] = _diff_dict(pdiff)
    out["produced_judgement"] = judge(pdiff, direction=HIGHER, margin=TOL_PARSE)
    return out


def _list(t: Optional[tuple]) -> Optional[list]:
    return list(t) if t is not None else None


def _thesis(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def analyze_signals(rows: Sequence[dict], window: Window, usage: dict[tuple[str, str], UsageHit]) -> dict:
    status = {g: Counter() for g in GROUPS}
    stance = {g: {d: Counter() for d in THESIS_DIMENSIONS} for g in GROUPS}
    for r in rows:
        g = attribute(model_key=r.get("model"), ts=_aware(r.get("created_at")), window=window,
                      usage=usage.get((TASK_SIGNAL, r.get("file_hash"))))
        if g is None:
            continue
        st = r.get("extraction_status")
        status[g][st] += 1
        if st in ("valid", "partial"):
            thesis = _thesis(r.get("thesis_dimensions"))
            for d in THESIS_DIMENSIONS:
                item = thesis.get(d)
                value = item.get("stance") if isinstance(item, dict) else None
                stance[g][d][value if value in STANCE_CONSTRUCTIVENESS[d] else "（缺或不在詞彙內）"] += 1
    out: dict = {"groups": {}}
    for g in GROUPS:
        c = status[g]
        n = c["valid"] + c["partial"] + c["rejected"]
        out["groups"][g] = {
            "rows": n,
            "valid": prop(c["valid"], n),
            "partial": prop(c["partial"], n),
            "rejected": prop(c["rejected"], n),
            "ok": prop(c["valid"] + c["partial"], n),
            "stance": {d: dict(stance[g][d]) for d in THESIS_DIMENSIONS},
        }
    ds, cl = out["groups"][DEEPSEEK]["ok"], out["groups"][CLAUDE]["ok"]
    diff = newcombe(ds["k"], ds["n"], cl["k"], cl["n"])
    out["ok_diff"] = _diff_dict(diff)
    out["ok_judgement"] = judge(diff, direction=HIGHER, margin=TOL_PARSE)
    return out


def analyze_text_field(reports: Sequence[dict], window: Window, usage: dict[tuple[str, str], UsageHit], *,
                       field_name: str, task: str) -> dict:
    """標題／摘要：入庫批次的填補率（應補未補）＋依產出模型分群的殘留簡體率與長度。"""
    filled = {g: [0, 0] for g in GROUPS}
    simp = {g: [0, 0] for g in GROUPS}
    lengths: dict[str, list[int]] = {g: [] for g in GROUPS}
    for rep in reports:
        value = rep.get(field_name)
        created = _aware(rep.get("created_at"))
        c = window.cohort(created)
        if c is not None:
            filled[c][1] += 1
            filled[c][0] += 1 if value else 0
        if not value:
            continue
        g = attribute(model_key=None, ts=created, window=window, usage=usage.get((task, rep.get("file_hash"))))
        if g is None:
            continue
        simp[g][1] += 1
        simp[g][0] += 1 if count_simplified(value) > 0 else 0
        lengths[g].append(len(value))
    out: dict = {"groups": {}}
    for g in GROUPS:
        out["groups"][g] = {
            "filled": prop(*filled[g]),
            "missing": prop(filled[g][1] - filled[g][0], filled[g][1]),
            "simplified": prop(*simp[g]),
            "length": _lengths(lengths[g]),
        }
    fdiff = newcombe(filled[DEEPSEEK][0], filled[DEEPSEEK][1], filled[CLAUDE][0], filled[CLAUDE][1])
    out["filled_diff"] = _diff_dict(fdiff)
    out["filled_judgement"] = judge(fdiff, direction=HIGHER, margin=TOL_PARSE)
    out["simplified_diff"] = _diff_dict(newcombe(simp[DEEPSEEK][0], simp[DEEPSEEK][1],
                                                 simp[CLAUDE][0], simp[CLAUDE][1]))
    return out


def analyze_tags(rows: Sequence[dict], window: Window, usage: dict[tuple[str, str], UsageHit],
                 tag_lookup: Callable[[str], Optional[tuple[Optional[str], bool]]]) -> dict:
    """標註：skip_non_research（extraction_log 的 not_research）與 market=None 比例、is_research／market 分布。

    extraction_log 的 not_research 同時涵蓋「非研報」與「是研報但沒有市場」，要拆開得讀 tags 快取；
    讀不到的列只進 skip_non_research，不進 market／is_research 的分母。
    """
    skip = {g: [0, 0] for g in GROUPS}
    none_market = {g: [0, 0] for g in GROUPS}
    is_research = {g: Counter() for g in GROUPS}
    markets = {g: Counter() for g in GROUPS}
    unknown_tag = Counter()
    for r in rows:
        g = attribute(model_key=None, ts=_aware(r.get("at")), window=window,
                      usage=usage.get((TASK_TAG, r.get("file_hash"))))
        if g is None:
            continue
        not_research = r.get("stopped_at") == "not_research"
        skip[g][1] += 1
        skip[g][0] += 1 if not_research else 0
        if not_research:
            info = tag_lookup(r["file_hash"])
        else:
            info = (r.get("market"), bool(r.get("is_research")) if r.get("is_research") is not None else True)
        if info is None:
            unknown_tag[g] += 1
            continue
        market, research = info
        none_market[g][1] += 1
        none_market[g][0] += 1 if market is None else 0
        is_research[g][str(bool(research)).lower()] += 1
        markets[g][market or "None"] += 1
    out: dict = {"groups": {}}
    for g in GROUPS:
        out["groups"][g] = {
            "skip_non_research": prop(*skip[g]),
            "market_none": prop(*none_market[g]),
            "tag_unknown": unknown_tag[g],
            "is_research": dict(is_research[g]),
            "market": dict(markets[g].most_common()),
        }
    sdiff = newcombe(skip[DEEPSEEK][0], skip[DEEPSEEK][1], skip[CLAUDE][0], skip[CLAUDE][1])
    mdiff = newcombe(none_market[DEEPSEEK][0], none_market[DEEPSEEK][1], none_market[CLAUDE][0],
                     none_market[CLAUDE][1])
    out["skip_diff"] = _diff_dict(sdiff)
    out["skip_judgement"] = judge(sdiff, direction=LOWER, margin=0.0)
    out["market_none_diff"] = _diff_dict(mdiff)
    out["market_none_judgement"] = judge(mdiff, direction=LOWER, margin=0.0)
    return out


def analyze_skip_list(rows: Optional[Sequence[dict]]) -> dict:
    if rows is None:
        return {"table_ready": False, "by_task_reason": [], "total": 0}
    counts: Counter = Counter()
    skipping: Counter = Counter()
    for r in rows:
        key = (r["task"], r["reason"])
        counts[key] += 1
        rec = FailureRecord(reason=r["reason"], model=r["model"], fail_count=int(r["fail_count"]))
        skipping[key] += 1 if should_skip(rec, r["model"]) else 0
    items = [{"task": t, "reason": reason, "count": n, "skipping": skipping[(t, reason)]}
             for (t, reason), n in sorted(counts.items())]
    return {"table_ready": True, "by_task_reason": items, "total": sum(counts.values())}


# ── 取數（唯讀）─────────────────────────────────────────────────────────────────


@dataclass
class RawData:
    takeaways: list[dict] = field(default_factory=list)
    texts: dict[str, Optional[str]] = field(default_factory=dict)
    signals: list[dict] = field(default_factory=list)
    reports: list[dict] = field(default_factory=list)
    tags: list[dict] = field(default_factory=list)
    skip_list: Optional[list[dict]] = None


def query_plan(window: Window, usage: dict[tuple[str, str], UsageHit]) -> list[tuple[str, str, dict]]:
    """(名稱, SQL, 參數)；--dry-run 印它，fetch 照它跑（全文那句的 ids 要等摘錄列取回才知道）。"""
    base = {"before_start": window.before_start, "until": window.until}
    return [
        ("read_only", READ_ONLY_SQL, {}),
        ("takeaways", TAKEAWAY_SQL, base),
        ("full_text", FULL_TEXT_SQL, {"ids": "<摘錄列涉及的 report_id>"}),
        ("signals", SIGNAL_SQL, {"before_start": window.before_start,
                                 "usage_hashes": usage_hashes(usage, [TASK_SIGNAL], window.switch_at)}),
        ("reports", REPORT_SQL, {**base, "usage_hashes": usage_hashes(usage, [TASK_TITLE, TASK_SUMMARY],
                                                                     window.switch_at)}),
        ("tags", TAG_SQL, base),
        ("failure_table_ready", FAILURE_TABLE_READY_SQL, {}),
        ("skip_list", SKIP_LIST_SQL, {"switch_at": window.switch_at}),
    ]


async def fetch(window: Window, usage: dict[tuple[str, str], UsageHit], *, session_factory=None) -> RawData:
    sf = session_factory or SessionFactory
    plan = {name: (sql, params) for name, sql, params in query_plan(window, usage)}
    data = RawData()

    async def rows(name: str, **override) -> list[dict]:
        sql, params = plan[name]
        result = await session.execute(text(sql), {**params, **override})
        return [dict(r) for r in result.mappings().all()]

    async with sf() as session:
        # 交易的第一句必須是 SET TRANSACTION；之後的 SET LOCAL 只活到交易結束。
        await session.execute(text(READ_ONLY_SQL))
        await relax_statement_timeout(session)
        data.takeaways = await rows("takeaways")
        ids = sorted({r["report_id"] for r in data.takeaways})
        if ids:
            data.texts = {r["report_id"]: r["full_text"] for r in await rows("full_text", ids=ids)}
        data.signals = await rows("signals")
        data.reports = await rows("reports")
        data.tags = await rows("tags")
        ready = (await session.execute(text(FAILURE_TABLE_READY_SQL))).scalar()
        if ready:
            data.skip_list = await rows("skip_list")
        await session.rollback()
    return data


def tags_lookup_from(tags_dir: Path) -> Callable[[str], Optional[tuple[Optional[str], bool]]]:
    def lookup(file_hash: str):
        try:
            tag = load_tag(tags_dir, file_hash)
        except OSError:
            return None
        return None if tag is None else (tag.market, bool(tag.is_research))
    return lookup


# ── 彙整與輸出 ──────────────────────────────────────────────────────────────────


def build_report(data: RawData, window: Window, usage: dict[tuple[str, str], UsageHit], usage_stats: dict, *,
                 tag_lookup, n_boot: int = 2000, seed: int = 20260925) -> dict:
    tk = analyze_takeaways(data.takeaways, data.texts, data.reports, window, n_boot=n_boot, seed=seed)
    sig = analyze_signals(data.signals, window, usage)
    title = analyze_text_field(data.reports, window, usage, field_name="title", task=TASK_TITLE)
    summary = analyze_text_field(data.reports, window, usage, field_name="summary", task=TASK_SUMMARY)
    tag = analyze_tags(data.tags, window, usage, tag_lookup)
    skip = analyze_skip_list(data.skip_list)

    def row(metric, criterion, groups_pair, diff, judgement):
        return {"metric": metric, "criterion": criterion, "claude": groups_pair[0], "deepseek": groups_pair[1],
                "diff": diff, **{k: judgement[k] for k in ("end", "end_value", "verdict", "point_within")}}

    def rates(block, key):
        return (block["groups"][CLAUDE][key]["rate"], block["groups"][DEEPSEEK][key]["rate"])

    verdicts = [
        row("摘錄：任一方式錨定成功率（主，D-A）", "差值 ≥ −5pp", rates(tk, "anchor_any"),
            tk["anchor_any_diff"], tk["anchor_any_judgement"]),
        row("摘錄：產出率（1 − 未產出率，D-N）", "差值 ≥ −2pp",
            (tk["produced"][CLAUDE]["rate"], tk["produced"][DEEPSEEK]["rate"]),
            tk["produced_diff"], tk["produced_judgement"]),
        row("訊號：非 rejected 率（D-N）", "差值 ≥ −2pp", rates(sig, "ok"), sig["ok_diff"], sig["ok_judgement"]),
        row("標題：填補率（D-N）", "差值 ≥ −2pp", rates(title, "filled"), title["filled_diff"],
            title["filled_judgement"]),
        row("摘要：填補率（D-N）", "差值 ≥ −2pp", rates(summary, "filled"), summary["filled_diff"],
            summary["filled_judgement"]),
        row("標註：skip_non_research 比例（批次 B）", "差值 ≤ 0", rates(tag, "skip_non_research"),
            tag["skip_diff"], tag["skip_judgement"]),
        row("標註：market=None 比例（批次 B）", "差值 ≤ 0", rates(tag, "market_none"),
            tag["market_none_diff"], tag["market_none_judgement"]),
    ]
    limitations = []
    if not usage_stats.get("exists"):
        limitations.append("沒有用量紀錄（data/llm_usage.jsonl）：標題、摘要、標註、訊號只能依時間分群，"
                           "切換前入庫、切換後才補的產出會被算成 Claude。")
    if not skip["table_ready"]:
        limitations.append("research.llm_task_failure 不存在（沒跑 make schema？）：跳過名單一節為空。")
    if tk["mixed_reports"]:
        limitations.append(f"{tk['mixed_reports']} 篇摘錄的列分屬兩群，未計入。")
    return {
        "window": {"switch_at": window.switch_at.isoformat(), "before_start": window.before_start.isoformat(),
                   "until": window.until.isoformat(), "grace_hours": window.grace.total_seconds() / 3600},
        "sources": {"usage_log": usage_stats},
        "verdicts": verdicts,
        "takeaway": tk,
        "signal": sig,
        "title": title,
        "summary": summary,
        "tag": tag,
        "skip_list": skip,
        "limitations": limitations,
        "bootstrap": {"n_boot": n_boot, "seed": seed},
    }


def _pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:.1f}%"


def _pp(v: Optional[float]) -> str:
    return "—" if v is None else f"{v * 100:+.1f}pp"


def _ci(ci: Optional[Sequence[float]], fmt=_pct) -> str:
    return "—" if not ci else f"[{fmt(ci[0])}, {fmt(ci[1])}]"


def _p(block: dict) -> str:
    return f"{_pct(block['rate'])}（{block['k']}/{block['n']}，CI {_ci(block['ci'])}）"


def _diff(d: Optional[dict]) -> str:
    return "—" if d is None else f"{_pp(d['point'])} {_ci(d['ci'], _pp)}"


def render_markdown(rep: dict) -> str:
    w = rep["window"]
    L = [
        "# DeepSeek 切換後批次產出觀測",
        "",
        f"- 切換時點 `{w['switch_at']}`；Claude 群 `[{w['before_start']}, {w['switch_at']})`；"
        f"DeepSeek 群 `[{w['switch_at']}, {w['until']})`（缺值率另扣最近 {w['grace_hours']:g} 小時）",
        f"- 用量紀錄：{'有' if rep['sources']['usage_log'].get('exists') else '無'}"
        f"（採用 {rep['sources']['usage_log'].get('used', 0)} 行）；CI 95%，bootstrap {rep['bootstrap']['n_boot']} 次",
        "- **只輸出判讀，不做任何切換。** 劣化時的處置只有修 prompt 或換 `deepseek-v4-pro`（沒有 Claude 可退）。",
        "",
        "## 判讀總表（計畫第四版 §判準）",
        "",
        "| 指標 | 判準 | Claude | DeepSeek | 差值（95% CI） | 取哪一端 | 端點值 | 點估計在容差內 | 判讀 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for v in rep["verdicts"]:
        within = "—" if v["point_within"] is None else ("是" if v["point_within"] else "否")
        L.append(f"| {v['metric']} | {v['criterion']} | {_pct(v['claude'])} | {_pct(v['deepseek'])} | "
                 f"{_diff(v['diff'])} | {v['end']} | {_pp(v['end_value'])} | {within} | **{v['verdict']}** |")

    tk = rep["takeaway"]
    L += ["", "## 摘錄", "", "| | Claude | DeepSeek |", "|---|---|---|"]
    gc, gd = tk["groups"][CLAUDE], tk["groups"][DEEPSEEK]
    L.append(f"| 研報數 | {gc['reports']} | {gd['reports']} |")
    L.append(f"| 有 quote 的條目 | {gc['quotes']} | {gd['quotes']} |")
    L.append(f"| 任一方式錨定成功率（主） | {_p(gc['anchor_any'])} | {_p(gd['anchor_any'])} |")
    L.append(f"| exact 錨定率（觀測） | {_p(gc['anchor_exact'])} | {_p(gd['anchor_exact'])} |")
    ipr = [("—" if g["items_per_report"] is None else f"{g['items_per_report']:.2f}") for g in (gc, gd)]
    L.append(f"| 每篇條數 | {ipr[0]} | {ipr[1]} |")
    L.append(f"| 未產出率（rejected＋失敗＋未跑；入庫批次） | {_p(_miss(tk['produced'][CLAUDE]))} | "
             f"{_p(_miss(tk['produced'][DEEPSEEK]))} |")
    L.append(f"| 全文已變、不計錨定的研報 | {gc['sha_mismatch_reports']} | {gd['sha_mismatch_reports']} |")
    L.append(f"| 存的列狀態 | {gc['stored_status']} | {gd['stored_status']} |")
    L.append("")
    L.append(f"exact 錨定率差值 {_diff(tk['anchor_exact_diff'])}；每篇條數差值 "
             f"{_num_diff(tk['items_diff'])}（觀測，不判）。")

    sig = rep["signal"]
    L += ["", "## 訊號", "", "| | Claude | DeepSeek |", "|---|---|---|"]
    for key, label in (("valid", "valid"), ("partial", "partial"), ("rejected", "rejected")):
        L.append(f"| {label} | {_p(sig['groups'][CLAUDE][key])} | {_p(sig['groups'][DEEPSEEK][key])} |")
    L += ["", "stance 分布（valid／partial 列）：", ""]
    for d in THESIS_DIMENSIONS:
        L.append(f"- `{d}`：Claude {sig['groups'][CLAUDE]['stance'][d]}；"
                 f"DeepSeek {sig['groups'][DEEPSEEK]['stance'][d]}")

    for key, label in (("title", "標題"), ("summary", "摘要")):
        b = rep[key]
        L += ["", f"## {label}", "", "| | Claude | DeepSeek |", "|---|---|---|"]
        L.append(f"| 缺值率（應補未補；入庫批次） | {_p(b['groups'][CLAUDE]['missing'])} | "
                 f"{_p(b['groups'][DEEPSEEK]['missing'])} |")
        L.append(f"| 殘留簡體率（觀測） | {_p(b['groups'][CLAUDE]['simplified'])} | "
                 f"{_p(b['groups'][DEEPSEEK]['simplified'])} |")
        L.append(f"| 長度 p10／p50／p90 | {_len(b['groups'][CLAUDE]['length'])} | "
                 f"{_len(b['groups'][DEEPSEEK]['length'])} |")

    tag = rep["tag"]
    L += ["", "## 標註", "", "| | Claude | DeepSeek |", "|---|---|---|"]
    L.append(f"| skip_non_research 比例 | {_p(tag['groups'][CLAUDE]['skip_non_research'])} | "
             f"{_p(tag['groups'][DEEPSEEK]['skip_non_research'])} |")
    L.append(f"| market=None 比例 | {_p(tag['groups'][CLAUDE]['market_none'])} | "
             f"{_p(tag['groups'][DEEPSEEK]['market_none'])} |")
    L.append(f"| 讀不到 tags 快取的非研報 | {tag['groups'][CLAUDE]['tag_unknown']} | "
             f"{tag['groups'][DEEPSEEK]['tag_unknown']} |")
    L.append(f"| is_research | {tag['groups'][CLAUDE]['is_research']} | {tag['groups'][DEEPSEEK]['is_research']} |")
    L.append(f"| market | {tag['groups'][CLAUDE]['market']} | {tag['groups'][DEEPSEEK]['market']} |")
    L += ["", "is_research 翻轉要逐筆人工看（同一篇沒有兩個模型的標註，這裡量不到）。"]

    skip = rep["skip_list"]
    L += ["", "## 跳過名單（切換後新增，`research.llm_task_failure`）", ""]
    if not skip["table_ready"]:
        L.append("表不存在。")
    elif not skip["by_task_reason"]:
        L.append("沒有。")
    else:
        L += ["| task | reason | 筆數 | 已被跳過 |", "|---|---|---|---|"]
        for it in skip["by_task_reason"]:
            L.append(f"| {it['task']} | {it['reason']} | {it['count']} | {it['skipping']} |")
        L += ["", "逐筆看 `make llm-blocked`。"]

    L += ["", "## 限制", ""]
    L += [f"- {x}" for x in rep["limitations"]]
    L.append("- 分群依據與各任務的已知偏差見本檔模組 docstring。")
    return "\n".join(L) + "\n"


def _miss(p: dict) -> dict:
    n, k = p["n"], p["k"]
    return prop(n - k, n)


def _len(b: Optional[dict]) -> str:
    return "—" if not b else f"{b['p10']:.0f}／{b['p50']:.0f}／{b['p90']:.0f}（n={b['n']}）"


def _num_diff(d: Optional[dict]) -> str:
    return "—" if d is None else f"{d['point']:+.2f} [{d['ci'][0]:+.2f}, {d['ci'][1]:+.2f}]"


# ── CLI ──────────────────────────────────────────────────────────────────────


def parse_switch_at(value: str) -> datetime:
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=TAIPEI)


def _out_allowed(path: Path) -> bool:
    resolved = path.resolve()
    return resolved != FORBIDDEN_OUT_DIR.resolve() and FORBIDDEN_OUT_DIR.resolve() not in resolved.parents


def _json_default(o):
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(type(o).__name__)


def render_dry_run(window: Window, usage: dict[tuple[str, str], UsageHit], usage_stats: dict) -> str:
    L = [f"-- dry-run：不連 DB。用量紀錄 {usage_stats}", ""]
    for name, sql, params in query_plan(window, usage):
        shown = {k: (f"<{len(v)} 個 file_hash>" if isinstance(v, list) else v) for k, v in params.items()}
        L += [f"-- [{name}] 參數 {json.dumps(shown, ensure_ascii=False, default=_json_default)}", sql + ";", ""]
    L.append("-- 之後 rollback；不寫任何表。")
    return "\n".join(L) + "\n"


def main(argv: Optional[Sequence[str]] = None, *, session_factory=None) -> int:
    ap = argparse.ArgumentParser(description="DeepSeek 切換後批次產出觀測（零 LLM、唯讀；只輸出判讀）")
    ap.add_argument("--switch-at", required=True, help="切換時點（ISO；沒帶時區視為台北時間）")
    ap.add_argument("--before-days", type=int, default=30, help="Claude 對照窗期：切換前幾天（預設 30）")
    ap.add_argument("--until", default=None, help="觀測截止（ISO；預設現在）")
    ap.add_argument("--grace-hours", type=float, default=6.0,
                    help="缺值率不計最近幾小時入庫的研報（下游批次還沒輪到；預設 6）")
    ap.add_argument("--usage-log", default=str(DEFAULT_USAGE_LOG), help="用量紀錄（只讀；不存在就只依時間分群）")
    ap.add_argument("--tags-dir", default=str(DEFAULT_TAGS_DIR), help="tags 快取目錄（只讀）")
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260925)
    ap.add_argument("--json", action="store_true", help="輸出機讀 JSON（預設 markdown）")
    ap.add_argument("--out", default=None, help="輸出檔（預設 stdout；不接受 repo 根 data/ 底下）")
    ap.add_argument("--dry-run", action="store_true", help="只印會跑的查詢，不連 DB")
    args = ap.parse_args(argv)

    try:
        switch_at = parse_switch_at(args.switch_at)
        until = parse_switch_at(args.until) if args.until else datetime.now(timezone.utc)
    except ValueError as exc:
        print(f"時間格式錯誤：{exc}", file=sys.stderr)
        return RC_CONFIG
    if args.before_days <= 0 or until <= switch_at:
        print("--before-days 必須 > 0，且 --until 必須晚於 --switch-at", file=sys.stderr)
        return RC_CONFIG
    if args.out and not _out_allowed(Path(args.out)):
        print(f"--out 不可寫進 {FORBIDDEN_OUT_DIR}（這支只讀 data/）", file=sys.stderr)
        return RC_CONFIG

    window = Window(switch_at=switch_at, before_start=switch_at - timedelta(days=args.before_days), until=until,
                    grace=timedelta(hours=args.grace_hours))
    usage, usage_stats = load_usage(Path(args.usage_log) if args.usage_log else None,
                                    [TASK_TAG, TASK_TITLE, TASK_SUMMARY, TASK_TAKEAWAY, TASK_SIGNAL])

    if args.dry_run:
        output = render_dry_run(window, usage, usage_stats)
    else:
        data = asyncio.run(fetch(window, usage, session_factory=session_factory))
        rep = build_report(data, window, usage, usage_stats, tag_lookup=tags_lookup_from(Path(args.tags_dir)),
                           n_boot=args.bootstrap, seed=args.seed)
        output = (json.dumps(rep, ensure_ascii=False, indent=2, default=_json_default) + "\n") if args.json \
            else render_markdown(rep)

    if args.out:
        Path(args.out).write_text(output, encoding="utf-8")
        print(f"已寫入 {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(output)
    return RC_OK


if __name__ == "__main__":
    raise SystemExit(main())
