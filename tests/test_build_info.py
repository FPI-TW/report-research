"""版本對帳：`app/services/build_info.py`、`scripts/build_info.py` 與 `/api/admin/diagnostics` 的 `versions.build`。

驗：建置資訊檔的解析（形狀不對整份作廢）、來源判定（映像／主機／只有 .git／都沒有、主機檔與正在跑的 commit
不符就作廢）、寫入腳本（映像模式取 arg、主機模式跑 git、讀不到 git 不寫檔不失敗、不合法的值退出 2）、
以及診斷頁 HTTP 層回報這幾個欄位。唯讀、不擋任何操作：診斷頁照樣 200。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fake_accounts import session_cookies  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

# 收集階段就 import web.server：conftest 的 _fake_accounts 只替換已載入的 web.deps。
from app.services import build_info  # noqa: E402
from scripts import build_info as writer  # noqa: E402
from web.server import app  # noqa: E402

SHA = "8aa38ce19e5dac15192dedf26d8db8cddaff894d"
OTHER_SHA = "aacec4500000000000000000000000000000beef"


def _file(**kw) -> dict:
    base = {"source": "image", "commit": SHA, "version": "v1.4.0", "tree_clean": True,
            "built_at": "2026-11-10T01:02:03Z"}
    return {**base, **kw}


class ParseTests(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(build_info.parse(_file())["version"], "v1.4.0")
        self.assertEqual(build_info.parse(_file(commit=None, version=None, tree_clean=None))["commit"], None)

    def test_bad_shapes_void_the_whole_file(self):
        for bad in (_file(source="laptop"), _file(commit="not-a-sha"), _file(version="v1 ; rm -rf"),
                    _file(tree_clean="yes"), _file(built_at="yesterday"), [], "x", None):
            with self.subTest(bad=bad):
                self.assertIsNone(build_info.parse(bad))

    def test_read_file_limits_and_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "b.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertIsNone(build_info.read_file(p))
            p.write_text(json.dumps(_file()) + " " * (build_info._MAX_BYTES + 10), encoding="utf-8")
            self.assertIsNone(build_info.read_file(p))
            self.assertIsNone(build_info.read_file(Path(tmp) / "missing.json"))


class ResolveTests(unittest.TestCase):
    def test_image_file_is_authoritative(self):
        out = build_info.resolve(build_info.parse(_file()), None)
        self.assertEqual((out["available"], out["source"], out["commit"], out["tree_clean"]),
                         (True, "image", SHA, True))

    def test_host_file_matching_running_commit(self):
        out = build_info.resolve(build_info.parse(_file(source="host", tree_clean=False)), SHA)
        self.assertEqual((out["source"], out["version"], out["tree_clean"], out["stale_file"]),
                         ("host", "v1.4.0", False, False))

    def test_stale_host_file_is_voided(self):
        out = build_info.resolve(build_info.parse(_file(source="host")), OTHER_SHA)
        self.assertEqual((out["source"], out["commit"], out["version"], out["tree_clean"], out["stale_file"]),
                         ("git", OTHER_SHA, None, None, True))

    def test_git_only_and_nothing(self):
        self.assertEqual(build_info.resolve(None, SHA)["source"], "git")
        self.assertEqual(build_info.resolve(None, None)["available"], False)


class WriterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "build_info.json"

    def test_image_mode_takes_args(self):
        rc = writer.main(["write", "--source", "image", "--out", str(self.out), "--commit", SHA.upper(),
                          "--version", "v2.0.0", "--tree-clean", "0"])
        self.assertEqual(rc, 0)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((data["source"], data["commit"], data["version"], data["tree_clean"]),
                         ("image", SHA, "v2.0.0", False))
        self.assertIsNotNone(build_info.read_file(self.out))

    def test_image_mode_without_args_is_unknown_not_guessed(self):
        self.assertEqual(writer.main(["write", "--source", "image", "--out", str(self.out), "--commit", "",
                                      "--version", "", "--tree-clean", ""]), 0)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((data["commit"], data["version"], data["tree_clean"]), (None, None, None))

    def test_invalid_values_exit_2_without_writing(self):
        rc = writer.main(["write", "--source", "image", "--out", str(self.out), "--commit", "zzz"])
        self.assertEqual(rc, 2)
        self.assertFalse(self.out.exists())

    def test_host_mode_reads_git(self):
        answers = {("rev-parse", "HEAD"): SHA, ("describe", "--tags", "--exact-match"): "v3.1.0",
                   ("status", "--porcelain", "--untracked-files=normal"): ""}
        with mock.patch.object(writer, "_git", lambda *a: answers.get(a)):
            self.assertEqual(writer.main(["write", "--out", str(self.out)]), 0)
        data = json.loads(self.out.read_text(encoding="utf-8"))
        self.assertEqual((data["source"], data["commit"], data["version"], data["tree_clean"]),
                         ("host", SHA, "v3.1.0", True))

    def test_host_mode_without_git_does_not_fail_or_write(self):
        with mock.patch.object(writer, "_git", lambda *a: None):
            self.assertEqual(writer.main(["write", "--out", str(self.out)]), 0)
        self.assertFalse(self.out.exists())


class DiagnosticsApiTests(unittest.TestCase):
    """HTTP 層：`versions.build` 帶 commit、版本、工作樹是否乾淨（容器建置寫入的那一份）。"""

    def setUp(self):
        from web.routers import admin_diagnostics

        admin_diagnostics.reset_caches()
        self.addCleanup(admin_diagnostics.reset_caches)
        build_info._reset_for_tests()
        self.addCleanup(build_info._reset_for_tests)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        path = Path(self._tmp.name) / "build_info.json"
        path.write_text(json.dumps(_file(tree_clean=False)), encoding="utf-8")
        p = mock.patch.dict(os.environ, {build_info.BUILD_INFO_ENV: str(path)})
        p.start()
        self.addCleanup(p.stop)

    def test_versions_build(self):
        client = TestClient(app, base_url="http://127.0.0.1", cookies=session_cookies())
        r = client.get("/api/admin/diagnostics")
        self.assertEqual(r.status_code, 200)
        build = r.json()["versions"]["build"]
        self.assertEqual(build, {"available": True, "source": "image", "commit": SHA, "version": "v1.4.0",
                                 "tree_clean": False, "built_at": "2026-11-10T01:02:03Z", "stale_file": False})
        # 同一份資訊也在 /metrics 的 report_mark_build_info。
        m = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 1)).get("/metrics").text
        self.assertIn(f'report_mark_build_info{{commit="{SHA}",source="image",tree_clean="false",'
                      f'version="v1.4.0"}} 1.0', m)

    def test_dockerfile_writes_build_info_from_args(self):
        text = (REPO_ROOT / "deploy" / "container" / "Dockerfile").read_text(encoding="utf-8")
        for arg in ("ARG BUILD_COMMIT=", "ARG BUILD_VERSION=", "ARG BUILD_TREE_CLEAN="):
            self.assertIn(arg, text)
        self.assertIn("scripts/build_info.py write --source image --out /app/build_info.json", text)
        self.assertIn("build_info.json", (REPO_ROOT / ".dockerignore").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
