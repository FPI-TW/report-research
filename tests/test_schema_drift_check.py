# tests/test_schema_drift_check.py
"""`scripts/schema_baseline.py` 的版本 drift 比對（`check --expect-head`）與每日檢查（`scheduled`）。

不連 DB：版本比對是純函式＋可替換的讀取函式；「DB 不可用」用一個不存在的 unix socket 真的去連。
真正連庫的版本比對在 CI 的 schema job（upgrade head 後必須一致、stamp 成 0001 的庫必須回落後）。

釘住的是告警語意：退出碼決定 systemd 要不要叫人（unit 以 SuccessExitStatus=3 放過「DB 掛了」），
所以「落後」「超前」「清理失敗」任何一條被放寬成 0 或 3，就是一次不會有人知道的 drift。
"""
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import schema_baseline as sb  # noqa: E402

CHAIN = ["0001", "0002", "0003"]
HEADS = ["0003"]


def _cmp(db_revs):
    return sb.compare_versions(db_revs, chain=CHAIN, heads=HEADS)


class CompareVersionsTests(unittest.TestCase):
    def test_same_revision_is_ok(self):
        r = _cmp(["0003"])
        self.assertEqual((r.status, r.exit_code, r.pending), ("ok", sb.EXIT_OK, []))

    def test_known_older_revision_is_behind_with_pending_list(self):
        r = _cmp(["0001"])
        self.assertEqual((r.status, r.exit_code), ("behind", sb.EXIT_DRIFT))
        self.assertEqual(r.pending, ["0002", "0003"])
        self.assertIn("make schema", r.message)

    def test_unknown_revision_is_ahead(self):
        r = _cmp(["0004"])
        self.assertEqual((r.status, r.exit_code, r.db_revision), ("ahead", sb.EXIT_ERROR, "0004"))

    def test_missing_table_is_unversioned(self):
        r = _cmp(None)
        self.assertEqual((r.status, r.exit_code), ("unversioned", sb.EXIT_ERROR))

    def test_empty_table_is_unversioned(self):
        self.assertEqual(_cmp([]).status, "unversioned")

    def test_multiple_rows_are_ambiguous(self):
        r = _cmp(["0002", "0003"])
        self.assertEqual((r.status, r.exit_code), ("ambiguous", sb.EXIT_ERROR))

    def test_multiple_code_heads_are_ambiguous(self):
        r = sb.compare_versions(["0003"], chain=[], heads=["0003", "0003b"])
        self.assertEqual((r.status, r.exit_code), ("ambiguous", sb.EXIT_ERROR))

    def test_every_status_is_in_vocabulary(self):
        for revs in (["0003"], ["0001"], ["0009"], None, [], ["0001", "0002"]):
            self.assertIn(_cmp(revs).status, sb.VERSION_STATUSES)

    def test_code_revisions_reads_the_real_chain(self):
        chain, heads = sb.code_revisions()
        self.assertEqual(len(heads), 1)
        self.assertEqual(chain[0], sb.sm.BASELINE_REVISION)
        self.assertEqual(chain[-1], heads[0])


