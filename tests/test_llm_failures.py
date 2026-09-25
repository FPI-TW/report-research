"""research.llm_task_failure：跳過規則、SQL 等價、fail-open，以及四支批次的記錄／清除。

不連 DB：規則等價與 upsert 語意在 sqlite（in-memory，ATTACH 成 `research` schema）
上驗證；批次腳本的 DB 寫入一律 patch 掉。
"""
from __future__ import annotations

import asyncio
import importlib.util
import io
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm_failures as lf  # noqa: E402


def _load_script(name: str):
    # 先註冊進 sys.modules 再 exec：腳本內有 @dataclass（理由見 test_extract_takeaways_sql.py）
    spec = importlib.util.spec_from_file_location(f"_lf_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gt = _load_script("generate_titles")
gs = _load_script("generate_summaries")
et = _load_script("extract_takeaways")
es = _load_script("extract_signals")
lb = _load_script("llm_blocked")


def _sqlite_with_tables() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.execute("ATTACH DATABASE ':memory:' AS research")
    con.execute("CREATE TABLE research.research_report (file_hash text PRIMARY KEY)")
    con.execute(
        "CREATE TABLE research.llm_task_failure ("
        " file_hash text NOT NULL, task text NOT NULL, reason text NOT NULL, model text NOT NULL,"
        " fail_count int NOT NULL DEFAULT 1, first_at text NOT NULL DEFAULT 'T0',"
        " last_at text NOT NULL DEFAULT 'T0', PRIMARY KEY (file_hash, task))"
    )
    return con


# (紀錄：None 或 (task, reason, model, fail_count)), 這次的 model, 期望 skip
CASES = [
    (None, "m1", False),
    (("title", "unparseable", "m1", 1), "m1", False),
    (("title", "unparseable", "m1", 2), "m1", False),
    (("title", "unparseable", "m1", 3), "m1", True),
    (("title", "unparseable", "m1", 7), "m1", True),
    (("title", "empty", "m1", 3), "m1", True),
    (("title", "bad_request", "m1", 2), "m1", False),
    (("title", "content_filter", "m1", 1), "m1", True),
    (("title", "truncated", "m1", 1), "m1", True),
    (("title", "content_filter", "m1", 1), "m2", False),  # 換 model 會重試
    (("title", "unparseable", "m1", 9), "m2", False),
    (("summary", "content_filter", "m1", 1), "m1", False),  # 別的任務的紀錄不影響
]


class SkipRuleEquivalenceTests(unittest.TestCase):
    """should_skip（摘錄、訊號用）與 skip_clause_sql（標題、摘要用）必須等價。"""

    def test_python_and_sql_agree(self):
        for rec, model, expected in CASES:
            with self.subTest(rec=rec, model=model):
                con = _sqlite_with_tables()
                con.execute("INSERT INTO research.research_report VALUES ('h1')")
                py_rec = None
                if rec:
                    task, reason, rec_model, n = rec
                    con.execute(
                        "INSERT INTO research.llm_task_failure (file_hash, task, reason, model, fail_count)"
                        " VALUES ('h1', ?, ?, ?, ?)",
                        (task, reason, rec_model, n),
                    )
                    if task == "title":
                        py_rec = lf.FailureRecord(reason, rec_model, n)
                sql = (
                    "SELECT count(*) FROM research.research_report r WHERE "
                    + lf.skip_clause_sql("r")
                )
                kept = con.execute(sql, lf.skip_params("title", model)).fetchone()[0]
                self.assertEqual(kept == 0, expected)
                self.assertEqual(lf.should_skip(py_rec, model), expected)

    def test_immediate_reasons_are_known_reasons(self):
        self.assertTrue(lf.SKIP_IMMEDIATELY <= lf.REASONS)


class UpsertSemanticsTests(unittest.TestCase):
    """RECORD_SQL：同 model 累加、換 model 歸 1；CLEAR_SQL 刪列。"""

    def setUp(self):
        self.con = _sqlite_with_tables()
        self.con.create_function("now", 0, lambda: "T1")

    def _row(self):
        return self.con.execute(
            "SELECT reason, model, fail_count, first_at FROM research.llm_task_failure"
        ).fetchall()

    def _record(self, reason, model):
        self.con.execute(
            lf.RECORD_SQL, {"file_hash": "h1", "task": "title", "reason": reason, "model": model}
        )

    def test_same_model_accumulates(self):
        self._record("unparseable", "m1")
        self._record("unparseable", "m1")
        self._record("empty", "m1")
        self.assertEqual(self._row(), [("empty", "m1", 3, "T0")])

    def test_model_change_resets_count_and_first_at(self):
        self._record("unparseable", "m1")
        self._record("unparseable", "m1")
        self._record("unparseable", "m2")
        self.assertEqual(self._row(), [("unparseable", "m2", 1, "T1")])

    def test_clear_deletes_only_that_task(self):
        self._record("unparseable", "m1")
        self.con.execute(
            lf.RECORD_SQL, {"file_hash": "h1", "task": "summary", "reason": "unparseable", "model": "m1"}
        )
        self.con.execute(lf.CLEAR_SQL, {"file_hash": "h1", "task": "title"})
        rows = self.con.execute("SELECT task FROM research.llm_task_failure").fetchall()
        self.assertEqual(rows, [("summary",)])


class _FakeSession:
    def __init__(self, calls, fail=False, scalar=True):
        self.calls, self.fail, self._scalar = calls, fail, scalar

    async def __aenter__(self):
        if self.fail:
            raise ConnectionError("db down")
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        self.calls.append((str(stmt), params))
        return SimpleNamespace(scalar=lambda: self._scalar, all=lambda: [])

    async def commit(self):
        self.calls.append(("commit", None))


class RecorderTests(unittest.IsolatedAsyncioTestCase):
    async def test_record_and_clear_issue_sql(self):
        calls: list = []
        rec = lf.FailureRecorder("title", "m1", lambda: _FakeSession(calls))
        await rec.record("h1", lf.UNPARSEABLE)
        await rec.clear("h1")
        self.assertIn("INSERT INTO research.llm_task_failure", calls[0][0])
        self.assertEqual(calls[0][1], {"file_hash": "h1", "task": "title", "reason": "unparseable", "model": "m1"})
        self.assertEqual(calls[1][0], "commit")
        self.assertIn("DELETE FROM research.llm_task_failure", calls[2][0])

    async def test_unknown_vocab_raises(self):
        with self.assertRaises(ValueError):
            lf.FailureRecorder("titles", "m1", lambda: _FakeSession([]))
        rec = lf.FailureRecorder("title", "m1", lambda: _FakeSession([]))
        with self.assertRaises(ValueError):
            await rec.record("h1", "timeout")  # 環境型不是詞彙的一部分

    async def test_missing_hash_is_noop(self):
        calls: list = []
        rec = lf.FailureRecorder("title", "m1", lambda: _FakeSession(calls))
        await rec.record(None, lf.UNPARSEABLE)
        await rec.clear("")
        self.assertEqual(calls, [])

    async def test_write_failure_is_fail_open(self):
        rec = lf.FailureRecorder("title", "m1", lambda: _FakeSession([], fail=True))
        err = io.StringIO()
        with redirect_stderr(err):
            await rec.record("h1", lf.UNPARSEABLE)
            await rec.clear("h1")
        self.assertIn("記錄失敗", err.getvalue())
        self.assertIn("清除失敗", err.getvalue())

    async def test_open_recorder_none_when_table_missing(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rec = await lf.open_recorder("title", "m1", lambda: _FakeSession([], scalar=False))
        self.assertIsNone(rec)
        self.assertIn("make schema", err.getvalue())

    async def test_open_recorder_none_when_db_down(self):
        with redirect_stderr(io.StringIO()):
            rec = await lf.open_recorder("title", "m1", lambda: _FakeSession([], fail=True))
        self.assertIsNone(rec)

    async def test_open_recorder_ready(self):
        rec = await lf.open_recorder("signal", "m1", lambda: _FakeSession([]))
        self.assertIsInstance(rec, lf.FailureRecorder)


class _SpyRecorder:
    def __init__(self):
        self.recorded: list = []
        self.cleared: list = []

    async def record(self, file_hash, reason):
        self.recorded.append((file_hash, reason))

    async def clear(self, file_hash):
        self.cleared.append(file_hash)


class CandidateSqlTests(unittest.TestCase):
    def test_skip_clause_only_when_requested(self):
        for mod in (gt, gs):
            with self.subTest(mod=mod.__name__):
                plain = mod.build_candidates_sql(by_hashes=False, skip_blocked=False, limit=False)
                self.assertNotIn("llm_task_failure", plain)
                sql = mod.build_candidates_sql(by_hashes=True, skip_blocked=True, limit=True)
                self.assertIn(lf.skip_clause_sql("r"), sql)
                self.assertIn("r.file_hash = ANY(:hashes)", sql)
                self.assertTrue(sql.endswith("LIMIT :limit"))
                self.assertIn("r.file_hash", sql.split("FROM")[0])  # SELECT 要帶 file_hash


class _NoDbSessionFactory:
    """title/summary 成功路徑的 UPDATE 用；只收下呼叫，不連 DB。"""

    def __call__(self):
        return _FakeSession([])


class TitleSummaryRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, mod, fn, cli_results):
        rec = _SpyRecorder()
        results = iter(cli_results)
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(mod, "FAIL_LOG", Path(tmp) / "f.log"), \
             mock.patch.object(mod, "SessionFactory", _NoDbSessionFactory()), \
             mock.patch.object(mod, "call_cli", side_effect=lambda *a, **k: next(results)):
            await getattr(mod, fn)(
                asyncio.Semaphore(1), "rid", "f.pdf", "內文" * 50, 3000, 1,
                file_hash="h1", recorder=rec,
            )
        return rec

    async def test_unparseable_is_recorded(self):
        for mod, fn in ((gt, "title_one"), (gs, "summarize_one")):
            with self.subTest(mod=fn):
                bad = mod.CliResult('{"title": null, "summary": null}', None)
                rec = await self._run(mod, fn, [bad, bad, bad])
                self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])
                self.assertEqual(rec.cleared, [])

    async def test_environment_failure_not_recorded(self):
        for mod, fn in ((gt, "title_one"), (gs, "summarize_one")):
            with self.subTest(mod=fn):
                err = mod.CliResult(None, "CLI 逾時（180s 內未回應）")
                rec = await self._run(mod, fn, [err, err, err])
                self.assertEqual(rec.recorded, [])

    async def test_mixed_round_with_one_unparseable_is_recorded(self):
        # 實測：同一篇有時解析不了、有時 CLI 退出碼 1。只要本輪有回過不能用的東西就算。
        bad = gt.CliResult('{"title": null}', None)
        err = gt.CliResult(None, "CLI 退出碼 1：（無 stderr）")
        rec = await self._run(gt, "title_one", [bad, err, err])
        self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])

    async def test_success_clears(self):
        ok_title = gt.CliResult('{"title": "台積電：先進製程需求強勁", "title_original": null, '
                                '"title_source": "extracted"}', None)
        rec = await self._run(gt, "title_one", [ok_title])
        self.assertEqual((rec.recorded, rec.cleared), ([], ["h1"]))
        ok_summary = gs.CliResult('{"summary": "先進製程需求強勁，上修全年營收預估。"}', None)
        rec = await self._run(gs, "summarize_one", [ok_summary])
        self.assertEqual((rec.recorded, rec.cleared), ([], ["h1"]))

    async def test_no_recorder_still_works(self):
        # 表不存在時 recorder=None：行為退回沒有這張表之前
        bad = gt.CliResult('{"title": null}', None)
        results = iter([bad, bad, bad])
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(gt, "FAIL_LOG", Path(tmp) / "f.log"), \
             mock.patch.object(gt, "call_cli", side_effect=lambda *a, **k: next(results)):
            await gt.title_one(asyncio.Semaphore(1), "rid", "f.pdf", "內文", 3000, 1, file_hash="h1")


