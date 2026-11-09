"""導入驗收的題集流程：草擬（假 LLM）→ 研究員審 → 凍結 → 指紋核對（`eval/question_draft.py`、
`eval/profile_eval.py`、`scripts/draft_eval_questions.py`）。

全程不連 DB、不呼叫 LLM：草擬以假的 `call` 取代 `run_claude`；profile 目錄一律建在 tempfile 裡，
不碰 repo 的 `profiles/`。
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import io
import json
import random
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from eval import profile_eval as pe
from eval import question_draft as qd
from scripts import draft_eval_questions as dq
from scripts._claude_cli import CliResult

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=timezone.utc)
SUBJECTS = ["台積電", "聯發科", "鴻海", "廣達", "日月光", "大立光", "台達電", "緯創", "華碩", "南亞科"]


def _hash(tag: str) -> str:
    return hashlib.sha256(tag.encode()).hexdigest()


def _item(n: int, category: str, *, status: str = qd.STATUS_APPROVED) -> dict:
    subject = SUBJECTS[n % len(SUBJECTS)]
    question = f"第{n}題：研報怎麼看{subject}在{category}面向的展望？"
    support = [{"file_hash": _hash(f"r{n}")}]
    if category == pe.CATEGORY_CROSS:
        support.append({"file_hash": _hash(f"r{n}-b")})
    item = qd.new_item(f"d{n:03d}", category=category, question=question, filters={},
                       reference_points=["要點一", "要點二"], supporting_reports=support)
    item["review"]["status"] = status
    return item


def _draft(per_category: int = 8, **overrides) -> dict:
    items, n = [], 0
    for cat in pe.CATEGORIES:
        for _ in range(per_category):
            n += 1
            items.append(_item(n, cat))
    doc = qd.new_draft(profile="demo", generated_at=NOW.isoformat(), model="gpt-6-luna",
                       sample={"seed": 0}, items=items)
    doc.update(overrides)
    return doc


def _freeze(doc: dict) -> tuple[bytes, dict]:
    return qd.freeze(doc, draft_sha256=_hash("draft"), profile="demo", frozen_at=NOW)


class _ProfileDir(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.profile = Path(self._tmp.name) / "demo"
        (self.profile / "eval").mkdir(parents=True)
        (self.profile / "profile.toml").write_text('[institution]\nname = "示範"\n', encoding="utf-8")

    def write_frozen(self, raw: bytes, manifest: dict) -> None:
        (self.profile / "eval" / pe.QUESTIONS_FILE).write_bytes(raw)
        (self.profile / "eval" / pe.MANIFEST_FILE).write_bytes(qd.dumps(manifest))


class FreezeTests(_ProfileDir):
    def test_freeze_produces_contract_valid_set_and_fingerprint(self):
        raw, manifest = _freeze(_draft())
        doc = json.loads(raw)
        self.assertEqual(doc["count"], 32)
        self.assertEqual([q["id"] for q in doc["questions"]], [f"q{i:03d}" for i in range(1, 33)])
        self.assertTrue(all(set(q) == {"id", "question", "filters", "scope"} for q in doc["questions"]))
        self.assertEqual(manifest["questions_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(manifest["coverage"], {c: 8 for c in pe.CATEGORIES})
        self.assertEqual(manifest["draft"]["sha256"], _hash("draft"))
        self.assertEqual(len(manifest["items"]), 32)
        self.assertFalse(any(i["edited"] for i in manifest["items"]))
        cross = [i for i in manifest["items"] if i["category"] == pe.CATEGORY_CROSS]
        self.assertTrue(all(len(i["supporting_reports"]) == 2 for i in cross))

    def test_frozen_set_round_trips_through_the_reader(self):
        raw, manifest = _freeze(_draft())
        self.write_frozen(raw, manifest)
        ds = pe.load_profile_dataset(self.profile)
        self.assertEqual(len(ds.questions), 32)
        self.assertEqual(ds.sha256, manifest["questions_sha256"])
        self.assertEqual(ds.path, self.profile / "eval" / pe.QUESTIONS_FILE)

    def test_hand_edited_frozen_set_is_refused(self):
        raw, manifest = _freeze(_draft())
        doc = json.loads(raw)
        doc["questions"][0]["question"] = "被手改過的題目內容是什麼？"
        self.write_frozen(qd.dumps(doc), manifest)
        with self.assertRaisesRegex(pe.EvalProfileError, "凍結指紋不符"):
            pe.load_profile_dataset(self.profile)

    def test_missing_manifest_is_refused(self):
        raw, _manifest = _freeze(_draft())
        (self.profile / "eval" / pe.QUESTIONS_FILE).write_bytes(raw)
        with self.assertRaisesRegex(pe.EvalProfileError, "沒有凍結清單"):
            pe.load_profile_dataset(self.profile)

    def test_manifest_tampering_is_refused(self):
        raw, manifest = _freeze(_draft())
        for mutate, pattern in (
            (lambda m: m["items"][0].update(category="stock" if m["items"][0]["category"] != "stock" else "macro"),
             "coverage"),
            (lambda m: m["items"][0].update(supporting_reports=[]), "支撐研報"),
            (lambda m: m["items"][0].update(id="q999"), "題號"),
            (lambda m: m.update(count=31), "count"),
        ):
            with self.subTest(pattern=pattern):
                m = copy.deepcopy(manifest)
                mutate(m)
                self.write_frozen(raw, m)
                with self.assertRaisesRegex(pe.EvalProfileError, pattern):
                    pe.load_profile_dataset(self.profile)

    def test_refuses_with_too_few_questions_and_says_how_many_are_missing(self):
        doc = _draft(per_category=7)  # 28 題、每類 7
        with self.assertRaises(qd.FreezeRefused) as cm:
            _freeze(doc)
        msg = "\n".join(cm.exception.problems)
        self.assertIn("核准題數 28", msg)
        self.assertIn("還差 2 題", msg)

    def test_refuses_when_a_category_is_under_covered(self):
        doc = _draft()
        for item in doc["items"]:
            if item["category"] == pe.CATEGORY_MACRO:
                item["review"]["status"] = qd.STATUS_REJECTED
        for n in range(33, 41):  # 補到 32 題核准，但總經 0 題
            doc["items"].append(_item(n, pe.CATEGORY_STOCK))
        with self.assertRaises(qd.FreezeRefused) as cm:
            _freeze(doc)
        msg = "\n".join(cm.exception.problems)
        self.assertIn("總經（macro）只有 0 題", msg)
        self.assertNotIn("核准題數", msg)

    def test_rejected_items_are_not_frozen(self):
        doc = _draft(per_category=9)
        rejected = doc["items"][0]
        rejected["review"]["status"] = qd.STATUS_REJECTED
        raw, manifest = _freeze(doc)
        self.assertEqual(manifest["count"], 35)
        self.assertNotIn(rejected["question"], raw.decode())

    def test_pending_items_block_freeze(self):
        doc = _draft()
        doc["items"][3]["review"]["status"] = qd.STATUS_PENDING
        with self.assertRaises(qd.FreezeRefused) as cm:
            _freeze(doc)
        self.assertIn("1 題未審", cm.exception.problems[0])
        self.assertIn("d004", cm.exception.problems[0])

    def test_modified_items_must_differ_and_approved_items_must_not(self):
        doc = _draft()
        doc["items"][0]["question"] = "研究員改寫後的題目：台積電先進封裝的進度？"
        with self.assertRaisesRegex(qd.FreezeRefused, "請標 modified"):
            _freeze(doc)
        doc["items"][0]["review"]["status"] = qd.STATUS_MODIFIED
        _raw, manifest = _freeze(doc)
        self.assertTrue(manifest["items"][0]["edited"])
        self.assertEqual(sum(i["edited"] for i in manifest["items"]), 1)
        doc = _draft()
        doc["items"][1]["review"]["status"] = qd.STATUS_MODIFIED
        with self.assertRaisesRegex(qd.FreezeRefused, "請標 approved"):
            _freeze(doc)

    def test_contract_violations_name_the_draft_item(self):
        doc = _draft()
        doc["items"][5]["question"] = "短"
        doc["items"][5]["review"]["status"] = qd.STATUS_MODIFIED
        doc["items"][6]["question"] = doc["items"][7]["question"] + "？"
        doc["items"][6]["review"]["status"] = qd.STATUS_MODIFIED
        doc["items"][9]["filters"] = {"market": "BAD"}
        doc["items"][9]["review"]["status"] = qd.STATUS_MODIFIED
        with self.assertRaises(qd.FreezeRefused) as cm:
            _freeze(doc)
        msg = "\n".join(cm.exception.problems)
        self.assertIn("d006：題目不合契約", msg)
        self.assertIn("d007", msg)
        self.assertIn("重複", msg)
        self.assertIn("d010：題目不合契約", msg)

    def test_cross_report_needs_two_distinct_reports(self):
        doc = _draft()
        cross = next(i for i in doc["items"] if i["category"] == pe.CATEGORY_CROSS)
        cross["supporting_reports"] = [cross["supporting_reports"][0], dict(cross["supporting_reports"][0])]
        with self.assertRaisesRegex(qd.FreezeRefused, "至少 2 份不同的支撐研報"):
            _freeze(doc)

    def test_malformed_draft_is_refused(self):
        for mutate in (
            lambda d: d.update(format="other"),
            lambda d: d.update(items=[]),
            lambda d: d["items"][0].update(category="sector"),
            lambda d: d["items"][0]["review"].update(status="ok"),
            lambda d: d["items"][0].pop("original"),
            lambda d: d["items"][1].update(draft_id="d001"),
            lambda d: d["items"][0].update(supporting_reports=[{"file_hash": "x"}]),
        ):
            with self.subTest(mutate=mutate):
                doc = _draft()
                mutate(doc)
                with self.assertRaises(qd.FreezeRefused):
                    _freeze(doc)

    def test_freeze_requires_aware_timestamp(self):
        with self.assertRaises(ValueError):
            qd.freeze(_draft(), draft_sha256=_hash("d"), profile="demo", frozen_at=datetime(2026, 11, 10))


class FreezeCliTests(_ProfileDir):
    def _cli(self, draft: dict, *, replace: bool = False, profile: Path | None = None) -> tuple[int, str]:
        draft_path = Path(self._tmp.name) / "draft.json"
        draft_path.write_bytes(qd.dumps(draft))
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = dq.cmd_freeze(str(profile or self.profile), draft_path, replace=replace, now=NOW)
        return rc, out.getvalue() + err.getvalue()

    def test_writes_set_and_manifest_and_refuses_to_overwrite(self):
        rc, out = self._cli(_draft())
        self.assertEqual(rc, 0, out)
        self.assertIn("已凍結 32 題", out)
        first = (self.profile / "eval" / pe.QUESTIONS_FILE).read_bytes()
        rc, out = self._cli(_draft(per_category=9))
        self.assertEqual(rc, dq.EXIT_INPUT)
        self.assertIn("--replace", out)
        self.assertEqual((self.profile / "eval" / pe.QUESTIONS_FILE).read_bytes(), first)
        rc, out = self._cli(_draft(per_category=9), replace=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(len(pe.load_profile_dataset(self.profile).questions), 36)

    def test_refusal_writes_nothing(self):
        rc, out = self._cli(_draft(per_category=3))
        self.assertEqual(rc, dq.EXIT_INPUT)
        self.assertIn("不能凍結", out)
        self.assertFalse((self.profile / "eval" / pe.QUESTIONS_FILE).exists())
        self.assertFalse((self.profile / "eval" / pe.MANIFEST_FILE).exists())

    def test_legacy_profile_is_not_frozen_by_the_tool(self):
        (self.profile / "eval" / pe.CONFIG_FILE).write_text(
            '[questions]\nlegacy_repo_path = "eval/ragas_questions.json"\n', encoding="utf-8")
        rc, out = self._cli(_draft())
        self.assertEqual(rc, dq.EXIT_INPUT)
        self.assertIn("沿用 repo 內的凍結題集", out)

    def test_status_reports_progress_and_gaps(self):
        doc = _draft(per_category=4)
        doc["items"][0]["review"]["status"] = qd.STATUS_PENDING
        path = Path(self._tmp.name) / "draft.json"
        path.write_bytes(qd.dumps(doc))
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = dq.cmd_status(path)
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("pending 1", text)
        self.assertIn("還不能凍結", text)
        self.assertIn("每一類至少要 5 題", text)


def _rows(n: int) -> list[dict]:
    markets = ["TW", "US", "MACRO"]
    types = ["個股", "產業", "總經"]
    out = []
    for i in range(n):
        out.append({
            "id": f"00000000-0000-0000-0000-{i:012d}", "file_hash": _hash(f"row{i}"), "label": f"研報 {i}",
            "market": markets[i % 3], "report_type": types[(i // 3) % 3],
            "report_date": date(2026, 1 + (i % 12), 1), "source": f"券商{(i // 12) % 4}",
            "company_name": None, "stock_code": None, "summary": f"摘要內容 {i}",
        })
    return out


class SamplingTests(unittest.TestCase):
    def test_stratified_sample_is_deterministic_and_spreads_over_strata(self):
        rows = _rows(120)
        a = dq.stratified_sample(rows, 30, random.Random(7))
        b = dq.stratified_sample(list(reversed(rows)), 30, random.Random(7))
        self.assertEqual([r["file_hash"] for r in a], [r["file_hash"] for r in b])
        self.assertEqual(len({r["file_hash"] for r in a}), 30)
        all_strata = {dq.stratum(r) for r in rows}
        self.assertEqual(len({dq.stratum(r) for r in a}), min(30, len(all_strata)))
        self.assertNotEqual([r["file_hash"] for r in dq.stratified_sample(rows, 30, random.Random(8))],
                            [r["file_hash"] for r in a])

    def test_pairs_share_market_and_quarter_and_differ_in_source(self):
        pairs = dq.pick_pairs(_rows(120), 10, random.Random(1))
        self.assertEqual(len(pairs), 10)
        for a, b in pairs:
            self.assertEqual(a["market"], b["market"])
            self.assertEqual(dq._quarter(a["report_date"]), dq._quarter(b["report_date"]))
            self.assertNotEqual(a["source"], b["source"])
        used = [r["file_hash"] for p in pairs for r in p]
        self.assertEqual(len(used), len(set(used)))

    def test_filters_are_decided_in_python(self):
        row = {"market": "TW"}
        self.assertEqual(dq.default_filters(pe.CATEGORY_STOCK, [row]), {"market": "TW"})
        self.assertEqual(dq.default_filters(pe.CATEGORY_INDUSTRY, [row]), {})
        self.assertEqual(dq.default_filters(pe.CATEGORY_STOCK, [{"market": "unknown"}]), {})


class ParseTests(unittest.TestCase):
    def test_accepts_fenced_json_and_rejects_bad_shapes(self):
        good = '```json\n{"category": "stock", "question": "台積電先進封裝的擴產進度如何？", ' \
               '"reference_points": ["CoWoS 產能倍增", "2027 年續擴"]}\n```'
        parsed = dq.parse_candidate(good, cross=False)
        self.assertEqual(parsed["category"], "stock")
        self.assertEqual(len(parsed["reference_points"]), 2)
        for bad in (None, "", "不是 JSON", '{"category": "cross_report", "question": "台積電的看法如何？", '
                    '"reference_points": ["a"]}', '{"category": "stock", "question": "短", "reference_points": ["a"]}',
                    '{"category": "stock", "question": "台積電的看法如何？", "reference_points": []}'):
            with self.subTest(bad=bad):
                self.assertIsNone(dq.parse_candidate(bad, cross=False))

    def test_cross_category_is_fixed_by_python(self):
        raw = '{"category": "macro", "question": "兩份研報對台股走勢的看法有何不同？", "reference_points": ["a"]}'
        self.assertEqual(dq.parse_candidate(raw, cross=True)["category"], pe.CATEGORY_CROSS)


class DraftWithFakeLlmTests(unittest.TestCase):
    def test_build_draft_end_to_end_then_review_and_freeze(self):
        calls: list[dict] = []
        categories = iter(["stock", "industry", "macro"] * 100)

        def fake_call(prompt: str, *, file_hash: str, report_id: str) -> CliResult:
            calls.append({"file_hash": file_hash, "report_id": report_id, "cross": "跨篇比較" in prompt})
            n = len(calls)
            if n == 3:
                return CliResult("不是 JSON", None)  # 第一次不採信、重試一次
            if "跨篇比較" in prompt:
                body = {"question": f"兩份研報對第{n}個主題的看法有何異同？", "reference_points": ["甲", "乙"]}
            else:
                body = {"category": next(categories), "question": f"研報怎麼看第{n}個主題的發展？",
                        "reference_points": ["要點"]}
            return CliResult(json.dumps(body, ensure_ascii=False), None)

        rows = _rows(120)
        draft = asyncio.run(dq.build_draft(rows, target=48, seed=3, workers=2, profile="demo", now=NOW,
                                           call=fake_call))
        items = qd.validate_draft(draft)
        self.assertEqual(len(calls), 49)  # 48 份工作＋1 次重試
        self.assertEqual(len(items), 48)
        self.assertEqual(draft["sample"]["cross_pairs"], 12)
        self.assertEqual(sum(i["category"] == pe.CATEGORY_CROSS for i in items), 12)
        self.assertTrue(all(i["review"]["status"] == qd.STATUS_PENDING for i in items))
        self.assertTrue(all(i["original"]["question"] == i["question"] for i in items))
        known = {r["file_hash"] for r in rows}
        self.assertTrue(all(s["file_hash"] in known for i in items for s in i["supporting_reports"]))
        # 未審就凍結：拒絕
        with self.assertRaisesRegex(qd.FreezeRefused, "48 題未審"):
            qd.freeze(draft, draft_sha256=_hash("x"), profile="demo", frozen_at=NOW)
        # 研究員全部核准 → 凍結
        for i in items:
            i["review"]["status"] = qd.STATUS_APPROVED
        raw, manifest = qd.freeze(draft, draft_sha256=_hash("x"), profile="demo", frozen_at=NOW)
        self.assertEqual(manifest["count"], 48)
        self.assertEqual(json.loads(raw)["count"], 48)

    def test_non_retryable_failure_is_not_retried(self):
        calls = []

        def fake_call(prompt, *, file_hash, report_id):
            calls.append(1)
            return CliResult(None, "API[content_filter] 觸發供應商內容審查")

        result = asyncio.run(dq.draft_one(asyncio.Semaphore(1), _rows(1), call=fake_call))
        self.assertIsNone(result)
        self.assertEqual(len(calls), 1)


class DraftCliGuardTests(unittest.TestCase):
    def test_draft_refuses_a_profile_other_than_the_active_one(self):
        with tempfile.TemporaryDirectory() as td:
            other = Path(td) / "other"
            other.mkdir()
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = dq.main(["draft", "--profile", str(other), "--out", str(Path(td) / "d.json")])
        self.assertEqual(rc, dq.EXIT_INPUT)
        self.assertIn("部署環境", err.getvalue())

    def test_sample_sql_filters_visibility_and_doc_type(self):
        sql = dq.sample_sql()
        self.assertIn("report_visibility", sql)
        self.assertIn("r.doc_type", sql)
        self.assertIn("r.summary IS NOT NULL", sql)


if __name__ == "__main__":
    unittest.main()
