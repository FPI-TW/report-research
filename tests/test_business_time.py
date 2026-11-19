"""業務時區（profile 的 `[locale].timezone`，`app/services/business_time.py`）。

- 預設（section 或鍵缺席、預設 profile）是 Asia/Taipei：切日、Retry-After 與訊息與框架化之前逐字相同。
- 非 +8、有日光節約的時區（America/New_York）下，每日配額、用量、分析、簡報、匯出檔名的日界都依它切。
- 時區在呼叫時取值：同一個行程換 profile（`reset_profile_cache()`）就換時區，沒有 import 期凍結的常數。
- SQL 的 `AT TIME ZONE` 一律是綁定參數，不內插時區名稱；認不得的名稱在載入 profile 時就拒絕。
- 容器 entrypoint 的 `TZ` 與 profile 的關係（`resolve_tz`）。
"""
import json
import os
import re
import sys
import tempfile
import textwrap
import unittest
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import profile as P  # noqa: E402
from app.services import business_time  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
UTC = timezone.utc

MINIMAL = """
[institution]
name = "測試機構"
product_name = "測試研報平台"
"""


def _write(root: Path, extra: str = "") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.toml").write_text(textwrap.dedent(MINIMAL) + textwrap.dedent(extra), encoding="utf-8")
    return root


class _ProfileCase(unittest.TestCase):
    """在 tempfile 建一份 profile、指給 PROFILE_DIR（conftest 每題前後清快取）。"""

    LOCALE = ""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _write(Path(self._tmp.name) / "p", self.LOCALE)
        self._env = patch.dict(os.environ, {"PROFILE_DIR": str(self.root)})
        self._env.start()
        P.reset_profile_cache()

    def tearDown(self):
        self._env.stop()
        P.reset_profile_cache()
        self._tmp.cleanup()


class NewYorkCase(_ProfileCase):
    LOCALE = '[locale]\ntimezone = "America/New_York"\ntimezone_label = "紐約"\ntimezone_label_en = "New York"\n'


# ── profile 的 [locale].timezone ─────────────────────────────────────────


class ProfileTimezoneTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "p"

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, extra: str) -> P.Profile:
        return P.load_profile(_write(self.root, extra))

    def rejected(self, extra: str, *needles: str) -> None:
        with self.assertRaises(P.ProfileError) as cm:
            self.load(extra)
        for needle in ("[locale].", *needles):
            self.assertIn(needle, str(cm.exception))

    def test_absent_is_taipei(self):
        loc = self.load("").locale
        self.assertEqual((loc.timezone, loc.timezone_label, loc.timezone_label_en), ("Asia/Taipei", "台北", "Taipei"))
        loc = self.load('[locale]\ndefault = "zh-Hant"\n').locale
        self.assertEqual(loc.timezone, "Asia/Taipei")
        self.assertEqual(P.Locale().timezone, "Asia/Taipei")  # dataclass 預設與 parser 預設一致

    def test_shipped_profiles_are_taipei(self):
        for name in ("tingfong", "demo"):
            with self.subTest(profile=name):
                loc = P.load_profile(REPO / "profiles" / name).locale
                self.assertEqual((loc.timezone, loc.timezone_label, loc.timezone_label_en),
                                 ("Asia/Taipei", "台北", "Taipei"))

    def test_other_zone_labels_default_to_the_name(self):
        loc = self.load('[locale]\ntimezone = "Asia/Hong_Kong"\n').locale
        self.assertEqual((loc.timezone, loc.timezone_label, loc.timezone_label_en),
                         ("Asia/Hong_Kong", "Asia/Hong_Kong", "Asia/Hong_Kong"))
        loc = self.load('[locale]\ntimezone = "Asia/Hong_Kong"\ntimezone_label = "香港"\n').locale
        self.assertEqual((loc.timezone_label, loc.timezone_label_en), ("香港", "香港"))  # 英文沒給沿用中文
        loc = self.load('[locale]\ntimezone = "Asia/Hong_Kong"\ntimezone_label = "香港"\n'
                        'timezone_label_en = "Hong Kong"\n').locale
        self.assertEqual((loc.timezone_label, loc.timezone_label_en), ("香港", "Hong Kong"))

    def test_rejections(self):
        self.rejected('[locale]\ntimezone = "Mars/Olympus"\n', "timezone", "IANA", "找不到")
        self.rejected('[locale]\ntimezone = "Asia"\n', "timezone", "找不到")  # 目錄不是時區
        self.rejected('[locale]\ntimezone = "../../etc/passwd"\n', "timezone", "IANA")
        self.rejected('[locale]\ntimezone = "/usr/share/zoneinfo/UTC"\n', "timezone", "IANA")
        self.rejected('[locale]\ntimezone = "localtime"\n', "timezone", "IANA")
        self.rejected("[locale]\ntimezone = \"Asia/Taipei'; DROP TABLE x; --\"\n", "timezone", "IANA")
        self.rejected('[locale]\ntimezone = "UTC+8"\n', "timezone")  # POSIX 寫法的正負號與直覺相反，不收
        self.rejected('[locale]\ntimezone = ""\n', "timezone", "空字串")
        self.rejected("[locale]\ntimezone = 8\n", "timezone", "字串")
        self.rejected('[locale]\ntimezone_label = ""\n', "timezone_label", "空字串")
        self.rejected('[locale]\ntz = "Asia/Taipei"\n', "tz", "未知的鍵")

    def test_names_postgres_and_zoneinfo_share_are_accepted(self):
        for name in ("UTC", "Etc/GMT-8", "America/Argentina/Buenos_Aires", "Europe/London"):
            with self.subTest(name=name):
                self.assertEqual(self.load(f'[locale]\ntimezone = "{name}"\n').locale.timezone, name)


