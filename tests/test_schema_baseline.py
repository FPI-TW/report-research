# tests/test_schema_baseline.py
"""`scripts/schema_baseline.py` 的判定邏輯（不連 DB、不跑 pg_dump）。

真正連庫的端到端演練在 CI 的 schema job（舊流程建的庫：upgrade 必須拒絕 → 零 drift → stamp）。
這裡釘的是「什麼情況下禁止 stamp」：任何一條放寬都會讓一個沒驗證過的庫被 alembic 接管。
"""
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts import schema_baseline as sb  # noqa: E402


class DiffCatalogTests(unittest.TestCase):
    def test_identical_catalogs_have_no_drift(self):
        cat = {"index": {"research.a": "CREATE INDEX a ..."}}
        self.assertEqual(sb.diff_catalogs(cat, cat).drift_count, 0)

    def test_missing_extra_and_changed_all_count(self):
        ref = {"index": {"research.a": "A", "research.b": "B"}, "column": {"research.t.x": "int"}}
        tgt = {"index": {"research.a": "A2", "research.c": "C"}, "column": {"research.t.x": "int"}}
        r = sb.diff_catalogs(ref, tgt)
        d = r.categories["index"]
        self.assertEqual(d.missing, ["research.b"])
        self.assertEqual(d.extra, ["research.c"])
        self.assertEqual(d.changed, [("research.a", "A", "A2")])
        self.assertEqual(r.drift_count, 3)

    def test_category_only_on_one_side_still_counts(self):
        r = sb.diff_catalogs({}, {"trigger": {"research.t.tg": "CREATE TRIGGER ..."}})
        self.assertEqual(r.drift_count, 1)

    def test_alembic_version_is_excluded(self):
        tgt = {
            "relation": {"public.alembic_version": "r"},
            "column": {"public.alembic_version.version_num": "varchar(32) NOT NULL"},
            "constraint": {"public.alembic_version.alembic_version_pkc": "p PRIMARY KEY"},
            "index": {"public.alembic_version_pkc": "CREATE UNIQUE INDEX ..."},
        }
        self.assertEqual(sb.diff_catalogs({}, tgt).drift_count, 0)

    def test_other_public_tables_are_drift(self):
        self.assertEqual(sb.diff_catalogs({}, {"relation": {"public.stray": "r"}}).drift_count, 1)

    def test_column_order_is_information_not_drift(self):
        r = sb.diff_catalogs({}, {}, {"research.t": "a,b"}, {"research.t": "b,a"})
        self.assertEqual(r.drift_count, 0)
        self.assertEqual(r.column_order, ["research.t"])

    def test_report_states_verdict(self):
        r = sb.diff_catalogs({"index": {"research.a": "A"}}, {})
        out = sb.format_report(r, identity="h:1/d", revision="0001", reference_desc="x")
        self.assertIn("禁止 stamp", out)
        self.assertIn("research.a", out)

    def test_catalog_covers_required_categories(self):
        for cat in ("extension", "relation", "column", "constraint", "index", "trigger", "function", "type"):
            self.assertIn(cat, sb.CATALOG_QUERIES)


def _args(**kw):
    base = dict(no_dump=False, dump_dir=None, dump_container=None, dump_timeout_min=90, json=None,
                reference_url_env=None, revision=None)
    base.update(kw)
    return SimpleNamespace(**base)


