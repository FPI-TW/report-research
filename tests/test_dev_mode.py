# tests/test_dev_mode.py
"""`DEV_NO_AUTH` 開發模式免登入（web/dev_mode.py）。

這個旗標把 deny-by-default 的認證關掉，而**這台機器的 repo root 就是部署目錄**、
外部入口是 Cloudflare Tunnel ＋ nginx。所以測試釘的不是「它能不能放行」，而是
**它在哪些情況下必須不放行**——那三條全是靜默失效型的：

1. 旗標若能從 repo 根 `.env` 生效，任何人 commit 一行 `DEV_NO_AUTH=1` 就永久關掉
   生產站的登入。守門是 import 順序（快照取在 load_env_file 之前），以 AST 靜態驗。
2. nginx 若與 app 同機直連 127.0.0.1，「只要求 loopback」會把外網一併放行。
   守門是代理 header 檢查。
3. 旗標沒開時必須完全沒有這條路徑。
"""
import ast
import ipaddress
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from web import dev_mode  # noqa: E402


class _Req:
    """最小的 request 替身：只有 dev_mode 會碰的三個東西。"""

    def __init__(self, *, peer="127.0.0.1", host="127.0.0.1", headers=None):
        self.client = type("C", (), {"host": peer})() if peer else None
        self.url = type("U", (), {"hostname": host})()
        self.headers = headers or {}


def _with_flag(enabled: bool):
    return mock.patch.object(dev_mode, "_ENABLED", enabled)


class BypassConditionTests(unittest.TestCase):
    def test_flag_off_never_bypasses(self):
        with _with_flag(False):
            self.assertFalse(dev_mode.bypass_allowed(_Req()))

    def test_local_direct_request_bypasses(self):
        with _with_flag(True):
            self.assertTrue(dev_mode.bypass_allowed(_Req()))
            self.assertTrue(dev_mode.bypass_allowed(_Req(peer="::1", host="localhost")))

    def test_non_loopback_peer_rejected(self):
        with _with_flag(True):
            self.assertFalse(dev_mode.bypass_allowed(_Req(peer="192.168.1.20")))
            self.assertFalse(dev_mode.bypass_allowed(_Req(peer="172.17.0.1")))
            self.assertFalse(dev_mode.bypass_allowed(_Req(peer=None)))

    def test_proxy_headers_reject_even_from_loopback(self):
        """nginx 與 app 同機時對端就是 127.0.0.1——loopback 這條單獨並不夠。

        `deploy/nginx.conf` 注入的每一個 header 都要能單獨擋下這條路徑。
        """
        for header in (
            "x-forwarded-for", "x-forwarded-proto", "x-forwarded-host",
            "x-real-ip", "x-edge-secret", "forwarded", "cf-connecting-ip",
        ):
            with self.subTest(header=header), _with_flag(True):
                self.assertFalse(dev_mode.bypass_allowed(_Req(headers={header: "x"})))

    def test_public_host_header_rejected(self):
        """經過邊緣的請求帶的是公開網域（nginx 的 proxy_set_header Host $host）。"""
        with _with_flag(True):
            self.assertFalse(dev_mode.bypass_allowed(_Req(host="research.example.com")))

    def test_nginx_shaped_request_rejected(self):
        """把 deploy/nginx.conf 實際會送出的那組 header 一次全帶上。"""
        with _with_flag(True):
            self.assertFalse(dev_mode.bypass_allowed(_Req(
                peer="127.0.0.1", host="research.example.com",
                headers={
                    "x-real-ip": "203.0.113.9",
                    "x-forwarded-for": "203.0.113.9",
                    "x-forwarded-proto": "https",
                    "x-edge-secret": "s3cret",
                },
            )))


