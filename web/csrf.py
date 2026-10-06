"""CSRF／Origin 檢查：會改變狀態的請求（POST／PUT／PATCH／DELETE）必須來自本站。

session cookie 是 `SameSite=Lax`，已擋掉多數跨站 POST，但 Lax 有已知缺口（例如同站不同子網域、
瀏覽器相容性）。這裡再加一道與瀏覽器行為無關的判斷：

- 有 `Origin`：它的 host[:port] 必須等於請求的 `Host`。反向代理（nginx）以 `proxy_set_header Host
  $host` 保留原始 Host，Vite 開發代理沒設 changeOrigin，兩者都會讓同站請求的兩邊一致。
  `Origin: null`（沙箱 iframe、隱私重導）一律拒絕。
- 沒有 `Origin`：只看 `Sec-Fetch-Site`，`cross-site`／`same-site` 拒絕。兩個標頭都沒有＝不是
  瀏覽器發的（curl、批次腳本、壓測、測試），CSRF 的前提不存在，放行。

刻意**沒有**可設定的允許清單：多一個旋鈕就多一種「為了讓某個工具能打而打開」的方式。
"""

from __future__ import annotations

from urllib.parse import urlsplit

UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def origin_problem(method: str, headers) -> str | None:
    """回 None＝放行；否則回拒絕理由（只進日誌，不回給呼叫端）。headers 是不分大小寫的 mapping。"""
    if method.upper() not in UNSAFE_METHODS:
        return None
    host = (headers.get("host") or "").strip().lower()
    origin = headers.get("origin")
    if origin is not None:
        origin = origin.strip()
        if origin.lower() == "null":
            return "Origin 為 null"
        netloc = urlsplit(origin).netloc.lower()
        if not netloc or netloc != host:
            return f"Origin {origin!r} 與 Host {host!r} 不符"
        return None
    site = (headers.get("sec-fetch-site") or "").strip().lower()
    if site in {"cross-site", "same-site"}:
        return f"Sec-Fetch-Site={site} 且沒有 Origin"
    return None