class CheckVersionTests(unittest.IsolatedAsyncioTestCase):
    URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5437/lane"

    async def _check(self, fetch):
        return await sb.check_version(self.URL, fetch=fetch, revisions=lambda: (CHAIN, HEADS))

    async def test_reads_and_compares(self):
        async def fetch(url):
            return ["0002"]
        r = await self._check(fetch)
        self.assertEqual((r.status, r.pending), ("behind", ["0003"]))

    async def test_connection_refused_is_db_unavailable(self):
        async def fetch(url):
            raise ConnectionRefusedError(111, "Connection refused")
        r = await self._check(fetch)
        self.assertEqual((r.status, r.exit_code), ("db_unavailable", sb.EXIT_DB_UNAVAILABLE))
        self.assertEqual(r.expected_head, "0003")

    async def test_timeout_is_db_unavailable(self):
        async def fetch(url):
            raise TimeoutError()
        self.assertEqual((await self._check(fetch)).status, "db_unavailable")

    async def test_server_starting_up_is_db_unavailable(self):
        from asyncpg.exceptions import CannotConnectNowError

        async def fetch(url):
            raise CannotConnectNowError("the database system is starting up")
        self.assertEqual((await self._check(fetch)).status, "db_unavailable")

    async def test_wrong_password_is_an_alerting_error(self):
        """帳密錯是設定錯誤，不會自己好：必須告警（2），不可當成 DB 掛了（3）。"""
        from asyncpg.exceptions import InvalidPasswordError

        async def fetch(url):
            try:
                raise OSError("前一個位址連不上")
            except OSError:
                raise InvalidPasswordError("password authentication failed")  # noqa: B904 - 刻意留 __context__
        r = await self._check(fetch)
        self.assertEqual((r.status, r.exit_code), ("error", sb.EXIT_ERROR))

    async def test_wrapped_connection_error_is_db_unavailable(self):
        async def fetch(url):
            try:
                raise ConnectionRefusedError(111, "refused")
            except ConnectionRefusedError as exc:
                raise RuntimeError("wrapped") from exc
        self.assertEqual((await self._check(fetch)).status, "db_unavailable")

    async def test_real_unreachable_socket_is_db_unavailable(self):
        url = "postgresql+asyncpg://nobody:x@/none?host=/nonexistent/report-mark-socket-dir"
        r = await sb.check_version(url, revisions=lambda: (CHAIN, HEADS))
        self.assertEqual((r.status, r.exit_code), ("db_unavailable", sb.EXIT_DB_UNAVAILABLE))

    async def test_broken_revision_scripts_are_an_error(self):
        async def fetch(url):
            raise AssertionError("不該連 DB")

        def boom():
            raise RuntimeError("bad revision file")
        r = await sb.check_version(self.URL, fetch=fetch, revisions=boom)
        self.assertEqual((r.status, r.exit_code), ("error", sb.EXIT_ERROR))


class ExpectHeadCliTests(unittest.TestCase):
    def test_expect_head_never_builds_a_reference_database(self):
        async def fail(*a, **kw):
            raise AssertionError("--expect-head 不得建暫存庫")

        async def ok(url, **kw):
            return sb.VersionResult("ok", "0003", "0003", message="一致")
        with mock.patch.object(sb, "build_reference_catalog", fail), mock.patch.object(sb, "check_version", ok):
            self.assertEqual(sb.main(["check", "--expect-head"]), sb.EXIT_OK)

    def test_expect_head_returns_version_exit_code(self):
        for status, rc in (("behind", 1), ("ahead", 2), ("db_unavailable", 3)):
            async def fake(url, _s=status, **kw):
                return sb.VersionResult(_s, "0003", "0001")
            with mock.patch.object(sb, "check_version", fake):
                self.assertEqual(sb.main(["check", "--expect-head"]), rc, status)

    def test_expect_head_rejects_drift_options(self):
        self.assertEqual(sb.main(["check", "--expect-head", "--revision", "0001"]), sb.EXIT_ERROR)


class RunCheckFirstContactTests(unittest.IsolatedAsyncioTestCase):
    """完整比對也分流「連不上目標」（3）；之後清理失敗仍是 2（既有保證不變）。"""

    def _args(self):
        return SimpleNamespace(reference_url_env=None, revision=None, json=None)

    async def test_unreachable_target_is_3_and_builds_nothing(self):
        async def refused(url):
            raise ConnectionRefusedError(111, "refused")

        async def fail(*a, **kw):
            raise AssertionError("連不上目標時不得建暫存庫")
        with mock.patch.object(sb, "current_revision", refused), mock.patch.object(sb, "build_reference_catalog", fail):
            rc, report, _ = await sb.run_check(self._args())
        self.assertEqual((rc, report), (sb.EXIT_DB_UNAVAILABLE, None))

    async def test_other_first_contact_failure_is_2(self):
        async def bad(url):
            raise ValueError("odd")
        with mock.patch.object(sb, "current_revision", bad):
            rc, _, _ = await sb.run_check(self._args())
        self.assertEqual(rc, sb.EXIT_ERROR)


# ───────────────────────── scheduled ─────────────────────────

V_OK = sb.VersionResult("ok", "0003", "0003", message="一致")
V_BEHIND = sb.VersionResult("behind", "0003", "0002", ["0003"], message="落後")
V_AHEAD = sb.VersionResult("ahead", "0003", "0009", message="超前")
V_DOWN = sb.VersionResult("db_unavailable", "0003", None, message="連不上")
V_ERR = sb.VersionResult("error", "0003", None, message="帳密錯")
D_OK = sb.DriftOutcome("ok", "0003", "暫存庫建在目標的同一台伺服器",
                       sb.DriftReport(categories={}, column_order=["research.t"]))