# ── 預設時區：行為與改動前相同 ─────────────────────────────────────────────


class DefaultIsUnchangedTests(unittest.TestCase):
    def test_taipei_day_boundary_and_retry_after(self):
        from app.services import quota
        from web import external_auth

        self.assertEqual(business_time.tz_name(), "Asia/Taipei")
        self.assertEqual(business_time.today(datetime(2026, 10, 7, 15, 59, tzinfo=UTC)), date(2026, 10, 7))
        self.assertEqual(business_time.today(datetime(2026, 10, 7, 16, 0, tzinfo=UTC)), date(2026, 10, 8))
        self.assertEqual(business_time.day_start(date(2026, 10, 7)).astimezone(UTC),
                         datetime(2026, 10, 6, 16, tzinfo=UTC))
        self.assertEqual(quota.retry_after_seconds(datetime(2026, 10, 7, 10, 0, tzinfo=UTC)), 6 * 3600)
        self.assertEqual(external_auth.seconds_until_quota_reset(datetime(2026, 10, 7, 10, 0, tzinfo=UTC)), 6 * 3600)

    def test_messages_keep_taipei_wording(self):
        from app.services import quota, upload_intake

        decision = quota.Decision(kind=quota.KIND_ASK, allowed=False, limit=100)
        self.assertEqual(quota.exceeded_message(decision), "今天（台北時間）的問答次數已達上限 100 次，午夜後重置")
        self.assertEqual(str(upload_intake.QuotaExceededError("daily", 30, 30)),
                         "今天（台北時間）已上傳 30 份，已達每人每日上限 30 份")

    def test_api_timezone_fields(self):
        from app.services import analytics

        plan = analytics.plan_range(None, None, today_=date(2026, 10, 7), live_days=90)
        self.assertEqual(analytics.range_info(plan, analytics.Params())["timezone"], "Asia/Taipei")


# ── 非 +8、有日光節約的時區 ───────────────────────────────────────────────