class StampFlowTests(unittest.TestCase):
    URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5437/devdb"
    ID = "127.0.0.1:5437/devdb"

    def setUp(self):
        p = mock.patch.object(sb, "DATABASE_URL", self.URL)
        p.start()
        self.addCleanup(p.stop)
        self.stamped = []
        self.dumped = []

    def _run(self, args, *, confirm=ID, drift_rc=0, current=None, after="0001", dump_ok=True, protected=""):
        state = {"rev": current}

        def revision_of():
            return state["rev"]

        def stamp(rev):
            self.stamped.append(rev)
            state["rev"] = after

        def dump(url, dump_dir, **kw):
            self.dumped.append(dump_dir)
            return sb.DumpResult(dump_ok, "ok" if dump_ok else "boom")

        env = {sb.sm.PROTECTED_ENV: protected}
        if confirm is not None:
            env[sb.sm.CONFIRM_ENV] = confirm
        with mock.patch.dict(os.environ, env, clear=False):
            if confirm is None:
                os.environ.pop(sb.sm.CONFIRM_ENV, None)
            return sb.cmd_stamp(args, check=lambda: (drift_rc, None, "0001"), stamp=stamp, dump=dump,
                                size_of=lambda: 1, revision_of=revision_of)

    def test_requires_exact_confirm(self):
        self.assertEqual(self._run(_args(no_dump=True), confirm=None), sb.EXIT_ERROR)
        self.assertEqual(self._run(_args(no_dump=True), confirm="localhost:5436/research"), sb.EXIT_ERROR)
        self.assertEqual(self.stamped, [])

    def test_drift_forbids_stamp(self):
        self.assertEqual(self._run(_args(no_dump=True), drift_rc=1), sb.EXIT_DRIFT)
        self.assertEqual(self.stamped, [])

    def test_check_error_forbids_stamp(self):
        self.assertEqual(self._run(_args(no_dump=True), drift_rc=2), sb.EXIT_ERROR)
        self.assertEqual(self.stamped, [])

    def test_protected_rejects_no_dump(self):
        self.assertEqual(self._run(_args(no_dump=True), protected=self.ID), sb.EXIT_ERROR)
        self.assertEqual(self.stamped, [])

    def test_dump_dir_required_unless_explicit_no_dump(self):
        self.assertEqual(self._run(_args()), sb.EXIT_ERROR)

    def test_failed_dump_forbids_stamp(self):
        self.assertEqual(self._run(_args(dump_dir="/tmp"), protected=self.ID, dump_ok=False), sb.EXIT_DRIFT)
        self.assertEqual(self.stamped, [])

    def test_happy_path_dumps_then_stamps(self):
        self.assertEqual(self._run(_args(dump_dir="/tmp"), protected=self.ID), sb.EXIT_OK)
        self.assertEqual(self.dumped, [Path("/tmp")])
        self.assertEqual(self.stamped, ["0001"])

    def test_already_baseline_is_noop(self):
        self.assertEqual(self._run(_args(no_dump=True), current="0001"), sb.EXIT_OK)
        self.assertEqual(self.stamped, [])

    def test_already_managed_at_other_revision_refuses(self):
        self.assertEqual(self._run(_args(no_dump=True), current="0002"), sb.EXIT_ERROR)

    def test_readback_mismatch_is_error(self):
        self.assertEqual(self._run(_args(no_dump=True), after=None), sb.EXIT_ERROR)


class FullDumpPreflightTests(unittest.TestCase):
    # 密碼刻意用不可能出現在暫存路徑裡的字串：曾用 "pw"，tempfile 的隨機目錄名偶爾含 "pw" 而誤判。
    SECRET = "NotInArgv-7f3c9e"
    URL = f"postgresql+asyncpg://postgres:{SECRET}@127.0.0.1:5437/research"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.calls = []

    def _runner(self, *, dump_rc=0, payload=b"PGDMP" + b"x" * 4096, restore_rc=0, listing=b"; x\n1; TABLE DATA t\n",
                timeout=False):
        def run(cmd, **kw):
            self.calls.append((cmd, kw))
            if "pg_dump" in cmd:
                if timeout:
                    raise subprocess.TimeoutExpired(cmd, 1)
                kw["stdout"].write(payload)
                return SimpleNamespace(returncode=dump_rc, stderr=b"err")
            return SimpleNamespace(returncode=restore_rc, stdout=listing, stderr=b"")
        return run

    def _go(self, runner, *, free=10 * 1024**3, db_size=1024, container="report-mark-devdb"):
        return sb.full_dump_preflight(
            self.URL, self.dir, db_size=db_size, container=container, runner=runner,
            disk_usage=lambda _p: SimpleNamespace(free=free), now=lambda: datetime(2026, 10, 5, 12, 0, 0),
        )

    def _leftovers(self):
        return sorted(p.name for p in self.dir.iterdir())

    def test_insufficient_space_fails_before_dumping(self):
        r = self._go(self._runner(), free=1024, db_size=1024)
        self.assertFalse(r.ok)
        self.assertIn("空間不足", r.message)
        self.assertEqual(self.calls, [])

    def test_success_renames_and_verifies_listing(self):
        r = self._go(self._runner())
        self.assertTrue(r.ok, r.message)
        self.assertEqual(self._leftovers(), ["report-mark-full-127.0.0.1-5437-research-20261005T120000.dump"])
        restore_cmd = self.calls[1][0]
        self.assertIn("pg_restore", restore_cmd)
        self.assertIn("-l", restore_cmd)

    def test_bad_header_fails_and_cleans_up(self):
        r = self._go(self._runner(payload=b"NOPE!" + b"x" * 4096))
        self.assertFalse(r.ok)
        self.assertEqual(self._leftovers(), [])

    def test_dump_failure_fails_and_cleans_up(self):
        r = self._go(self._runner(dump_rc=1))
        self.assertFalse(r.ok)
        self.assertIn("rc=1", r.message)
        self.assertEqual(self._leftovers(), [])

    def test_timeout_fails_and_cleans_up(self):
        r = self._go(self._runner(timeout=True))
        self.assertFalse(r.ok)
        self.assertIn("時限", r.message)
        self.assertEqual(self._leftovers(), [])

    def test_unreadable_listing_fails(self):
        r = self._go(self._runner(listing=b"; empty\n"))
        self.assertFalse(r.ok)
        self.assertEqual(self._leftovers(), [])

    def test_direct_mode_keeps_password_out_of_argv(self):
        r = self._go(self._runner(), container=None)
        self.assertTrue(r.ok, r.message)
        for cmd, kw in self.calls:
            self.assertNotIn(self.SECRET, " ".join(cmd))
        self.assertEqual(self.calls[0][1]["env"]["PGPASSWORD"], self.SECRET)
        self.assertEqual(self.calls[0][1]["env"]["PGPORT"], "5437")

    def test_container_mode_never_allocates_tty(self):
        self._go(self._runner())
        dump_cmd = self.calls[0][0]
        self.assertIn("-i", dump_cmd)
        self.assertNotIn("-t", dump_cmd)
        self.assertNotIn("-it", dump_cmd)