class TakeawaySignalRecordingTests(unittest.IsolatedAsyncioTestCase):
    async def _takeaway(self, cli_results, rows):
        rec = _SpyRecorder()
        results = iter(cli_results)
        item = et.WorkItem("rep-1", "f.pdf", None, "券商甲", "正典文字", "sha", file_hash="h1")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(et, "FAIL_LOG", Path(tmp) / "f.log"), \
             mock.patch.object(et, "call_cli", side_effect=lambda *a, **k: next(results)), \
             mock.patch.object(et, "build_rows", return_value=rows), \
             mock.patch.object(et, "_replace_rows", new=mock.AsyncMock()):
            await et.extract_one(asyncio.Semaphore(1), item, 24000, "m", 1, recorder=rec)
        return rec

    async def test_takeaway_rejected_with_response_is_recorded(self):
        bad = et.CliResult("不是 JSON", None)
        rec = await self._takeaway([bad, bad, bad], rows=[])
        self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])

    async def test_takeaway_env_failure_not_recorded(self):
        err = et.CliResult(None, "CLI 逾時")
        rec = await self._takeaway([err, err, err], rows=[])
        self.assertEqual(rec.recorded, [])

    async def test_takeaway_success_clears(self):
        good = et.CliResult('{"takeaways": []}', None)
        with mock.patch.object(et, "parse_takeaways", return_value=SimpleNamespace(ok=True, error=None)):
            rec = await self._takeaway([good], rows=[object()])
        self.assertEqual((rec.recorded, rec.cleared), ([], ["h1"]))

    async def _signal(self, cli_results, statuses):
        rec = _SpyRecorder()
        results = iter(cli_results)
        item = es.WorkItem("rep-1", "TW", "券商甲", None, "f.pdf", "內文", ["2330"], file_hash="h1")
        rows = [SimpleNamespace(extraction_status=s) for s in statuses]
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(es, "FAIL_LOG", Path(tmp) / "f.log"), \
             mock.patch.object(es, "call_cli", side_effect=lambda *a, **k: next(results)), \
             mock.patch.object(es, "build_rows", return_value=rows), \
             mock.patch.object(es, "_upsert_rows", new=mock.AsyncMock()):
            await es.extract_one(asyncio.Semaphore(1), item, 16000, "m", 1, recorder=rec)
        return rec

    async def test_signal_all_rejected_with_response_is_recorded(self):
        bad = es.CliResult("不是 JSON", None)
        rec = await self._signal([bad, bad, bad], ["rejected"])
        self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])

    async def test_signal_env_failure_not_recorded(self):
        err = es.CliResult(None, "CLI 逾時")
        rec = await self._signal([err, err, err], ["rejected"])
        self.assertEqual(rec.recorded, [])

    async def test_signal_success_clears(self):
        good = es.CliResult("{}", None)
        with mock.patch.object(es, "parse_signal", return_value=SimpleNamespace(ok=True, error=None)):
            rec = await self._signal([good], ["valid"])
        self.assertEqual((rec.recorded, rec.cleared), ([], ["h1"]))