class NonTaipeiDayBoundaryTests(NewYorkCase):
    # 2026-11-01 是紐約夏令時間結束日（02:00 EDT → 01:00 EST），那一天有 25 小時。

    def test_today_and_day_start(self):
        self.assertEqual(business_time.tz_name(), "America/New_York")
        # 2026-10-07 03:30 UTC＝紐約 10/06 23:30（EDT）；台北早已是 10/07。
        self.assertEqual(business_time.today(datetime(2026, 10, 7, 3, 30, tzinfo=UTC)), date(2026, 10, 6))
        self.assertEqual(business_time.today(datetime(2026, 10, 7, 4, 0, tzinfo=UTC)), date(2026, 10, 7))
        start = business_time.day_start
        self.assertEqual(start(date(2026, 10, 7)).astimezone(UTC), datetime(2026, 10, 7, 4, tzinfo=UTC))
        self.assertEqual(start(date(2026, 11, 2)).astimezone(UTC), datetime(2026, 11, 2, 5, tzinfo=UTC))  # EST

    def test_seconds_until_midnight_across_dst_end(self):
        from app.services import quota
        from web import external_auth

        at = datetime(2026, 11, 1, 4, 30, tzinfo=UTC)  # 紐約 11/01 00:30 EDT；下一個午夜是 11/02 00:00 EST
        expected = int((datetime(2026, 11, 2, 5, 0, tzinfo=UTC) - at).total_seconds())
        self.assertEqual(expected, 24 * 3600 + 30 * 60)  # 不是牆上時間相減的 23.5 小時
        self.assertEqual(business_time.seconds_until_midnight(at), expected)
        self.assertEqual(quota.retry_after_seconds(at), expected)
        self.assertEqual(external_auth.seconds_until_quota_reset(at), expected)

    def test_daily_quota_and_usage_day(self):
        from app.services import backfill_tiers, quota, usage_events

        at = datetime(2026, 10, 7, 3, 30, tzinfo=UTC)
        self.assertEqual(quota.local_day(at), date(2026, 10, 6))
        self.assertEqual(quota.taipei_day(at), date(2026, 10, 6))  # 舊名同一個函式
        self.assertEqual(backfill_tiers.local_today(at), date(2026, 10, 6))
        with patch.object(business_time, "now", lambda at=None: datetime(2026, 10, 6, 23, 30,
                                                                          tzinfo=business_time.tz())):
            self.assertEqual(usage_events.today(), date(2026, 10, 6))

    def test_analytics_range_and_bind(self):
        from app.services import analytics

        self.assertEqual(analytics.day_start(date(2026, 10, 7)).astimezone(UTC), datetime(2026, 10, 7, 4, tzinfo=UTC))
        bind = analytics._bind(date(2026, 10, 1), date(2026, 10, 7), analytics.Params())
        self.assertEqual(bind["tz"], "America/New_York")
        self.assertEqual(bind["start_ts"].astimezone(UTC), datetime(2026, 10, 1, 4, tzinfo=UTC))
        self.assertEqual(bind["end_ts"].astimezone(UTC), datetime(2026, 10, 8, 4, tzinfo=UTC))
        plan = analytics.plan_range(None, None, today_=date(2026, 10, 7), live_days=90)
        self.assertEqual(analytics.range_info(plan, analytics.Params())["timezone"], "America/New_York")

    def test_db_snapshot_day_start(self):
        from app.services import db_insights

        self.assertEqual(db_insights.day_start(date(2026, 10, 7)), datetime(2026, 10, 7, 4, tzinfo=UTC))

    def test_llm_usage_cuts_days_in_profile_zone(self):
        from app.services import llm_usage

        with tempfile.TemporaryDirectory() as d:
            log = Path(d) / "llm_usage.jsonl"
            rows = [{"ts": "2026-10-07T03:30:00+00:00", "task": "summary", "model_req": "m"},  # 紐約 10/06
                    {"ts": "2026-10-07T04:30:00+00:00", "task": "summary", "model_req": "m"}]  # 紐約 10/07
            log.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            out = llm_usage.summarize(datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 10, 9, tzinfo=UTC), path=log)
        self.assertEqual(out["timezone"], "America/New_York")
        self.assertEqual([d["day"] for d in out["by_day"]], ["2026-10-06", "2026-10-07"])
        self.assertEqual(llm_usage.online_days(datetime(2026, 10, 7, 3, 30, tzinfo=UTC),
                                               datetime(2026, 10, 7, 4, 30, tzinfo=UTC)),
                         (date(2026, 10, 6), date(2026, 10, 7)))

    def test_brief_day_end_and_export_filename(self):
        from scripts import generate_brief
        from web import csv_export

        self.assertEqual(generate_brief.end_of_local_day(date(2026, 10, 6)), datetime(2026, 10, 7, 4, tzinfo=UTC))
        self.assertEqual(csv_export.filename("qa", datetime(2026, 10, 7, 3, 30, tzinfo=UTC)),
                         "report-mark-qa-20261006.csv")

    def test_messages_use_profile_label(self):
        from app.services import quota, upload_intake

        decision = quota.Decision(kind=quota.KIND_EXPORT, allowed=False, limit=20)
        self.assertEqual(quota.exceeded_message(decision), "今天（紐約時間）的匯出次數已達上限 20 次，午夜後重置")
        self.assertIn("今天（紐約時間）", str(upload_intake.QuotaExceededError("daily", 30, 30)))


