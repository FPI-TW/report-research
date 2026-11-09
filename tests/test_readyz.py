# tests/test_readyz.py
"""`/readyz`：就緒探測（免認證，容器部署用；判斷在 app/services/readiness.py）。

與 `/healthz` 同一個保守原則：回應只有 `status` 一鍵，哪一項沒過只進日誌。四項：DB、嵌入模型、profile、
LLM 設定（不呼叫 LLM）。
"""
import logging
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fastapi.testclient import TestClient  # noqa: E402

from app.services import llm_models, readiness  # noqa: E402
from web.routers import health  # noqa: E402
from web.server import app  # noqa: E402


class _FakeSession:
    def __init__(self, boom: bool = False):
        self.boom = boom

    async def __aenter__(self):
        if self.boom:
            raise RuntimeError("db down")
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return None


def _client() -> TestClient:
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class ReadyzHttpTests(unittest.TestCase):
    def setUp(self):
        health._cache = (0.0, False)
        health._READY_CACHE.clear()
        self.patches = [
            patch.object(health.deps, "SessionFactory", lambda: _FakeSession()),
            patch.object(readiness, "embedding_ready", lambda: True),
            patch.object(readiness, "profile_ready", lambda: True),
            patch.object(readiness, "llm_config_ready", lambda env=None: True),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        health._cache = (0.0, False)
        health._READY_CACHE.clear()

    def test_ready_returns_200_ok(self):
        r = _client().get("/readyz")
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))

    def test_no_auth_required(self):
        r = TestClient(app, follow_redirects=False).get("/readyz")
        self.assertEqual(r.status_code, 200)

    def test_each_failing_check_returns_503_without_details(self):
        cases = {
            "db": patch.object(health.deps, "SessionFactory", lambda: _FakeSession(boom=True)),
            "embedding": patch.object(readiness, "embedding_ready", lambda: False),
            "profile": patch.object(readiness, "profile_ready", lambda: False),
            "llm": patch.object(readiness, "llm_config_ready", lambda env=None: False),
        }
        for name, p in cases.items():
            with self.subTest(check=name), p, self.assertLogs("web.routers.health", logging.WARNING) as logs:
                health._cache = (0.0, False)
                health._READY_CACHE.clear()
                r = _client().get("/readyz")
                self.assertEqual((r.status_code, r.json()), (503, {"status": "not_ready"}))
                self.assertIn(name, "\n".join(logs.output), "沒過的項目要進日誌，才查得到原因")

    def test_check_that_raises_is_not_ready(self):
        def boom():
            raise RuntimeError("x")

        with patch.object(readiness, "embedding_ready", boom):
            r = _client().get("/readyz")
        self.assertEqual(r.status_code, 503)

    def test_result_is_cached(self):
        calls = []
        with patch.object(readiness, "profile_ready", lambda: calls.append(1) or True):
            _client().get("/readyz")
            _client().get("/readyz")
        self.assertEqual(len(calls), 1)

    def test_healthz_is_unchanged(self):
        with patch.object(readiness, "llm_config_ready", lambda env=None: False):
            r = _client().get("/healthz")
        self.assertEqual((r.status_code, r.json()), (200, {"status": "ok"}))


class LlmConfigReadyTests(unittest.TestCase):
    KEY = "fixed-test-secret-" + "llmkey"

    def test_valid_config(self):
        self.assertTrue(readiness.llm_config_ready({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": self.KEY}))

    def test_missing_key(self):
        self.assertFalse(readiness.llm_config_ready({"LLM_PROVIDER": "openai", "OPENAI_API_KEY": ""}))

    def test_misspelled_provider_is_not_ready_even_though_runtime_falls_back(self):
        self.assertFalse(readiness.llm_config_ready({"LLM_PROVIDER": "opanai", "OPENAI_API_KEY": self.KEY}))

    def test_model_outside_whitelist(self):
        env = {"OPENAI_API_KEY": self.KEY, llm_models.TASK_ENV[llm_models.ONLINE_TASKS[0]]: "claude-x"}
        self.assertFalse(readiness.llm_config_ready(env))


class ProfileReadyTests(unittest.TestCase):
    def test_missing_profile_module_counts_as_ready(self):
        err = ModuleNotFoundError("No module named 'app.profile'", name="app.profile")
        with patch.object(readiness.importlib, "import_module", side_effect=err):
            self.assertTrue(readiness.profile_ready())

    def test_profile_module_with_broken_import_is_not_ready(self):
        err = ModuleNotFoundError("No module named 'tomli_w'", name="tomli_w")
        with patch.object(readiness.importlib, "import_module", side_effect=err):
            self.assertFalse(readiness.profile_ready())

    def test_profile_validation_failure_is_not_ready(self):
        import sys
        import types

        fake = types.ModuleType("app.profile")

        def get_profile():
            raise ValueError("profile.toml 驗證失敗")

        fake.get_profile = get_profile
        with patch.dict(sys.modules, {"app.profile": fake}):
            self.assertFalse(readiness.profile_ready())


class EmbeddingReadyTests(unittest.TestCase):
    def test_loaded_model_is_ready(self):
        from app.services import embed

        with patch.object(embed, "_model", object()):
            self.assertTrue(readiness.embedding_ready())

    def test_missing_cache_is_not_ready(self):
        from app.services import embed

        with patch.object(embed, "_model", None), \
                patch("huggingface_hub.snapshot_download", side_effect=OSError("not cached")):
            self.assertFalse(readiness.embedding_ready())

    def test_cached_snapshot_with_weights_is_ready(self):
        import tempfile
        from pathlib import Path

        from app.services import embed

        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "config.json").write_text("{}", encoding="utf-8")
            (Path(d) / "pytorch_model.bin").write_bytes(b"x")
            with patch.object(embed, "_model", None), patch("huggingface_hub.snapshot_download", return_value=d):
                self.assertTrue(readiness.embedding_ready())


if __name__ == "__main__":
    unittest.main()
