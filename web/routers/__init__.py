"""web/server.py 拆出的 APIRouter 模組。

每個模組匯出一個 `router`，由 web/server.py 以 include_router 掛載。
共用/可 mock 的依賴一律走 web.deps（見 web/deps.py），router 不從 web.server 匯入。
"""
