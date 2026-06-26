"""把研報圖規格畫成 inline SVG（純函式、無 I/O、無第三方依賴）。

WeasyPrint 渲染 inline <svg>，文字沿用既有 fontconfig 的 Noto CJK（不需 matplotlib
那套獨立字型登記）。支援 bar / line / pie 三型；規格壞或數據缺 → 回 ""（呼叫端略過）。
"""

from __future__ import annotations

import html as _html
import math

_W, _H = 640, 380
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 72, 28, 48, 64
_PLOT_W = _W - _PAD_L - _PAD_R
_PLOT_H = _H - _PAD_T - _PAD_B
_BASE_Y = _PAD_T + _PLOT_H
_PALETTE = ["#AE7415", "#3B6EA5", "#5A9E6F", "#B5573F", "#7A6AA8", "#C79A3E"]
_STYLE = '<style>text{font-family:"Noto Sans CJK TC","Noto Sans TC",sans-serif;}</style>'


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _valid(spec) -> bool:
    if not isinstance(spec, dict) or spec.get("type") not in ("bar", "line", "pie"):
        return False
    x = spec.get("x")
    series = spec.get("series")
    if not isinstance(x, list) or not x:
        return False
    if not isinstance(series, list) or not series:
        return False
    for s in series:
        if not isinstance(s, dict):
            return False
        vals = s.get("values")
        if not isinstance(vals, list) or not vals or not all(_is_num(v) for v in vals):
            return False
    return True


def _esc(s) -> str:
    return _html.escape(str(s))


def _yrange(series: list) -> tuple[float, float]:
    """回 (lo, hi) y 軸範圍，恆含 0（零基線），支援負值。"""
    allv = [v for s in series for v in s["values"]]
    lo = min(0.0, min(allv))
    hi = max(0.0, max(allv))
    span = (hi - lo) or 1.0
    hi += span * 0.12  # 頂部留白給數值標籤
    if lo < 0:
        lo -= span * 0.12  # 有負值時底部也留白
    return lo, hi


def _ymap(v: float, lo: float, hi: float) -> float:
    """數值 → SVG y 座標（lo 在底、hi 在頂）。"""
    return _BASE_Y - _PLOT_H * (v - lo) / (hi - lo)


def _axes(lo: float, hi: float) -> str:
    zero_y = _ymap(0.0, lo, hi)
    out = [
        f'<line x1="{_PAD_L}" y1="{_PAD_T}" x2="{_PAD_L}" y2="{_BASE_Y}" stroke="#ccc"/>',
        f'<line x1="{_PAD_L}" y1="{zero_y:.1f}" x2="{_W - _PAD_R}" y2="{zero_y:.1f}" stroke="#ccc"/>',
    ]
    for val in (lo, 0.0, hi):
        y = _ymap(val, lo, hi)
        out.append(
            f'<text x="{_PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10" '
            f'fill="#888">{val:.0f}</text>'
        )
    return "".join(out)


def _x_labels(x: list) -> str:
    step = _PLOT_W / len(x)
    out = []
    for i, lab in enumerate(x):
        cx = _PAD_L + step * (i + 0.5)
        out.append(
            f'<text x="{cx:.1f}" y="{_BASE_Y + 18:.0f}" text-anchor="middle" '
            f'font-size="10" fill="#555">{_esc(lab)}</text>'
        )
    return "".join(out)


def _legend(series: list) -> str:
    if len(series) < 2:
        return ""
    out = []
    for i, s in enumerate(series):
        c = _PALETTE[i % len(_PALETTE)]
        lx = _PAD_L + i * 120
        out.append(f'<rect x="{lx}" y="{_H - 22}" width="10" height="10" fill="{c}"/>')
        out.append(
            f'<text x="{lx + 14}" y="{_H - 13}" font-size="10" fill="#555">'
            f'{_esc(s.get("name") or "")}</text>'
        )
    return "".join(out)


