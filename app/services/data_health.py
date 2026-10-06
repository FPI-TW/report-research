"""資料健康：彙整既有的唯讀檢查給管理後台（`GET /api/admin/data-health`）。

三個來源，成本不同，所以取得方式不同：

| 來源 | 產生者 | web 怎麼拿 |
|---|---|---|
| 批次新鮮度 | `app/services/batch_freshness.py`（四個 `max()`＋心跳檔） | **即時判讀**（便宜；路由層 TTL 快取） |
| 資料完整性稽核 | `scripts/db_audit.py`（timer 每日 08:45） | 讀它最後一次寫的結果檔 |
| R2 對帳 | `scripts/reconcile_object_storage.py --dry-run`（timer 每週一 07:00） | 讀它最後一次寫的結果檔 |

**稽核與對帳刻意不在 web 裡跑**：稽核有幾條是 57 萬列 `report_chunk` 的全表掃描（單次數十秒，還要放寬
statement_timeout），對帳是一萬多次 R2 HEAD＋整檔 sha256——放進請求路徑等於讓一個管理頁拖垮 web 與 DB。
兩支腳本跑完時把結構化結果原子寫進 `data/health/<名稱>.json`（`write_result`），web 只讀檔（`read_result`）。
沒有結果檔＝「還沒跑過（或結果檔寫不進去）」，不是錯誤。

結果檔是**輔助顯示**：寫入失敗只印一行警告、絕不改變腳本的退出碼（告警鏈仍以 OnFailure 為準）；
內容只有計數、檢查代碼與物件 key／研報 id 這類識別字，沒有研報內文、問答或任何祕密。

判讀規則（`audit_failed`、`reconcile_status`）只在這裡定義一次：`scripts/db_audit.py` 的退出碼用同一個
`audit_failed`（「只讀不修、warn 也算失敗」），web 的狀態燈也用它，兩邊不會各說各話。

`DATA_HEALTH_DIR` 只給測試用（`tests/conftest.py` 以賦值導向不存在的路徑：寫入 fail-open、讀取當作沒有）；
生產一律 repo 根的 `data/health/`。
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

RESULT_DB_AUDIT = "db_audit"
RESULT_R2_RECONCILE = "r2_reconcile"
RESULT_NAMES = (RESULT_DB_AUDIT, RESULT_R2_RECONCILE)
SCHEMA_VERSION = 1

# 結果檔大小上限：正常只有幾 KB（對帳的問題清單最多 MAX_RECONCILE_ISSUES 筆）。超過＝檔案被動過，不讀。
MAX_RESULT_BYTES = 256 * 1024
# 對帳結果裡保留的問題樣本數（全部的計數在 stats；完整清單看該次 journal）。
MAX_RECONCILE_ISSUES = 50

# 結果多舊算「過期」：排程週期＋餘裕。過期只讓狀態燈變黃（warn），內容照樣顯示。
# 稽核每日一次 → 48 小時；對帳每週一次 → 9 天。
STALE_AFTER_HOURS = {RESULT_DB_AUDIT: 48.0, RESULT_R2_RECONCILE: 9 * 24.0}

STATUS_OK = "ok"
STATUS_WARN = "warn"
STATUS_FAIL = "fail"
STATUS_UNKNOWN = "unknown"
_STATUS_RANK = {STATUS_OK: 0, STATUS_UNKNOWN: 1, STATUS_WARN: 2, STATUS_FAIL: 3}

# 對帳 stats 的分級：會讓腳本非零退出的（與 reconcile_object_storage.run 的 rc 判斷一致）
# 與只是差異（腳本 rc=0，但值得人去看）。
RECONCILE_FAIL_KEYS = ("errors", "unkeyed", "key_mismatch")
RECONCILE_WARN_KEYS = ("missing", "size_mismatch", "sha_mismatch", "sha_metadata_missing", "orphans")


def results_dir() -> Path:
    return Path(os.environ.get("DATA_HEALTH_DIR") or REPO_ROOT / "data" / "health")


def result_path(name: str) -> Path:
    if name not in RESULT_NAMES:
        raise ValueError(f"未知的結果檔名稱：{name!r}")
    return results_dir() / f"{name}.json"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def write_result(name: str, payload: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """原子寫入一份結果（同目錄 tempfile → fsync → os.replace）。任何失敗都只警告、回 False，不拋。

    呼叫端是批次腳本（logging 在那裡無聲），所以警告直接印到 stderr，journal 看得到。
    """
    try:
        path = result_path(name)
        body = {
            **payload,
            "schema_version": SCHEMA_VERSION,
            "name": name,
            "written_at": (now or _now()).isoformat(),
        }
        data = json.dumps(body, ensure_ascii=False, default=str, sort_keys=True)
        if len(data.encode("utf-8")) > MAX_RESULT_BYTES:
            raise ValueError(f"結果超過 {MAX_RESULT_BYTES} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return True
    except Exception as exc:  # noqa: BLE001 — 結果檔只是給管理頁看的，不能改變腳本的結果
        print(f"資料健康結果檔寫入失敗（{name}，不影響本次結果）：{type(exc).__name__}: {exc}", file=sys.stderr)
        return False


def read_result(name: str) -> tuple[dict | None, str | None]:
    """讀一份結果。回 (內容, 讀不到的原因)；兩者恰有一個為 None。檔案不存在＝("missing")。"""
    path = result_path(name)
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, f"unreadable:{type(exc).__name__}"
    if size > MAX_RESULT_BYTES:
        return None, "too_large"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, f"corrupt:{type(exc).__name__}"
    if not isinstance(data, dict) or data.get("name") != name:
        return None, "corrupt:shape"
    return data, None


def worst(statuses: Iterable[str]) -> str:
    """多個狀態取最嚴重的（fail > warn > unknown > ok）；空的回 unknown。"""
    statuses = list(statuses)
    if not statuses:
        return STATUS_UNKNOWN
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 1))


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def age_hours(value: Any, now: datetime | None = None) -> float | None:
    parsed = parse_time(value)
    if parsed is None:
        return None
    return ((now or _now()) - parsed).total_seconds() / 3600.0


def is_stale(name: str, finished_at: Any, now: datetime | None = None) -> bool:
    """結果是否比排程週期＋餘裕還舊。時間讀不到或在未來也算過期（不能讓壞掉的時鐘把燈變綠）。"""
    hours = age_hours(finished_at, now)
    return hours is None or hours < -1 or hours > STALE_AFTER_HOURS[name]


# ── 資料完整性稽核（scripts/db_audit.py）──────────────────────────────────


def audit_failed(findings: Iterable[Any]) -> bool:
    """「warn 也算失敗」：任何一條檢查的違反數 > 0 就是失敗，不分 error／warn。

    `scripts/db_audit.py` 的退出碼與管理後台的狀態燈都用這一個函式。分級只影響閱讀順序——「warn 不算
    失敗」會讓 warn 區三個月內永遠有東西、從此無人閱讀（理由見該腳本的模組 docstring）。
    接受 dataclass（有 `.count`）或 dict（有 `"count"`）。
    """
    for f in findings:
        count = f.get("count") if isinstance(f, Mapping) else getattr(f, "count", 0)
        if isinstance(count, (int, float)) and count > 0:
            return True
    return False


def audit_status(result: Mapping[str, Any] | None) -> str:
    """稽核結果檔 → ok／fail／unknown（DB 不可用那次是 unknown，不是 ok）。過期另由 `is_stale` 判。"""
    if not result or result.get("error"):
        return STATUS_UNKNOWN
    findings = result.get("findings")
    if not isinstance(findings, list):
        return STATUS_UNKNOWN
    return STATUS_FAIL if audit_failed(findings) else STATUS_OK


# ── R2 對帳（scripts/reconcile_object_storage.py）─────────────────────────


def reconcile_status(result: Mapping[str, Any] | None) -> str:
    """對帳結果檔 → ok／warn／fail／unknown。

    - 腳本非零退出的三類（連不上／錯誤、DB 缺 key、key 與正典不符）→ fail；
    - 缺檔、大小或 sha 不符、舊檔沒有 sha metadata、orphan → warn（腳本 rc=0，但要有人去看）；
    - `OBJECT_STORAGE_MODE=local`（沒有 R2 可對）→ ok。
    """
    if not result:
        return STATUS_UNKNOWN
    if result.get("mode") == "local":
        return STATUS_OK
    stats = result.get("stats")
    if not isinstance(stats, Mapping):
        return STATUS_UNKNOWN
    if result.get("exit_code") not in (0, None) or any(_positive(stats.get(k)) for k in RECONCILE_FAIL_KEYS):
        return STATUS_FAIL
    if any(_positive(stats.get(k)) for k in RECONCILE_WARN_KEYS):
        return STATUS_WARN
    return STATUS_OK


def _positive(value: Any) -> bool:
    return isinstance(value, (int, float)) and value > 0
