"""HTTP 層：另一份 profile（tempfile 建立）下的 /api/branding、asset 端點、登入頁、prompt 注入與市場子集。

廷豐 profile 的對照放在每組的最後一題：預設 profile 的行為與改動前相同（名稱、logo、全部市場啟用）。
"""

import os
import sys
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import profile as P  # noqa: E402
from app.services import answer as ans  # noqa: E402
from app.services import api_clients  # noqa: E402
from app.services import retrieval_pipeline as rp  # noqa: E402
from app.services import scope_router as sr  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402
from app.services.tagging import MARKETS  # noqa: E402
from web.server import app  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"acme-logo" * 8
DARK_PNG = b"\x89PNG\r\n\x1a\n" + b"acme-dark" * 8
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64"/></svg>'

ACME = """
[institution]
name = "艾克米"
product_name = "艾克米研究平台"
short_name = "艾克米研報"

[branding]
logo_light = "logo.png"
logo_dark = "logo-dark.png"
favicon = "favicon.svg"

[branding.colors.light]
accent_strong = "#1f5fbf"
accent_text = "#174a96"

[branding.colors.dark]
accent_strong = "rgba(120, 170, 255, 0.9)"

[prompt]
system_name = "艾克米研報"
system_name_en = "ACME Research"

[markets]
enabled = ["US", "TW"]

[markets.display_names]
US = "美國股市"

[security]
totp_issuer = "ACME Research"
"""


def _anon() -> TestClient:
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


def _login() -> TestClient:
    client = _anon()
    r = client.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, r.status_code
    return client


class AcmeProfileCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "acme"
        (root / "assets").mkdir(parents=True)
        (root / "profile.toml").write_text(textwrap.dedent(ACME), encoding="utf-8")
        (root / "assets" / "logo.png").write_bytes(PNG)
        (root / "assets" / "logo-dark.png").write_bytes(DARK_PNG)
        (root / "assets" / "favicon.svg").write_bytes(SVG)
        (root / "assets" / "unreferenced.png").write_bytes(PNG)  # 在 assets/ 裡但沒被引用：不可交出
        self.root = root
        self._env = patch.dict(os.environ, {"PROFILE_DIR": str(root)})
        self._env.start()
        P.reset_profile_cache()

    def tearDown(self):
        self._env.stop()
        P.reset_profile_cache()
        self._tmp.cleanup()