class SnapshotOrderTests(unittest.TestCase):
    """旗標必須在 `.env` 被灌進 os.environ **之前**取快照。"""

    def test_env_file_cannot_enable_it(self):
        """模組已 import 完畢，之後改 os.environ（load_env_file 就是在改它）不生效。"""
        with mock.patch.dict(os.environ, {"DEV_NO_AUTH": "1"}):
            self.assertEqual(dev_mode.enabled(), dev_mode._ENABLED)
            self.assertFalse(dev_mode.enabled())  # 測試行程沒帶旗標啟動

    def test_server_imports_dev_mode_before_load_env_file(self):
        """靜態驗 web/server.py 的順序。

        反過來寫不會壞、不會有錯誤訊息——只會讓 `.env` 裡一行 DEV_NO_AUTH=1
        永久關掉生產站的登入。這種東西只能靠測試釘。
        """
        tree = ast.parse((REPO_ROOT / "web" / "server.py").read_text(encoding="utf-8"))
        import_line = None
        call_line = None
        for node in ast.walk(tree):
            if (
                import_line is None
                and isinstance(node, ast.ImportFrom)
                and node.module == "web"
                and any(a.name == "dev_mode" for a in node.names)
            ):
                import_line = node.lineno
            if (
                call_line is None
                and isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "load_env_file"
            ):
                call_line = node.lineno
        self.assertIsNotNone(import_line, "web/server.py 沒有 import web.dev_mode")
        self.assertIsNotNone(call_line, "web/server.py 沒有呼叫 load_env_file")
        self.assertLess(import_line, call_line)

    def test_no_logging_at_import_time(self):
        """dev_mode 排在 configure_logging 之前，所以它 import 期不得寫日誌。

        `tests/test_logging_setup.py` 的「服務模組必須晚於 configure_logging」對它
        開了豁免；這條是那個豁免的對價。唯一的 logger 呼叫在 log_banner() 內，
        由 lifespan 呼叫——那時 logging 早已設定好。
        """
        tree = ast.parse((REPO_ROOT / "web" / "dev_mode.py").read_text(encoding="utf-8"))
        for node in tree.body:  # 只看模組層級（函式內的呼叫是執行期，不在此列）
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue  # docstring
            self.assertNotIsInstance(node, ast.Expr, "模組層級不得有裸呼叫（含 logging）")

    def test_flag_not_in_env_example(self):
        """刻意不寫進 .env.example：那份檔案的用途正是「複製成 .env」。

        比照 CLAUDE_LOCK_DISABLE（同樣是逃生口、同樣不入 .env.example）。
        """
        text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertNotIn("DEV_NO_AUTH", text)


class LoopbackHelperTests(unittest.TestCase):
    def test_loopback_detection(self):
        for host in ("127.0.0.1", "127.0.0.53", "::1", "localhost"):
            self.assertTrue(dev_mode._is_loopback(host), host)
        for host in ("192.168.1.1", "10.0.0.1", "172.17.0.1", "example.com", "", None):
            self.assertFalse(dev_mode._is_loopback(host), host)

    def test_docker_bridge_is_not_loopback(self):
        """外網走 cloudflared → nginx → host.docker.internal，對端是橋接位址。"""
        self.assertFalse(ipaddress.ip_address("172.17.0.1").is_loopback)


class MiddlewareBypassTests(unittest.TestCase):
    """走 **HTTP 層**驗中介層真的接上了（不是只有 dev_mode 這支函式對）。

    本 repo 兩度在「單元測試對、端點錯」上出事（Pydantic 靜默 strip、裝飾器套錯
    對象），認證這條更不能只驗函式物件。
    """

    @staticmethod
    def _client(**kw):
        from fastapi.testclient import TestClient

        from web.server import app

        return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1", **kw)

    def test_flag_off_still_redirects_to_login(self):
        with _with_flag(False):
            r = self._client().get("/app/ask")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["location"], "/login")

    def test_flag_off_api_still_401(self):
        with _with_flag(False):
            r = self._client().get("/api/markets")
        self.assertEqual(r.status_code, 401)

    def test_local_request_passes_without_cookie(self):
        with _with_flag(True):
            r = self._client(client=("127.0.0.1", 51000)).get("/api/markets")
        self.assertNotIn(r.status_code, (302, 401))
        # 放行是這一個請求的事，不留下可帶走的憑證。
        self.assertNotIn("set-cookie", {k.lower() for k in r.headers})

    def test_proxied_request_still_requires_login(self):
        with _with_flag(True):
            r = self._client(client=("127.0.0.1", 51000)).get(
                "/api/markets", headers={"X-Forwarded-For": "203.0.113.9"}
            )
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()
