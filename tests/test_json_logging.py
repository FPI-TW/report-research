"""結構化 JSON 日誌（`LOG_FORMAT=json`，app/logging_setup.py）：格式、欄位白名單、遮罩、與 request_id 的對應。

text 模式的既有行為由 tests/test_logging_setup.py 守；這裡只驗 JSON 模式，以及「預設仍是 text」。
假祕密一律 fixed-test-secret-…（gitleaks 的 allowlist）。
"""

from __future__ import annotations

import io
import json
import logging
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from app import logging_setup, request_context  # noqa: E402
from app.logging_setup import JsonFormatter, logging_config, redact  # noqa: E402

SECRET = "fixed-test-secret-jsonlog9a8b7c"


def _record(msg, *args, name="app.services.x", level=logging.INFO, exc_info=None, **extra):
    rec = logging.LogRecord(name, level, __file__, 1, msg, args, exc_info)
    for k, v in extra.items():
        setattr(rec, k, v)
    logging_setup.RequestIdFilter().filter(rec)
    return rec


def _fmt(rec) -> dict:
    line = JsonFormatter().format(rec)
    assert "\n" not in line, "一筆一行"
    return json.loads(line)


class ShapeTests(unittest.TestCase):
    def test_fields(self):
        token = request_context.set_request_id("rid1234567890ab")
        try:
            out = _fmt(_record("哈囉 %s", "世界"))
        finally:
            request_context.reset_request_id(token)
        self.assertEqual(set(out), {"ts", "level", "logger", "request_id", "message"})
        self.assertEqual(out["level"], "INFO")
        self.assertEqual(out["logger"], "app.services.x")
        self.assertEqual(out["request_id"], "rid1234567890ab")
        self.assertEqual(out["message"], "哈囉 世界")
        self.assertRegex(out["ts"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

    def test_no_request_is_dash(self):
        self.assertEqual(_fmt(_record("x"))["request_id"], request_context.NO_REQUEST)

    def test_extra_attributes_are_not_emitted(self):
        out = _fmt(_record("x", user_id="fixed-user-id", question="fixed-question"))
        self.assertNotIn("fixed-user-id", json.dumps(out, ensure_ascii=False))
        self.assertNotIn("fixed-question", json.dumps(out, ensure_ascii=False))

    def test_exception_is_included_and_redacted(self):
        try:
            raise RuntimeError(f"連線失敗 postgresql://u:{SECRET}@db/x")
        except RuntimeError:
            out = _fmt(_record("boom", level=logging.ERROR, exc_info=sys.exc_info()))
        self.assertEqual(out["exc_type"], "RuntimeError")
        self.assertIn("Traceback", out["exc"])
        self.assertNotIn(SECRET, out["exc"])

    def test_bad_format_args_do_not_drop_the_line(self):
        out = _fmt(_record("%s %s", "only-one"))
        self.assertIn("%s", out["message"])

    def test_request_id_matches_error_response(self):
        """錯誤回應的 request_id 與 JSON 日誌的 request_id 是同一個值（web/errors.py 讀同一個 contextvar）。"""
        from fastapi.testclient import TestClient

        from web.server import app

        handler = logging.StreamHandler(io.StringIO())
        handler.setFormatter(JsonFormatter())
        handler.addFilter(logging_setup.RequestIdFilter())
        lg = logging.getLogger("web.request_log")
        old_level = lg.level
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)
        try:
            r = TestClient(app, base_url="http://127.0.0.1").get("/api/reports")
        finally:
            lg.removeHandler(handler)
            lg.setLevel(old_level)
        self.assertEqual(r.status_code, 401)
        lines = [json.loads(ln) for ln in handler.stream.getvalue().splitlines() if ln.strip()]
        self.assertTrue(lines)
        self.assertEqual(lines[-1]["request_id"], r.json()["request_id"])


class RedactionTests(unittest.TestCase):
    def setUp(self):
        logging_setup._secrets_cache = (0.0, [])
        self.addCleanup(setattr, logging_setup, "_secrets_cache", (0.0, []))

    def test_secret_env_values(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": SECRET, "SOME_WEBHOOK": SECRET + "x"}):
            logging_setup._secrets_cache = (0.0, [])
            out = redact(f"key={SECRET} hook={SECRET}x")
        self.assertNotIn(SECRET, out)
        self.assertIn("[redacted]", out)

    def test_query_strings(self):
        out = redact("GET /api/search?q=台積電&market=TW done; https://x.example/a/b?token=abc")
        self.assertNotIn("台積電", out)
        self.assertNotIn("token=abc", out)
        self.assertIn("/api/search?[redacted]", out)

    def test_bearer_and_url_credentials(self):
        out = redact(f"Authorization: Bearer {SECRET} and https://user:{SECRET}@host/x")
        self.assertNotIn(SECRET, out)

    def test_sqlalchemy_parameters(self):
        msg = ("(sqlalchemy.dbapi.Error) boom\n[SQL: SELECT 1 WHERE q = $1]\n"
               "[parameters: ('使用者的問題原文 fixed-question',)]\n(Background on this error at: x)")
        out = redact(msg)
        self.assertNotIn("fixed-question", out)
        self.assertIn("[SQL: SELECT 1 WHERE q = $1]", out, "SQL 本身（無參數值）保留，排查要用")

    def test_uvicorn_access_drops_client_and_query(self):
        rec = _record('%s - "%s %s HTTP/%s" %d', "203.0.113.5:5555", "GET", "/api/search?q=fixed-q", "1.1", 200,
                      name="uvicorn.access")
        out = _fmt(rec)
        self.assertEqual(out["message"], "GET /api/search HTTP/1.1 200")
        self.assertNotIn("203.0.113.5", json.dumps(out))

    def test_text_mode_is_untouched(self):
        fmt = logging.Formatter(logging_config("INFO")["formatters"]["standard"]["format"])
        rec = _record("GET /api/search?q=fixed-q")
        self.assertIn("q=fixed-q", fmt.format(rec), "text 模式維持現狀，不套遮罩")


class ConfigTests(unittest.TestCase):
    def test_json_config_writes_stdout(self):
        cfg = logging_config("INFO", "json")
        self.assertEqual(cfg["root"]["handlers"], ["stdout"])
        self.assertEqual(cfg["handlers"]["stdout"]["stream"], "ext://sys.stdout")
        self.assertNotIn("loggers", cfg)

    def test_default_is_text(self):
        from app import config

        with mock.patch.dict(os.environ, {"LOG_FORMAT": ""}):
            self.assertEqual(config._log_format(), "text")
        with mock.patch.dict(os.environ, {"LOG_FORMAT": "JSON"}):
            self.assertEqual(config._log_format(), "json")
        with mock.patch.dict(os.environ, {"LOG_FORMAT": "xml"}):
            self.assertEqual(config._log_format(), "text")
        self.assertEqual(logging_config("INFO")["root"]["handlers"], ["stderr"])

    def test_container_image_defaults_to_json(self):
        dockerfile = (REPO_ROOT / "deploy" / "container" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("LOG_FORMAT=json", dockerfile)
        for unit in (REPO_ROOT / "deploy" / "systemd").glob("*.service"):
            with self.subTest(unit=unit.name):
                self.assertNotIn("LOG_FORMAT", unit.read_text(encoding="utf-8"), "systemd 主機維持現狀")

    def test_configure_json_routes_uvicorn_through_root(self):
        root = logging.getLogger()
        saved_root = (root.handlers[:], root.level)
        saved = {n: (logging.getLogger(n).handlers[:], logging.getLogger(n).propagate)
                 for n in ("uvicorn", "uvicorn.error", "uvicorn.access")}
        logging.getLogger("uvicorn.access").addHandler(logging.NullHandler())
        logging_setup._reset_for_tests()
        try:
            logging_setup.configure_logging("INFO", "json")
            self.assertIsInstance(root.handlers[0].formatter, JsonFormatter)
            for name in saved:
                with self.subTest(logger=name):
                    self.assertEqual(logging.getLogger(name).handlers, [])
                    self.assertTrue(logging.getLogger(name).propagate)
        finally:
            root.handlers[:], level = saved_root
            root.setLevel(level)
            for name, (handlers, propagate) in saved.items():
                logging.getLogger(name).handlers[:] = handlers
                logging.getLogger(name).propagate = propagate
            logging_setup._reset_for_tests()


if __name__ == "__main__":
    unittest.main()
