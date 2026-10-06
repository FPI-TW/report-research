"""LLM 用量記錄（`data/llm_usage.jsonl`）的位置與彙總，給管理後台的 `GET /api/admin/llm-usage`。

寫入端是 `scripts/_claude_cli.record_usage`：**批次**每次 LLM 呼叫（DeepSeek HTTP 與 CLI）追加一行 JSON
（欄位見那支模組的 docstring）。線上問答（`app/services/llm.py` 的 `stream_completion`）不寫這份檔，所以這裡
的數字是「批次的用量」，不是全站用量。費用真值看 DeepSeek 餘額差分；這份是**歸因**依據（哪個任務、哪個模型
花了多少 token）。

讀取的規則：

- **只輸出彙總**：呼叫次數、失敗次數、token 四類、耗時。`prompt_sha256`、`file_hash`、`report_id` 一律不出
  這個模組（檔案本身就不含 prompt 原文與金鑰；這裡連雜湊都不外流）。
- **有上限**：只讀檔尾 `MAX_SCAN_BYTES`、最多 `MAX_LINES` 行（取最新的）；超過時 `truncated=true` 並回報
  實際涵蓋到的最早時間。任務／模型的相異值最多 `MAX_GROUPS` 個（其餘併成「(其他)」），日×任務×模型的
  明細最多 `MAX_ROWS` 列。檔案只增不減，這些上限讓一個長年累積的檔不會拖垮 web。
- **檔案不存在＝空結果**，不是錯誤（新機器、或批次還沒跑過）。壞行（非 JSON、缺時間）只計數、略過。
- 日期以台北時間（UTC+8，無日光節約）切日。
- 費用：寫入端目前不記費用；若日後某些行帶數值 `cost` 欄，`cost` 會加總，否則為 null（`cost_available=false`）。
  刻意不在這裡內建價目表——價格會變，寫死的價目表會安靜地算錯。

`usage_log_path()` 是路徑的唯一定義：`scripts/_claude_cli.usage_log_path` 直接呼叫它。`LLM_USAGE_LOG` 只給測試用
（conftest 指到 os.devnull）。這個函式是同步、會讀檔的；web 端請丟到 thread（`asyncio.to_thread`）。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]

TZ = timezone(timedelta(hours=8))
TZ_NAME = "Asia/Taipei"

DEFAULT_WINDOW = timedelta(days=30)
MAX_WINDOW = timedelta(days=366)
MAX_SCAN_BYTES = 16 * 1024 * 1024
MAX_LINES = 100_000
MAX_LINE_BYTES = 64 * 1024
MAX_GROUPS = 200
MAX_ROWS = 5000
MAX_LABEL_CHARS = 64
UNLABELED = "(未標示)"
OTHER = "(其他)"

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def usage_log_path() -> Path:
    """`LLM_USAGE_LOG` 只給測試用（conftest 指到 os.devnull）；生產一律 repo 根的 `data/llm_usage.jsonl`。"""
    return Path(os.environ.get("LLM_USAGE_LOG") or REPO_ROOT / "data" / "llm_usage.jsonl")


@dataclass
class Totals:
    calls: int = 0
    failures: int = 0
    prompt_hit_tokens: int = 0
    prompt_miss_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    calls_without_tokens: int = 0  # CLI 路徑、或失敗在第一個 chunk 之前：沒有用量可記
    total_ms: int = 0
    cost: float | None = None

    def add(self, row: dict) -> None:
        self.calls += 1
        if row.get("kind"):
            self.failures += 1
        tokens = row.get("tokens")
        if isinstance(tokens, dict):
            self.prompt_hit_tokens += _int(tokens.get("hit"))
            self.prompt_miss_tokens += _int(tokens.get("miss"))
            self.completion_tokens += _int(tokens.get("completion"))
            self.reasoning_tokens += _int(tokens.get("reasoning"))
        else:
            self.calls_without_tokens += 1
        self.total_ms += _int(row.get("total_ms"))
        cost = row.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool) and cost >= 0:
            self.cost = (self.cost or 0.0) + float(cost)

    def as_dict(self) -> dict:
        return {
            "calls": self.calls, "failures": self.failures, "prompt_hit_tokens": self.prompt_hit_tokens,
            "prompt_miss_tokens": self.prompt_miss_tokens, "completion_tokens": self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens, "calls_without_tokens": self.calls_without_tokens,
            "total_ms": self.total_ms, "cost": round(self.cost, 6) if self.cost is not None else None,
        }


@dataclass
class _Source:
    exists: bool = False
    size_bytes: int = 0
    scanned_bytes: int = 0
    truncated: bool = False
    lines_scanned: int = 0
    lines_invalid: int = 0
    lines_in_range: int = 0
    earliest_ts: str | None = None
    latest_ts: str | None = None


@dataclass
class _Groups:
    """相異值有上限的分組：第 MAX_GROUPS 個之後的新值一律併進「(其他)」。"""

    seen: set[str] = field(default_factory=set)

    def label(self, value: Any) -> str:
        text = _CONTROL.sub("", value).strip()[:MAX_LABEL_CHARS] if isinstance(value, str) else ""
        text = text or UNLABELED
        if text in self.seen:
            return text
        if len(self.seen) >= MAX_GROUPS:
            return OTHER
        self.seen.add(text)
        return text


def _int(value: Any) -> int:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def _parse_ts(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _read_tail(path: Path, max_bytes: int, max_lines: int, src: _Source) -> list[bytes]:
    with path.open("rb") as f:
        size = os.fstat(f.fileno()).st_size
        src.exists = True
        src.size_bytes = size
        start = max(0, size - max_bytes)
        f.seek(start)
        data = f.read(max_bytes)
    src.scanned_bytes = len(data)
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]  # 從檔案中段開始讀：第一行是殘段
        src.truncated = True
    lines = [ln for ln in lines if ln.strip()]
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
        src.truncated = True
    return lines


def summarize(since: datetime, until: datetime, *, path: Path | None = None,
              max_bytes: int = MAX_SCAN_BYTES, max_lines: int = MAX_LINES) -> dict:
    """彙總 `[since, until)` 之間的用量。回傳可直接交給 pydantic 的 dict（見 `web/routers/admin_data_health.py`）。"""
    path = path or usage_log_path()
    src = _Source()
    try:
        raw_lines = _read_tail(path, max_bytes, max_lines, src) if path.is_file() else []
    except FileNotFoundError:
        raw_lines = []

    totals = Totals()
    by_day: dict[date, Totals] = {}
    by_task: dict[str, Totals] = {}
    by_model: dict[str, Totals] = {}
    rows: dict[tuple[date, str, str], Totals] = {}
    tasks, models = _Groups(), _Groups()
    earliest: datetime | None = None
    latest: datetime | None = None

    for raw in raw_lines:
        src.lines_scanned += 1
        if len(raw) > MAX_LINE_BYTES:
            src.lines_invalid += 1
            continue
        try:
            row = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            src.lines_invalid += 1
            continue
        ts = _parse_ts(row.get("ts")) if isinstance(row, dict) else None
        if ts is None:
            src.lines_invalid += 1
            continue
        earliest = ts if earliest is None or ts < earliest else earliest
        latest = ts if latest is None or ts > latest else latest
        if not (since <= ts < until):
            continue
        src.lines_in_range += 1
        day = ts.astimezone(TZ).date()
        task = tasks.label(row.get("task"))
        model = models.label(row.get("model_req") or row.get("model_resp"))
        totals.add(row)
        by_day.setdefault(day, Totals()).add(row)
        by_task.setdefault(task, Totals()).add(row)
        by_model.setdefault(model, Totals()).add(row)
        rows.setdefault((day, task, model), Totals()).add(row)

    src.earliest_ts = earliest.isoformat() if earliest else None
    src.latest_ts = latest.isoformat() if latest else None
    ordered_rows = sorted(rows.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2]))
    by_calls = lambda kv: (-kv[1].calls, kv[0])  # noqa: E731
    return {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "timezone": TZ_NAME,
        "source": src.__dict__.copy(),
        "totals": totals.as_dict(),
        "by_day": [{"day": d.isoformat(), **t.as_dict()} for d, t in sorted(by_day.items())],
        "by_task": [{"task": k, **t.as_dict()} for k, t in sorted(by_task.items(), key=by_calls)],
        "by_model": [{"model": k, **t.as_dict()} for k, t in sorted(by_model.items(), key=by_calls)],
        "rows": [{"day": d.isoformat(), "task": k, "model": m, **t.as_dict()}
                 for (d, k, m), t in ordered_rows[:MAX_ROWS]],
        "rows_truncated": len(ordered_rows) > MAX_ROWS,
        "cost_available": totals.cost is not None,
    }


def file_signature(path: Path | None = None) -> tuple[int, int] | None:
    """(大小, mtime_ns)：web 端的快取鍵帶它，檔案一變就重算。不存在回 None。"""
    try:
        st = (path or usage_log_path()).stat()
    except OSError:
        return None
    return st.st_size, st.st_mtime_ns
