"""機構的業務時區：「今天」「一天」「幾點」的唯一來源。

時區由 profile 的 `[locale].timezone` 宣告（IANA 名稱，缺席＝`Asia/Taipei`；載入時以 zoneinfo 驗證，見
`app/profile.py`）。用到它的有：每人每日配額與對外 API 的每日額度（`quota`、`api_clients`、`upload_intake`）、
使用量與分析的切日（`usage_events`、`analytics`、`llm_usage`）、DB 快照的每日彙總與週趨勢（`db_insights`）、首次導入
背景補齊的每日 token 帳（`backfill_tiers`）、每日簡報的鐘點（`scripts/generate_brief.py`）與匯出檔名的日期。

規則：

- **在呼叫時取值**，不要在 import 期把 `tz()`、`tz_name()` 存成模組常數：profile 有快取、測試每題前後
  `reset_profile_cache()`，凍結的常數會留著上一個 profile 的時區。
- SQL 的 `AT TIME ZONE` 一律以綁定參數傳 `tz_name()`（`:tz`），不內插進字串。
- 兩個 aware datetime 的 tzinfo 是同一個物件時，Python 以牆上時間相減、不看 utcoffset：有日光節約的時區在切換日
  會差一小時。算「距下一個午夜幾秒」一律換成 UTC 再減（`seconds_until_midnight`）。

本模組只依賴 `app.profile`（標準函式庫＋`tagging`），純計算模組也能安全 import。
"""

from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from app.profile import get_profile


def tz_name() -> str:
    """業務時區的 IANA 名稱（API 回應的 `timezone` 欄、SQL 的 `:tz`）。"""
    return get_profile().locale.timezone


def tz() -> ZoneInfo:
    return ZoneInfo(tz_name())  # ZoneInfo 自己以名稱快取實例


def label() -> str:
    """錯誤訊息裡的地名：「今天（{label}時間）」。"""
    return get_profile().locale.timezone_label


def now(at: datetime | None = None) -> datetime:
    """`at`（預設現在）換成業務時區的時間。"""
    return (at or datetime.now(timezone.utc)).astimezone(tz())


def today(at: datetime | None = None) -> date:
    return now(at).date()


def day_start(d: date) -> datetime:
    """業務時區某日 00:00（tz-aware）。"""
    return datetime.combine(d, time(0), tzinfo=tz())


def seconds_until_midnight(at: datetime | None = None) -> int:
    """距下一個業務時區午夜的秒數（無條件進位、至少 1）：每日額度 429 的 `Retry-After`。"""
    local = now(at)
    midnight = day_start(local.date() + timedelta(days=1))
    delta = midnight.astimezone(timezone.utc) - local.astimezone(timezone.utc)
    return max(1, math.ceil(delta.total_seconds()))