class BrandingEndpointTests(AcmeProfileCase):
    def test_branding_is_public_and_display_only(self):
        r = _anon().get("/api/branding")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["cache-control"], "no-cache")
        body = r.json()
        self.assertEqual(set(body), {"institution_name", "product_name", "short_name", "logo_light_url",
                                     "logo_dark_url", "favicon_url", "colors", "institution_name_en",
                                     "product_name_en", "short_name_en", "locale"})
        self.assertEqual((body["institution_name"], body["product_name"], body["short_name"]),
                         ("艾克米", "艾克米研究平台", "艾克米研報"))
        # 沒給英文名稱：英文介面沿用原名；[locale] 缺席＝兩種語系、預設中文。
        self.assertEqual((body["institution_name_en"], body["product_name_en"], body["short_name_en"]),
                         ("艾克米", "艾克米研究平台", "艾克米研報"))
        self.assertEqual(body["locale"], {"default": "zh-Hant", "available": ["zh-Hant", "en"]})
        self.assertEqual(body["colors"], {"light": {"accent_strong": "#1f5fbf", "accent_text": "#174a96"},
                                          "dark": {"accent_strong": "rgba(120, 170, 255, 0.9)"}})
        self.assertTrue(body["logo_light_url"].startswith("/api/branding/assets/logo.png?v="))
        self.assertTrue(body["logo_dark_url"].startswith("/api/branding/assets/logo-dark.png?v="))
        self.assertTrue(body["favicon_url"].startswith("/api/branding/assets/favicon.svg?v="))
        # 其他設定（prompt 參數、市場子集、TOTP 發行者）一律不出現在免登入的回應裡。
        self.assertNotIn("ACME Research", r.text)
        self.assertNotIn("US", r.text.replace("logo", ""))

    def test_asset_versioned_url_is_immutable(self):
        url = _anon().get("/api/branding").json()["logo_light_url"]
        r = _anon().get(url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, PNG)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertEqual(r.headers["cache-control"], "public, max-age=31536000, immutable")
        self.assertEqual(r.headers["x-content-type-options"], "nosniff")
        self.assertIn("sandbox", r.headers["content-security-policy"])
        etag = r.headers["etag"]
        again = _anon().get(url, headers={"If-None-Match": etag})
        self.assertEqual((again.status_code, again.content), (304, b""))

    def test_asset_without_or_with_stale_version_is_revalidated(self):
        for url in ("/api/branding/assets/logo.png", "/api/branding/assets/logo.png?v=0000000000000000"):
            with self.subTest(url=url):
                r = _anon().get(url)
                self.assertEqual((r.status_code, r.headers["cache-control"]), (200, "no-cache"))

    def test_svg_content_type(self):
        r = _anon().get("/api/branding/assets/favicon.svg")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("image/svg+xml"))
        self.assertEqual(r.content, SVG)

    def test_only_referenced_assets_are_served(self):
        for path in ("/api/branding/assets/unreferenced.png", "/api/branding/assets/profile.toml",
                     "/api/branding/assets/..%2Fprofile.toml", "/api/branding/assets/%2E%2E%2Fprofile.toml",
                     "/api/branding/assets/../profile.toml", "/api/branding/assets/assets%2Flogo.png"):
            with self.subTest(path=path):
                r = _anon().get(path)
                # 404＝路由查無此檔；401＝路徑被正規化到白名單外（例如 `/api/profile.toml`）。兩者都沒交出內容。
                self.assertIn(r.status_code, (401, 404), r.status_code)
                self.assertNotIn(b"[institution]", r.content)
                self.assertNotEqual(r.content, PNG)
        r = _anon().get("/api/branding/assets/unreferenced.png")
        self.assertEqual((r.status_code, r.json()["code"]), (404, "asset_not_found"))

    def test_login_page_renders_profile_brand(self):
        html = _anon().get("/login").text
        self.assertIn("<title>登入 · 艾克米研究平台</title>", html)
        self.assertIn('<div class="name">艾克米研究平台</div>', html)
        self.assertIn('<img class="glyph" src="/api/branding/assets/logo.png?v=', html)
        self.assertIn('<link rel="icon" href="/api/branding/assets/favicon.svg?v=', html)
        self.assertNotIn("廷豐", html)
        self.assertNotIn("{{", html)

    def test_login_page_without_logo(self):
        (self.root / "profile.toml").write_text(
            "[institution]\nname = \"<b>X</b>\"\nproduct_name = \"<script>x</script>\"\n", encoding="utf-8")
        P.reset_profile_cache()
        html = _anon().get("/login").text
        self.assertNotIn('class="glyph"', html)
        self.assertNotIn('rel="icon"', html)
        self.assertNotIn("<script>x</script>", html)  # 名稱一律 HTML 跳脫
        self.assertIn("&lt;script&gt;x&lt;/script&gt;", html)
        body = _anon().get("/api/branding").json()
        self.assertEqual((body["logo_light_url"], body["favicon_url"]), (None, None))


async def _fake_search(*a, **k):
    row = ChunkRow(chunk_id=None, report_id="r1", file_hash="h-r1", file_name="x.pdf", title=None, market="TW",
                   source=None,
                   report_date=date(2026, 6, 1), summary=None, report_type=None, instrument_types=None,
                   relates_stock=None, relates_futures=None, stock_targets=None, futures_targets=None,
                   chunk_index=0, content="可口可樂財報。", distance=0.4)
    return [(0, 0.6, row)]


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None


