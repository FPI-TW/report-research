"""應用層監控訊號的資料來源。

`GET /metrics`（`web/metrics.py`）與通用 webhook 告警（`app/services/alert_webhook.py`）共用。

只讀、便宜、不呼叫 LLM：
- 上傳佇列：`research.report_upload` 依狀態計數（一次 GROUP BY，表很小）＋最舊一筆「處理中」（`IN_FLIGHT_STATES`）
  的上傳已等了多久。狀態詞彙一律取自 `app/services/uploads.py`。
- 批次新鮮度：直接重用 `app/services/data_health.freshness_section`（＝`scripts/check_batch_freshness.py` 與管理後台
  資料健康頁同一組函式與門檻），不另寫一份判斷。
- LLM 批次斷路器：`data/.llm_breaker` 標記在不在、還在不在有效期內。web 不 import `scripts/`（慣例，見
  `app/services/diagnostics.py`），所以路徑與有效期在這裡各留一份，`tests/test_metrics.py` 對帳
  `scripts/_llm_env.py` 的 `breaker_path`／`BREAKER_TTL_S`。web 沒有 sync 輪次，只看有效期。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from sqlalchemy import text

from app.services import uploads

REPO_ROOT = Path(__file__).resolve().parents[2]
# 與 scripts/_llm_env.py 相同（對帳見模組 docstring）。
BREAKER_TTL_S = 30 * 60


def breaker_path() -> Path:
    return Path(os.environ.get("LLM_BREAKER_FILE") or REPO_ROOT / "data" / ".llm_breaker")


def breaker_active(now: float | None = None) -> bool:
    """標記存在且未過有效期。讀不到一律當作沒有（同 `scripts/_llm_env._fresh_breaker`）。"""
    try:
        mtime = breaker_path().stat().st_mtime
    except OSError:
        return False
    return ((now if now is not None else time.time()) - mtime) < BREAKER_TTL_S


_UPLOAD_SQL = """
SELECT state, count(*) AS n, EXTRACT(EPOCH FROM (now() - min(uploaded_at))) AS oldest_s
FROM research.report_upload
GROUP BY state
"""


async def upload_queue(session) -> dict:
    """{"counts": {每個狀態: 筆數（沒有的補 0）}, "oldest_in_flight_age_s": 秒或 None}。"""
    counts = {state: 0 for state in uploads.STATES}
    oldest: float | None = None
    for state, n, oldest_s in (await session.execute(text(_UPLOAD_SQL))).all():
        if state not in counts:
            continue  # CHECK 擋得住；防的是詞彙與 DB 版本錯開時冒出未知 label
        counts[state] = int(n)
        if state in uploads.IN_FLIGHT_STATES and oldest_s is not None:
            oldest = max(oldest or 0.0, float(oldest_s))
    return {"counts": counts, "oldest_in_flight_age_s": oldest}


async def freshness(session_factory) -> dict:
    """批次新鮮度（`data_health.freshness_section` 原樣）：{status, exit_code, error, findings: [...]}。"""
    from app.services import data_health

    return await data_health.freshness_section(session_factory)
