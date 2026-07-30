import re
from pathlib import Path

from fastapi.testclient import TestClient

from web.server import app

TOKENS_CSS = (
    Path(__file__).resolve().parents[1] / "frontend" / "src" / "styles" / "tokens.css"
)


def _token(name: str) -> str:
    """從 tokens.css 取出一個六位 hex token 的值。"""
    match = re.search(rf"{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})", TOKENS_CSS.read_text(encoding="utf-8"))
    assert match, f"{name} 應在 tokens.css 定義為六位 hex"
    return match.group(1)


def test_login_page_keeps_form_semantics():
    html = TestClient(app).get("/login").text
    assert 'action="/login"' in html
    assert 'name="username"' in html and 'name="password"' in html
    assert 'name="next"' in html


def test_login_page_theme_tracks_tokens_css():
    """登入頁在 auth 牆外、不可引用受保護的 tokens.css，色值是手抄副本。

    釘住字面色碼只會在每次改版時變成待更新的雜訊；真正要守的是**兩邊不漂移**——
    改了主題卻漏改登入頁，使用者第一眼看到的就是舊配色。故改為比對 tokens.css
    的實際值有沒有出現在登入頁裡。
    """
    html = TestClient(app).get("/login").text

    for token in ("--tf-canvas", "--tf-gold-text", "--tf-surface", "--tf-border"):
        value = _token(token)
        assert value in html, f"登入頁未跟上 tokens.css 的 {token}（{value}）"


def test_login_page_uses_no_serif_family():
    """全站已改為無襯線；登入頁若殘留襯線宣告，品牌字就會與應用內不一致。"""
    html = TestClient(app).get("/login").text
    assert "Noto Serif" not in html
    assert "serif" not in html.replace("sans-serif", "")