class PromptInjectionHttpTests(AcmeProfileCase):
    """POST /api/ask 走真的 answer_question：送進 LLM 的系統提示與回給使用者的婉拒文案都帶 profile 的稱謂。"""

    def _ask(self, question: str, route_reply: str, captured: list):
        async def fake_route_stream(prompt, **kwargs):
            captured.append(("route", kwargs.get("system")))
            yield route_reply

        async def fake_answer_stream(prompt, **kwargs):
            captured.append(("answer", kwargs.get("system")))
            yield "答案[1]"

        patches = [
            patch.object(sr, "stream_completion", fake_route_stream),
            patch.object(ans, "stream_completion", fake_answer_stream),
            patch.object(rp, "hybrid_search", _fake_search),
            patch.object(rp, "embed_query_cached", lambda q: [0.0]),
            # rerank 的 fail-open 契約：回傳同一個 list＝未套用。不換掉的話會真的去載 cross-encoder 模型。
            patch.object(rp, "rerank_scored", lambda question, scored, **k: scored),
            patch.object(rp, "SessionFactory", lambda: _FakeSession()),
            patch.object(ans, "SessionFactory", lambda: _FakeSession()),
        ]
        for p in patches:
            p.start()
        try:
            client = _login()
            with client.stream("POST", "/api/ask", json={"question": question}) as resp:
                self.assertEqual(resp.status_code, 200)
                return "".join(resp.iter_text())
        finally:
            for p in reversed(patches):
                p.stop()

    def test_off_topic_route_and_notice_use_profile_name(self):
        captured: list = []
        body = self._ask("推薦我一部電影", "OFF_TOPIC", captured)
        self.assertEqual([k for k, _ in captured], ["route"])  # 離題不跑主答
        self.assertTrue(captured[0][1].startswith("你是「艾克米研報」投資問答系統的前置路由器。"))
        self.assertIn("這裡是艾克米研報的投資研究問答", body)
        self.assertNotIn("廷豐", body)

    def test_answer_system_prompt_uses_profile_name(self):
        captured: list = []
        self._ask("可口可樂財報表現如何", "CORPUS_QA", captured)
        systems = dict(captured)
        self.assertIn("answer", systems, captured)  # 假物件簽章過期時會被 fail-open 吞掉，這裡先確認真的走到主答
        self.assertTrue(systems["answer"].startswith("你是「艾克米研報」的研究問答助理。"))
        self.assertNotIn("廷豐", systems["answer"])
        self.assertNotIn("廷豐", systems["route"])


class MarketSubsetHttpTests(AcmeProfileCase):
    def test_markets_endpoint_follows_profile(self):
        r = _login().get("/api/markets")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["markets"], ["TW", "US"])  # 標準順序
        self.assertEqual(body["items"], [{"code": "US", "label": "美國股市"}, {"code": "TW", "label": "台股"}])
        self.assertEqual(set(body["labels"]), set(MARKETS))  # 未啟用的市場也有名稱（研報可能標到它）
        self.assertEqual(body["labels"]["US"], "美國股市")

    def test_disabled_market_filter_is_rejected(self):
        client = _login()
        for path in ("/api/reports?market=HK", "/api/search?q=x&market=HK", "/api/radar/instruments?market=HK"):
            with self.subTest(path=path):
                r = client.get(path)
                self.assertEqual((r.status_code, r.json()["code"]), (422, "market_disabled"), r.text)
        r = client.post("/api/ask", json={"question": "港股展望", "market": "HK"})
        self.assertEqual((r.status_code, r.json()["code"]), (422, "market_disabled"))

    def test_entitlement_must_be_enabled_market(self):
        self.assertEqual(api_clients.normalize_entitlements({"market": ["TW"]}), {"market": ("TW",)})
        with self.assertRaises(api_clients.ApiClientError) as cm:
            api_clients.normalize_entitlements({"market": ["TW", "HK"]})
        self.assertIn("HK", str(cm.exception))


class TingfongDefaultTests(unittest.TestCase):
    def test_branding_and_markets_unchanged(self):
        body = _anon().get("/api/branding").json()
        self.assertEqual(body["product_name"], "廷豐智能研報")
        # 廷豐明確宣告鎏金（框架預設是中性色票）；逐變數的比對在 tests/test_brand_accent.py。
        self.assertEqual((body["colors"]["light"]["accent_strong"], body["colors"]["dark"]["accent_strong"]),
                         ("#ae7415", "#d4a64a"))
        self.assertEqual(_anon().get(body["logo_light_url"]).content,
                         (P.REPO_ROOT / "profiles" / "tingfong" / "assets" / "logo.png").read_bytes())
        r = _login().get("/api/markets")
        self.assertEqual(r.json()["markets"], list(MARKETS))  # 與改動前同一個值
        html = _anon().get("/login").text
        self.assertIn("<title>登入 · 廷豐智能研報</title>", html)
        self.assertIn('<div class="name">廷豐智能研報</div>', html)
        for code in MARKETS:
            api_clients.normalize_entitlements({"market": [code]})


if __name__ == "__main__":
    unittest.main()
