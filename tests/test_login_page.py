from fastapi.testclient import TestClient

from web.server import app


def test_login_page_matches_new_design_and_keeps_form():
    client = TestClient(app)
    html = client.get("/login").text
    # 保留表單語意
    assert 'action="/login"' in html
    assert 'name="username"' in html and 'name="password"' in html
    assert 'name="next"' in html
    # 新設計 token（金色主色與平色畫布）
    assert "#8a5a0f" in html
    assert "#f6f7f9" in html
    # 襯線品牌字
    assert "Noto Serif TC" in html
