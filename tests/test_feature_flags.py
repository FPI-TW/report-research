"""功能旗標讀取核心（app/services/feature_flags.py）：env 上限 AND DB 政策、DB 失敗退回預設、5 秒快取與立即失效。

不連 DB：session factory 換成假的。`_LOAD_SQL` 對真的 PostgreSQL 的行為在 tests/test_usage_events_db.py。
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import unittest
import uuid
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
        """沒有任何 DB 覆寫時，每個旗標＝它的環境變數上限，兩個例外預設關：

        - quota.enforce：影子模式（使用者定案 5，觀察兩週再決定）。
        - ask.web_search：v1 的前端把網搜寫死成暫停（不論 ASK_ENABLE_WEB 都看不到開關、請求一律 web=false），
          網搜後端也不存在；預設關才是零行為改變——環境變數未設（＝1）時網搜開關也不會冒出來。
        """
        settings = config.get_settings()
        state = asyncio.run(ff.snapshot(session_factory=_factory()))
        default_off = {"quota.enforce", "ask.web_search"}
        for key, spec in ff.REGISTRY.items():
            expected = spec.ceiling(settings) and key not in default_off
            self.assertEqual(state[key].effective, expected, key)
            self.assertEqual(state[key].source, "default")
        self.assertEqual({k for k, s in ff.REGISTRY.items() if not s.default}, default_off)

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


def _routing_factory(rows=(), *, role=None, role_fail=False, calls=None):
    """覆寫查詢回 rows、角色查詢回 role（或拋錯）。calls 記下每次查的是哪一種。"""

    class _Result:
        def __init__(self, data):
            self._data = data

        def all(self):
            return list(self._data)

        def scalar_one_or_none(self):
            return self._data[0][0] if self._data else None

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt, params=None):
            kind = "role" if "research.app_user" in str(stmt) else "flags"
            if calls is not None:
                calls.append(kind)
            if kind == "role":
                if role_fail:
                    raise RuntimeError("DB 掛了")
                return _Result([(role,)] if role else [])
            return _Result(rows)

    return lambda: _Session()


class PolicyTests(unittest.TestCase):
    """`policy()`：只有 DB 政策、不含上限；身分可以是 User、UUID 字串或省略。"""

    def setUp(self):
        ff.invalidate()

    def tearDown(self):
        ff.invalidate()

    def _with_ceilings(self, **values):
        return mock.patch.object(config, "_SETTINGS", dataclasses.replace(config.get_settings(), **values))

    def test_policy_ignores_the_ceiling_is_enabled_does_not(self):
        with self._with_ceilings(qa_agentic_enabled=False):
            self.assertTrue(asyncio.run(ff.policy("qa.agentic", session_factory=_routing_factory())))
            self.assertFalse(asyncio.run(ff.is_enabled("qa.agentic", session_factory=_routing_factory())))

    def test_web_search_needs_ceiling_and_explicit_override(self):
        on = [("ask.web_search", True, None, None)]
        with self._with_ceilings(ask_enable_web=True):
            self.assertFalse(asyncio.run(ff.is_enabled("ask.web_search", session_factory=_routing_factory())))
            ff.invalidate()
            self.assertTrue(asyncio.run(ff.is_enabled("ask.web_search", session_factory=_routing_factory(on))))
        ff.invalidate()
        with self._with_ceilings(ask_enable_web=False):
            self.assertFalse(asyncio.run(ff.is_enabled("ask.web_search", session_factory=_routing_factory(on))))

    def test_ceiling_off_does_not_even_read_the_db(self):
        calls: list = []
        with self._with_ceilings(qa_agentic_enabled=False):
            self.assertFalse(asyncio.run(ff.is_enabled("qa.agentic", session_factory=_routing_factory(calls=calls))))
        self.assertEqual(calls, [])

    def test_user_id_string_matches_allow_users_without_a_role_lookup(self):
        calls: list = []
        rows = [("qa.agentic", True, ["admin"], [MEMBER.id.upper()])]
        f = _routing_factory(rows, role="user", calls=calls)
        self.assertTrue(asyncio.run(ff.policy("qa.agentic", MEMBER.id, session_factory=f)))
        self.assertEqual(calls, ["flags"])  # allow_users 命中就不查角色

    def test_role_scope_with_user_id_string_looks_up_the_role_and_caches_it(self):
        calls: list = []
        rows = [("qa.agentic", True, ["admin"], None)]
        f = _routing_factory(rows, role="admin", calls=calls)
        self.assertTrue(asyncio.run(ff.policy("qa.agentic", ADMIN.id, session_factory=f)))
        self.assertTrue(asyncio.run(ff.policy("qa.agentic", ADMIN.id, session_factory=f)))
        self.assertEqual(calls, ["flags", "role"])  # 覆寫與角色都快取
        ff.invalidate()
        self.assertFalse(asyncio.run(ff.policy("qa.agentic", OTHER.id,
                                               session_factory=_routing_factory(rows, role="user"))))

    def test_role_lookup_failure_means_the_scope_does_not_apply(self):
        rows = [("qa.agentic", True, ["admin"], None)]
        with self.assertLogs("app.services.feature_flags", "WARNING"):
            ok = asyncio.run(ff.policy("qa.agentic", ADMIN.id, session_factory=_routing_factory(rows, role_fail=True)))
        self.assertFalse(ok)

    def test_garbage_user_id_is_no_identity(self):
        rows = [("qa.agentic", True, None, [MEMBER.id])]
        self.assertFalse(asyncio.run(ff.policy("qa.agentic", "not-a-uuid", session_factory=_routing_factory(rows))))
        self.assertFalse(asyncio.run(ff.policy("qa.agentic", None, session_factory=_routing_factory(rows))))

    def test_snapshot_with_user_object(self):
        rows = [("ask.rerank", True, ["admin"], None)]
        with self._with_ceilings(ask_rerank_enabled=True):
            state = asyncio.run(ff.snapshot(ADMIN, session_factory=_routing_factory(rows)))
            self.assertTrue(state["ask.rerank"].effective)
            ff.invalidate()
            self.assertFalse(
                asyncio.run(ff.snapshot(MEMBER, session_factory=_routing_factory(rows)))["ask.rerank"].effective)


class ReplacedConstantsTests(unittest.TestCase):
    """被替換的常數讀取：呼叫點寫成「模組常數 AND policy()」，所以模組常數必須就是 registry 的上限。"""

    def test_module_constants_are_the_registry_ceilings(self):
        from app.services import answer, trusted_market_data

        s = config.get_settings()
        self.assertEqual(answer.ASK_ENABLE_WEB, ff.REGISTRY["ask.web_search"].ceiling(s))
        self.assertEqual(answer.ASK_FAITHFULNESS_ENABLED, ff.REGISTRY["qa.faithfulness"].ceiling(s))
        rerank_on = ff.REGISTRY["ask.rerank"].ceiling(s) and s.ask_rerank_candidates > 0
        self.assertEqual(answer.ASK_RERANK_TOP_M > 0, rerank_on)
        self.assertEqual(trusted_market_data.TRUSTED_DATA_ENABLED, ff.REGISTRY["trusted_data"].ceiling(s))

    def test_ceiling_env_names_match_the_config_reads(self):
        """registry 寫的環境變數名稱就是 app/config.py 讀的那一個（管理頁與文件照它說「需先在環境檔開啟」）。"""
        from pathlib import Path

        src = (Path(config.__file__)).read_text(encoding="utf-8")
        for spec in ff.REGISTRY.values():
            self.assertIn(f'"{spec.ceiling_env}"', src, spec.key)

    def test_registry_version_is_a_stable_fingerprint(self):
        self.assertRegex(ff.REGISTRY_VERSION, r"^[0-9a-f]{12}$")


class NormalizeTests(unittest.TestCase):
    """寫入前的驗證與正規化（不連 DB）。"""

    def test_dedupes_sorts_and_lowercases(self):
        o = ff._normalize("qa.agentic", enabled=True, allow_roles=["user", "admin", "user"],
                          allow_users=[MEMBER.id.upper(), MEMBER.id], note="  先關掉觀察  ")
        self.assertEqual(o.allow_roles, ("admin", "user"))
        self.assertEqual(o.allow_users, (MEMBER.id,))
        self.assertEqual(o.note, "先關掉觀察")

    def test_rejections(self):
        cases = [
            dict(key="no.such", enabled=True),
            dict(key="qa.agentic", enabled=True, allow_roles=["root"]),
            dict(key="qa.agentic", enabled=True, allow_users=["bob"]),
            dict(key="qa.agentic", enabled=True, allow_roles=[], allow_users=[]),  # 空作用域不可變成全站
            dict(key="qa.agentic", enabled=True, allow_roles=[]),
            dict(key="qa.agentic", enabled=True, note="字" * 501),
            dict(key="qa.agentic", enabled=True, allow_users=[str(uuid.uuid4()) for _ in range(201)]),
        ]
        for kw in cases:
            key = kw.pop("key")
            with self.subTest(key=key, **{k: str(v)[:30] for k, v in kw.items()}):
                with self.assertRaises(ff.FlagError):
                    ff._normalize(key, allow_roles=kw.get("allow_roles"), allow_users=kw.get("allow_users"),
                                  note=kw.get("note"), enabled=kw["enabled"])
        with self.assertRaises(ff.UnknownFlagError):
            ff._normalize("no.such", enabled=True, allow_roles=None, allow_users=None, note=None)

    def test_empty_note_is_none_and_global_scope_stays_global(self):
        o = ff._normalize("qa.agentic", enabled=False, allow_roles=None, allow_users=None, note="   ")
        self.assertEqual((o.allow_roles, o.allow_users, o.note), (None, None, None))

    def test_scope_summary(self):
        self.assertEqual(ff._scope_summary(None), "沒有覆寫（registry 預設）")
        self.assertEqual(ff._scope_summary(ff.StoredOverride("k", False, None, None)), "關閉")
        self.assertEqual(ff._scope_summary(ff.StoredOverride("k", True, None, None)), "全站開啟")
        self.assertEqual(ff._scope_summary(ff.StoredOverride("k", True, ("admin",), (MEMBER.id,))),
                         "限定開啟：角色 admin；指定使用者 1 位")


if __name__ == "__main__":
    unittest.main()
