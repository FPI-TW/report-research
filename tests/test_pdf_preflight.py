"""上傳 PDF 的入庫前檢查（app/services/pdf_preflight.py）：結構判定、子行程的限制與環境白名單。

PDF 都是 `tests/pdf_samples.py` 在記憶體裡組的；子行程用預設的 pypdf 抽取器，不載模型、不連網、不碰 DB。
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pdf_samples as ps

from app.services import pdf_preflight as pf
from app.services import uploads


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def put(self, name: str, data: bytes) -> Path:
        path = self.dir / f"{name}.bin"  # 隔離區的檔名一律是 <upload_id>.bin：抽取器不能靠副檔名
        path.write_bytes(data)
        return path


class StructureTests(_Tmp):
    def test_kinds_match_upload_vocabulary(self):
        for kind in (pf.KIND_ACTIVE_CONTENT, pf.KIND_ENCRYPTED, pf.KIND_TOO_MANY_PAGES, pf.KIND_EXTRACT_ERROR,
                     pf.KIND_EXTRACT_TIMEOUT):
            self.assertIn(kind, uploads.FAILURE_KINDS)

    def test_design_limits(self):
        """設計 1.1：頁數上限 300、子行程逾時 300 秒。"""
        self.assertEqual((pf.MAX_PAGES, pf.TIMEOUT_SECONDS), (300, 300))

    def test_plain_and_open_action_goto_pass(self):
        """設計決策 9：只有 /OpenAction 放行（翻頁這種）。"""
        for name in ("plain", "open_action_goto"):
            with self.subTest(name=name):
                res = pf.check_structure(self.put(name, getattr(ps, name)()))
                self.assertTrue(res.ok, res)
                self.assertEqual(res.pages, 1)

    def test_active_content_rejected(self):
        """/JavaScript、/Launch、/EmbeddedFile、/XFA、/RichMedia；OpenAction 指向 JavaScript 也一樣拒收。"""
        for name in ("open_action_js", "page_aa_js", "launch", "embedded_file", "xfa", "rich_media"):
            with self.subTest(name=name):
                res = pf.check_structure(self.put(name, getattr(ps, name)()))
                self.assertEqual((res.ok, res.kind), (False, uploads.FAILURE_ACTIVE_CONTENT), res)

    def test_escaped_names_are_decoded(self):
        """`/J#61vaScript` 是 `/JavaScript` 的跳脫寫法，閱讀器照樣執行。"""
        raw = ps.page_aa_js().replace(b"/JavaScript", b"/J#61vaScript")
        self.assertNotIn(b"/JavaScript", raw)
        res = pf.check_structure(self.put("escaped", raw))
        self.assertEqual(res.kind, uploads.FAILURE_ACTIVE_CONTENT)

    def test_encrypted_rejected_even_with_empty_password(self):
        """設計決策 10：加密一律拒收，包含空密碼任何閱讀器都打得開的。"""
        for pw in ("", "secret"):
            with self.subTest(password=pw):
                res = pf.check_structure(self.put(f"enc{pw}", ps.encrypted(pw)))
                self.assertEqual((res.ok, res.kind), (False, uploads.FAILURE_ENCRYPTED))

    def test_page_limit(self):
        path = self.put("p3", ps.plain(3))
        self.assertTrue(pf.check_structure(path, max_pages=3).ok)
        res = pf.check_structure(path, max_pages=2)
        self.assertEqual((res.ok, res.kind, res.pages), (False, uploads.FAILURE_TOO_MANY_PAGES, 3))

    def test_unparseable_is_not_a_pass(self):
        """安全閘門：看不懂不等於沒有主動內容。"""
        res = pf.check_structure(self.put("junk", b"%PDF-1.7\n" + os.urandom(2048) + b"\n%%EOF\n"))
        self.assertFalse(res.ok)
        self.assertEqual(res.kind, uploads.FAILURE_EXTRACT_ERROR)

    def test_object_graph_cap(self):
        with mock.patch.object(pf, "MAX_OBJECTS", 2):
            res = pf.check_structure(self.put("plain", ps.plain()))
        self.assertEqual((res.ok, res.kind), (False, uploads.FAILURE_EXTRACT_ERROR))


class SubprocessTests(_Tmp):
    def test_plain_passes_with_trial_extraction(self):
        res = pf.run_preflight(self.put("plain", ps.plain(2)))
        self.assertTrue(res.ok, res)
        self.assertEqual(res.pages, 2)
        self.assertGreater(res.chars or 0, 100, "試抽字要真的抽得出字（經 .pdf 符號連結，不靠 .bin 副檔名）")

    def test_active_content_in_subprocess(self):
        res = pf.run_preflight(self.put("js", ps.open_action_js()))
        self.assertEqual((res.ok, res.kind), (False, uploads.FAILURE_ACTIVE_CONTENT))

    def test_timeout_is_extract_timeout(self):
        res = pf.run_preflight(self.put("plain", ps.plain()), timeout=0.01)
        self.assertEqual((res.ok, res.kind), (False, uploads.FAILURE_EXTRACT_TIMEOUT))

    def test_memory_limit_is_enforced(self):
        """RLIMIT_AS 壓到 64 MB：直譯器連 import 都做不完。無論死在哪一步，結果都是不通過、不是放行。"""
        res = pf.run_preflight(self.put("plain", ps.plain()), memory_mb=64)
        self.assertFalse(res.ok)
        self.assertEqual(res.kind, uploads.FAILURE_EXTRACT_ERROR)

    def test_abnormal_exit_and_garbage_output_fail_closed(self):
        path = self.put("plain", ps.plain())
        res = pf.run_preflight(path, python="/bin/false")
        self.assertEqual((res.ok, res.kind), (False, uploads.FAILURE_EXTRACT_ERROR))
        res = pf.run_preflight(path, python="/bin/echo")  # rc 0、輸出不是 JSON
        self.assertEqual((res.ok, res.kind), (False, uploads.FAILURE_EXTRACT_ERROR))

    def test_child_env_carries_no_secrets(self):
        src = {
            "PATH": "/usr/bin", "HOME": "/home/x", "EXTRACTOR": "pdfplumber", "EXTRACTION_REVIEW_MIN": "0.5",
            "DEEPSEEK_API_KEY": "fixed-test-secret-deepseek0", "R2_SECRET_ACCESS_KEY": "s",
            "REPORT_MARK_DB_URL": "postgresql://x", "REPORT_MARK_ALERT_WEBHOOK": "https://hooks.example/x",
            "REPORT_MARK_SESSION_SECRET": "s", "UPLOAD_WORKER_LOCK_FD": "9",
        }
        env = pf.child_env(src)
        self.assertEqual(env["EXTRACTOR"], "pdfplumber")
        self.assertEqual(env["EXTRACTION_REVIEW_MIN"], "0.5")
        for key in ("DEEPSEEK_API_KEY", "R2_SECRET_ACCESS_KEY", "REPORT_MARK_DB_URL", "REPORT_MARK_ALERT_WEBHOOK",
                    "REPORT_MARK_SESSION_SECRET", "UPLOAD_WORKER_LOCK_FD"):
            self.assertNotIn(key, env)

    def test_child_runs_with_whitelisted_env_only(self):
        """實際 spawn 時傳的就是白名單環境（不是繼承父行程的 os.environ）。"""
        seen = {}

        def fake_run(argv, **kw):
            seen.update(kw)
            raise pf.subprocess.TimeoutExpired(argv, 1)

        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "fixed-test-secret-deepseek0"}), \
                mock.patch.object(pf.subprocess, "run", fake_run):
            pf.run_preflight(self.dir / "x.bin")
        self.assertNotIn("DEEPSEEK_API_KEY", seen["env"])
        self.assertTrue(seen["close_fds"])
        self.assertEqual(seen["cwd"], pf.REPO_ROOT)


if __name__ == "__main__":
    unittest.main()
