"""待複核佇列 `/api/review/queue`（HTTP 層）。SQL 對真 DB 的驗證在 test_review_db.py。"""

from __future__ import annotations

import os
import sys
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from web import deps  # noqa: E402
from web.routers import review  # noqa: E402
from web.server import app  # noqa: E402


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value

    def all(self):
        return self._value


class _Session:
    """依序回放結果：每個 kind 都是「count 一條＋當頁一條」。"""

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt, params=None):
        self.calls.append((" ".join(str(getattr(stmt, "text", stmt)).split()), dict(params or {})))
        return _Result(self._results.pop(0))


def _authed() -> TestClient:
    c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
    r = c.post("/login", data={"username": "tester", "password": "testpass"})
    assert r.status_code == 303, r.status_code
    return c


_TS = datetime(2026, 9, 20, 3, 0, tzinfo=timezone.utc)
_QA_ROW = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222",
           "台積電目標價多少", _TS, 0.208, None, "claude-haiku-4-5")
_RR_ROW = ("33333333-3333-4333-8333-333333333333", "h" * 64, "a.pdf", "標題", "kgi",
           date(2026, 9, 1), 0.41, {"garbled_ratio": 0.05}, [2, 7])


class ReviewQueueTests(unittest.TestCase):
    def setUp(self):
        self._orig = deps.SessionFactory

    def tearDown(self):
        deps.SessionFactory = self._orig

    def _use(self, results) -> _Session:
        session = _Session(results)
        deps.SessionFactory = lambda: session
        return session

    def test_requires_login(self):
        r = TestClient(app, follow_redirects=False).get("/api/review/queue?kind=faithfulness")
        self.assertEqual(r.status_code, 401)

    def test_kind_is_required_and_closed(self):
        self.assertEqual(_authed().get("/api/review/queue").status_code, 422)
        self.assertEqual(_authed().get("/api/review/queue?kind=everything").status_code, 422)

    def test_faithfulness_page_shape_and_threshold(self):
        session = self._use([3, [_QA_ROW]])
        r = _authed().get("/api/review/queue?kind=faithfulness&limit=1&offset=0")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(
            {k: body[k] for k in ("kind", "total", "limit", "offset", "has_more", "next_offset")},
            {"kind": "faithfulness", "total": 3, "limit": 1, "offset": 0, "has_more": True, "next_offset": 1},
        )
        self.assertEqual(body["min_score"], review._FAITHFULNESS_MIN)
        item = body["items"][0]
        self.assertEqual(item["qa_id"], _QA_ROW[0])
        self.assertEqual(item["conversation_id"], _QA_ROW[1])
        self.assertEqual(item["faithfulness_score"], 0.208)
        self.assertIsNone(item["report_id"])
        # 門檻與窗期要與監控頁那張卡同一套定義，兩邊的數字才對得起來。
        count_sql, count_params = session.calls[0]
        self.assertIn("< :fmin", count_sql)
        self.assertIn("active AND stopped IS NOT TRUE", count_sql)
        self.assertEqual(
            count_params, {"days": 30, "fmin": review._FAITHFULNESS_MIN, "judge_model": review._JUDGE_MODEL}
        )
        # 只列現行 judge 的低分：與監控卡同一段過濾（app/services/judge_schema.py）。
        self.assertIn("= :judge_model", count_sql)
        self.assertEqual(item["judge_model"], "claude-haiku-4-5")
        # count 與當頁必須是同一個 WHERE，否則 total 與實際翻得到的筆數會對不上。
        page_sql = session.calls[1][0]
        self.assertIn(count_sql.split("WHERE", 1)[1].strip(), page_sql)

    def test_feedback_filters_dislike_and_last_page_has_no_next(self):
        session = self._use([1, [_QA_ROW[:5] + ("dislike", None)]])
        body = _authed().get("/api/review/queue?kind=feedback&days=7").json()
        self.assertEqual((body["total"], body["has_more"], body["next_offset"]), (1, False, None))
        self.assertIsNone(body["min_score"])
        self.assertEqual(body["items"][0]["feedback"], "dislike")
        self.assertIn("feedback = 'dislike'", session.calls[0][0])
        self.assertEqual(session.calls[0][1], {"days": 7})
        # 倒讚列不一定有 evaluation；沒有就是 None，不是被補成舊預設的 judge。
        self.assertIsNone(body["items"][0]["judge_model"])
        self.assertNotIn(":judge_model", session.calls[0][0])

    def test_extraction_lists_needs_review_reports(self):
        session = self._use([1, [_RR_ROW]])
        body = _authed().get("/api/review/queue?kind=extraction").json()
        item = body["items"][0]
        self.assertEqual(item["file_hash"], "h" * 64)
        self.assertEqual(item["title"], "標題")
        self.assertEqual(item["quality_score"], 0.41)
        self.assertEqual(item["quality_flags"], {"garbled_ratio": 0.05})
        self.assertEqual(item["pages_failed"], [2, 7])
        # 0.41 低於 EXTRACTION_REVIEW_MIN、有失敗頁、亂碼率 0.05 過線；coverage 沒量到不算。
        self.assertEqual(item["review_reasons"], ["pages_failed", "low_score", "garbled"])
        self.assertEqual(item["report_date"], "2026-09-01")
        self.assertIsNone(item["qa_id"])
        self.assertIn("WHERE needs_review", session.calls[0][0])
        # needs_review 是研報的現況不是事件：沒有窗期。
        self.assertNotIn("days", session.calls[0][1])

    def test_reasons_explain_a_report_whose_score_is_not_low(self):
        """全庫實測被標到的研報分數多在 0.87–0.93：原因是 coverage 或亂碼率，不是分數。

        只回分數的話，畫面上是一排不低的數字，看不出為什麼要複核。
        """
        row = _RR_ROW[:6] + (0.93, {"layout_coverage": 0.12, "garbled_ratio": 0.001}, None)
        self._use([1, [row]])
        item = _authed().get("/api/review/queue?kind=extraction").json()["items"][0]
        self.assertEqual(item["review_reasons"], ["low_coverage"])

    def test_reasons_can_be_empty_when_thresholds_moved_since_ingest(self):
        """needs_review 是入庫當時寫下的布林；原因以現行門檻重算，兩者可能不一致。"""
        row = _RR_ROW[:6] + (0.95, {"layout_coverage": 0.8}, None)
        self._use([1, [row]])
        item = _authed().get("/api/review/queue?kind=extraction").json()["items"][0]
        self.assertEqual(item["review_reasons"], [])

    def test_paging_bounds_are_validated(self):
        for qs in ("limit=0", "limit=101", "offset=-1", "days=0", "days=366"):
            r = _authed().get(f"/api/review/queue?kind=feedback&{qs}")
            self.assertEqual(r.status_code, 422, qs)


if __name__ == "__main__":
    unittest.main()
