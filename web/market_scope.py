"""使用者端的市場篩選依 profile 的啟用子集（`app/profile.py` 的 `[markets].enabled`）。

子集管的是「能選什麼」：選單、篩選參數、API 用戶端的 entitlement。它**不是**可見性——標到未啟用市場的研報照常
出現在不帶篩選的清單與問答裡（要藏研報走 `app/services/visibility.py`）。

篩選帶了**未啟用的標準市場代碼**時回 422 `market_disabled`；不是標準代碼的值維持各端點原本的行為（多半是查無結果），
所以全部啟用的 profile（例如預設 profile）行為與改動前完全相同。
"""

from __future__ import annotations

from app.profile import get_profile
from app.services.tagging import MARKETS
from web.errors import AppError

_STANDARD = frozenset(MARKETS)


def check_market_filter(market: str | None) -> None:
    if market and market in _STANDARD and not get_profile().markets.is_enabled(market):
        raise AppError(422, "market_disabled", f"此市場未啟用：{market}")


def markets_payload() -> dict:
    """`GET /api/markets`：`markets` 是啟用的代碼（標準順序，與改動前同一個鍵）；`items` 依 profile 的顯示順序附名稱，
    `labels` 是全部標準代碼的顯示名稱（研報可能標到未啟用的市場，照樣要有名稱可顯示）。"""
    m = get_profile().markets
    return {
        "markets": [c for c in MARKETS if m.is_enabled(c)],
        "items": [{"code": c, "label": m.label(c)} for c in m.enabled],
        "labels": dict(m.display_names),
    }