D_DRIFT = sb.DriftOutcome("drift", "0003", "x", sb.diff_catalogs({"index": {"research.a": "A"}}, {}))
D_CLEANUP = sb.DriftOutcome("cleanup_failed", "0003", message='DROP DATABASE IF EXISTS "schema_ref_x" WITH (FORCE);')
D_ERR = sb.DriftOutcome("error", "0003", message="無法比對")


class SummarizeTests(unittest.TestCase):
    def test_matrix(self):
        skipped = sb.DriftOutcome("skipped")
        cases = [
            (V_OK, D_OK, 0, []),
            (V_OK, D_DRIFT, 1, ["schema_drift"]),
            (V_BEHIND, D_OK, 1, ["version_behind"]),
            (V_BEHIND, D_DRIFT, 1, ["version_behind", "schema_drift"]),
            (V_OK, D_CLEANUP, 2, ["reference_cleanup_failed"]),
            (V_BEHIND, D_CLEANUP, 2, ["version_behind", "reference_cleanup_failed"]),
            (V_OK, D_ERR, 2, ["check_error"]),
            (V_ERR, skipped, 2, ["check_error"]),
            (V_AHEAD, skipped, 2, ["version_ahead"]),
            (V_DOWN, skipped, 3, ["db_unavailable"]),
            (V_OK, skipped, 0, []),
        ]
        for version, drift, rc, problems in cases:
            with self.subTest(v=version.status, d=drift.status):
                self.assertEqual(sb.summarize(version, drift), (rc, problems))
                self.assertTrue(set(problems) <= set(sb.PROBLEM_CODES))

    def test_combine_priority(self):
        self.assertEqual(sb.combine_exit_codes(3, 1), 1)
        self.assertEqual(sb.combine_exit_codes(1, 2, 3), 2)
        self.assertEqual(sb.combine_exit_codes(0, 3), 3)
        self.assertEqual(sb.combine_exit_codes(0, 0), 0)