class SwitchingProfileTests(unittest.TestCase):
    """沒有 import 期凍結的時區：同一個行程換 profile，下一次呼叫就換時區。"""

    def test_reset_profile_cache_switches_zone(self):
        from app.services import analytics, quota

        at = datetime(2026, 10, 7, 3, 30, tzinfo=UTC)
        self.assertEqual(quota.local_day(at), date(2026, 10, 7))
        with tempfile.TemporaryDirectory() as d, \
                patch.dict(os.environ, {"PROFILE_DIR": str(_write(Path(d) / "p", NewYorkCase.LOCALE))}):
            P.reset_profile_cache()
            self.assertEqual(quota.local_day(at), date(2026, 10, 6))
            self.assertEqual(analytics._bind(date(2026, 10, 1), date(2026, 10, 1), analytics.Params())["tz"],
                             "America/New_York")
        P.reset_profile_cache()
        self.assertEqual(quota.local_day(at), date(2026, 10, 7))

    def test_no_module_level_zone_constants(self):
        from app.services import analytics, api_clients, db_insights, llm_usage, quota, upload_intake, usage_events

        for mod in (analytics, api_clients, db_insights, llm_usage, quota, upload_intake, usage_events):
            for name in ("TZ", "TZ_NAME"):
                with self.subTest(module=mod.__name__, name=name):
                    self.assertFalse(hasattr(mod, name), "時區要在呼叫時經 business_time 取值")


# ── SQL：時區一律是綁定參數 ───────────────────────────────────────────────


class SqlBindingTests(unittest.TestCase):
    _LITERAL = re.compile(r"AT\s+TIME\s+ZONE\s+(?!'UTC')['\"{]", re.I)  # 固定的 'UTC' 不是業務時區

    def test_no_interpolated_time_zone_in_sql(self):
        hits = []
        for base in ("app", "web", "scripts"):
            for path in sorted((REPO / base).rglob("*.py")):
                for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if self._LITERAL.search(line):
                        hits.append(f"{path.relative_to(REPO)}:{n}: {line.strip()}")
        self.assertEqual(hits, [], "AT TIME ZONE 要用綁定參數 :tz（business_time.tz_name()），不要內插字串")

    def test_queries_with_tz_param_receive_it(self):
        from app.services import analytics, quota

        self.assertIn(":tz", analytics._DAY)
        self.assertIn(":tz", analytics._OPS_SQL)
        self.assertIn(":tz", quota._UPLOAD_STATS_SQL)
        self.assertIn(":tz", quota._upload_window_sql())


# ── /api/branding 下發 ────────────────────────────────────────────────────


