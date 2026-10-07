"""每人配額的服務層（app/services/quota.py），不連 DB：session factory 換成假的。

驗：Retry-After＝距台北午夜的秒數；影子模式的雙開關真值表（env `QUOTA_ENFORCE` AND 旗標 `quota.enforce`，旗標讀取
失敗退回影子模式）；影子模式超額照常放行但記 `<kind>_over`；正式阻擋才不放行；DB 失敗放行並記 WARNING；沒有
身分不計數；個人覆寫（NULL＝不限、0＝完全不能用）；覆寫輸入的驗證。SQL 本身對真的 PostgreSQL 的行為（含並發）在
tests/test_quota_db.py。
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
import os
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app import config
from app.services import feature_flags as ff
from app.services import quota
from app.services.accounts import User

ALICE = User(id="aaaaaaaa-0000-0000-0000-00000000000a", username="alice", role="user")
_MISSING = object()


class _Result:
    def __init__(self, row):
        self.row = row

    def first(self):
        return self.row


class FakeDB:
    """模擬 `_LIMIT_SQL`、`_CHARGE_SQL`、`_OVER_SQL` 的語意（計數存在 self.counts）。"""

    def __init__(self, *, override=_MISSING, fail: Exception | None = None):
        self.override = override
        self.fail = fail
        self.counts: dict[str, int] = {}
        self.commits = 0
        self.statements: list[str] = []

    def factory(self):
        db = self

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, stmt, params=None):
                sql = str(stmt)
                db.statements.append(sql)
                if db.fail is not None:
                    raise db.fail
                if "FROM research.user_quota" in sql:
                    return _Result(None if db.override is _MISSING else (db.override,))
                kind = params["kind"]
                if "CAST(:limit AS integer)" in sql:
                    limit, cur = params["limit"], db.counts.get(kind)
                    if cur is None:
                        if limit is None or limit > 0:
                            db.counts[kind] = 1
                            return _Result((1,))
                        return _Result(None)
                    if limit is None or cur < limit:
                        db.counts[kind] = cur + 1
                        return _Result((cur + 1,))
                    return _Result(None)
                db.counts[kind] = db.counts.get(kind, 0) + 1  # _OVER_SQL：不設上限
                return _Result(None)

            async def commit(self):
                db.commits += 1

        return _Session()


def _settings(**over):
    return mock.patch.object(config, "_SETTINGS", dataclasses.replace(config.get_settings(), **over))


def _flag_cache(enabled: bool | None, *, ok: bool = True):
    """直接放進 feature_flags 的快取：enabled=None＝DB 沒有這個旗標的列；ok=False＝讀取失敗（退回預設）。"""
    data = {} if enabled is None else {quota.FLAG_KEY: ff.Override(enabled)}
    ff._cache = (time.monotonic(), data, ok)


def _loaded_defaults():
    """環境變數沒設時 `app/config.py` 載入的值。"""
    keys = ("QUOTA_ASK_DAILY", "QUOTA_EXPORT_DAILY", "UPLOAD_DAILY_QUOTA", "QUOTA_ENFORCE")
    env = {k: v for k, v in os.environ.items() if k not in keys}
    with mock.patch.dict(os.environ, env, clear=True):
        return config._load()


def _charge(db: FakeDB, user=ALICE, kind="ask", **kw):
    return asyncio.run(quota.charge(user, kind, session_factory=db.factory, **kw))


class RetryAfterTests(unittest.TestCase):
    def test_seconds_until_taipei_midnight(self):
        # 台北 23:59:30 → 30 秒；台北 00:00:00 → 一整天
        self.assertEqual(quota.retry_after_seconds(datetime(2026, 10, 7, 15, 59, 30, tzinfo=timezone.utc)), 30)
        self.assertEqual(quota.retry_after_seconds(datetime(2026, 10, 6, 16, 0, 0, tzinfo=timezone.utc)), 86400)

    def test_rounds_up_and_is_at_least_one(self):
        t = datetime(2026, 10, 7, 15, 59, 59, 500_000, tzinfo=timezone.utc)
        self.assertEqual(quota.retry_after_seconds(t), 1)

    def test_taipei_day_cuts_at_16_utc(self):
        self.assertEqual(str(quota.taipei_day(datetime(2026, 10, 7, 15, 59, tzinfo=timezone.utc))), "2026-10-07")
        self.assertEqual(str(quota.taipei_day(datetime(2026, 10, 7, 16, 0, tzinfo=timezone.utc))), "2026-10-08")


class EnforcementTruthTableTests(unittest.TestCase):
    """正式阻擋＝env 上限 AND DB 旗標；任何一道關＝影子模式。旗標讀取失敗＝registry 預設（關）。"""

    def tearDown(self):
        ff.invalidate()

    def test_truth_table(self):
        for env, flag in itertools.product((False, True), (None, False, True)):
            with self.subTest(env=env, flag=flag), _settings(quota_enforce=env):
                _flag_cache(flag)
                expected = env and flag is True
                self.assertEqual(asyncio.run(quota.enforcement_active(ALICE)), expected)

    def test_flag_read_failure_falls_back_to_shadow(self):
        with _settings(quota_enforce=True):
            _flag_cache(True, ok=False)
            self.assertFalse(asyncio.run(quota.enforcement_active(ALICE)))

    def test_registry_default_is_off(self):
        self.assertFalse(ff.REGISTRY[quota.FLAG_KEY].default)
        self.assertFalse(_loaded_defaults().quota_enforce)


class ChargeTests(unittest.TestCase):
    def setUp(self):
        self._p = _settings(quota_ask_daily=2, quota_export_daily=1)
        self._p.start()
        self.addCleanup(self._p.stop)
        _flag_cache(None)  # 旗標「沒有列」：不讓超額時的旗標讀取去連 DB
        self.addCleanup(ff.invalidate)

    def test_within_limit_counts(self):
        db = FakeDB()
        d1, d2 = _charge(db), _charge(db)
        self.assertEqual((d1.allowed, d1.counted, d1.count, d1.limit), (True, True, 1, 2))
        self.assertEqual((d2.count, d2.over), (2, False))
        self.assertEqual(db.counts, {"ask": 2})

    def test_shadow_mode_allows_and_records_over(self):
        db = FakeDB()
        with _settings(quota_ask_daily=2, quota_enforce=False):
            _flag_cache(True)  # 旗標開了，但 env 上限關 → 仍是影子模式
            with self.assertLogs("app.services.quota", "INFO") as logs:
                decisions = [_charge(db) for _ in range(4)]
        self.assertTrue(all(d.allowed for d in decisions))
        self.assertEqual([d.over for d in decisions], [False, False, True, True])
        self.assertEqual([d.enforced for d in decisions[2:]], [False, False])
        self.assertEqual(db.counts, {"ask": 2, "ask_over": 2}, "封頂在上限、超出的部分記在 ask_over")
        self.assertTrue(any("quota_over kind=ask" in line and "enforced=False" in line for line in logs.output))

    def test_enforced_blocks_with_retry_after(self):
        db = FakeDB()
        now = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)  # 台北 23:00
        with _settings(quota_ask_daily=1, quota_enforce=True):
            _flag_cache(True)
            ok, blocked = _charge(db, now=now), _charge(db, now=now)
        self.assertTrue(ok.allowed)
        self.assertEqual((blocked.allowed, blocked.over, blocked.enforced, blocked.limit), (False, True, True, 1))
        self.assertEqual(blocked.retry_after, 3600)
        self.assertEqual(db.counts, {"ask": 1, "ask_over": 1})

    def test_flag_lookup_only_when_over(self):
        db = FakeDB()
        with mock.patch.object(quota, "enforcement_active", side_effect=AssertionError("不該查旗標")):
            self.assertTrue(_charge(db).allowed)

    def test_db_failure_allows_and_warns(self):
        db = FakeDB(fail=RuntimeError("DB 掛了"))
        with self.assertLogs("app.services.quota", "WARNING") as logs:
            d = _charge(db)
        self.assertEqual((d.allowed, d.error, d.counted), (True, True, False))
        self.assertIn("計數寫入失敗", logs.output[0])

    def test_no_identity_is_not_counted(self):
        db = FakeDB()
        dev = User(id=None, username="dev", role="admin")
        self.assertEqual(_charge(db, user=dev), quota.Decision(kind="ask", allowed=True))
        self.assertEqual(db.statements, [])

    def test_override_null_is_unlimited(self):
        db = FakeDB(override=None)
        decisions = [_charge(db) for _ in range(5)]
        self.assertTrue(all(d.counted and d.limit is None for d in decisions))
        self.assertEqual(db.counts, {"ask": 5})

    def test_override_zero_blocks_from_the_first(self):
        db = FakeDB(override=0)
        with _settings(quota_enforce=True):
            _flag_cache(True)
            d = _charge(db)
        self.assertEqual((d.allowed, d.over, d.limit), (False, True, 0))
        self.assertEqual(db.counts, {"ask_over": 1})

    def test_override_replaces_default(self):
        db = FakeDB(override=3)
        self.assertEqual([_charge(db).over for _ in range(4)], [False, False, False, True])

    def test_export_uses_its_own_default(self):
        db = FakeDB()
        self.assertEqual([_charge(db, kind="export").over for _ in range(2)], [False, True])
        self.assertEqual(db.counts, {"export": 1, "export_over": 1})

    def test_upload_is_not_charged_here(self):
        with self.assertRaises(ValueError):
            _charge(FakeDB(), kind="upload")

    def test_message(self):
        d = quota.Decision(kind="ask", allowed=False, over=True, enforced=True, limit=100)
        self.assertIn("100", quota.exceeded_message(d))
        self.assertIn("問答", quota.exceeded_message(d))


class ResolveLimitTests(unittest.TestCase):
    def _resolve(self, override, user_id=ALICE.id):
        db = FakeDB(override=override)

        async def go():
            async with db.factory() as s:
                return await quota.resolve_limit(s, user_id, "upload", 30)

        return asyncio.run(go())

    def test_cases(self):
        self.assertEqual(self._resolve(_MISSING), 30)
        self.assertIsNone(self._resolve(None))
        self.assertEqual(self._resolve(0), 0)
        self.assertEqual(self._resolve(5), 5)
        self.assertEqual(self._resolve(5, user_id=None), 30, "開發模式（沒有帳號）不查覆寫")


class ValidateTests(unittest.TestCase):
    def test_rejects(self):
        bad = [("nope", "limit", 1, None), ("ask", "weird", 1, None), ("ask", "limit", None, None),
               ("ask", "limit", -1, None), ("ask", "limit", quota.MAX_DAILY_LIMIT + 1, None),
               ("ask", "limit", True, None), ("ask", "limit", 1, "x" * 501)]
        for args in bad:
            with self.subTest(args=args[:3]), self.assertRaises(quota.InvalidQuotaInput):
                quota._validate(*args)

    def test_normalises(self):
        self.assertEqual(quota._validate("ask", "limit", 5, "  原因 "), (5, "原因"))
        self.assertEqual(quota._validate("ask", "unlimited", 5, "x"), (None, "x"))
        self.assertEqual(quota._validate("ask", "default", 5, "x"), (None, None), "清除覆寫不留理由")


class DefaultsTests(unittest.TestCase):
    def test_config_defaults(self):
        s = _loaded_defaults()
        self.assertEqual((s.quota_ask_daily, s.quota_export_daily, s.upload_daily_quota), (100, 20, 30))
        self.assertEqual({k: quota.default_limit(k, s) for k in quota.KINDS}, {"ask": 100, "export": 20, "upload": 30})

    def test_kinds_match_schema_check_and_usage_events(self):
        from app.services import usage_events

        self.assertEqual(set(quota.KINDS), set(usage_events.QUOTA_KINDS))
        self.assertTrue(set(quota.COUNTED_KINDS).isdisjoint(usage_events.COUNTER_KINDS), "middleware 不寫這幾類")
        for k in quota.COUNTED_KINDS:
            self.assertRegex(quota.over_kind(k), r"^[a-z][a-z_]{0,31}$")  # usage_counter.kind 的 CHECK

    def test_retry_after_never_exceeds_a_day(self):
        base = datetime(2026, 10, 7, tzinfo=timezone.utc)
        for minutes in range(0, 24 * 60, 37):
            secs = quota.retry_after_seconds(base + timedelta(minutes=minutes))
            self.assertTrue(1 <= secs <= 86400)