class ScheduledTests(unittest.TestCase):
    URL = "postgresql+asyncpg://postgres:fixed-test-secret-notinpayload@127.0.0.1:5437/lane"
    START = datetime(2026, 10, 6, 5, 20, 0, tzinfo=timezone(timedelta(hours=8)))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.status = Path(self.tmp.name) / "schema_check.json"
        p = mock.patch.object(sb, "DATABASE_URL", self.URL)
        p.start()
        self.addCleanup(p.stop)
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for key in (sb.MODE_ENV, sb.REFERENCE_URL_ENV_ENV):
            os.environ.pop(key, None)
        self.drift_calls = []

    def _run(self, version, drift=D_OK, *, mode=None, status_file=None):
        def fake_drift(rev):
            self.drift_calls.append(rev)
            return drift
        ticks = iter([100.0, 112.34])
        args = SimpleNamespace(mode=mode, reference_url_env=None, status_file=str(status_file or self.status))
        return sb.cmd_scheduled(args, version_check=lambda: version, drift=fake_drift,
                                now=lambda: self.START, clock=lambda: next(ticks))

    def _payload(self):
        return json.loads(self.status.read_text(encoding="utf-8"))

    def test_ok_writes_status_file_with_pinned_shape(self):
        self.assertEqual(self._run(V_OK), sb.EXIT_OK)
        p = self._payload()
        self.assertEqual(set(p), {"format", "checked_at", "duration_s", "mode", "target", "exit_code", "alert",
                                  "problems", "message", "version", "drift"})
        self.assertEqual(p["format"], 1)
        self.assertEqual(p["checked_at"], "2026-10-06T05:20:00+08:00")
        self.assertEqual(p["duration_s"], 12.3)
        self.assertEqual((p["mode"], p["target"], p["exit_code"], p["alert"], p["problems"]),
                         ("full", "127.0.0.1:5437/lane", 0, False, []))
        self.assertEqual(p["version"], {"status": "ok", "expected_head": "0003", "db_revision": "0003", "pending": []})
        self.assertEqual(set(p["drift"]), {"status", "revision", "reference", "message", "drift_count", "categories",
                                           "column_order_differs"})
        self.assertEqual((p["drift"]["status"], p["drift"]["drift_count"], p["drift"]["column_order_differs"]),
                         ("ok", 0, ["research.t"]))
        self.assertEqual(self.drift_calls, ["0003"])

    def test_payload_never_contains_credentials(self):
        self._run(V_OK)
        self.assertNotIn("fixed-test-secret-notinpayload", self.status.read_text(encoding="utf-8"))

    def test_behind_still_runs_full_check_against_db_revision(self):
        """落後的庫仍以「它自己宣稱的 revision」做完整比對：版本與結構是兩件事。"""
        self.assertEqual(self._run(V_BEHIND), sb.EXIT_DRIFT)
        self.assertEqual(self.drift_calls, ["0002"])
        p = self._payload()
        self.assertEqual((p["problems"], p["alert"], p["version"]["pending"]), (["version_behind"], True, ["0003"]))

    def test_drift_alerts_and_lists_objects(self):
        self.assertEqual(self._run(V_OK, D_DRIFT), sb.EXIT_DRIFT)
        p = self._payload()
        self.assertEqual(p["drift"]["drift_count"], 1)
        self.assertEqual(p["drift"]["categories"]["index"]["missing"], ["research.a"])

    def test_cleanup_failure_is_2_and_keeps_manual_instruction(self):
        self.assertEqual(self._run(V_OK, D_CLEANUP), sb.EXIT_ERROR)
        p = self._payload()
        self.assertEqual((p["problems"], p["alert"]), (["reference_cleanup_failed"], True))
        self.assertIn("DROP DATABASE", p["message"])

    def test_db_unavailable_skips_drift_and_is_3(self):
        self.assertEqual(self._run(V_DOWN), sb.EXIT_DB_UNAVAILABLE)
        self.assertEqual(self.drift_calls, [])
        p = self._payload()
        self.assertEqual((p["problems"], p["alert"], p["drift"]["status"]), (["db_unavailable"], False, "skipped"))

    def test_ahead_skips_drift_and_alerts(self):
        self.assertEqual(self._run(V_AHEAD), sb.EXIT_ERROR)
        self.assertEqual(self.drift_calls, [])
        self.assertEqual(self._payload()["problems"], ["version_ahead"])

    def test_version_mode_never_runs_full_check(self):
        self.assertEqual(self._run(V_OK, mode="version"), sb.EXIT_OK)
        self.assertEqual(self.drift_calls, [])
        p = self._payload()
        self.assertEqual((p["mode"], p["drift"]["status"]), ("version", "skipped"))
        self.assertEqual(self._run(V_BEHIND, mode="version"), sb.EXIT_DRIFT)

    def test_mode_from_environment(self):
        os.environ[sb.MODE_ENV] = "version"
        self._run(V_OK)
        self.assertEqual(self.drift_calls, [])

    def test_invalid_mode_is_rejected_as_error(self):
        os.environ[sb.MODE_ENV] = "fast"
        self.assertEqual(self._run(V_OK), sb.EXIT_ERROR)
        self.assertFalse(self.status.exists())

    def test_unwritable_status_file_does_not_change_exit_code(self):
        missing = Path(self.tmp.name) / "no-such-dir" / "schema_check.json"
        self.assertEqual(self._run(V_OK, D_DRIFT, status_file=missing), sb.EXIT_DRIFT)
        self.assertFalse(missing.parent.exists(), "落點不存在時不替它建目錄")

    def test_status_write_is_atomic_and_leaves_no_temp_files(self):
        self._run(V_OK)
        self._run(V_OK, D_DRIFT)
        self.assertEqual(sorted(p.name for p in Path(self.tmp.name).iterdir()), ["schema_check.json"])
        self.assertEqual(self._payload()["exit_code"], 1)


class StatusFileLocationTests(unittest.TestCase):
    def test_default_is_under_repo_data(self):
        self.assertEqual(sb.DEFAULT_STATUS_FILE, REPO_ROOT / "data" / "schema_check.json")

    def test_conftest_redirects_status_file_by_assignment(self):
        """部署目錄的狀態檔不得被測試蓋掉（tests/conftest.py 以賦值導向不存在的路徑）。"""
        self.assertTrue(os.environ[sb.STATUS_FILE_ENV].startswith("/nonexistent/"))
        self.assertEqual(sb.status_file_path(None), Path(os.environ[sb.STATUS_FILE_ENV]))
        conftest = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        self.assertIn(f'os.environ["{sb.STATUS_FILE_ENV}"] = ', conftest)


if __name__ == "__main__":
    unittest.main()
