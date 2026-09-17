# tests/test_repoint_file_paths.py
"""`scripts/repoint_file_paths.py` 的判定邏輯：純函式、不碰 DB。

守的是「保守優先」：只有目標檔存在且大小相同才 REPOINT；其餘每一種情況都要有
自己的名字，因為它們的處置不同（缺檔＝鏡像沒同步到；大小不同＝檔案被換過；
舊檔讀不到＝9p 沒掛，改用 --verify-hash）。
"""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts import repoint_file_paths as mod  # noqa: E402

OLD_PREFIX = "/mnt/c/legacy/研報自動匯入"


class PlanRowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.mirror = Path(self._tmp.name) / "mirror"
        self.old_root = Path(self._tmp.name) / "old"
        (self.mirror / "2026-07").mkdir(parents=True)
        (self.old_root / "2026-07").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _plan(self, rel: str, *, old_prefix: str | None = None, verify_hash: bool = False, file_hash: str = "x"):
        old_prefix = old_prefix or str(self.old_root)
        return mod.plan_row("rid", file_hash, f"{old_prefix}/{rel}",
                            old_prefix=old_prefix, mirror_root=str(self.mirror), verify_hash=verify_hash)

    def test_same_size_target_is_repointed_preserving_subdirectory(self):
        (self.old_root / "2026-07" / "a.pdf").write_bytes(b"12345")
        (self.mirror / "2026-07" / "a.pdf").write_bytes(b"abcde")
        plan = self._plan("2026-07/a.pdf")
        self.assertEqual(plan.verdict, "REPOINT")
        self.assertEqual(plan.new_path, str(self.mirror / "2026-07" / "a.pdf"))

    def test_missing_target_is_named_not_repointed(self):
        (self.old_root / "2026-07" / "a.pdf").write_bytes(b"12345")
        self.assertEqual(self._plan("2026-07/a.pdf").verdict, "MISSING_TARGET")

    def test_size_mismatch_is_named(self):
        (self.old_root / "2026-07" / "a.pdf").write_bytes(b"12345")
        (self.mirror / "2026-07" / "a.pdf").write_bytes(b"123456")
        self.assertEqual(self._plan("2026-07/a.pdf").verdict, "SIZE_MISMATCH")

    def test_old_unreadable_is_named_when_not_verifying_hash(self):
        # 舊掛載點不在（9p 沒掛）：不能拿「目標存在」就當相同
        (self.mirror / "2026-07" / "a.pdf").write_bytes(b"abcde")
        self.assertEqual(self._plan("2026-07/a.pdf").verdict, "OLD_UNREADABLE")

    def test_verify_hash_ignores_old_file_and_checks_target_digest(self):
        data = b"real bytes"
        (self.mirror / "2026-07" / "a.pdf").write_bytes(data)
        good = self._plan("2026-07/a.pdf", verify_hash=True, file_hash=hashlib.sha256(data).hexdigest())
        bad = self._plan("2026-07/a.pdf", verify_hash=True, file_hash="0" * 64)
        self.assertEqual(good.verdict, "REPOINT")
        self.assertEqual(bad.verdict, "HASH_MISMATCH")

    def test_prefix_with_trailing_slash_is_equivalent(self):
        self.assertEqual(mod.normalize_prefix("/a/b/"), "/a/b")
        self.assertEqual(mod.normalize_prefix("/a/b"), "/a/b")


class ArgsTests(unittest.TestCase):
    def test_apply_is_opt_in(self):
        args = mod.parse_args(["--old-prefix", OLD_PREFIX, "--mirror-root", "/m"])
        self.assertFalse(args.apply)
        self.assertFalse(args.verify_hash)
        self.assertEqual(args.limit, 0)


if __name__ == "__main__":
    unittest.main()