class _FakeOps:
    def __init__(self, *, create_exc=None, apply_exc=None, drop_exc=None, still_exists=False):
        self.create_exc, self.apply_exc = create_exc, apply_exc
        self.drop_exc, self.still_exists = drop_exc, still_exists
        self.log = []

    async def create(self, name):
        self.log.append(("create", name))
        if self.create_exc:
            raise self.create_exc

    async def apply(self, name, chain):
        self.log.append(("apply", name))
        if self.apply_exc:
            raise self.apply_exc

    async def fetch(self, name):
        self.log.append(("fetch", name))
        return {"index": {}}, {}

    async def drop(self, name):
        self.log.append(("drop", name))
        if self.drop_exc:
            raise self.drop_exc

    async def exists(self, name):
        return self.still_exists

    async def close(self):
        self.log.append(("close", None))


class ReferenceCleanupTests(unittest.IsolatedAsyncioTestCase):
    """暫存基準庫建在目標伺服器（含生產）：只刪自己建的、刪完要確認、清不掉就整次作廢。"""

    URL = "postgresql+asyncpg://postgres:postgres@127.0.0.1:5437/research"

    def setUp(self):
        p = mock.patch.object(sb.sm, "schema_sql_chain", lambda rev: [("0001", "SELECT 1")])
        p.start()
        self.addCleanup(p.stop)

    async def _build(self, ops):
        return await sb.build_reference_catalog(self.URL, "0001", ops=ops, name="schema_ref_t")

    def _actions(self, ops):
        return [a for a, _ in ops.log]

    async def test_success_drops_and_closes(self):
        ops = _FakeOps()
        await self._build(ops)
        self.assertEqual(self._actions(ops), ["create", "apply", "fetch", "drop", "close"])

    async def test_create_failure_never_drops(self):
        """撞名時 CREATE 失敗：那個同名庫不是我們的，絕不可以刪。"""
        ops = _FakeOps(create_exc=RuntimeError("already exists"))
        with self.assertRaises(RuntimeError):
            await self._build(ops)
        self.assertNotIn("drop", self._actions(ops))

    async def test_apply_failure_still_cleans_up(self):
        ops = _FakeOps(apply_exc=RuntimeError("bad sql"))
        with self.assertRaises(RuntimeError) as cm:
            await self._build(ops)
        self.assertNotIsInstance(cm.exception, sb.ReferenceCleanupError)
        self.assertIn("drop", self._actions(ops))

    async def test_drop_failure_voids_successful_check(self):
        ops = _FakeOps(drop_exc=RuntimeError("permission denied"))
        with self.assertRaises(sb.ReferenceCleanupError) as cm:
            await self._build(ops)
        self.assertIn('DROP DATABASE IF EXISTS "schema_ref_t"', str(cm.exception))
        self.assertIn("close", self._actions(ops))

    async def test_still_exists_after_drop_is_cleanup_failure(self):
        with self.assertRaises(sb.ReferenceCleanupError):
            await self._build(_FakeOps(still_exists=True))

    async def test_cleanup_failure_reports_earlier_error_too(self):
        ops = _FakeOps(apply_exc=RuntimeError("bad sql"), drop_exc=RuntimeError("perm"))
        with self.assertRaises(sb.ReferenceCleanupError) as cm:
            await self._build(ops)
        self.assertIn("bad sql", str(cm.exception))

    async def test_run_check_turns_cleanup_failure_into_error_exit(self):
        async def boom(*a, **kw):
            raise sb.ReferenceCleanupError("schema_ref_t", "h:1/d", RuntimeError("perm"))
        with mock.patch.object(sb, "build_reference_catalog", boom), \
                mock.patch.object(sb, "current_revision", mock.AsyncMock(return_value=None)):
            rc, report, _ = await sb.run_check(_args())
        self.assertEqual(rc, sb.EXIT_ERROR)
        self.assertIsNone(report)

    def test_reference_names_are_unique_and_valid_identifiers(self):
        names = {sb.reference_db_name() for _ in range(50)}
        self.assertEqual(len(names), 50)
        for n in names:
            self.assertRegex(n, r"^schema_ref_\d{14}_\d+_[0-9a-f]{8}$")
            self.assertLessEqual(len(n), 63, "PostgreSQL 識別字上限 63 bytes")


if __name__ == "__main__":
    unittest.main()
