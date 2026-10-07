"""功能旗標讀取核心（app/services/feature_flags.py）：env 上限 AND DB 政策、DB 失敗退回預設、5 秒快取與立即失效。

不連 DB：session factory 換成假的。`_LOAD_SQL` 對真的 PostgreSQL 的行為在 tests/test_usage_events_db.py。
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import unittest
from unittest import mock

from app import config
from app.services import feature_flags as ff
from app.services.accounts import User

ADMIN = User(id="aaaaaaaa-0000-0000-0000-00000000000a", username="a", role="admin")
MEMBER = User(id="aaaaaaaa-0000-0000-0000-00000000000b", username="b", role="user")
OTHER = User(id="aaaaaaaa-0000-0000-0000-00000000000c", username="c", role="user")


def _factory(rows=(), *, fail=False, calls=None):
    class _Result:
        def all(self):
            return list(rows)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, *_a, **_k):
            if calls is not None:
                calls.append(1)
            if fail:
                raise RuntimeError("DB 掛了")
            return _Result()

    return lambda: _Session()


class EvaluateTruthTableTests(unittest.TestCase):
    spec_on = ff.FlagSpec("x.on", "", True, "X", lambda s: True)
    spec_off = ff.FlagSpec("x.off", "", False, "X", lambda s: True)

    def test_ceiling_off_always_wins(self):
        overrides = [None, ff.Override(True), ff.Override(False), ff.Override(True, frozenset({"admin"})),
                     ff.Override(True, None, frozenset({ADMIN.id}))]
        for spec, ov, user in itertools.product((self.spec_on, self.spec_off), overrides, (None, ADMIN, MEMBER)):
            with self.subTest(spec=spec.key, ov=ov, user=user and user.role):
                self.assertFalse(ff.evaluate(spec, ceiling=False, override=ov, user=user))

    def test_no_override_uses_registry_default(self):
        self.assertTrue(ff.evaluate(self.spec_on, ceiling=True, override=None))
        self.assertFalse(ff.evaluate(self.spec_off, ceiling=True, override=None))

    def test_global_override(self):
        for spec in (self.spec_on, self.spec_off):
            self.assertTrue(ff.evaluate(spec, ceiling=True, override=ff.Override(True), user=None))
            self.assertFalse(ff.evaluate(spec, ceiling=True, override=ff.Override(False), user=ADMIN))

    def test_scoped_override(self):
        by_role = ff.Override(True, frozenset({"admin"}))
        self.assertTrue(ff.evaluate(self.spec_off, ceiling=True, override=by_role, user=ADMIN))
        self.assertFalse(ff.evaluate(self.spec_off, ceiling=True, override=by_role, user=MEMBER))
        self.assertFalse(ff.evaluate(self.spec_off, ceiling=True, override=by_role, user=None))  # 背景工作
        by_user = ff.Override(True, None, frozenset({MEMBER.id}))
        self.assertTrue(ff.evaluate(self.spec_off, ceiling=True, override=by_user, user=MEMBER))
        self.assertFalse(ff.evaluate(self.spec_off, ceiling=True, override=by_user, user=OTHER))
        both = ff.Override(True, frozenset({"admin"}), frozenset({MEMBER.id}))
        self.assertTrue(ff.evaluate(self.spec_off, ceiling=True, override=both, user=MEMBER))
        self.assertTrue(ff.evaluate(self.spec_off, ceiling=True, override=both, user=ADMIN))
        self.assertFalse(ff.evaluate(self.spec_off, ceiling=True, override=both, user=OTHER))
        scoped_off = ff.Override(False, frozenset({"admin"}))
        self.assertFalse(ff.evaluate(self.spec_on, ceiling=True, override=scoped_off, user=ADMIN))


class RegistryTests(unittest.TestCase):
    def test_security_gates_are_not_flags(self):
        """DB 遺失時旗標退回預設：安全閘門不可以是旗標（管理員 TOTP 強制是 ADMIN_MFA_REQUIRED）。"""
        for key in ff.REGISTRY:
            self.assertFalse(any(word in key for word in ("mfa", "totp", "auth", "csrf", "security")), key)

    def test_keys_match_the_db_shape(self):
        import re

        shape = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")
        for key, spec in ff.REGISTRY.items():
            self.assertEqual(key, spec.key)
            self.assertTrue(shape.fullmatch(key) and len(key) <= 64, key)

    def test_defaults_leave_behaviour_unchanged(self):
        """沒有任何 DB 覆寫時，每個旗標＝它的環境變數上限（quota.enforce 例外：預設關＝影子模式）。"""
        settings = config.get_settings()
        state = asyncio.run(ff.snapshot(session_factory=_factory()))
        for key, spec in ff.REGISTRY.items():
            expected = spec.ceiling(settings) and key != "quota.enforce"
            self.assertEqual(state[key].effective, expected, key)
            self.assertEqual(state[key].source, "default")
        self.assertFalse(ff.REGISTRY["quota.enforce"].default)

    def test_unknown_key_is_a_programming_error(self):
        with self.assertRaises(KeyError):
            asyncio.run(ff.is_enabled("no.such.flag", session_factory=_factory()))


class DbOverrideTests(unittest.TestCase):
    def setUp(self):
        ff.invalidate()

    def tearDown(self):
        ff.invalidate()

    def _with_ceilings(self, **values):
        return mock.patch.object(config, "_SETTINGS", dataclasses.replace(config.get_settings(), **values))

    def test_db_override_applies_under_the_ceiling(self):
        rows = [("qa.agentic", False, None, None), ("quota.enforce", True, None, None),
                ("not.registered", True, None, None)]
        with self._with_ceilings(qa_agentic_enabled=True, quota_enforce=False):
            state = asyncio.run(ff.snapshot(session_factory=_factory(rows)))
        self.assertFalse(state["qa.agentic"].effective)
        self.assertEqual(state["qa.agentic"].source, "db")
        self.assertFalse(state["quota.enforce"].effective)  # DB 開了，但上限 QUOTA_ENFORCE=0
        self.assertNotIn("not.registered", state)
        ff.invalidate()
        with self._with_ceilings(quota_enforce=True):
            self.assertTrue(asyncio.run(ff.is_enabled("quota.enforce", session_factory=_factory(rows))))

    def test_scoped_override_from_db(self):
        rows = [("ask.rerank", True, ["admin"], None)]
        with self._with_ceilings(ask_rerank_enabled=True):
            self.assertTrue(asyncio.run(ff.is_enabled("ask.rerank", ADMIN, session_factory=_factory(rows))))
            self.assertFalse(asyncio.run(ff.is_enabled("ask.rerank", MEMBER, session_factory=_factory(rows))))

    def test_db_failure_falls_back_to_registry_default(self):
        with self._with_ceilings(qa_agentic_enabled=True, quota_enforce=True), \
                self.assertLogs("app.services.feature_flags", "WARNING"):
            state = asyncio.run(ff.snapshot(session_factory=_factory(fail=True)))
        self.assertTrue(state["qa.agentic"].effective)  # 預設 true、上限 true
        self.assertFalse(state["quota.enforce"].effective)  # 預設 false：DB 掛掉不會變成正式阻擋
        self.assertEqual({s.source for s in state.values()}, {"fallback"})

    def test_cache_ttl_and_invalidate(self):
        calls: list = []
        factory = _factory([("qa.agentic", False, None, None)], calls=calls)
        clock = [1000.0]
        with mock.patch.object(ff.time, "monotonic", lambda: clock[0]):
            asyncio.run(ff.is_enabled("qa.agentic", session_factory=factory))
            asyncio.run(ff.is_enabled("qa.agentic", session_factory=factory))
            self.assertEqual(len(calls), 1)  # 5 秒內共用
            clock[0] += 4.9
            asyncio.run(ff.is_enabled("qa.agentic", session_factory=factory))
            self.assertEqual(len(calls), 1)
            clock[0] += 0.2
            asyncio.run(ff.is_enabled("qa.agentic", session_factory=factory))
            self.assertEqual(len(calls), 2)  # 過期重查
            ff.invalidate()
            asyncio.run(ff.is_enabled("qa.agentic", session_factory=factory))
            self.assertEqual(len(calls), 3)  # 寫入後立即失效

    def test_failure_is_cached_too(self):
        calls: list = []
        factory = _factory(fail=True, calls=calls)
        with self.assertLogs("app.services.feature_flags", "WARNING"):
            asyncio.run(ff.is_enabled("qa.agentic", session_factory=factory))
            asyncio.run(ff.is_enabled("qa.agentic", session_factory=factory))
        self.assertEqual(len(calls), 1)  # DB 掛掉時不會每個請求都去撞


if __name__ == "__main__":
    unittest.main()
