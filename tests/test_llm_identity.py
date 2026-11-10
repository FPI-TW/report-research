# tests/test_llm_identity.py
"""送給 LLM 供應商的自報身分（OpenCode Go 的 User-Agent）由載入 profile 的兩個入口設定。

`app/services/llm_http.py` 是葉模組、不 import profile（`tests/test_llm_http.py` 的 LeafModuleTests），網址
（profile 的 `[institution].website`）由 web 的 lifespan 與 LLM 批次／評測入口必經的
`scripts/_llm_env.load_llm_env()` 以 `llm_models.set_identity_url()` 設定。漏了其中一處，那條路徑送出的 User-Agent
就靜默少了網址；預設 profile 的完整值由 `tests/fixtures/llm_wire_golden.json` 釘住。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

from app.profile import get_profile
from app.services import llm_http, llm_models
from scripts import _llm_env as le

TINGFONG_UA = "report-mark/1 (+https://research.tingfong.com)"


class LoadLlmEnvTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "llm.env"
        patcher = mock.patch.dict(os.environ, {"LLM_ENV_FILE": str(self.path)})
        patcher.start()
        self.addCleanup(patcher.stop)
        le._STATE.clear()
        self.addCleanup(le._STATE.clear)
        wt = mock.patch.object(le, "_warn_if_not_deploy_root")
        wt.start()
        self.addCleanup(wt.stop)

    def test_sets_identity_from_profile_even_without_env_file(self):
        llm_models.set_identity_url("")
        le.load_llm_env()
        self.assertEqual(le._STATE["error"], "missing")
        self.assertEqual(llm_http.go_user_agent(), TINGFONG_UA)

    def test_sets_identity_after_loading_the_file(self):
        self.path.write_text("SUMMARY_MODEL=gpt-6-luna\n", encoding="utf-8")
        llm_models.set_identity_url("")
        le.load_llm_env()
        self.assertEqual(llm_http.go_user_agent(), TINGFONG_UA)

    def test_profile_without_website_sends_bare_product_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "profile.toml").write_text(
                '[institution]\nname = "測試機構"\nproduct_name = "測試研報平台"\n', encoding="utf-8")
            with mock.patch.dict(os.environ, {"PROFILE_DIR": tmp}):
                from app import profile as P

                P.reset_profile_cache()
                llm_models.set_identity_url("https://stale.example.com")
                le.load_llm_env()
                P.reset_profile_cache()
        self.assertEqual(llm_http.go_user_agent(), "report-mark/1")


class LifespanTests(unittest.TestCase):
    def test_web_lifespan_sets_identity_from_profile(self):
        from web.server import app

        llm_models.set_identity_url("")
        with mock.patch("web.deps.embed_texts", return_value=[[0.0]]), \
             mock.patch("web.deps.rerank_warmup", return_value=None):
            with TestClient(app):
                self.assertEqual(llm_http.go_user_agent(), TINGFONG_UA)
        self.assertEqual(get_profile().institution.website, "https://research.tingfong.com")


if __name__ == "__main__":
    unittest.main()
