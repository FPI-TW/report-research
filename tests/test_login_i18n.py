"""登入頁的中英介面（web/static/login.html＋login.i18n.json，web/routers/auth_pages.py 的伺服器端注入）。

語系依序取 `?lang=` → `tf_locale` cookie（SPA 寫的偏好）→ profile 的 `[locale].default`；不看 Accept-Language。
廷豐 profile 的中文頁除了語言切換連結以外與改動前相同（test_branding_api.py 的 TingfongDefaultTests 釘名稱）。
"""

import json
import os
import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app import profile as P  # noqa: E402
from web.routers import auth_pages  # noqa: E402
from web.server import app  # noqa: E402

STATIC = Path(__file__).resolve().parents[1] / "web" / "static"
CJK = re.compile(r"[㐀-鿿豈-﫿　-〿＀-￯]")


def _visible(html: str) -> str:
    """去掉註解、樣式與 JS 註解：剩下的才是會顯示或送進 script 的字。"""
    t = re.sub(r"<!--.*?-->", "", html, flags=re.S)
    t = re.sub(r"<style.*?</style>", "", t, flags=re.S)
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return re.sub(r"(?m)^\s*//[^\n]*", "", t)


def _anon() -> TestClient:
    return TestClient(app)


class DictionaryTests(unittest.TestCase):
    def setUp(self):
        self.messages = json.loads((STATIC / "login.i18n.json").read_text(encoding="utf-8"))["messages"]
        self.template = (STATIC / "login.html").read_text(encoding="utf-8")

    def test_both_locales_have_every_key(self):
        for key, entry in self.messages.items():
            self.assertEqual(set(entry), set(P.LOCALES), key)
            self.assertTrue(all(v.strip() for v in entry.values()), key)

    def test_template_placeholders_and_dictionary_match(self):
        used = set(re.findall(r"\{\{(?:t|tjs):([a-z0-9_]+)\}\}", self.template))
        # 副標依 OIDC 模式由路由挑（hint_off／hint_on／hint_only），不寫在模板裡。
        self.assertEqual(used | {"hint_off", "hint_on", "hint_only"}, set(self.messages))

    def test_template_has_no_literal_cjk_outside_comments(self):
        """新增登入頁文字要進字典：模板裡（註解與樣式以外）不可再有中文字面值。"""
        self.assertEqual([line for line in _visible(self.template).splitlines() if CJK.search(line)], [])


class RenderTests(unittest.TestCase):
    def test_default_is_profile_default_chinese(self):
        html = _anon().get("/login").text
        self.assertIn('<html lang="zh-Hant">', html)
        self.assertIn("<title>登入 · 廷豐智能研報</title>", html)
        self.assertIn('<label for="username">帳號</label>', html)
        self.assertIn('totp: "驗證碼不正確或已使用過，請輸入驗證器目前顯示的碼。"', html)
        self.assertNotIn("{{", html)
        # 另一個語系以它自己的名稱列出。
        switch = '<a class="lang-switch" id="lang-switch" href="/login?lang=en" hreflang="en" lang="en">English</a>'
        self.assertIn(switch, html)

    def test_lang_query_renders_english_and_remembers(self):
        r = _anon().get("/login?lang=en")
        self.assertEqual(r.status_code, 200)
        self.assertIn("tf_locale=en", r.headers["set-cookie"])
        html = r.text
        self.assertIn('<html lang="en">', html)
        self.assertIn('<label for="username">Username</label>', html)
        self.assertIn("Sign in with the account issued by an administrator.", html)
        self.assertIn('hreflang="zh-Hant" lang="zh-Hant">中文</a>', html)
        self.assertNotIn("{{", html)
        # 英文頁只剩品牌名稱（廷豐 profile 沒給英文名，沿用原名）與語言切換的「中文」是中文。
        leftovers = [line for line in _visible(html).splitlines() if CJK.search(line)]
        for line in leftovers:
            self.assertTrue("廷豐智能研報" in line or ">中文<" in line, line)

    def test_cookie_preference_is_used_and_unknown_values_ignored(self):
        client = _anon()
        client.cookies.set("tf_locale", "en")
        self.assertIn('<html lang="en">', client.get("/login").text)
        client.cookies.set("tf_locale", "fr")
        self.assertIn('<html lang="zh-Hant">', client.get("/login").text)
        r = _anon().get("/login?lang=fr")
        self.assertIn('<html lang="zh-Hant">', r.text)
        self.assertNotIn("set-cookie", r.headers)

    def test_js_strings_cannot_close_the_script(self):
        self.assertEqual(auth_pages._js_string('a</script>"b'), '"a\\u003c/script>\\"b"')


class ProfileLocaleTests(unittest.TestCase):
    """另一份 profile：預設英文、只開英文；英文名稱。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "acme"
        root.mkdir()
        (root / "profile.toml").write_text(textwrap.dedent("""
            [institution]
            name = "艾克米"
            product_name = "艾克米研究平台"
            product_name_en = "Acme Research"

            [locale]
            default = "en"
            available = ["en"]
        """), encoding="utf-8")
        self._env = patch.dict(os.environ, {"PROFILE_DIR": str(root)})
        self._env.start()
        P.reset_profile_cache()

    def tearDown(self):
        self._env.stop()
        P.reset_profile_cache()
        self._tmp.cleanup()

    def test_english_only_profile(self):
        client = _anon()
        client.cookies.set("tf_locale", "zh-Hant")  # 不在開放清單裡：忽略
        html = client.get("/login").text
        self.assertIn('<html lang="en">', html)
        self.assertIn("<title>Sign in to Acme Research</title>", html)
        self.assertIn('<div class="name">Acme Research</div>', html)
        self.assertNotIn('<a class="lang-switch"', html)  # 只有一種語系：不列切換
        body = _anon().get("/api/branding").json()
        self.assertEqual(body["locale"], {"default": "en", "available": ["en"]})
        self.assertEqual((body["product_name"], body["product_name_en"]), ("艾克米研究平台", "Acme Research"))


if __name__ == "__main__":
    unittest.main()
