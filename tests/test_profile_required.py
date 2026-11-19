# tests/test_profile_required.py
"""沒有預設 profile（`DEFAULT_PROFILE_DIR` 是空字串）時 `PROFILE_DIR` 必填：每個入口一律拒絕啟動。

交付給其他券商的原始碼就是這個狀態（打包時改寫，見 deploy/EXPORT.toml）；源頭 repo 的預設 profile 不變，所以這裡
以 patch 把預設清空，兩邊驗的是同一套行為：
- `app.profile.resolve_profile_dir`／`get_profile` 拋 ProfileError，訊息說出要設哪個變數、指向哪種目錄。
- web 的 lifespan、LLM 批次與評測入口的 `load_llm_env()`（rc=2）、容器 entrypoint 的 `resolve_tz` 都在第一步拒絕。
- `eval.profile_eval.active_profile_dir` 同樣拒絕（容器排程與題集草擬用它）。
"""
from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app import profile as P  # noqa: E402
from deploy.container import entrypoint as ep  # noqa: E402
from eval import profile_eval as pe  # noqa: E402
from scripts import _llm_env as le  # noqa: E402
from web.server import app  # noqa: E402


class _NoDefault(unittest.TestCase):
    def setUp(self):
        for patcher in (mock.patch.object(P, "DEFAULT_PROFILE_DIR", ""),
                        mock.patch.object(pe, "DEFAULT_PROFILE_DIR", ""),
                        mock.patch.dict(os.environ, {"PROFILE_DIR": ""})):
            patcher.start()
            self.addCleanup(patcher.stop)
        P.reset_profile_cache()
        self.addCleanup(P.reset_profile_cache)

    def assertExplains(self, message: str):
        self.assertIn("PROFILE_DIR", message)
        self.assertIn("profiles/demo", message)


class ProfileRequiredTests(_NoDefault):
    def test_resolve_and_get_profile_refuse_with_an_explanation(self):
        for call in (P.resolve_profile_dir, P.get_profile):
            with self.subTest(call=call.__name__), self.assertRaises(P.ProfileError) as cm:
                call()
            self.assertExplains(str(cm.exception))
        self.assertEqual(P.PROFILE_DIR_REQUIRED, str(cm.exception))

    def test_explicit_dir_still_works(self):
        with mock.patch.dict(os.environ, {"PROFILE_DIR": "profiles/demo"}):
            P.reset_profile_cache()
            self.assertEqual(P.get_profile().root, (REPO_ROOT / "profiles" / "demo").resolve())

    def test_web_lifespan_refuses_to_start(self):
        with self.assertRaises(P.ProfileError) as cm:
            with TestClient(app):
                pass
        self.assertExplains(str(cm.exception))

    def test_llm_batch_entry_refuses_with_rc2(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as cm:
            le.load_llm_env()
        self.assertEqual(cm.exception.code, le.RC_CONFIG)
        self.assertExplains(err.getvalue())

    def test_container_entrypoint_refuses_every_role(self):
        for role in ("web", "worker", "scheduler", "migrate"):
            with self.subTest(role=role):
                tz, problem = ep.resolve_tz(role, {})
                self.assertIsNone(tz)
                self.assertExplains(problem or "")

    def test_eval_entries_refuse(self):
        with self.assertRaises(pe.EvalProfileError) as cm:
            pe.active_profile_dir({})
        self.assertIn("PROFILE_DIR", str(cm.exception))
        self.assertEqual(pe.active_profile_dir({"PROFILE_DIR": "profiles/demo"}), REPO_ROOT / "profiles" / "demo")


if __name__ == "__main__":
    unittest.main()
