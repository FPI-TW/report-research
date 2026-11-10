# tests/test_brand_accent.py
"""強調色：框架預設是中性色票，廷豐 profile 明確宣告鎏金，廷豐畫面上的每個強調色變數與去品牌之前逐值相同。

`GOLD` 是去品牌之前 `frontend/src/styles/tokens.css` 與 `web/static/login.html` 寫死的值（當時的預設即廷豐色票）。
這裡模擬執行期的覆寫——SPA 是 `frontend/src/lib/branding.ts` 的 `ACCENT_CSS_VARS`（依表的順序輸出），登入頁是
`web/routers/auth_pages.py` 的 `LOGIN_ACCENT_VARS`（伺服器端注入）——算出廷豐 profile 下每個變數的最終值，
與 `GOLD` 逐一比對。另一半：沒有覆寫的 profile 看到的是中性預設，任何一個變數都不能是鎏金。

jsdom／TestClient 不算 CSS 的層疊，所以這裡驗「宣告了什麼、誰蓋過誰」；瀏覽器實際畫出來的樣子另以截圖比對
（PR 說明）。寫死在模組 CSS 裡的半透明金色改成以 color-mix 從變數算出，`test_no_literal_gold_left` 守它不回來。
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app import profile as P
from web.routers import auth_pages
from web.server import app

REPO = Path(__file__).resolve().parents[1]
TOKENS = REPO / "frontend" / "src" / "styles" / "tokens.css"
BRANDING_TS = REPO / "frontend" / "src" / "lib" / "branding.ts"
LOGIN_HTML = REPO / "web" / "static" / "login.html"

LIGHT_BLOCKS = (":root {", ":root, :root[data-theme='light'] {")
DARK_BLOCK = ":root[data-theme='dark'] {"

GOLD = {
    "light": {
        "--tf-gold-text": "#8a5a0f", "--tf-gold-strong": "#ae7415", "--tf-gold-hover": "#7a4f0c",
        "--tf-gold-tint": "#f8f1e0", "--tf-gold-line": "#ead9ae", "--tf-gold-panel": "#fbf7ec",
        "--tf-on-gold": "#ffffff", "--tf-fluid-accent": "#8a5a0f",
        "--ac-gold": "#ae7415", "--ac-goldt": "#8a5a0f", "--ac-ongold": "#ffffff", "--ac-goldbtn": "#946110",
        "--ac-gsel": "#fbf6ea", "--ac-gline": "#e6d3a3",
    },
    "dark": {
        "--tf-gold-text": "#e2b968", "--tf-gold-strong": "#d4a64a", "--tf-gold-hover": "#edc97f",
        "--tf-gold-tint": "rgba(212, 166, 74, 0.14)", "--tf-gold-line": "rgba(212, 166, 74, 0.4)",
        "--tf-gold-panel": "rgba(212, 166, 74, 0.07)", "--tf-on-gold": "#17130a",
        "--ac-gold": "#d4a64a", "--ac-goldt": "#e2b968", "--ac-ongold": "#17130a", "--ac-goldbtn": "#d4a64a",
        "--ac-gsel": "rgba(212, 166, 74, 0.07)", "--ac-gline": "rgba(212, 166, 74, 0.4)",
        # Fluid 元件的強調色（tailwind.css 的 --fluid-accent）在深色主題下向來是亮色那一階
        "--tf-fluid-accent": "#8a5a0f",
    },
    "login": {
        "--tf-gold-text": "#8a5a0f", "--gold-hover": "#7a4f0c", "--gold-ink": "#ae7415", "--gold-tint": "#f8f1e0",
    },
}

_DECL = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;]+);")


def _strip(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _block(css: str, head: str) -> dict[str, str]:
    css = _strip(css)
    start = css.index(head) + len(head)
    return {k: " ".join(v.split()) for k, v in _DECL.findall(css[start:css.index("}", start)])}


def token_defaults(scheme: str) -> dict[str, str]:
    css = TOKENS.read_text(encoding="utf-8")
    if scheme == "dark":
        return _block(css, DARK_BLOCK)
    out: dict[str, str] = {}
    for head in LIGHT_BLOCKS:
        out.update(_block(css, head))
    return out


def accent_css_vars(table: str = "ACCENT_CSS_VARS") -> list[tuple[str, list[str]]]:
    """branding.ts 的對照表（保留順序：前端依表的順序輸出宣告，後寫的蓋過先寫的）。"""
    src = BRANDING_TS.read_text(encoding="utf-8")
    match = re.search(rf"export const {table}\b[^=]*=\s*\{{(.*?)\n\}}", src, re.S)
    assert match, f"branding.ts 應定義 {table}"
    return [(k, re.findall(r"'(--[a-z0-9-]+)'", v)) for k, v in re.findall(r"(\w+):\s*\[([^\]]*)\]", match.group(1))]


def _apply(out: dict[str, str], colors: dict[str, str], tables: tuple[str, ...]) -> None:
    for table in tables:
        for key, variables in accent_css_vars(table):
            if key in colors:
                for v in variables:
                    out[v] = colors[key]


def effective(scheme: str, light: dict[str, str], dark: dict[str, str] | None = None) -> dict[str, str]:
    """層疊後的值：亮色區塊＋亮色覆寫；深色主題再疊上深色區塊與深色覆寫（`:root` 規則在深色下照樣成立）。"""
    out = token_defaults("light")
    _apply(out, light, ("ACCENT_CSS_VARS", "LIGHT_ONLY_CSS_VARS"))
    if scheme == "dark":
        out.update(token_defaults("dark"))
        _apply(out, dark or {}, ("ACCENT_CSS_VARS",))
    return out


def login_effective(html: str) -> dict[str, str]:
    """登入頁 `:root` 的強調色：頁面自己的預設，再套上伺服器注入的覆寫（排在後面的 <style>）。"""
    head = html[:html.index("</head>")]
    out: dict[str, str] = {}
    for style in re.findall(r"<style>(.*?)</style>", head, re.S):
        root = re.search(r":root\s*\{([^}]*)\}", _strip(style))
        if root:
            out.update({k: " ".join(v.split()) for k, v in _DECL.findall(root.group(1))})
    return out


def _gold_values() -> set[str]:
    return {v.lower() for scheme in GOLD.values() for v in scheme.values() if v != "#ffffff"}


class TingfongAccentUnchangedTests(unittest.TestCase):
    def setUp(self):
        self.brand = P.get_profile().branding

    def test_spa_light_and_admin_tokens(self):
        got = effective("light", dict(self.brand.colors_light))
        self.assertEqual({k: got[k] for k in GOLD["light"]}, GOLD["light"])

    def test_admin_dark_tokens(self):
        got = effective("dark", dict(self.brand.colors_light), dict(self.brand.colors_dark))
        self.assertEqual({k: got[k] for k in GOLD["dark"]}, GOLD["dark"])

    def test_login_page(self):
        got = login_effective(TestClient(app).get("/login").text)
        self.assertEqual({k: got[k] for k in GOLD["login"]}, GOLD["login"])

    def test_every_frontend_key_is_exercised(self):
        """廷豐宣告了前端認得的每個亮色鍵：少宣告一個，那個變數就會掉回中性預設。"""
        self.assertEqual(set(self.brand.colors_light), {k for k, _ in accent_css_vars()})
        self.assertEqual(set(self.brand.colors_light), set(P.ACCENT_TOKENS))
        self.assertTrue(set(auth_pages.LOGIN_ACCENT_VARS) <= set(P.ACCENT_TOKENS))


class NeutralDefaultTests(unittest.TestCase):
    def test_token_defaults_are_not_gold(self):
        gold = _gold_values()
        for scheme in ("light", "dark"):
            defaults = effective(scheme, {}, {})
            with self.subTest(scheme=scheme):
                leaked = {k: defaults[k] for k in GOLD[scheme] if defaults[k].lower() in gold}
                self.assertFalse(leaked, f"tokens.css 的預設仍是鎏金：{leaked}")

    def test_login_without_profile_colors_is_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "profile.toml").write_text(
                '[institution]\nname = "測試機構"\nproduct_name = "測試研報平台"\n', encoding="utf-8")
            with mock.patch.dict("os.environ", {"PROFILE_DIR": tmp}):
                P.reset_profile_cache()
                try:
                    html = TestClient(app).get("/login").text
                finally:
                    P.reset_profile_cache()
        self.assertNotIn("{{brand_colors}}", html)
        got = login_effective(html)
        self.assertEqual(len(re.findall(r"<style>", html[:html.index("</head>")])), 1)  # 沒有注入覆寫
        leaked = {k: got[k] for k in GOLD["login"] if got[k].lower() in _gold_values()}
        self.assertFalse(leaked, f"登入頁的預設仍是鎏金：{leaked}")

    def test_login_injection_only_takes_known_keys(self):
        css = auth_pages._brand_colors_style({"accent_text": "#123456", "accent_button": "#654321"})
        self.assertEqual(css, "<style>:root { --tf-gold-text: #123456; }</style>")
        self.assertEqual(auth_pages._brand_colors_style({}), "")

    def test_no_literal_gold_left(self):
        """模組 CSS 與登入頁不再寫死鎏金：半透明的強調色以 color-mix 從變數算出，換 profile 時才跟得上。"""
        literal = re.compile(
            r"#(8a5a0f|ae7415|7a4f0c|f8f1e0|946110|e6d3a3|fbf6ea|d4a64a|e2b968|edc97f)\b"
            r"|rgba?\(\s*(174,\s*116,\s*21|138,\s*90,\s*15|212,\s*166,\s*74)\b", re.I)
        files = sorted((REPO / "frontend" / "src").rglob("*.css")) + [LOGIN_HTML]
        found = [f"{p.relative_to(REPO)}:{i}" for p in files
                 for i, line in enumerate(_strip(p.read_text(encoding="utf-8")).splitlines(), 1)
                 if literal.search(line)]
        self.assertFalse(found, "寫死的鎏金：" + "、".join(found))


if __name__ == "__main__":
    unittest.main()
