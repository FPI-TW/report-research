# tests/test_pre_split_guards.py
"""拆分 web/server.py 前補上的兩條回歸線：兩者目前都零覆蓋，且失效時是「安靜地錯」。

1. `/app/assets/*` 必須由 StaticFiles mount 服務，不能落到 SPA catch-all
   `/app/{spa_path:path}`。順序反了每個 Vite hash 資產都會回傳 index.html
   （HTTP 200、Content-Type text/html），SPA 整頁白掉——沒有任何例外、沒有
   4xx，只有瀏覽器 console 一句 MIME 錯誤。既有 test_spa_serving.py 只驗
   require_login 對 assets 放行，沒驗 mount 實際回應。

2. `/api/qa/{root_qa_id}/versions` 的 `_valid_uuid` 守衛。`_valid_uuid` 同時被
   ask/report/qa_versions 三處使用，前兩者有測試、這條沒有。拆分時若把
   `_valid_uuid` 跟著 ask 搬走，這條會 NameError 而無人察覺。
   （tests/test_answer.py 測的是服務層 list_qa_versions，不經過端點。）
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from web import deps  # noqa: E402
from web.server import app  # noqa: E402


def _authed_client():
    client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
    r = client.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, f"login failed: {r.status_code}"
    return client


# ── 1. assets mount 必須贏過 SPA catch-all ──────────────────────────────
#
# mount 是條件式的（frontend/dist/assets 存在才註冊），而 CI 不建置 dist，
# 直接對已匯入的 app 斷言會在 CI 變成 skip＝沒有保護。故沿用本 repo 既有的
# subprocess 手法（見 test_env_loading.py）：先造出 dist 骨架，再於子行程
# 全新匯入 web.server，讓 mount 真的被註冊後才驗行為。

_PROBE_JS = "export const probe = 1;\n"

_SUBPROCESS_PROBE = """
import os, sys
sys.path.insert(0, os.getcwd())
from fastapi.testclient import TestClient
import web.server as server

client = TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1")
r = client.get("/app/assets/probe.js")
print("STATUS", r.status_code)
print("CTYPE", r.headers.get("content-type", ""))
print("BODY", repr(r.text))
"""


class AssetsMountPrecedesSpaCatchAllTests(unittest.TestCase):
    def setUp(self):
        self.dist = REPO_ROOT / "frontend" / "dist"
        self.assets = self.dist / "assets"
        self.probe = self.assets / "probe.js"
        self.index = self.dist / "index.html"
        # 只記錄「我們建立的」路徑，tearDown 精準還原，不動既有 dist 產物。
        self._created_probe = not self.probe.exists()
        self._created_index = not self.index.exists()
        self._created_assets = not self.assets.exists()
        self._created_dist = not self.dist.exists()
        self.assets.mkdir(parents=True, exist_ok=True)
        if self._created_probe:
            self.probe.write_text(_PROBE_JS, encoding="utf-8")
        if self._created_index:
            self.index.write_text("<!doctype html><title>spa shell</title>", encoding="utf-8")

    def tearDown(self):
        if self._created_probe and self.probe.exists():
            self.probe.unlink()
        if self._created_index and self.index.exists():
            self.index.unlink()
        if self._created_assets and self.assets.exists() and not any(self.assets.iterdir()):
            self.assets.rmdir()
        if self._created_dist and self.dist.exists() and not any(self.dist.iterdir()):
            self.dist.rmdir()

    def test_assets_served_by_mount_not_spa_shell(self):
        env = os.environ.copy()
        env.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
        env.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
        env.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")
        proc = subprocess.run(
            [sys.executable, "-c", _SUBPROCESS_PROBE],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = proc.stdout

        self.assertIn("STATUS 200", out, f"assets 未被服務：\n{out}\n{proc.stderr}")
        # 這行才是重點：catch-all 搶走時 status 也是 200，只有內容會變成 shell。
        self.assertIn("probe", out, f"/app/assets/probe.js 回傳的不是資產本身：\n{out}")
        self.assertNotIn(
            "spa shell",
            out,
            "SPA catch-all 搶走了 /app/assets/*——mount 必須註冊在 "
            "/app/{spa_path:path} 之前，否則所有 Vite hash 資產都會回傳 index.html",
        )
        self.assertNotIn(
            "text/html",
            out,
            "/app/assets/*.js 的 Content-Type 是 text/html＝被 SPA shell 攔截",
        )


# ── 2. qa_versions 的 _valid_uuid 守衛 ──────────────────────────────────


class QaVersionsUuidGuardTests(unittest.TestCase):
    """`_valid_uuid` 被 ask/qa_versions 共用，只有這條沒被測到。

    此端點對非法 uuid 回 404（回 400 的是 POST 端點）。
    但「不存在的合法 uuid」同樣會是 404，所以只斷言狀態碼證明不了守衛生效——
    改以哨兵確認**根本沒走到查詢層**，這才鎖得住 `_valid_uuid` 真的擋在前面。
    """

    def setUp(self):
        import web.server as server

        self.server = server
        self._orig = deps.list_qa_versions
        self.called = []

        async def _sentinel(*a, **kw):
            self.called.append(a)
            raise AssertionError("非法 uuid 不該走到 list_qa_versions")

        deps.list_qa_versions = _sentinel

    def tearDown(self):
        # **必須還原到 patch 的那個物件**（deps），不是 self.server。
        # 原本寫成 `self.server.list_qa_versions = self._orig`：deps 上的 sentinel
        # 永遠留著、web.server 還憑空長出一個同名屬性。已同進程實測坐實——
        # 測試本身 OK，但跑完後 deps.list_qa_versions 仍是會 raise 的 sentinel，
        # 之後任何走 /api/qa/{id}/versions 成功路徑的測試都會拿到與自身無關的失敗
        # （而該成功路徑目前零覆蓋，所以今天沒炸只是運氣）。
        deps.list_qa_versions = self._orig

    def test_invalid_root_qa_id_rejected_before_query(self):
        client = _authed_client()
        for bad in ["not-a-uuid", "1234", "123e4567-e89b-12d3-a456-42661417400"]:
            with self.subTest(bad=bad):
                r = client.get(f"/api/qa/{bad}/versions")
                self.assertEqual(
                    r.status_code,
                    404,
                    f"{bad!r} 應被 _valid_uuid 擋下（得到 {r.status_code}）",
                )
        self.assertEqual(
            self.called, [], "守衛失效：非法 uuid 被放行到 list_qa_versions"
        )

    def test_unauthenticated_is_rejected(self):
        client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        r = client.get("/api/qa/123e4567-e89b-12d3-a456-426614174000/versions")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()


class PatchHygieneTests(unittest.TestCase):
    """守門：上面那組測試不得把 sentinel 留在 deps 上。

    2026-07-28 實測：tearDown 還原到 self.server 而非 deps，跑完後
    deps.list_qa_versions 仍是 sentinel。這種汙染的症狀是「別人的測試莫名失敗、
    單檔重現不了」，排查成本遠高於寫這條防護。
    """

    def test_deps_symbol_is_not_left_patched(self):
        self.assertNotEqual(
            getattr(deps.list_qa_versions, "__name__", ""), "_sentinel",
            "deps.list_qa_versions 被留成 sentinel（tearDown 還原到錯的物件）",
        )

    def test_server_has_no_stray_attribute(self):
        """web.server 不該有 list_qa_versions —— 那是還原到錯物件時憑空長出來的。"""
        import web.server as server

        self.assertFalse(
            hasattr(server, "list_qa_versions"),
            "web.server 憑空長出 list_qa_versions（tearDown 寫錯物件的痕跡）",
        )
