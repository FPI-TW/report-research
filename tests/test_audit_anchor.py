"""scripts/audit_anchor.py：驗鏈、比對先前錨點、追加今天的鏈頭（不連 DB，用假帳號庫）。"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from fake_accounts import FakeAccounts

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import audit_anchor as aa  # noqa: E402


def _args(**kw):
    return SimpleNamespace(**{"verify_only": False, "anchor_file": None, **kw})


class AuditAnchorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / aa.ANCHOR_NAME
        self.store = FakeAccounts()
        uid = self.store.add_user("boss", "boss-password", "admin", is_super=True)
        asyncio.run(self.store.force_logout(uid, actor_id=None))

    def _run(self, **kw):
        kw.setdefault("anchor_file", str(self.path))
        return asyncio.run(aa.run(_args(**kw), api=self.store, now=lambda: datetime(2026, 10, 6, 4, 15)))

    def _lines(self):
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]

    def test_first_run_writes_anchor(self):
        self.assertEqual(self._run(), aa.EXIT_OK)
        (rec,) = self._lines()
        self.assertEqual(rec["head_id"], self.store.audit[0].id)
        self.assertEqual(rec["total"], 1)

    def test_second_run_checks_previous_anchor_and_appends(self):
        self._run()
        asyncio.run(self.store.force_logout(next(iter(self.store.users)), actor_id=None))
        self.assertEqual(self._run(), aa.EXIT_OK)
        self.assertEqual(len(self._lines()), 2)

    def test_rewritten_row_behind_an_anchor_is_tamper(self):
        self._run()
        self.store.broken_audit_ids.add(self._lines()[0]["head_id"])  # 該列 hash 變了（但鏈本身也會斷）
        self.assertEqual(self._run(), aa.EXIT_TAMPER)

    def test_anchor_mismatch_even_when_chain_looks_ok(self):
        """整條鏈被重算過：鏈驗證通過，但舊錨點的 hash 對不上。"""
        self.path.write_text(json.dumps({"at": "x", "head_id": self.store.audit[0].id,
                                         "head_hash": "deadbeef"}) + "\n", encoding="utf-8")
        self.assertEqual(self._run(), aa.EXIT_TAMPER)
        self.assertEqual(len(self._lines()), 1, "不符時不得追加新錨點")

    def test_anchor_pointing_to_missing_row_is_flagged(self):
        self.path.write_text(json.dumps({"at": "x", "head_id": 999999, "head_hash": "h"}) + "\n", encoding="utf-8")
        self.assertEqual(self._run(), aa.EXIT_TAMPER)

    def test_broken_chain_fails_before_touching_anchor_file(self):
        self.store.broken_audit_ids.add(self.store.audit[0].id)
        self.assertEqual(self._run(), aa.EXIT_TAMPER)
        self.assertFalse(self.path.exists())

    def test_verify_only_never_writes(self):
        self.assertEqual(self._run(verify_only=True), aa.EXIT_OK)
        self.assertFalse(self.path.exists())

    def test_missing_destination_does_not_fall_back_to_local(self):
        self.assertEqual(self._run(anchor_file=str(Path(self.tmp.name) / "nope" / "a.jsonl")), aa.EXIT_ERROR)

    def test_no_destination_configured(self):
        self.assertIsNone(aa.default_anchor_file({}))
        self.assertEqual(aa.default_anchor_file({"REPORT_MARK_BACKUP_DIR": "/mnt/x"}), Path("/mnt/x") / aa.ANCHOR_NAME)

    def test_db_unavailable_is_error_not_tamper(self):
        self.store.fail_with = RuntimeError("db down")
        self.assertEqual(self._run(), aa.EXIT_ERROR)

    def test_corrupt_anchor_file_is_flagged(self):
        self.path.write_text("not json\n", encoding="utf-8")
        self.assertEqual(self._run(), aa.EXIT_TAMPER)


if __name__ == "__main__":
    unittest.main()
