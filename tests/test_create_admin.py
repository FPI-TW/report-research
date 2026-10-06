"""scripts/create_admin.py：第一位管理員與救援入口（對假帳號庫跑，不連 DB）。"""

from __future__ import annotations

import asyncio
import io
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fake_accounts import FakeAccounts

from scripts import create_admin


class _Engine:
    disposed = 0

    async def dispose(self):
        _Engine.disposed += 1


class CreateAdminCliTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeAccounts()
        self._patches = [
            patch.object(create_admin, "accounts", self.store),
            patch.object(create_admin, "db", SimpleNamespace(engine=_Engine())),
        ]
        for p in self._patches:
            p.start()

    def tearDown(self):
        for p in self._patches:
            p.stop()

    def _run(self, argv, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with patch("sys.stdin", io.StringIO(stdin)), patch("sys.stdout", out), patch("sys.stderr", err):
            rc = create_admin.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def _user(self, name):
        return next(u for u in self.store.users.values() if u.username == name)

    def test_creates_admin_by_default(self):
        rc, out, _ = self._run(["--username", "alice", "--password-stdin"], "alice-password-1\n")
        self.assertEqual(rc, 0, out)
        self.assertEqual(self._user("alice").role, "admin")
        self.assertEqual(self.store.audit[0].detail["via"], "cli")
        self.assertIsNone(self.store.audit[0].actor_user_id)

    def test_first_admin_becomes_super_when_none_exists(self):
        rc, out, _ = self._run(["--username", "alice", "--password-stdin"], "alice-password-1\n")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self._user("alice").is_super)
        self.assertIn("super admin", out)

    def test_later_admin_is_not_super_unless_asked(self):
        self.store.add_user("boss", "boss-password-1", "admin", is_super=True)
        self._run(["--username", "alice", "--password-stdin"], "alice-password-1\n")
        self.assertFalse(self._user("alice").is_super)
        self._run(["--username", "carol", "--super", "--password-stdin"], "carol-password-1\n")
        self.assertTrue(self._user("carol").is_super)

    def test_reset_with_super_promotes_for_rescue(self):
        self.store.add_user("boss", "boss-password-1", "admin", is_super=True, enabled=False)
        self.store.add_user("alice", "old-password-1", "admin")
        rc, out, _ = self._run(["--username", "alice", "--reset-password", "--super", "--password-stdin"],
                               "new-password-12\n")
        self.assertEqual(rc, 0, out)
        self.assertTrue(self._user("alice").is_super)
        self.assertIn("user.set_privileges", [e.action for e in self.store.audit])

    def test_super_requires_admin_role(self):
        rc, _, err = self._run(["--username", "bot", "--role", "user", "--super", "--password-stdin"],
                               "bot-password-123\n")
        self.assertEqual(rc, 1)
        self.assertIn("--super", err)

    def test_from_env_creates_super(self):
        with patch.dict("os.environ", {"REPORT_MARK_ACCESS_USERNAME": "tester",
                                       "REPORT_MARK_ACCESS_PASSWORD": "legacy-password-1"}):
            rc, *_ = self._run(["--from-env"])
        self.assertEqual(rc, 0)
        self.assertTrue(self._user("tester").is_super)

    def test_role_user(self):
        rc, *_ = self._run(["--username", "linebot", "--role", "user", "--password-stdin"], "bot-password-123\n")
        self.assertEqual(rc, 0)
        self.assertEqual(self._user("linebot").role, "user")

    def test_existing_without_reset_is_rejected(self):
        self.store.add_user("alice", "old-password-1", "admin")
        rc, _, err = self._run(["--username", "ALICE", "--password-stdin"], "new-password-12\n")
        self.assertEqual(rc, 1)
        self.assertIn("--reset-password", err)

    def test_reset_password_reenables_and_logs_out(self):
        uid = self.store.add_user("alice", "old-password-1", "admin", enabled=False)
        sid = asyncio.run(self.store.create_session(uid, max_age_seconds=3600))
        rc, out, err = self._run(["--username", "alice", "--reset-password", "--password-stdin"],
                                 "new-password-12\n")
        self.assertEqual(rc, 0, err)
        row = self._user("alice")
        self.assertEqual((row.password, row.enabled, row.role), ("new-password-12", True, "admin"))
        self.assertTrue(self.store.sessions[sid].revoked)

    def test_reset_totp_alone_disables_two_step(self):
        uid = self.store.add_user("alice", "alice-password-1", "admin")
        row = self.store.users[uid]
        row.totp_secret, row.totp_enabled, row.totp_last_step = "ABCDEFGHABCDEFGH", True, 1
        rc, out, err = self._run(["--username", "alice", "--reset-totp"])
        self.assertEqual(rc, 0, err)
        self.assertFalse(row.totp_enabled)
        self.assertIsNone(row.totp_secret)
        self.assertEqual(row.password, "alice-password-1")  # 沒有動密碼
        self.assertEqual(self.store.audit[0].action, "user.totp_reset")
        self.assertEqual(self.store.audit[0].detail["via"], "cli")
        self.assertEqual(self._run(["--username", "nobody", "--reset-totp"])[0], 1)

    def test_reset_password_refuses_account_pending_deletion(self):
        admin = self.store.add_user("root", "root-password-1", "admin")
        uid = self.store.add_user("alice", "alice-password-1", "user")
        asyncio.run(self.store.request_deletion(uid, actor_id=admin))
        rc, _out, err = self._run(["--username", "alice", "--reset-password", "--password-stdin"], "new-password-1\n")
        self.assertEqual(rc, 1)
        self.assertIn("取消刪除", err)
        self.assertFalse(self.store.users[uid].enabled)

    def test_policy_violation_exit_1(self):
        rc, _, err = self._run(["--username", "alice", "--password-stdin"], "short\n")
        self.assertEqual(rc, 1)
        self.assertIn("至少", err)
        self.assertEqual(self.store.users, {})

    def test_password_never_from_argv(self):
        # 沒有任何旗標能吃密碼值：argparse 對未知旗標直接報錯退出
        with self.assertRaises(SystemExit):
            self._run(["--username", "alice", "--password", "x"])

    def test_from_env_is_idempotent(self):
        env = {"REPORT_MARK_ACCESS_USERNAME": "legacy", "REPORT_MARK_ACCESS_PASSWORD": "legacy-password"}
        with patch.dict("os.environ", env):
            rc1, *_ = self._run(["--from-env"])
            rc2, out2, _ = self._run(["--from-env"])
        self.assertEqual((rc1, rc2), (0, 0))
        self.assertIn("已存在", out2)
        self.assertEqual(self._user("legacy").role, "admin")
        self.assertEqual(len(self.store.users), 1)

    def test_from_env_rejects_weak_legacy_password(self):
        env = {"REPORT_MARK_ACCESS_USERNAME": "legacy", "REPORT_MARK_ACCESS_PASSWORD": "short"}
        with patch.dict("os.environ", env):
            rc, _, err = self._run(["--from-env"])
        self.assertEqual(rc, 1)
        self.assertIn("--username legacy", err)
        self.assertEqual(self.store.users, {})

    def test_db_failure_is_exit_2(self):
        self.store.fail_with = RuntimeError("connection refused")
        rc, _, err = self._run(["--list"])
        self.assertEqual(rc, 2)
        self.assertIn("make schema", err)

    def test_requires_an_action(self):
        with self.assertRaises(SystemExit):
            self._run([])


if __name__ == "__main__":
    unittest.main()