class _Ctx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class WorklistSkipTests(unittest.IsolatedAsyncioTestCase):
    async def test_takeaway_worklist_skips_blocked(self):
        reports = [
            ("r1", "a.pdf", None, "券商", "甲乙丙", "hA"),
            ("r2", "b.pdf", None, "券商", "丁戊己", "hB"),
        ]
        failures = {"hA": lf.FailureRecord(lf.CONTENT_FILTER, "m", 1)}
        with mock.patch.object(et, "SessionFactory", _Ctx), \
             mock.patch.object(et, "_fetch_reports", new=mock.AsyncMock(return_value=reports)), \
             mock.patch.object(et, "_fetch_done_map", new=mock.AsyncMock(return_value={})), \
             mock.patch.object(et, "excerpt_without_tables", side_effect=lambda ft, fh, c: c), \
             mock.patch.object(et.llm_failures, "fetch_failures", new=mock.AsyncMock(return_value=failures)):
            _, skipped = await et.build_worklist(90, False, None, skip_model="m")
            _, retried = await et.build_worklist(90, False, None, skip_model="other")
            _, unfiltered = await et.build_worklist(90, False, None, skip_model=None)
        self.assertEqual([w.file_hash for w in skipped], ["hB"])
        self.assertEqual([w.file_hash for w in retried], ["hA", "hB"])
        self.assertEqual([w.file_hash for w in unfiltered], ["hA", "hB"])

    async def test_signal_worklist_skips_blocked(self):
        reports = [
            ("r1", "券商", None, "TW", "a.pdf", "內文", ["2330"], "hA"),
            ("r2", "券商", None, "TW", "b.pdf", "內文", ["2330"], "hB"),
        ]
        failures = {"hB": lf.FailureRecord(lf.UNPARSEABLE, "m", 3)}
        with mock.patch.object(es, "SessionFactory", _Ctx), \
             mock.patch.object(es, "_fetch_subset", new=mock.AsyncMock(return_value=[("2330", "TW", 5, 9)])), \
             mock.patch.object(es, "_fetch_reports", new=mock.AsyncMock(return_value=reports)), \
             mock.patch.object(es, "_fetch_done_map", new=mock.AsyncMock(return_value={})), \
             mock.patch.object(es.llm_failures, "fetch_failures", new=mock.AsyncMock(return_value=failures)):
            _, worklist = await es.build_worklist(3, 5, 50, False, skip_model="m")
        self.assertEqual([w.file_hash for w in worklist], ["hA"])

    def test_signal_reports_sql_selects_file_hash_last(self):
        # build_worklist 以位置解包，file_hash 必須是最後一欄
        select = es.build_reports_sql().split("FROM")[0]
        self.assertTrue(select.rstrip().endswith("r.file_hash"))


