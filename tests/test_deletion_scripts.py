"""scripts/execute_deletions.py、scripts/replay_deletions.py 與 app/services/tombstones.py（假帳號庫，不連 DB）。

真的 SQL（刪哪些表、同一筆交易）由 tests/test_accounts_db.py 驗；這裡驗批次流程：落點不存在就
整批不做、先寫 tombstone 再刪、重放偵測到殘留時重新刪除並回 rc=1。
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from fake_accounts import FakeAccounts

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import tombstones  # noqa: E402
from scripts import execute_deletions as ex  # noqa: E402
from scripts import replay_deletions as rp  # noqa: E402

NOW = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / tombstones.TOMBSTONE_NAME
        self.store = FakeAccounts()
        self.admin = self.store.add_user("root", "root-password-1", "admin")
        self.alice = self.store.add_user("alice", "alice-password-1", "user")
        self.bob = self.store.add_user("bob", "bob-password-1", "user")
        self.store.seed_qa_log(self.alice, with_review=True)
        self.store.seed_qa_log(self.bob)

    def _exec(self, **kw):
        args = SimpleNamespace(**{"dry_run": False, "tombstone_file": str(self.path), **kw})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = asyncio.run(ex.run(args, api=self.store, now=lambda: NOW))
        return rc, out.getvalue(), err.getvalue()

    def _replay(self, **kw):
        args = SimpleNamespace(**{"check_only": False, "tombstone_file": str(self.path), **kw})
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = asyncio.run(rp.run(args, api=self.store))
        return rc, out.getvalue(), err.getvalue()

    def _lines(self):
        return [json.loads(x) for x in self.path.read_text(encoding="utf-8").splitlines() if x.strip()]


class ExecuteDeletionsTests(_Base):
    def test_only_due_deletions_run_and_tombstone_has_no_username(self):
        asyncio.run(self.store.request_deletion(self.alice, actor_id=self.admin, delay_seconds=0))
        asyncio.run(self.store.request_deletion(self.bob, actor_id=self.admin))  # 24 小時後才到期
        rc, out, err = self._exec()
        self.assertEqual(rc, 0, err)
        self.assertIn(self.alice, out)
        self.assertEqual(self._lines(), [{"user_id": self.alice, "executed_at": NOW.isoformat(timespec="seconds")}])
        self.assertNotIn("alice", self.path.read_text(encoding="utf-8"))
        self.assertTrue(asyncio.run(self.store.deletion_residue(self.alice)).clean)
        self.assertEqual(asyncio.run(self.store.deletion_residue(self.bob)).qa_log, 1)  # 還沒到期，不動
        self.assertEqual(self._exec()[0], 0)  # 再跑一次：沒有到期的
        self.assertEqual(len(self._lines()), 1)

    def test_dry_run_touches_nothing(self):
        asyncio.run(self.store.request_deletion(self.alice, actor_id=self.admin, delay_seconds=0))
        rc, out, _ = self._exec(dry_run=True)
        self.assertEqual(rc, 0)
        self.assertIn("dry-run", out)
        self.assertFalse(self.path.exists())
        self.assertEqual(asyncio.run(self.store.deletion_residue(self.alice)).qa_log, 1)

    def test_missing_destination_refuses_before_touching_db(self):
        asyncio.run(self.store.request_deletion(self.alice, actor_id=self.admin, delay_seconds=0))
        missing = Path(self.tmp.name) / "nas-not-mounted" / tombstones.TOMBSTONE_NAME
        rc, _, err = self._exec(tombstone_file=str(missing))
        self.assertEqual(rc, 2)
        self.assertIn("刻意不改寫到本機", err)
        self.assertEqual(asyncio.run(self.store.deletion_residue(self.alice)).qa_log, 1)

    def test_no_destination_configured(self):
        with self.assertRaises(tombstones.TombstoneError):
            tombstones.resolve_tombstone_file(None, env={})
        self.assertEqual(tombstones.default_tombstone_file({"REPORT_MARK_BACKUP_DIR": "/mnt/nas"}),
                         Path("/mnt/nas") / tombstones.TOMBSTONE_NAME)

    def test_db_failure_after_tombstone_is_rc1(self):
        asyncio.run(self.store.request_deletion(self.alice, actor_id=self.admin, delay_seconds=0))

        async def boom(*_a, **_k):
            raise RuntimeError("db gone")

        self.store.execute_deletion = boom
        rc, _, err = self._exec()
        self.assertEqual(rc, 1)
        self.assertIn("replay_deletions", err)
        self.assertEqual([x["user_id"] for x in self._lines()], [self.alice])  # tombstone 先寫了


class ReplayDeletionsTests(_Base):
    def test_clean_is_rc0_and_missing_file_means_nothing_deleted_yet(self):
        rc, out, _ = self._replay()
        self.assertEqual(rc, 0)
        self.assertIn("0 個 tombstone", out)
        asyncio.run(self.store.request_deletion(self.alice, actor_id=self.admin, delay_seconds=0))
        self._exec()
        rc, out, _ = self._replay()
        self.assertEqual(rc, 0, out)

    def test_restored_data_is_deleted_again_with_rc1(self):
        asyncio.run(self.store.request_deletion(self.alice, actor_id=self.admin, delay_seconds=0))
        self._exec()
        # 模擬從刪除前的備份還原：問答回來了、帳號又可以登入
        self.store.seed_qa_log(self.alice, with_review=True)
        row = self.store.users[self.alice]
        row.username, row.password, row.enabled, row.deleted_at = "alice", "alice-password-1", True, None
        rc, _, err = self._check_only_then_replay()
        self.assertEqual(rc, 1)
        self.assertIn("已重新刪除", err)
        self.assertTrue(asyncio.run(self.store.deletion_residue(self.alice)).clean)
        self.assertEqual(self.store.audit[0].action, "user.delete_replayed")
        self.assertEqual(self._replay()[0], 0)  # 之後就乾淨了

    def _check_only_then_replay(self):
        rc, _, err = self._replay(check_only=True)
        self.assertEqual(rc, 1)
        self.assertIn("--check-only", err)
        self.assertEqual(asyncio.run(self.store.deletion_residue(self.alice)).qa_log, 1)  # 只回報不刪
        return self._replay()

    def test_pending_schedule_from_old_backup_is_closed(self):
        asyncio.run(self.store.request_deletion(self.alice, actor_id=self.admin, delay_seconds=0))
        tombstones.append_tombstone(self.path, self.alice, NOW)  # tombstone 已寫，DB 執行前就還原了
        rc, _, _ = self._replay()
        self.assertEqual(rc, 1)
        self.assertFalse(asyncio.run(self.store.deletion_residue(self.alice)).pending_deletion)
        self.assertEqual(asyncio.run(self.store.due_deletions()), [])

    def test_corrupt_tombstone_file_is_rc2(self):
        self.path.write_text('{"user_id": "not-a-uuid"}\n', encoding="utf-8")
        rc, _, err = self._replay()
        self.assertEqual(rc, 2)
        self.assertIn("第 1 行", err)

    def test_duplicate_lines_are_checked_once(self):
        for _ in range(2):
            tombstones.append_tombstone(self.path, self.alice, NOW)
        self.assertEqual(tombstones.read_tombstones(self.path), [self.alice])


if __name__ == "__main__":
    unittest.main()
