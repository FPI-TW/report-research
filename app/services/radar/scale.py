"""觀點雷達決定性基元（純函式，零 DB、零 LLM）。

實作設計規格「比較與聚合規則」：評等五級尺度、目標價同幣別四分位、EPS 同分組鍵比較、
四維論點 stance 建設性分類、單券商前後差異。stance 詞彙 import 自 signal_extract（共用契約）。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Optional

from app.services.radar.types import Change, Signal
from app.services.signal_extract import STANCE_CONSTRUCTIVENESS, THESIS_DIMENSIONS

# 五級評等序位（unknown → None，不計入分布/方向）
RATING_SCALE = {"sell": -2, "underweight": -1, "neutral": 0, "overweight": 1, "buy": 2}
RATING_BUCKET = {
    "buy": "bullish", "overweight": "bullish", "neutral": "neutral",
    "underweight": "bearish", "sell": "bearish",
}
_INV_RATING_SCALE = {v: k for k, v in RATING_SCALE.items()}
RATING_DISPLAY = {
    "buy": "買進", "overweight": "加碼", "neutral": "中立",
    "underweight": "減碼", "sell": "賣出", "unknown": "未評等",
}
BUCKET_DISPLAY = {"bullish": "偏多", "neutral": "中立", "bearish": "偏空"}
DIM_DISPLAY = {"outlook": "展望", "catalyst": "催化劑", "risk": "風險", "valuation": "估值"}
DIMLABEL_DISPLAY = {
    "strengthen": "轉強", "weaken": "轉弱", "diverging": "分歧擴大",
    "stable": "無顯著變化", "insufficient": "資料不足",
}

# 正反立場接近才算「分歧擴大」的門檻（弱側佔比 ≥ 此值）
DIVERGENCE_RATIO = 0.4


@dataclass(frozen=True)
class Quartiles:
    median: float
    q1: float
    q3: float
    low: float
    high: float
    count: int


def rating_scale(rating: Optional[str]) -> Optional[int]:
    """五級 → 序位；unknown/未知 → None。"""
    return RATING_SCALE.get(rating or "")


def rating_bucket(rating: Optional[str]) -> Optional[str]:
    """五級 → 三桶（bullish/neutral/bearish）；unknown → None。"""
    return RATING_BUCKET.get(rating or "")


def median_rating(distribution: list[tuple[str, int]]) -> Optional[str]:
    """五級評等分佈的加權中位立場（買進>加碼>中立>減碼>賣出）。

    展開為序位樣本取中位；偶數樣本恰跨兩級時向偏多側取整（round-half-up）。
    空分佈（或全 unknown）→ None。
    """
    samples: list[int] = []
    for rating, count in distribution:
        s = RATING_SCALE.get(rating)
        if s is None or count <= 0:
            continue
        samples.extend([s] * count)
    if not samples:
        return None
    m = statistics.median(samples)
    idx = max(-2, min(2, math.floor(m + 0.5)))
    return _INV_RATING_SCALE[idx]


def rating_direction(prev: Optional[str], curr: Optional[str]) -> str:
    """評等方向：任一 unknown → 'none'（不產生方向事件）；否則 up/down/flat。"""
    p, c = rating_scale(prev), rating_scale(curr)
    if p is None or c is None:
        return "none"
    if c > p:
        return "up"
    if c < p:
        return "down"
    return "flat"


def pct_change(prev: Optional[float], curr: Optional[float]) -> Optional[float]:
    """百分比變化（相對前值絕對值）；缺值或前值為 0 → None。"""
    if prev is None or curr is None or prev == 0:
        return None
    return (curr - prev) / abs(prev) * 100.0


def _sign_direction(prev: Optional[float], curr: Optional[float]) -> str:
    if prev is None or curr is None:
        return "none"
    if curr > prev:
        return "up"
    if curr < prev:
        return "down"
    return "flat"


def quantiles(values: list[Optional[float]]) -> Optional[Quartiles]:
    """中位數 + 上下四分位 + 極值（決定性，內建 statistics，無 numpy）。

    n==1 四值皆等於該值；n>=2 用 inclusive 四分位。空 → None。
    """
    vals = sorted(v for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        v = vals[0]
        return Quartiles(median=v, q1=v, q3=v, low=v, high=v, count=1)
    med = statistics.median(vals)
    try:
        q1, _q2, q3 = statistics.quantiles(vals, n=4, method="inclusive")
    except statistics.StatisticsError:
        q1 = q3 = med
    return Quartiles(median=med, q1=q1, q3=q3, low=vals[0], high=vals[-1], count=len(vals))


def median_pct(pairs: list[tuple[Optional[float], Optional[float]]]) -> tuple[Optional[float], str]:
    """逐筆 (prev, curr) 的 pct_change 取中位數與方向（避免樣本組成偏誤）。"""
    pcts = [p for p in (pct_change(a, b) for a, b in pairs) if p is not None]
    if not pcts:
        return None, "none"
    m = statistics.median(pcts)
    direction = "up" if m > 0 else ("down" if m < 0 else "flat")
    return m, direction


def stance_constructiveness(dimension: str, stance: Optional[str]) -> Optional[int]:
    """某維度某 stance 的建設性序位（+1/0/−1）；未映射 → None。"""
    return STANCE_CONSTRUCTIVENESS.get(dimension, {}).get(stance or "")


def classify_dimension(up: int, down: int, flat: int, n_cmp: int) -> str:
    """四維論點彙總結論：insufficient/stable/diverging/strengthen/weaken。"""
    if n_cmp == 0:
        return "insufficient"
    directional = up + down
    if directional == 0:
        return "stable"
    if up > 0 and down > 0 and min(up, down) >= DIVERGENCE_RATIO * directional:
        return "diverging"
    return "strengthen" if up >= down else "weaken"


def _rating_label(sig: Signal) -> str:
    return sig.rating_raw or RATING_DISPLAY.get(sig.rating_normalized, sig.rating_normalized)


def _fmt_price(value: Optional[float], currency: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    num = f"{value:,.2f}".rstrip("0").rstrip(".")
    return f"{currency} {num}" if currency else num


def diff_signals(prev: Optional[Signal], curr: Signal) -> list[Change]:
    """單券商前後兩份訊號的差異清單（事件卡與時間線共用）。

    prev is None（無前一份）→ 回空清單（呼叫端標「無前次可比較」，不畫箭頭）。
    每欄位依設計規格判可比性：評等含 unknown 不比；目標價須同幣別；EPS 須同分組鍵；
    論點須前後皆有可映射 stance 且 stance 不同才產生事件（相同 stance 不產生）。
    """
    if prev is None:
        return []
    changes: list[Change] = []

    # 評等：兩者皆非 unknown 才比（含 flat，供時間線顯示「維持」）
    if prev.rating_normalized != "unknown" and curr.rating_normalized != "unknown":
        d = rating_direction(prev.rating_normalized, curr.rating_normalized)
        changes.append(
            Change(
                field="rating", dimension=None,
                label=f"{_rating_label(prev)} → {_rating_label(curr)}",
                direction=d, prev_value=_rating_label(prev), curr_value=_rating_label(curr),
                pct_change=None, comparable=True,
            )
        )

    # 目標價：兩者皆有值才比；幣別須相同，否則不可比較
    if prev.target_price is not None and curr.target_price is not None:
        if (
            prev.target_currency
            and curr.target_currency
            and prev.target_currency == curr.target_currency
        ):
            pct = pct_change(prev.target_price, curr.target_price)
            changes.append(
                Change(
                    field="target_price", dimension=None, label="目標價",
                    direction=_sign_direction(prev.target_price, curr.target_price),
                    prev_value=_fmt_price(prev.target_price, prev.target_currency),
                    curr_value=_fmt_price(curr.target_price, curr.target_currency),
                    pct_change=pct, comparable=True,
                )
            )
        else:
            changes.append(
                Change(
                    field="target_price", dimension=None, label="目標價",
                    direction="incomparable",
                    prev_value=_fmt_price(prev.target_price, prev.target_currency),
                    curr_value=_fmt_price(curr.target_price, curr.target_currency),
                    pct_change=None, comparable=False, incomparable_reason="幣別不同",
                )
            )

    # EPS：對 curr 每個分組鍵找 prev 同鍵比較（同 FY/期間/幣別/單位才可比）
    prev_eps = {e.group_key(): e for e in prev.eps if e.value is not None}
    for c in curr.eps:
        if c.value is None:
            continue
        p = prev_eps.get(c.group_key())
        label = f"{c.fiscal_year} {c.period} EPS".strip()
        if p is not None:
            changes.append(
                Change(
                    field="eps", dimension=label, label=label,
                    direction=_sign_direction(p.value, c.value),
                    prev_value=f"{p.value:g}", curr_value=f"{c.value:g}",
                    pct_change=pct_change(p.value, c.value), comparable=True,
                )
            )

    # 四維論點：前後皆有可映射 stance 且 stance 不同才產生事件
    for dim in THESIS_DIMENSIONS:
        pc, cc = prev.thesis.get(dim), curr.thesis.get(dim)
        if not pc or not cc:
            continue
        ps = stance_constructiveness(dim, pc.stance)
        cs = stance_constructiveness(dim, cc.stance)
        if ps is None or cs is None or cc.stance == pc.stance:
            continue
        d = "up" if cs > ps else ("down" if cs < ps else "flat")
        changes.append(
            Change(
                field="thesis", dimension=dim, label=DIM_DISPLAY.get(dim, dim),
                direction=d, prev_value=pc.stance, curr_value=cc.stance,
                pct_change=None, comparable=True,
            )
        )

    return changes


def is_material(change: Change) -> bool:
    """是否構成「近期關鍵變化」事件：可比較且方向為 up/down（flat/none/不可比不算）。"""
    return change.comparable and change.direction in ("up", "down")