class TitleExcludeHashesTests(unittest.IsolatedAsyncioTestCase):
    """審查 L2：sync 的標題積壓段排掉本輪 4b 剛打過的新研報，失敗才不會一輪記兩次。"""

    def test_sql_excludes_only_when_requested(self):
        plain = gt.build_candidates_sql(by_hashes=False, skip_blocked=True, limit=True)
        self.assertNotIn(":exclude", plain)
        sql = gt.build_candidates_sql(by_hashes=False, skip_blocked=True, limit=True, exclude=True)
        self.assertIn("AND NOT (r.file_hash = ANY(:exclude))", sql)
        self.assertLess(sql.index(":exclude"), sql.index("ORDER BY"))

    async def _fetch(self, exclude_hashes):
        sess = _FakeSession([])
        with mock.patch.object(gt, "SessionFactory", lambda: sess):
            await gt.fetch_candidates(60, None, skip_blocked=False, exclude_hashes=exclude_hashes)
        return sess.calls[0]

    async def test_empty_or_none_adds_no_condition(self):
        for excl in (None, []):
            with self.subTest(excl=excl):
                sql, params = await self._fetch(excl)
                self.assertNotIn(":exclude", sql)
                self.assertNotIn("exclude", params)

    async def test_exclude_list_is_bound(self):
        sql, params = await self._fetch(["h1", "h2"])
        self.assertIn("ANY(:exclude)", sql)
        self.assertEqual(params["exclude"], ["h1", "h2"])


