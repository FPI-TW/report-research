"""隔離區寫入（app/services/quarantine.py）：權限、O_EXCL、雜湊、上限、PDF 字面檢查、清理。

全部寫在 tempfile 底下；conftest 已把 `UPLOAD_QUARANTINE_DIR` 指到不存在的路徑。
"""

from __future__ import annotations

import dataclasses
import hashlib
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app import config
from app.services import quarantine as q

PDF = b"%PDF-1.7\n" + b"x" * 5000 + b"\n%%EOF\n"


def _mode(path: Path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


class QuarantineWriterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "quarantine"

    def tearDown(self):
        self._tmp.cleanup()

    def _writer(self, upload_id="u1", max_bytes=1 << 20):
        q.ensure_dirs(self.root)
        w = q.QuarantineWriter(self.root, upload_id, max_bytes=max_bytes)
        w.open()
        return w

    def test_dirs_are_0700_even_if_they_existed_wider(self):
        (self.root / "incoming").mkdir(parents=True, mode=0o755)
        os.chmod(self.root, 0o755)
        os.chmod(self.root / "incoming", 0o777)
        q.ensure_dirs(self.root)
        self.assertEqual(_mode(self.root), 0o700)
        self.assertEqual(_mode(self.root / "incoming"), 0o700)

    def test_round_trip_hash_size_mode_and_name(self):
        w = self._writer()
        self.assertEqual(_mode(w.part), 0o600)
        for i in range(0, len(PDF), 777):
            w.write(PDF[i:i + 777])
        stored = w.finalize()
        self.assertEqual(stored.path, self.root / "u1.bin")
        self.assertEqual(stored.sha256, hashlib.sha256(PDF).hexdigest())
        self.assertEqual(stored.size_bytes, len(PDF))
        self.assertEqual(stored.path.read_bytes(), PDF)
        self.assertEqual(_mode(stored.path), 0o600)
        self.assertFalse(w.part.exists())
        self.assertEqual(os.listdir(self.root / "incoming"), [])

    def test_o_excl_refuses_existing_part(self):
        q.ensure_dirs(self.root)
        q.part_path(self.root, "dup").write_bytes(b"old")
        w = q.QuarantineWriter(self.root, "dup", max_bytes=100)
        with self.assertRaises(q.QuarantineUnavailable):
            w.open()
        self.assertEqual(q.part_path(self.root, "dup").read_bytes(), b"old")

    def test_does_not_follow_symlinked_part(self):
        q.ensure_dirs(self.root)
        target = Path(self._tmp.name) / "victim"
        target.write_bytes(b"keep")
        os.symlink(target, q.part_path(self.root, "link"))
        w = q.QuarantineWriter(self.root, "link", max_bytes=100)
        with self.assertRaises(q.QuarantineUnavailable):
            w.open()
        self.assertEqual(target.read_bytes(), b"keep")

    def test_too_large_stops_before_writing_past_limit(self):
        w = self._writer(max_bytes=10)
        w.write(b"%PDF-1.4\n")
        with self.assertRaises(q.UploadTooLarge):
            w.write(b"0123456789")
        self.assertEqual(w.size, 9)
        w.discard()
        self.assertFalse(w.part.exists())

    def test_magic_must_be_in_first_1024_bytes(self):
        for body in (b"hello" + PDF[5:], b" " * 1024 + PDF, b"%PDF-3.0\n%%EOF", b""):
            with self.subTest(body=body[:12]):
                w = self._writer(upload_id=f"m{len(body)}")
                w.write(body)
                with self.assertRaises(q.NotPdf):
                    w.finalize()
                w.discard()
                self.assertFalse(w.part.exists())
                self.assertFalse(w.final.exists())
        w = self._writer(upload_id="ok")
        w.write(b"\n" * 1000 + b"%PDF-2.0\n" + b"y" * 3000 + b"%%EOF")
        self.assertTrue(w.finalize().path.exists())

    def test_eof_must_be_in_last_1024_bytes(self):
        w = self._writer(upload_id="trunc")
        w.write(b"%PDF-1.7\n%%EOF\n" + b"z" * 2000)
        with self.assertRaises(q.NotPdf):
            w.finalize()
        w.discard()
        self.assertFalse(w.part.exists())

    def test_eof_split_across_chunks(self):
        w = self._writer(upload_id="split")
        w.write(b"%PDF-1.7\n" + b"a" * 3000 + b"%%E")
        w.write(b"OF\n")
        self.assertTrue(w.finalize().path.exists())

    def test_discard_after_finalize_removes_bin(self):
        w = self._writer(upload_id="gone")
        w.write(PDF)
        stored = w.finalize()
        w.discard()
        self.assertFalse(stored.path.exists())

    def test_finalize_refuses_to_overwrite(self):
        w = self._writer(upload_id="taken")
        w.write(PDF)
        q.bin_path(self.root, "taken").write_bytes(b"existing")
        with self.assertRaises(q.QuarantineUnavailable):
            w.finalize()
        w.discard()
        self.assertEqual(q.bin_path(self.root, "taken").read_bytes(), b"existing")

    def test_unavailable_dir(self):
        with self.assertRaises(q.QuarantineUnavailable):
            q.ensure_dirs(Path("/nonexistent/report-mark-quarantine-test"))
        not_dir = Path(self._tmp.name) / "file"
        not_dir.write_bytes(b"")
        with self.assertRaises(q.QuarantineUnavailable):
            q.ensure_dirs(not_dir)

    def test_free_space(self):
        q.ensure_dirs(self.root)
        q.check_free_space(self.root, needed_bytes=100, min_free_bytes=0)
        fake = mock.Mock(free=1000)
        with mock.patch.object(q.shutil, "disk_usage", return_value=fake):
            q.check_free_space(self.root, needed_bytes=100, min_free_bytes=900)
            with self.assertRaises(q.QuarantineUnavailable):
                q.check_free_space(self.root, needed_bytes=101, min_free_bytes=900)
        with mock.patch.object(q.shutil, "disk_usage", side_effect=OSError("gone")):
            with self.assertRaises(q.QuarantineUnavailable):
                q.check_free_space(self.root, needed_bytes=0, min_free_bytes=0)


class QuarantineDirSettingTests(unittest.TestCase):
    def test_conftest_points_away_from_repo(self):
        self.assertEqual(os.environ["UPLOAD_QUARANTINE_DIR"], "/nonexistent/report-mark-quarantine")

    def test_resolution(self):
        base = config.get_settings()
        self.assertEqual(q.quarantine_dir(dataclasses.replace(base, upload_quarantine_dir="")), q.DEFAULT_DIR)
        self.assertEqual(q.DEFAULT_DIR, q.REPO_ROOT / "data" / "quarantine")
        self.assertEqual(q.quarantine_dir(dataclasses.replace(base, upload_quarantine_dir="/x/y")), Path("/x/y"))
        self.assertEqual(q.quarantine_dir(dataclasses.replace(base, upload_quarantine_dir="rel")), q.REPO_ROOT / "rel")


class UploadKnobTests(unittest.TestCase):
    def test_defaults(self):
        env = {k: "" for k in ("UPLOAD_ENABLED", "UPLOAD_MAX_BYTES", "UPLOAD_DAILY_QUOTA",
                               "UPLOAD_MAX_IN_FLIGHT", "UPLOAD_MIN_FREE_MB")}
        with mock.patch.dict(os.environ, env):
            s = config._load()
        self.assertFalse(s.upload_enabled)
        self.assertEqual(s.upload_max_bytes, 25 * 1024 * 1024)
        self.assertEqual((s.upload_daily_quota, s.upload_max_in_flight, s.upload_min_free_mb), (30, 50, 1024))

    def test_overrides_and_bad_values(self):
        env = {"UPLOAD_ENABLED": "1", "UPLOAD_MAX_BYTES": "1000", "UPLOAD_DAILY_QUOTA": "0",
               "UPLOAD_MAX_IN_FLIGHT": "x", "UPLOAD_MIN_FREE_MB": "0"}
        with mock.patch.dict(os.environ, env):
            s = config._load()
        self.assertTrue(s.upload_enabled)
        self.assertEqual(s.upload_max_bytes, 1000)
        self.assertEqual(s.upload_daily_quota, 30)  # 0 不合法 → 預設
        self.assertEqual(s.upload_max_in_flight, 50)
        self.assertEqual(s.upload_min_free_mb, 0)


if __name__ == "__main__":
    unittest.main()
