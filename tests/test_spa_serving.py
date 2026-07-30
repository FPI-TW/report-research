"""SPA 服務行為：登入導向、deep-link 殼、資產快取標頭。

**為什麼這裡不再無條件 skip**：`frontend/dist` 不存在時原本直接 `pytest.skip`，而 CI
從來不跑 `npm run build`，所以這條在 CI 永遠是 skip、在本機只有「剛好 build 過」時
才跑——等於沒有守門。現在 CI 的前端 job 會 build 並把 dist 當 artifact 傳給後端 job，
所以缺 dist 一律視為**環境沒準備好＝失敗**，只有明確設了 `SKIP_SPA_TESTS` 才跳過
（給「只想跑後端邏輯、不想裝 node」的本機情境一條明路，但那是自己選的，不是預設）。

與 `tests/test_pre_split_guards.py` 的分工：那支用**合成的** dist 骨架＋子行程重新
匯入 `web.server`，守的是「`/app/assets` mount 必須贏過 `/app/{spa_path}` catch-all」
這條順序（合成骨架就足以驗）。這裡守的是另一半——**真 build 產物**的服務行為與
快取契約（deep-link 回 index.html + no-cache、雜湊資產 immutable），那半隻能對真
產物驗：檔名是 Vite 算的內容雜湊，合成不出來。
"""
import os
import time
import unittest
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

from web import auth
from web.server import SPA_DIST, app, require_login

_SKIP_ENV = "SKIP_SPA_TESTS"


def _auth_cookies() -> dict[str, str]:
    return {auth.COOKIE_NAME: auth.issue_token(int(time.time()))}


def _require_dist() -> None:
    if (SPA_DIST / "index.html").is_file():
        return
    if os.getenv(_SKIP_ENV):
        pytest.skip(f"{_SKIP_ENV} 已設定，跳過需要 frontend/dist 的 SPA 服務測試")
    pytest.fail(
        "frontend/dist 不存在，SPA 服務契約無法驗證。請先 `make build-web`"
        f"（或 cd frontend && npm run build）；真的要跳過請設 {_SKIP_ENV}=1。"
    )


def _first_hashed_asset() -> Path:
    """挑一個 Vite 產出的雜湊資產（檔名帶內容雜湊，故只能從真產物取）。"""
    assets = sorted((SPA_DIST / "assets").glob("*.js"))
    if not assets:
        pytest.fail("frontend/dist/assets 沒有任何 .js —— build 產物不完整")
    return assets[0]


def test_unauthed_app_redirects_to_login():
    client = TestClient(app)
    resp = client.get("/app/search", follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/login"


def test_authed_app_deeplink_serves_shell():
    _require_dist()
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get("/app/ask", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    # 殼一律 no-cache：資產名帶雜湊可以長快取，index.html 不行——快取住的殼會
    # 一直指向上一版的資產檔名，部署後使用者拿到 404 的 chunk 而整頁白掉。
    assert resp.headers.get("cache-control") == "no-cache"


def test_hashed_assets_served_with_immutable_cache():
    """真 build 產物的資產必須由 mount 服務、且帶 immutable 長快取。

    兩種失效都不會有例外：mount 沒註冊 → 落到 catch-all 拿到 index.html
    （200 + text/html，瀏覽器只在 console 抱怨 MIME）；`_ImmutableStatic` 被換回
    普通 `StaticFiles` → 每次部署後所有使用者重抓全部 JS/CSS，沒有人會發現。
    """
    _require_dist()
    asset = _first_hashed_asset()
    client = TestClient(app, cookies=_auth_cookies())
    resp = client.get(f"/app/assets/{asset.name}", follow_redirects=False)
    assert resp.status_code == 200
    assert "text/html" not in resp.headers.get("content-type", "")
    assert "immutable" in resp.headers.get("cache-control", "")


class SpaAssetAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_require_login_allows_spa_assets_without_auth(self):
        seen = {"called": False}

        async def call_next(_request: Request):
            seen["called"] = True
            return Response("missing", status_code=404, media_type="text/plain")

        req = Request({
            "type": "http",
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/app/assets/chunk.js",
            "raw_path": b"/app/assets/chunk.js",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        })

        resp = await require_login(req, call_next)

        self.assertTrue(seen["called"])
        self.assertEqual(resp.status_code, 404)