class _Recorder:
    """main() 接線測試用的假 recorder（只看有沒有被傳進逐篇函式）。"""


def _bound(fn, call) -> dict:
    """把 mock 收到的呼叫依真函式簽章綁回參數名，位置或關鍵字都能斷言。"""
    import inspect

    bound = inspect.signature(fn).bind(*call.args, **call.kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


class MainWiringTests(unittest.IsolatedAsyncioTestCase):
    """審查 L3：四支批次 main() 的接線——跳過參數、--retry-blocked、recorder 傳遞。

    不連 DB：open_recorder、候選查詢與逐篇函式全部 patch。open_recorder 回 None
    （表不存在、DB 掛）時一律不套跳過條件，行為退回沒有這張表之前。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.fail_log = Path(self._tmp.name) / "fail.log"

    # ── 標題、摘要：SQL 端跳過（fetch_candidates 的 skip_blocked） ──

    async def _run_sql_side(self, mod, one_name, recorder, retry_blocked, **extra):
        fetch = mock.AsyncMock(return_value=[("rid", "f.pdf", "內文", "h1")])
        one = mock.AsyncMock()
        opener = mock.AsyncMock(return_value=recorder)
        with mock.patch.object(mod, "FAIL_LOG", self.fail_log), \
             mock.patch.object(mod.llm_failures, "open_recorder", opener), \
             mock.patch.object(mod, "fetch_candidates", fetch), \
             mock.patch.object(mod, one_name, one), \
             redirect_stderr(io.StringIO()), mock.patch("builtins.print"):
            await mod.main(2, None, 3000, None, retry_blocked, **extra)
        return opener, fetch, one

    async def test_titles_and_summaries(self):
        cases = [
            # (recorder, retry_blocked, 期望 skip_blocked)
            (_Recorder(), False, True),
            (_Recorder(), True, False),
            (None, False, False),
            (None, True, False),
        ]
        for mod, one_name, task in ((gt, "title_one", lf.TASK_TITLE), (gs, "summarize_one", lf.TASK_SUMMARY)):
            for rec, retry, want_skip in cases:
                with self.subTest(mod=mod.__name__, recorder=rec is not None, retry=retry):
                    opener, fetch, one = await self._run_sql_side(mod, one_name, rec, retry)
                    self.assertEqual(opener.await_args.args[:2], (task, mod.MODEL))
                    self.assertIs(_bound(mod.fetch_candidates, fetch.await_args)["skip_blocked"], want_skip)
                    self.assertEqual(one.await_count, 1)
                    self.assertIs(one.await_args.kwargs["recorder"], rec)
                    self.assertEqual(one.await_args.kwargs["file_hash"], "h1")

    async def test_titles_pass_exclude_hashes(self):
        excl = Path(self._tmp.name) / "excl.txt"
        excl.write_text("hA\n\nhB\n", encoding="utf-8")
        _, fetch, _ = await self._run_sql_side(gt, "title_one", _Recorder(), False, exclude_hashes_file=str(excl))
        self.assertEqual(_bound(gt.fetch_candidates, fetch.await_args)["exclude_hashes"], ["hA", "hB"])
        _, fetch, _ = await self._run_sql_side(gt, "title_one", _Recorder(), False)
        self.assertIsNone(_bound(gt.fetch_candidates, fetch.await_args)["exclude_hashes"])

    # ── 摘錄、訊號：Python 端跳過（build_worklist 的 skip_model） ──

    def _takeaway_args(self, **kw):
        base = dict(hashes_file=None, model="m", retry_blocked=False, reextract=False, since_days=90,
                    dry_run=False, limit=None, workers=1, excerpt=24000)
        base.update(kw)
        return SimpleNamespace(**base)

    def _signal_args(self, **kw):
        base = dict(model="m", retry_blocked=False, reextract=False, min_brokers=3, min_reports=5,
                    top_n=50, dry_run=False, limit=None, workers=1, excerpt=16000)
        base.update(kw)
        return SimpleNamespace(**base)

    async def _run_py_side(self, mod, args, recorder, worklist_ret):
        build = mock.AsyncMock(return_value=worklist_ret)
        one = mock.AsyncMock()
        opener = mock.AsyncMock(return_value=recorder)
        with mock.patch.object(mod, "FAIL_LOG", self.fail_log), \
             mock.patch.object(mod.llm_failures, "open_recorder", opener), \
             mock.patch.object(mod, "build_worklist", build), \
             mock.patch.object(mod, "extract_one", one), \
             redirect_stderr(io.StringIO()), mock.patch("builtins.print"):
            await mod.main(args)
        return opener, build, one

    async def test_takeaways_and_signals(self):
        item = SimpleNamespace(file_hash="h1")
        cases = [
            # (recorder, retry_blocked, reextract, 期望 skip_model)
            (_Recorder(), False, False, "m"),
            (_Recorder(), True, False, None),
            (_Recorder(), False, True, None),  # 審查 L4：--reextract 隱含 --retry-blocked
            (None, False, False, None),
        ]
        runs = (
            (et, self._takeaway_args, (1, [item]), lf.TASK_TAKEAWAY),
            (es, self._signal_args, ([("2330", "TW", 5, 9)], [item]), lf.TASK_SIGNAL),
        )
        for mod, make_args, ret, task in runs:
            for rec, retry, reextract, want in cases:
                with self.subTest(mod=mod.__name__, recorder=rec is not None, retry=retry, reextract=reextract):
                    args = make_args(retry_blocked=retry, reextract=reextract)
                    opener, build, one = await self._run_py_side(mod, args, rec, ret)
                    self.assertEqual(opener.await_args.args[:2], (task, "m"))
                    bound = _bound(mod.build_worklist, build.await_args)
                    self.assertEqual(bound["skip_model"], want)
                    self.assertIs(bound["reextract"], reextract)
                    self.assertEqual(one.await_count, 1)
                    self.assertIs(one.await_args.kwargs["recorder"], rec)


class LlmBlockedListingTests(unittest.TestCase):
    def _row(self, task, reason, n, model="m"):
        ts = datetime(2026, 9, 24, 8, 0)
        return (task, reason, n, model, ts, ts, "h" + task, "x.pdf")

    def test_default_lists_only_blocked(self):
        rows = [self._row("title", "unparseable", 3), self._row("signal", "unparseable", 1)]
        items = lb.select_rows(rows, None, include_pending=False)
        self.assertEqual([i[1] for i in items], ["title"])

    def test_all_and_task_filter(self):
        rows = [self._row("title", "unparseable", 1), self._row("signal", "content_filter", 1)]
        self.assertEqual(len(lb.select_rows(rows, None, include_pending=True)), 2)
        self.assertEqual([i[1] for i in lb.select_rows(rows, "signal", False)], ["signal"])

    def test_format_row(self):
        line = lb.format_row(lb.select_rows([self._row("title", "truncated", 1)], None, False)[0])
        self.assertIn("跳過", line)
        self.assertIn("truncated×1", line)
        self.assertIn("2026-09-24 08:00", line)


if __name__ == "__main__":
    unittest.main()
