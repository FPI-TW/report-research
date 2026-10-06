"""檢索回歸檢查（app/services/retrieval_regression.py、scripts/retrieval_regression.py）。

不連 DB、不載模型：假 session（認 `_CORPUS_SQL`／`_META_SQL` 兩句）、假嵌入、假 `hybrid_search`（從記憶體裡的
「語料」回 ChunkRow）。基準與結果檔都寫 tempfile。驗：指標、新研報／隱藏／下架不算劣化、三種劣化判定、
退出碼分流（0／1／2／3）、略過不蓋掉上一次比對、管理頁的狀態燈，以及 unit 與入口檔的靜態約束。
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from app.services import data_health
from app.services import retrieval_regression as rr
from app.services.rows import ChunkRow
from eval.question_contract import load_dataset
from scripts import retrieval_regression as script

REPO_ROOT = Path(__file__).resolve().parents[1]
T0 = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
LATER = T0 + timedelta(days=2)
QUESTIONS, _DIGEST = load_dataset(rr.DATASET)
QID_BY_TEXT = {q["question"]: q["id"] for q in QUESTIONS}


def H(n: int) -> str:
    return f"{n:064x}"


def _row(file_hash: str, chunk_index: int) -> ChunkRow:
    fields = dict.fromkeys(ChunkRow._fields)
    fields.update(chunk_id=f"c-{file_hash[-6:]}-{chunk_index}", report_id=f"r-{file_hash[-6:]}", file_hash=file_hash,
                  file_name=f"{file_hash[-4:]}.pdf", chunk_index=chunk_index, content="", distance=0.2)
    return ChunkRow(**fields)


class FakeCorpus:
    """記憶體裡的語料：研報 meta＋每題的候選清單（已排序，hybrid_search 會回的樣子）。"""

    def __init__(self, n_reports: int = 80):
        self.reports = {H(i): {"created_at": T0 - timedelta(days=i % 30), "hidden": False, "title": f"研報{i}"}
                        for i in range(1, n_reports + 1)}
        self.results = {q["id"]: [(H(1 + (n * 3 + j) % n_reports), j % 3) for j in range(14)]
                        for n, q in enumerate(QUESTIONS)}
        self.fail: BaseException | None = None
        self.embedded: list[str] = []
        self.search_kwargs: list[dict] = []

    # 假 SessionFactory
    def session(self):
        corpus = self

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, stmt, params=None):
                if corpus.fail is not None:
                    raise corpus.fail
                if stmt is rr._CORPUS_SQL:
                    cutoff = max(m["created_at"] for m in corpus.reports.values())
                    row = SimpleNamespace(cutoff=cutoff, n=len(corpus.reports))
                    return SimpleNamespace(one=lambda: row)
                if stmt is rr._META_SQL:
                    rows = [SimpleNamespace(file_hash=h, created_at=m["created_at"], label=m["title"],
                                            hidden=m["hidden"])
                            for h, m in corpus.reports.items() if h in set(params["hashes"])]
                    return SimpleNamespace(all=lambda: rows)
                raise AssertionError(f"未預期的 SQL：{stmt}")

        return _Session()

    def embed(self, text: str) -> list[float]:
        self.embedded.append(text)
        return [0.0] * 4

    async def search(self, session, q, vec, *, k, dense_scan, **filters):
        await session.execute(rr._CORPUS_SQL)  # 讓注入的 DB 失敗也打在檢索這一步
        self.search_kwargs.append({"k": k, "dense_scan": dense_scan, **filters})
        visible = [(h, c) for h, c in self.results[QID_BY_TEXT[q]]
                   if h in self.reports and not self.reports[h]["hidden"]]
        return [(1, round(0.9 - n * 0.01, 4), _row(h, c)) for n, (h, c) in enumerate(visible)]

    def baseline_hashes(self, qid: str, k: int = 10) -> list[str]:
        return [h for h, _ in self.results[qid][:k]]

    def unrelated(self, n: int) -> list[tuple[str, int]]:
        """n 篇舊研報（基準時點之前就在語料裡、但不在任何一題的基準裡）。"""
        out = []
        for j in range(n):
            h = H(900 + j)
            self.reports[h] = {"created_at": T0 - timedelta(days=40), "hidden": False, "title": f"舊研報{j}"}
            out.append((h, 0))
        return out


def _settings(**over):
    base = dict(retrieval_regression_k=10, retrieval_regression_min_mean_recall=0.8,
                retrieval_regression_min_question_recall=0.5, retrieval_regression_max_degraded_questions=2,
                retrieval_regression_min_available_gib=4.0, ask_dense_scan=400)
    base.update(over)
    return SimpleNamespace(**base)


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.baseline = self.tmp / "baseline" / "baseline.json"
        env = mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": str(self.tmp / "health")})
        env.start()
        self.addCleanup(env.stop)
        self.corpus = FakeCorpus()
        self.now = T0 + timedelta(days=3)

    def _deps(self, **over):
        d = dict(settings=_settings(), session_factory=self.corpus.session, embed_fn=self.corpus.embed,
                 search_fn=self.corpus.search, available_gib_fn=lambda: 16.0, sync_running_fn=lambda: False,
                 now_fn=lambda: self.now)
        d.update(over)
        return d

    def capture(self, **over) -> int:
        force = over.pop("force", False)
        as_of = over.pop("as_of", None)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return asyncio.run(script.run_capture(self.baseline, force=force, as_of=as_of, **self._deps(**over)))

    def check(self, **over) -> int:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = asyncio.run(script.run_check(self.baseline, **self._deps(**over)))
        self.stdout, self.stderr = out.getvalue(), err.getvalue()
        return rc

    def result(self) -> dict:
        data, why = data_health.read_result(rr.RESULT_NAME)
        self.assertIsNone(why)
        return data


# ── 指標 ───────────────────────────────────────────────────────────────────


class RboTests(unittest.TestCase):
    def test_bounds(self):
        self.assertEqual(rr.rbo([1, 2, 3], [1, 2, 3]), 1.0)
        self.assertEqual(rr.rbo([1, 2, 3], [4, 5, 6]), 0.0)
        self.assertIsNone(rr.rbo([], []))

    def test_top_weighted(self):
        # 同樣換掉一個元素，換在頂端比換在尾端分數低。
        head = rr.rbo([1, 2, 3, 4], [9, 2, 3, 4])
        tail = rr.rbo([1, 2, 3, 4], [1, 2, 3, 9])
        self.assertLess(head, tail)
        self.assertLess(tail, 1.0)

    def test_swap_is_less_than_identity(self):
        self.assertLess(rr.rbo([1, 2, 3], [2, 1, 3]), 1.0)
        self.assertGreater(rr.rbo([1, 2, 3], [2, 1, 3]), 0.5)


class CompareQuestionTests(unittest.TestCase):
    def setUp(self):
        self.base = [{"file_hash": H(i), "chunk_index": 0} for i in range(1, 6)]
        self.meta = {H(i): rr.ReportMeta(created_at=T0, hidden=False, label=f"研報{i}") for i in range(1, 30)}

    def cmp(self, current, meta=None, k=5):
        return rr.compare_question(self.base, [{"file_hash": h, "chunk_index": c} for h, c in current],
                                   meta or self.meta, cutoff=T0, k=k)

    def test_identical(self):
        r = self.cmp([(H(i), 0) for i in range(1, 8)])
        self.assertEqual((r["report_recall"], r["chunk_recall"], r["rbo"]), (1.0, 1.0, 1.0))
        self.assertEqual((r["lost"], r["gained"], r["excluded_new_reports"]), ([], [], 0))

    def test_new_reports_are_excluded_before_truncation(self):
        meta = {**self.meta, H(100): rr.ReportMeta(created_at=LATER, hidden=False, label="新研報"),
                H(101): rr.ReportMeta(created_at=LATER, hidden=False, label="新研報2")}
        r = self.cmp([(H(100), 0), (H(101), 0)] + [(H(i), 0) for i in range(1, 8)], meta)
        self.assertEqual(r["report_recall"], 1.0)
        self.assertEqual(r["raw_report_recall"], 0.6)  # 不排除的話後兩篇被擠出 top-5
        self.assertEqual(r["excluded_new_reports"], 2)

    def test_reingested_baseline_report_is_not_new(self):
        # 重新入庫會重設 created_at；在基準裡的研報不能因此被當成新的排除。
        meta = {**self.meta, H(1): rr.ReportMeta(created_at=LATER, hidden=False)}
        r = self.cmp([(H(i), 0) for i in range(1, 8)], meta)
        self.assertEqual((r["report_recall"], r["excluded_new_reports"]), (1.0, 0))

    def test_hidden_and_removed_leave_the_denominator(self):
        meta = dict(self.meta)
        meta[H(2)] = rr.ReportMeta(created_at=T0, hidden=True)
        del meta[H(3)]
        r = self.cmp([(H(1), 0), (H(4), 0), (H(5), 0), (H(6), 0), (H(7), 0)], meta)
        self.assertEqual((r["hidden_reports"], r["removed_reports"], r["eligible_reports"]), (1, 1, 3))
        self.assertEqual(r["report_recall"], 1.0)
        self.assertEqual([g["file_hash"] for g in r["gained"]], [H(6), H(7)])

    def test_real_loss(self):
        r = self.cmp([(H(1), 0), (H(20), 0), (H(21), 0), (H(22), 0), (H(23), 0)])
        self.assertEqual(r["report_recall"], 0.2)
        self.assertEqual([x["file_hash"] for x in r["lost"]], [H(2), H(3), H(4), H(5)])
        self.assertEqual(r["lost"][0]["label"], "研報2")
        self.assertEqual(r["lost_total"], 4)

    def test_chunk_level_change_keeps_report_recall(self):
        # 重切片段（E1d 回填）：同一篇、不同 chunk_index ⇒ 研報召回不變、片段召回下降。
        r = self.cmp([(H(i), 1) for i in range(1, 6)])
        self.assertEqual((r["report_recall"], r["chunk_recall"]), (1.0, 0.0))

    def test_all_baseline_gone_is_not_comparable(self):
        r = self.cmp([(H(20), 0)], meta={H(20): rr.ReportMeta(created_at=T0, hidden=False)})
        self.assertFalse(r["comparable"])
        self.assertIsNone(r["report_recall"])


class SummarizeTests(unittest.TestCase):
    T = rr.Thresholds(min_mean_recall=0.8, min_question_recall=0.5, max_degraded_questions=2)

    def q(self, i, recall, comparable=True):
        return {"id": f"q{i:03d}", "comparable": comparable, "report_recall": recall if comparable else None,
                "raw_report_recall": recall, "chunk_recall": recall, "rbo": recall,
                "hidden_reports": 0, "removed_reports": 0, "excluded_new_reports": 1}

    def test_ok(self):
        s = rr.summarize([self.q(i, 1.0) for i in range(10)] + [self.q(10, 0.0), self.q(11, 0.4)], self.T)
        self.assertEqual(s["verdict"], rr.VERDICT_OK)
        self.assertEqual(s["degraded_ids"], ["q010", "q011"])

    def test_too_many_collapsed_questions(self):
        s = rr.summarize([self.q(i, 1.0) for i in range(15)] + [self.q(i, 0.0) for i in range(15, 18)], self.T)
        self.assertGreaterEqual(s["mean_report_recall"], 0.8)  # 平均還過得去，但三題崩掉
        self.assertEqual(s["verdict"], rr.VERDICT_DEGRADED)

    def test_mean_below_threshold(self):
        s = rr.summarize([self.q(i, 0.7) for i in range(18)], self.T)
        self.assertEqual((s["verdict"], s["degraded_questions"]), (rr.VERDICT_DEGRADED, 0))

    def test_mostly_incomparable(self):
        s = rr.summarize([self.q(i, 1.0) for i in range(4)] + [self.q(i, None, False) for i in range(4, 10)],
                         self.T)
        self.assertEqual(s["verdict"], rr.VERDICT_INCOMPARABLE)


# ── 基準檔 ─────────────────────────────────────────────────────────────────


class BaselineFileTests(_Tmp):
    def test_missing_and_corrupt(self):
        with self.assertRaises(rr.BaselineMissing):
            rr.load_baseline(self.baseline)
        self.baseline.parent.mkdir(parents=True)
        self.baseline.write_text("{nope", encoding="utf-8")
        with self.assertRaises(rr.BaselineError):
            rr.load_baseline(self.baseline)
        self.baseline.write_text(json.dumps({"format": rr.BASELINE_FORMAT, "version": 99}), encoding="utf-8")
        with self.assertRaisesRegex(rr.BaselineError, "版本"):
            rr.load_baseline(self.baseline)

    def test_capture_writes_versioned_baseline(self):
        self.assertEqual(self.capture(), script.EXIT_OK)
        doc = rr.load_baseline(self.baseline)
        self.assertEqual((doc["format"], doc["version"]), (rr.BASELINE_FORMAT, rr.BASELINE_VERSION))
        self.assertEqual(doc["dataset"], {"path": rr.DATASET_REL, "sha256": _DIGEST, "count": 18})
        self.assertEqual(doc["params"]["k"], 10)
        self.assertEqual(doc["params"]["dense_scan"], 400)
        self.assertEqual(data_health.parse_time(doc["corpus_cutoff"]), T0)
        q1 = doc["questions"][0]
        self.assertEqual([i["file_hash"] for i in q1["items"]], self.corpus.baseline_hashes("q001"))
        self.assertNotIn("report_id", q1["items"][0])  # UUID 會在重新入庫時換掉，不存
        # 過濾條件照題集傳給 hybrid_search。
        self.assertEqual(self.corpus.search_kwargs[0], {"k": 10, "dense_scan": 400, "market": "TW"})
        self.assertEqual([p.name for p in self.baseline.parent.iterdir()], ["baseline.json"])

    def test_capture_refuses_to_overwrite_without_force(self):
        self.assertEqual(self.capture(), script.EXIT_OK)
        before = self.baseline.read_text(encoding="utf-8")
        self.assertEqual(self.capture(), script.EXIT_ERROR)
        self.assertEqual(self.baseline.read_text(encoding="utf-8"), before)
        self.assertEqual(self.capture(force=True), script.EXIT_OK)

    def test_capture_as_of_leaves_out_later_reports(self):
        newest = self.corpus.results["q001"][0][0]
        self.corpus.reports[newest]["created_at"] = LATER
        self.assertEqual(self.capture(as_of=T0), script.EXIT_OK)
        doc = rr.load_baseline(self.baseline)
        self.assertTrue(doc["simulated_as_of"])
        self.assertNotIn(newest, {i["file_hash"] for i in doc["questions"][0]["items"]})
        self.assertEqual(len(doc["questions"][0]["items"]), 10)

    def test_capture_skips_when_db_down(self):
        self.corpus.fail = ConnectionRefusedError(111, "refused")
        self.assertEqual(self.capture(), script.EXIT_SKIPPED)
        self.assertFalse(self.baseline.exists())


# ── check：退出碼與結果檔 ──────────────────────────────────────────────────


class CheckTests(_Tmp):
    def setUp(self):
        super().setUp()
        self.assertEqual(self.capture(), script.EXIT_OK)

    def test_unchanged_corpus(self):
        self.assertEqual(self.check(), script.EXIT_OK)
        res = self.result()
        self.assertEqual((res["outcome"], res["exit_code"], res["reason"]), ("ok", 0, None))
        s = res["comparison"]["summary"]
        self.assertEqual((s["mean_report_recall"], s["mean_chunk_recall"], s["mean_rbo"]), (1.0, 1.0, 1.0))
        self.assertEqual((s["comparable"], s["questions"]), (18, 18))
        self.assertFalse(res["comparison"]["params_changed"])
        self.assertIn("沒有劣化", self.stdout)

    def test_new_reports_crowding_topk_is_not_a_regression(self):
        for n in range(3):
            h = H(500 + n)
            self.corpus.reports[h] = {"created_at": LATER, "hidden": False, "title": "新研報"}
            for items in self.corpus.results.values():
                items.insert(0, (h, 0))
        self.assertEqual(self.check(), script.EXIT_OK)
        s = self.result()["comparison"]["summary"]
        self.assertEqual(s["mean_report_recall"], 1.0)
        self.assertEqual(s["mean_raw_report_recall"], 0.7)  # 沒排除的話每題掉 3 篇
        self.assertEqual(s["excluded_new_reports"], 3 * 18)

    def test_hidden_and_removed_are_counted_separately(self):
        q1 = self.corpus.baseline_hashes("q001")
        self.corpus.reports[q1[0]]["hidden"] = True
        del self.corpus.reports[q1[1]]
        self.assertEqual(self.check(), script.EXIT_OK)
        res = self.result()["comparison"]
        first = res["questions"][0]
        self.assertEqual((first["hidden_reports"], first["removed_reports"], first["report_recall"]), (1, 1, 1.0))
        self.assertGreaterEqual(res["summary"]["hidden_reports"], 1)

    def test_three_collapsed_questions_alert(self):
        for qid in ("q002", "q005", "q009"):
            self.corpus.results[qid] = self.corpus.unrelated(10)
        self.assertEqual(self.check(), script.EXIT_DEGRADED)
        res = self.result()
        self.assertEqual(res["outcome"], "degraded")
        self.assertEqual(res["comparison"]["summary"]["degraded_questions"], 3)
        degraded = [q["id"] for q in res["comparison"]["questions"] if q["degraded"]]
        self.assertEqual(degraded, ["q002", "q005", "q009"])
        self.assertIn("檢索劣化", self.stderr)

    def test_one_collapsed_question_does_not_alert(self):
        self.corpus.results["q003"] = self.corpus.unrelated(10)
        self.assertEqual(self.check(), script.EXIT_OK)
        self.assertEqual(self.result()["comparison"]["summary"]["degraded_questions"], 1)

    def test_broad_drift_alerts_by_mean(self):
        for qid, items in self.corpus.results.items():
            keep = items[:7]
            self.corpus.results[qid] = keep + self.corpus.unrelated(5)
        self.assertEqual(self.check(), script.EXIT_DEGRADED)
        s = self.result()["comparison"]["summary"]
        self.assertLess(s["mean_report_recall"], 0.8)
        self.assertEqual(s["degraded_questions"], 0)

    def test_db_down_skips_and_keeps_previous_comparison(self):
        self.assertEqual(self.check(), script.EXIT_OK)
        first = self.result()["comparison"]
        self.corpus.fail = ConnectionRefusedError(111, "refused")
        self.now += timedelta(days=1)
        self.assertEqual(self.check(), script.EXIT_SKIPPED)
        res = self.result()
        self.assertEqual((res["outcome"], res["reason"], res["exit_code"]), ("skipped", "db_unavailable", 2))
        self.assertEqual(res["comparison"], first)

    def test_db_error_that_is_not_connectivity_alerts(self):
        self.corpus.fail = RuntimeError("column r.title does not exist")
        self.assertEqual(self.check(), script.EXIT_ERROR)
        self.assertEqual(self.result()["reason"], "query_failed")

    def test_low_memory_skips_before_loading_model(self):
        self.corpus.embedded.clear()
        self.assertEqual(self.check(available_gib_fn=lambda: 1.5), script.EXIT_SKIPPED)
        self.assertEqual(self.result()["reason"], "low_memory")
        self.assertEqual(self.corpus.embedded, [])

    def test_sync_running_skips(self):
        self.assertEqual(self.check(sync_running_fn=lambda: True), script.EXIT_SKIPPED)
        self.assertEqual(self.result()["reason"], "sync_running")

    def test_embed_failure_alerts(self):
        def boom(_text):
            raise OSError("model files missing")

        self.assertEqual(self.check(embed_fn=boom), script.EXIT_ERROR)
        self.assertEqual(self.result()["reason"], "embed_failed")

    def test_no_baseline_alerts(self):
        self.baseline.unlink()
        self.assertEqual(self.check(), script.EXIT_ERROR)
        res = self.result()
        self.assertEqual((res["outcome"], res["reason"]), ("error", "no_baseline"))
        self.assertIn("capture", res["message"])

    def test_dataset_change_is_incomparable(self):
        doc = json.loads(self.baseline.read_text(encoding="utf-8"))
        doc["dataset"]["sha256"] = "0" * 64
        self.baseline.write_text(json.dumps(doc), encoding="utf-8")
        self.assertEqual(self.check(), script.EXIT_ERROR)
        self.assertEqual(self.result()["reason"], "incomparable")

    def test_stale_baseline_is_incomparable_not_degraded(self):
        for qid in [q["id"] for q in QUESTIONS][:12]:
            for h in self.corpus.baseline_hashes(qid):
                self.corpus.reports.pop(h, None)
        self.assertEqual(self.check(), script.EXIT_ERROR)
        res = self.result()
        self.assertEqual(res["reason"], "incomparable")
        self.assertEqual(res["comparison"]["summary"]["verdict"], "incomparable")

    def test_dense_scan_change_is_flagged(self):
        self.assertEqual(self.check(settings=_settings(ask_dense_scan=200)), script.EXIT_OK)
        res = self.result()["comparison"]
        self.assertTrue(res["params_changed"])
        self.assertEqual(res["dense_scan"], 200)
        self.assertEqual(self.corpus.search_kwargs[-1]["dense_scan"], 200)
        self.assertEqual(self.corpus.search_kwargs[-1]["k"], 10)  # k 一律用基準的

    def test_json_output(self):
        self.assertEqual(self.check(json_out=True), script.EXIT_OK)
        self.assertEqual(json.loads(self.stdout)["summary"]["verdict"], "ok")

    def test_unexpected_error_is_rc3_not_degraded(self):
        with mock.patch.object(script, "run_check", side_effect=KeyError("boom")), \
                mock.patch.object(rr, "baseline_path", return_value=self.baseline), \
                contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(script.main(["check"]), script.EXIT_ERROR)
        self.assertEqual(self.result()["reason"], "unexpected")


class ProbeTests(unittest.TestCase):
    def test_sync_pid_file(self):
        with tempfile.TemporaryDirectory() as d:
            pid_file = Path(d) / ".sync_new_reports.lock"
            self.assertFalse(script.sync_running(pid_file))
            pid_file.write_text(str(os.getpid()))
            self.assertTrue(script.sync_running(pid_file))
            pid_file.write_text("999999999")
            self.assertFalse(script.sync_running(pid_file))
            pid_file.write_text("garbage")
            self.assertFalse(script.sync_running(pid_file))

    def test_sync_pid_file_matches_sync_script(self):
        sh = (REPO_ROOT / "scripts" / "sync_new_reports.sh").read_text(encoding="utf-8")
        self.assertIn(f'LOCK="{script.SYNC_PID_FILE.relative_to(REPO_ROOT).as_posix()}"', sh)

    def test_meminfo(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "meminfo"
            f.write_text("MemTotal: 20000000 kB\nMemAvailable:    4194304 kB\n")
            self.assertEqual(script.available_gib(f), 4.0)
            self.assertIsNone(script.available_gib(Path(d) / "missing"))

    def test_connection_failure_classification(self):
        from sqlalchemy.exc import OperationalError, ProgrammingError

        self.assertTrue(script.is_connection_failure(ConnectionRefusedError()))
        self.assertTrue(script.is_connection_failure(OperationalError("x", {}, ConnectionRefusedError())))
        self.assertFalse(script.is_connection_failure(ProgrammingError("x", {}, ValueError("syntax"))))
        self.assertFalse(script.is_connection_failure(RuntimeError("x")))


# ── 管理頁判讀（section）───────────────────────────────────────────────────


class SectionTests(_Tmp):
    def write(self, **payload):
        data_health.write_result(rr.RESULT_NAME, payload)

    def comparison(self, finished_at: datetime, *, verdict="ok", params_changed=False):
        return {"finished_at": finished_at.isoformat(), "duration_s": 12.5, "dense_scan": 400,
                "params_changed": params_changed,
                "baseline": {"captured_at": T0.isoformat(), "corpus_cutoff": T0.isoformat(), "corpus_reports": 9,
                             "simulated_as_of": False, "k": 10, "dense_scan": 400, "dataset_sha256": _DIGEST},
                "thresholds": {"min_mean_recall": 0.8, "min_question_recall": 0.5, "max_degraded_questions": 2},
                "summary": {"verdict": verdict, "questions": 1, "comparable": 1, "mean_report_recall": 1.0,
                            "degraded_questions": 0, "hidden_reports": 0, "removed_reports": 0,
                            "excluded_new_reports": 0},
                "questions": [{"id": "q001", "question": "台積電", "comparable": True, "degraded": False,
                               "report_recall": 1.0, "lost": [{"file_hash": H(1), "label": "x"},
                                                              {"file_hash": "../etc", "label": "bad"}],
                               "gained": "not-a-list", "hidden_reports": -3}]}

    def test_missing(self):
        s = rr.section()
        self.assertEqual((s["status"], s["available"], s["unavailable_reason"]), ("unknown", False, "missing"))

    def test_status_mapping(self):
        now = datetime.now(timezone.utc)
        cases = [
            ("ok", None, self.comparison(now), "ok"),
            ("degraded", None, self.comparison(now, verdict="degraded"), "fail"),
            ("error", "no_baseline", None, "fail"),
            ("skipped", "db_unavailable", self.comparison(now - timedelta(hours=5)), "unknown"),
            ("ok", None, self.comparison(now, params_changed=True), "warn"),
            ("ok", None, self.comparison(now - timedelta(hours=60)), "warn"),
        ]
        for outcome, reason, comparison, expected in cases:
            with self.subTest(outcome=outcome, expected=expected):
                self.write(finished_at=now.isoformat(), exit_code=0, outcome=outcome, reason=reason,
                           message="m", comparison=comparison)
                self.assertEqual(rr.section(now)["status"], expected)

    def test_staleness_follows_last_real_comparison(self):
        now = datetime.now(timezone.utc)
        self.write(finished_at=now.isoformat(), exit_code=2, outcome="skipped", reason="low_memory",
                   message="m", comparison=self.comparison(now - timedelta(hours=72)))
        s = rr.section(now)
        self.assertTrue(s["stale"])
        self.assertEqual(s["status"], "warn")

    def test_fields_are_sanitised(self):
        now = datetime.now(timezone.utc)
        self.write(finished_at=now.isoformat(), exit_code=True, outcome="weird", reason="nope",
                   message=123, comparison=self.comparison(now))
        s = rr.section(now)
        self.assertEqual((s["exit_code"], s["outcome"], s["reason"], s["message"]), (None, None, None, None))
        q = s["comparison"]["questions"][0]
        self.assertEqual([r["file_hash"] for r in q["lost"]], [H(1)])
        self.assertEqual((q["gained"], q["hidden_reports"], q["chunk_recall"]), ([], 0, None))


class KnobTests(unittest.TestCase):
    def test_typos_fall_back_instead_of_silencing_alerts(self):
        from app import config

        bad = {"RETRIEVAL_REGRESSION_K": "0", "RETRIEVAL_REGRESSION_MIN_MEAN_RECALL": "1.5",
               "RETRIEVAL_REGRESSION_MIN_QUESTION_RECALL": "nan", "RETRIEVAL_REGRESSION_MAX_DEGRADED_QUESTIONS": "x",
               "RETRIEVAL_REGRESSION_MIN_AVAILABLE_GIB": "-1"}
        with mock.patch.dict(os.environ, bad), self.assertLogs("app.config", "WARNING"):
            s = config._load()
        self.assertEqual((s.retrieval_regression_k, s.retrieval_regression_min_mean_recall,
                          s.retrieval_regression_min_question_recall, s.retrieval_regression_max_degraded_questions,
                          s.retrieval_regression_min_available_gib), (10, 0.8, 0.5, 2, 4.0))

    def test_valid_values(self):
        from app import config

        good = {"RETRIEVAL_REGRESSION_K": "8", "RETRIEVAL_REGRESSION_MIN_MEAN_RECALL": "0.9",
                "RETRIEVAL_REGRESSION_MAX_DEGRADED_QUESTIONS": "0"}
        with mock.patch.dict(os.environ, good):
            s = config._load()
        self.assertEqual((s.retrieval_regression_k, s.retrieval_regression_min_mean_recall,
                          s.retrieval_regression_max_degraded_questions), (8, 0.9, 0))


# ── 靜態約束 ───────────────────────────────────────────────────────────────


class StaticContractTests(unittest.TestCase):
    def test_entry_does_not_import_llm_layers(self):
        """零 LLM：入口與服務模組都不 import 呼叫層或間接層，所以不需要 scripts._llm_env（也不該列進
        NON_LLM_ENTRIES——沒被掃到的檔案列進去，那邊的測試會紅）。"""
        import ast

        from test_llm_env_loading import _imports_llm

        for rel in ("scripts/retrieval_regression.py", "app/services/retrieval_regression.py"):
            with self.subTest(rel=rel):
                tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
                self.assertFalse(_imports_llm(tree), f"{rel} import 了 LLM 呼叫層")

    def test_reuses_the_frozen_dataset(self):
        from test_eval_question_contract import DATASET

        self.assertEqual(rr.DATASET, DATASET)

    def test_conftest_redirects_baseline(self):
        self.assertTrue(os.environ["RETRIEVAL_REGRESSION_BASELINE"].startswith("/nonexistent/"))
        self.assertEqual(str(rr.baseline_path()), os.environ["RETRIEVAL_REGRESSION_BASELINE"])

    def test_unit_files(self):
        systemd = REPO_ROOT / "deploy" / "systemd"
        service = (systemd / "report-mark-retrieval-regression.service").read_text(encoding="utf-8")
        timer = (systemd / "report-mark-retrieval-regression.timer").read_text(encoding="utf-8")
        live = [ln.strip() for ln in service.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        for expected in (
            "OnFailure=report-mark-alert@%n.service", "Type=oneshot", "SuccessExitStatus=2",
            "Environment=EMBED_TORCH_THREADS=2", "MemoryMax=4G", "Nice=15", "IOSchedulingClass=idle",
            "Environment=HOME=/home/kashionz",
        ):
            self.assertIn(expected, live)
        exec_start = next(ln for ln in live if ln.startswith("ExecStart="))
        self.assertIn("scripts/retrieval_regression.py check", exec_start)
        self.assertFalse(any("report-mark-llm" in ln for ln in live))  # 零 LLM：不載 LLM 環境檔
        self.assertEqual(sum(ln.startswith("SuccessExitStatus=") for ln in live), 1)
        tlive = [ln.strip() for ln in timer.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
        cal = next(ln for ln in tlive if ln.startswith("OnCalendar="))
        hh, mm = cal.split()[-1].split(":")[:2]
        # 不撞 sync（00/3 整點起跑，實測約 20 分鐘）：不在 sync 小時的前 30 分鐘內。
        self.assertFalse(int(hh) % 3 == 0 and int(mm) < 30, cal)
        self.assertIn("Persistent=false", tlive)


if __name__ == "__main__":
    unittest.main()