def _bar(spec: dict) -> str:
    x, series = spec["x"], spec["series"]
    lo, hi = _yrange(series)
    zero_y = _ymap(0.0, lo, hi)
    n, ns = len(x), len(series)
    step = _PLOT_W / n
    group_w = step * 0.7
    bar_w = group_w / ns
    out = [_axes(lo, hi), _x_labels(x)]
    for si, s in enumerate(series):
        c = _PALETTE[si % len(_PALETTE)]
        vals = s["values"]
        for i in range(min(n, len(vals))):
            v = vals[i]
            yv = _ymap(v, lo, hi)
            top = min(zero_y, yv)
            h = abs(yv - zero_y)
            gx = _PAD_L + step * i + (step - group_w) / 2
            bx = gx + bar_w * si
            out.append(
                f'<rect x="{bx:.1f}" y="{top:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{c}"/>'
            )
            if ns == 1:
                ty = top - 4 if v >= 0 else top + h + 10
                out.append(
                    f'<text x="{bx + bar_w / 2:.1f}" y="{ty:.1f}" text-anchor="middle" '
                    f'font-size="9" fill="#555">{v:g}</text>'
                )
    out.append(_legend(series))
    return "".join(out)


def _line(spec: dict) -> str:
    x, series = spec["x"], spec["series"]
    lo, hi = _yrange(series)
    n = len(x)
    step = _PLOT_W / n
    out = [_axes(lo, hi), _x_labels(x)]
    for si, s in enumerate(series):
        c = _PALETTE[si % len(_PALETTE)]
        vals = s["values"]
        pts = []
        for i in range(min(n, len(vals))):
            cx = _PAD_L + step * (i + 0.5)
            cy = _ymap(vals[i], lo, hi)
            pts.append((cx, cy))
        out.append(
            f'<polyline fill="none" stroke="{c}" stroke-width="2" '
            f'points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in pts)}"/>'
        )
        for px, py in pts:
            out.append(f'<circle cx="{px:.1f}" cy="{py:.1f}" r="3" fill="{c}"/>')
    out.append(_legend(series))
    return "".join(out)


def _pie(spec: dict) -> str:
    x = spec["x"]
    vals = spec["series"][0]["values"]
    n = min(len(x), len(vals))
    if any(v < 0 for v in vals[:n]):
        return ""  # 圓餅為組成佔比，負值無意義 → 略過
    total = sum(vals[:n])
    if total <= 0:
        return ""
    cx, cy = _PAD_L + _PLOT_W / 2, _PAD_T + _PLOT_H / 2
    r = min(_PLOT_W, _PLOT_H) / 2.2
    if n == 1:  # 單一 100% → 整圓（退化弧線不可見）
        c0 = _PALETTE[0]
        return (
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{c0}"/>'
            f'<rect x="{_W - _PAD_R - 110}" y="{_PAD_T}" width="10" height="10" fill="{c0}"/>'
            f'<text x="{_W - _PAD_R - 96}" y="{_PAD_T + 9}" font-size="10" fill="#555">'
            f'{_esc(x[0])} 100%</text>'
        )
    out = []
    ang = -math.pi / 2
    for i in range(n):
        frac = vals[i] / total
        a2 = ang + frac * 2 * math.pi
        x1, y1 = cx + r * math.cos(ang), cy + r * math.sin(ang)
        x2, y2 = cx + r * math.cos(a2), cy + r * math.sin(a2)
        large = 1 if frac > 0.5 else 0
        c = _PALETTE[i % len(_PALETTE)]
        out.append(
            f'<path d="M{cx:.1f},{cy:.1f} L{x1:.1f},{y1:.1f} '
            f'A{r:.1f},{r:.1f} 0 {large} 1 {x2:.1f},{y2:.1f} Z" fill="{c}"/>'
        )
        ly = _PAD_T + i * 18
        out.append(f'<rect x="{_W - _PAD_R - 110}" y="{ly}" width="10" height="10" fill="{c}"/>')
        out.append(
            f'<text x="{_W - _PAD_R - 96}" y="{ly + 9}" font-size="10" fill="#555">'
            f'{_esc(x[i])} {frac * 100:.0f}%</text>'
        )
        ang = a2
    return "".join(out)


def render_chart_svg(spec: dict) -> str:
    """圖規格 → inline SVG 字串；不支援/缺漏 → ""。"""
    if not _valid(spec):
        return ""
    kind = spec["type"]
    body = {"bar": _bar, "line": _line, "pie": _pie}[kind](spec)
    if not body:
        return ""
    title = _esc(spec.get("title") or "")
    title_el = (
        f'<text x="{_W / 2:.0f}" y="26" text-anchor="middle" font-size="14" '
        f'fill="#1a1a1a">{title}</text>'
        if title
        else ""
    )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_W} {_H}" '
        f'width="{_W}" height="{_H}">{_STYLE}{title_el}{body}</svg>'
    )
