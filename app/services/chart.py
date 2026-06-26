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


def _ymax(series: list) -> float:
    m = max((max(s["values"]) for s in series), default=0)
    return m * 1.15 if m > 0 else 1.0


def _axes(ymax: float) -> str:
    out = [
        f'<line x1="{_PAD_L}" y1="{_PAD_T}" x2="{_PAD_L}" y2="{_BASE_Y}" stroke="#ccc"/>',
        f'<line x1="{_PAD_L}" y1="{_BASE_Y}" x2="{_W - _PAD_R}" y2="{_BASE_Y}" stroke="#ccc"/>',
    ]
    for i in range(3):
        frac = i / 2.0
        y = _BASE_Y - _PLOT_H * frac
        out.append(
            f'<text x="{_PAD_L - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="10" '
            f'fill="#888">{ymax * frac:.0f}</text>'
        )
        if i:
            out.append(
                f'<line x1="{_PAD_L}" y1="{y:.1f}" x2="{_W - _PAD_R}" y2="{y:.1f}" stroke="#eee"/>'
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
    ymax = _ymax(series)
    n, ns = len(x), len(series)
    step = _PLOT_W / n
    group_w = step * 0.7
    bar_w = group_w / ns
    out = [_axes(ymax), _x_labels(x)]
    for si, s in enumerate(series):
        c = _PALETTE[si % len(_PALETTE)]
        vals = s["values"]
        for i in range(min(n, len(vals))):
            v = vals[i]
            h = _PLOT_H * (v / ymax) if ymax else 0
            gx = _PAD_L + step * i + (step - group_w) / 2
            bx = gx + bar_w * si
            by = _BASE_Y - h
            out.append(
                f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bar_w:.1f}" height="{h:.1f}" fill="{c}"/>'
            )
            if ns == 1:
                out.append(
                    f'<text x="{bx + bar_w / 2:.1f}" y="{by - 4:.1f}" text-anchor="middle" '
                    f'font-size="9" fill="#555">{v:g}</text>'
                )
    out.append(_legend(series))
    return "".join(out)


def _line(spec: dict) -> str:
    x, series = spec["x"], spec["series"]
    ymax = _ymax(series)
    n = len(x)
    step = _PLOT_W / n
    out = [_axes(ymax), _x_labels(x)]
    for si, s in enumerate(series):
        c = _PALETTE[si % len(_PALETTE)]
        vals = s["values"]
        pts = []
        for i in range(min(n, len(vals))):
            cx = _PAD_L + step * (i + 0.5)
            cy = _BASE_Y - _PLOT_H * (vals[i] / ymax if ymax else 0)
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
    total = sum(vals[:n])
    if total <= 0:
        return ""
    cx, cy = _PAD_L + _PLOT_W / 2, _PAD_T + _PLOT_H / 2
    r = min(_PLOT_W, _PLOT_H) / 2.2
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
