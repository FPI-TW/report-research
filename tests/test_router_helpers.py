"""`web/routers/` 每一支 router：底線開頭的輔助函式不得成為端點（結構檢查，不打 HTTP）。

2026-07-28 事故（見 `tests/test_monitor_http.py` 的 docstring）：輔助函式被夾在 `@router.get(...)`
與 handler 的 `def` 之間，裝飾器套到輔助函式上，端點對正常請求回 422；直呼 handler 的測試照樣綠。
該檔只守 monitor 一支，本檔把同一條守門一般化到每一支 router 模組：模組裡任何 `_` 開頭的
callable 都不能是 app 上任何路由的 endpoint。

正向對照是這條守門有沒有在空轉的唯一證據：FastAPI 0.137 起 `app.routes` 是樹，直接迭代只看得到
`/docs` 那幾條，offenders 恆為空。所以每支有路由的 router 都必須在攤平後的 app 路由裡看得到
自己至少一個 endpoint——這同時抓得到「寫了 router 卻沒 include 進 app」。
"""

from __future__ import annotations

import importlib
import os
import pkgutil
import sys
import unittest
from pathlib import Path

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import web.routers  # noqa: E402
from web.server import app  # noqa: E402

try:
    from fastapi.routing import iter_route_contexts  # FastAPI ≥ 0.137.2
except ImportError:  # 更舊的版本：routes 本身就是攤平的清單
    iter_route_contexts = None

# 寫這支測試時 web/routers/ 有 31 支 router 模組；下限防「列舉模組的方式壞掉、迴圈零次照樣綠」。
MIN_ROUTER_MODULES = 25


def _flat_routes(routes) -> list:
    """攤平後的有效路由（同 `tests/test_monitor_http.py` 的 `_app_routes`，理由見該處 docstring）。"""
    if iter_route_contexts is None:
        return list(routes)
    return list(iter_route_contexts(routes))


def _endpoints(routes) -> list:
    return [ep for ep in (getattr(r, "endpoint", None) for r in _flat_routes(routes)) if ep is not None]


def _router_modules() -> list:
    names = sorted(m.name for m in pkgutil.iter_modules(web.routers.__path__) if not m.name.startswith("__"))
    return [importlib.import_module(f"web.routers.{name}") for name in names]


def _contains(items, obj) -> bool:
    """以物件身分比對：有些 callable（例如帶 `__eq__` 的類別實例）不可雜湊或會誤判相等。"""
    return any(obj is it for it in items)


class RouterHelperIsNotEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app_endpoints = _endpoints(app.routes)
        cls.modules = _router_modules()

    def test_enumerates_every_router_module(self):
        self.assertGreaterEqual(
            len(self.modules), MIN_ROUTER_MODULES, f"只列舉到 {len(self.modules)} 支 router 模組：列舉方式可能失效"
        )
        missing_router = [m.__name__ for m in self.modules if getattr(m, "router", None) is None]
        self.assertEqual([], missing_router, "web/routers/ 底下的模組都應該有模組層的 `router`")

    def test_every_router_is_visible_in_app(self):
        """正向對照：每支有路由的 router，攤平後的 app 路由裡至少看得到它一個 endpoint。"""
        checked = 0
        for mod in self.modules:
            own = _endpoints(mod.router.routes)
            if not own:
                continue
            checked += 1
            with self.subTest(router=mod.__name__):
                self.assertTrue(
                    any(_contains(self.app_endpoints, ep) for ep in own),
                    f"{mod.__name__} 有 {len(own)} 條路由，但攤平後的 app 路由一條都看不到"
                    "（沒 include 進 app，或路由列舉方式失效）",
                )
        self.assertGreaterEqual(checked, MIN_ROUTER_MODULES, f"只有 {checked} 支 router 有路由，列舉方式可能失效")

    def test_no_private_helper_is_an_endpoint(self):
        for mod in self.modules:
            with self.subTest(router=mod.__name__):
                offenders = sorted(
                    name
                    for name, obj in vars(mod).items()
                    if name.startswith("_") and callable(obj) and _contains(self.app_endpoints, obj)
                )
                self.assertEqual(
                    [], offenders,
                    f"{mod.__name__} 的輔助函式被裝飾成端點"
                    f"（多半是夾在 @router.* 與 handler 的 def 之間）：{offenders}",
                )

    def test_guard_catches_a_misplaced_helper(self):
        """對照組：把輔助函式夾在裝飾器與 handler 之間，本檔的判定要抓得到。"""
        from fastapi import APIRouter, FastAPI

        router = APIRouter()

        @router.get("/x")
        def _helper(done: int):  # 裝飾器套到這裡（事故的形狀）
            return done

        def handler():
            return _helper(1)

        probe = FastAPI()
        probe.include_router(router)
        endpoints = _endpoints(probe.routes)
        self.assertTrue(_contains(endpoints, _helper))
        self.assertFalse(_contains(endpoints, handler))
