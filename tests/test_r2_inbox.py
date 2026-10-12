"""scripts/r2_inbox.py：沒有 NAS 的部署經 R2 inbox 收新研報（push／pull 對稱、路徑安全、mtime 保留）。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from app.services.object_storage import ObjectStorageError
from scripts import r2_inbox


class FakeStorage:
    """以 dict 模擬 bucket：key → (bytes, metadata)。只實作 r2_inbox 會用到的四個方法。"""

    enabled = True

    def __init__(self, fail_keys: set[str] | None = None) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.fail_keys = fail_keys or set()

    def put_transport_file(self, path, key, *, metadata):
        if key in self.fail_keys:
            raise ObjectStorageError("boom")
        self.objects[key] = (Path(path).read_bytes(), dict(metadata))

    def list_objects(self, prefix):
        return [(k, len(v[0])) for k, v in sorted(self.objects.items()) if k.startswith(prefix)]

    def download_to(self, key, target):
        if key in self.fail_keys:
            raise ObjectStorageError("boom")
        Path(target).write_bytes(self.objects[key][0])

    def head_object(self, key):
        return {"Metadata": self.objects[key][1]}


class DeltaNamesTests(unittest.TestCase):
    def test_skips_blank_dirs_and_duplicates(self):
        lines = ["", "券商A/", "券商A/a.pdf", "券商A/a.pdf", "  b.docx  "]
        self.assertEqual(r2_inbox.delta_names(lines), ["券商A/a.pdf", "b.docx"])


class SafeRelativeTests(unittest.TestCase):
    def test_rejects_paths_that_escape_the_mirror(self):
        for bad in ("", "/etc/passwd", "../x.pdf", "a/../../x.pdf", "a/.."):
            self.assertIsNone(r2_inbox.safe_relative(bad), bad)

    def test_accepts_nested_cjk_paths(self):
        self.assertEqual(r2_inbox.safe_relative("券商A/2026/報告.pdf"), "券商A/2026/報告.pdf")


class PushPullTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.src = base / "src"
        self.dst = base / "dst"
        (self.src / "券商A").mkdir(parents=True)
        self.dst.mkdir()
        self.report = self.src / "券商A" / "報告.pdf"
        self.report.write_bytes(b"%PDF-1.4 hello")
        os.utime(self.report, (1_700_000_000, 1_700_000_000))

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip_keeps_relative_path_and_mtime(self):
        storage = FakeStorage()
        pushed, failed = r2_inbox.push(storage, ["券商A/", "券商A/報告.pdf"], self.src)
        self.assertEqual((pushed, failed), (1, 0))
        self.assertIn("inbox/券商A/報告.pdf", storage.objects)

        landed, failed = r2_inbox.pull(storage, self.dst)
        self.assertEqual((landed, failed), (["券商A/報告.pdf"], 0))
        got = self.dst / "券商A" / "報告.pdf"
        self.assertEqual(got.read_bytes(), b"%PDF-1.4 hello")
        self.assertEqual(int(got.stat().st_mtime), 1_700_000_000)

    def test_pull_skips_files_already_mirrored_with_same_size(self):
        storage = FakeStorage()
        r2_inbox.push(storage, ["券商A/報告.pdf"], self.src)
        r2_inbox.pull(storage, self.dst)
        landed, _ = r2_inbox.pull(storage, self.dst)
        self.assertEqual(landed, [])

    def test_pull_refetches_when_size_differs(self):
        storage = FakeStorage()
        r2_inbox.push(storage, ["券商A/報告.pdf"], self.src)
        (self.dst / "券商A").mkdir()
        (self.dst / "券商A" / "報告.pdf").write_bytes(b"partial")
        landed, _ = r2_inbox.pull(storage, self.dst)
        self.assertEqual(landed, ["券商A/報告.pdf"])

    def test_pull_failure_leaves_no_partial_file(self):
        storage = FakeStorage()
        r2_inbox.push(storage, ["券商A/報告.pdf"], self.src)
        storage.fail_keys = {"inbox/券商A/報告.pdf"}
        landed, failed = r2_inbox.pull(storage, self.dst)
        self.assertEqual((landed, failed), ([], 1))
        self.assertEqual([p for p in self.dst.rglob("*") if p.is_file()], [])

    def test_pull_rejects_unsafe_keys(self):
        storage = FakeStorage()
        storage.objects["inbox/../evil.pdf"] = (b"x", {})
        landed, failed = r2_inbox.pull(storage, self.dst)
        self.assertEqual((landed, failed), ([], 0))
        self.assertFalse((self.dst.parent / "evil.pdf").exists())

    def test_push_counts_failures_and_skips_missing(self):
        storage = FakeStorage(fail_keys={"inbox/券商A/報告.pdf"})
        pushed, failed = r2_inbox.push(storage, ["券商A/報告.pdf", "不存在.pdf"], self.src)
        self.assertEqual((pushed, failed), (0, 1))


class MainTests(unittest.TestCase):
    def test_partial_pull_signals_failure_and_keeps_successful_delta(self):
        with (
            mock.patch.object(sys, "argv", ["r2_inbox", "pull", "--delta", "fake-delta"]),
            mock.patch.object(r2_inbox, "get_object_storage", return_value=FakeStorage()),
            mock.patch.object(r2_inbox, "pull", return_value=(["券商A/報告.pdf"], 1)),
            mock.patch.object(r2_inbox, "Path") as fake_path,
        ):
            self.assertEqual(r2_inbox.main(), 1)
            fake_path.return_value.write_text.assert_called_once_with("券商A/報告.pdf\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