class BrandingTimezoneTests(_ProfileCase):
    LOCALE = '[locale]\ntimezone = "Asia/Hong_Kong"\ntimezone_label = "香港"\ntimezone_label_en = "Hong Kong"\n'

    def test_branding_hands_timezone_to_the_frontend(self):
        from web.routers.branding import branding_payload

        self.assertEqual(branding_payload()["timezone"],
                         {"name": "Asia/Hong_Kong", "label": "香港", "label_en": "Hong Kong"})

    def test_frontend_schema_reads_the_same_keys(self):
        src = (REPO / "frontend" / "src" / "lib" / "branding.ts").read_text(encoding="utf-8")
        block = re.search(r"timezone: z\.object\(\{(.*?)\}\)", src, re.S)
        self.assertIsNotNone(block)
        self.assertEqual(set(re.findall(r"(\w+): z\.", block.group(1))), {"name", "label", "label_en"})

    def test_frontend_default_matches_backend_default(self):
        src = (REPO / "frontend" / "src" / "i18n" / "index.ts").read_text(encoding="utf-8")
        self.assertIn(f"export const DEFAULT_TIME_ZONE = '{P.DEFAULT_TIMEZONE}'", src)


# ── 容器：TZ 與 profile ───────────────────────────────────────────────────


class EntrypointTzTests(NewYorkCase):
    def setUp(self):
        super().setUp()
        from deploy.container import entrypoint

        self.ep = entrypoint
        self.env = {"PROFILE_DIR": str(self.root)}

    def test_unset_tz_follows_the_profile(self):
        for tz in (None, "", "  "):
            env = dict(self.env) if tz is None else {**self.env, "TZ": tz}
            self.assertEqual(self.ep.resolve_tz("scheduler", env), ("America/New_York", None))

    def test_matching_tz_passes(self):
        self.assertEqual(self.ep.resolve_tz("scheduler", {**self.env, "TZ": "America/New_York"}),
                         ("America/New_York", None))

    def test_mismatch_refuses_scheduler_and_warns_others(self):
        tz, problem = self.ep.resolve_tz("scheduler", {**self.env, "TZ": "Asia/Taipei"})
        self.assertIsNone(tz)
        self.assertIn("America/New_York", problem)
        self.assertIn("Asia/Taipei", problem)
        with patch.object(self.ep, "log") as log:
            self.assertEqual(self.ep.resolve_tz("web", {**self.env, "TZ": "Asia/Taipei"}), ("Asia/Taipei", None))
        self.assertIn("不同", log.call_args.args[0])

    def test_broken_profile_refuses(self):
        (self.root / "profile.toml").write_text(textwrap.dedent(MINIMAL) + '[locale]\ntimezone = "Mars/Olympus"\n',
                                                encoding="utf-8")
        tz, problem = self.ep.resolve_tz("web", self.env)
        self.assertIsNone(tz)
        self.assertIn("timezone", problem)

    def test_main_sets_tz_for_execed_roles_and_refuses_mismatched_scheduler(self):
        execs = []
        env = {**self.env, "REPORT_MARK_DB_URL": "postgresql+asyncpg://u:p@postgres:5432/research",
               "REPORT_MARK_SESSION_SECRET": "s" * 40}
        rc = self.ep.main(["entrypoint", "web"], env, execvpe=lambda f, a, e: execs.append(e), gate=lambda e: 0)
        self.assertEqual((rc, execs[0]["TZ"]), (0, "America/New_York"))
        with patch.object(self.ep, "log"):
            rc = self.ep.main(["entrypoint", "scheduler"], {**env, "TZ": "Asia/Taipei"},
                              execvpe=lambda f, a, e: execs.append(e), gate=lambda e: 0)
        self.assertEqual((rc, len(execs)), (self.ep.EXIT_REFUSED, 1))

    def test_compose_and_image_leave_tz_to_the_profile(self):
        compose = (REPO / "deploy" / "compose" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("TZ: ${TZ:-}", compose)
        self.assertNotIn("Asia/Taipei", compose)
        dockerfile = (REPO / "deploy" / "container" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s*TZ=", dockerfile, re.M))


if __name__ == "__main__":
    unittest.main()
