# tests/test_eval_readpath.py
"""M8 查核結果的讀取路徑（E）：彙總純函式 + `/api/progress` 的 evaluation 區塊。

`qa_log.evaluation` / `report_doc.evaluation` 從 M8 上線起就零消費端，查核只寫不看。
本檔守住新加的兩條出口，重點在**別把「沒量到」讀成「品質差」**：

    degraded=true  judge fail-open，該筆實際未被查核，分數欄位皆 None
    低分           真的量到了，而且低

兩者混算會讓故障偽裝成品質問題（或反過來）。彙總刻意把 degraded 排除在分數統計外、
另外單獨計數。

HTTP 層測試不可省：`/api/progress` 才剛因為輔助函式插在裝飾器與 handler 之間而回 422，
而所有測試都直接呼叫 `monitor.progress()` 函式物件、繞過路由層，所以全綠也擋不住。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import eval_faithfulness as ef  # noqa: E402


def _ev(score=None, numeric=None, degraded=False, claims=()):
    return {
        "faithfulness_score": score,
        "numeric_support_rate": numeric,
        "citation_coverage": None,
        "degraded": degraded,
        "claims": list(claims),
    }


def _row(i, ev, created="2026-07-28 01:00:00", q="問題"):
    return {"id": i, "created_at": created, "question": q, "evaluation": ev}


class SummarizeTests(unittest.TestCase):
    def test_degraded_excluded_from_score_stats_but_counted(self):
        """fail-open 那筆不該被當成 0 分拉低平均，也不該被當成有分數。"""
        rows = [
            _row("a", _ev(score=0.9)),
            _row("b", _ev(degraded=True)),   # 沒量到
            _row("c", _ev(score=0.5)),
        ]
        s = ef.summarize(rows, 0.9)
        self.assertEqual(s["checked"], 3)
        self.assertEqual(s["degraded"], 1)
        self.assertEqual(s["scored"], 2)
        self.assertAlmostEqual(s["avg_score"], 0.7)

    def test_unchecked_rows_counted_in_total_only(self):
        s = ef.summarize([_row("a", None), _row("b", _ev(score=1.0))], 0.9)
        self.assertEqual((s["total"], s["checked"], s["scored"]), (2, 1, 1))

    def test_below_min_uses_strict_less_than(self):
        """恰好等於門檻不算待複核——門檻語意是「低於此值才修正」。"""
        rows = [_row("a", _ev(score=0.9)), _row("b", _ev(score=0.8999))]
        self.assertEqual(ef.summarize(rows, 0.9)["below_min"], 1)

    def test_malformed_score_ignored_not_zero(self):
        """畸形值當「沒有分數」，不是當 0。

        當 0 會憑空製造一筆滿分不合格的紀錄，比缺一筆更糟。
        """
        rows = [_row("a", _ev(score="0.5")), _row("b", _ev(score=True)), _row("c", _ev(score=0.6))]
        s = ef.summarize(rows, 0.9)
        self.assertEqual(s["scored"], 1)
        self.assertAlmostEqual(s["avg_score"], 0.6)

    def test_empty_input_is_all_none_not_crash(self):
        s = ef.summarize([], 0.9)
        self.assertEqual(s["total"], 0)
        self.assertIsNone(s["avg_score"])
        self.assertIsNone(s["latest_checked"])


class WorstTests(unittest.TestCase):
    def test_sorted_ascending_and_degraded_excluded(self):
        rows = [
            _row("a", _ev(score=0.8)),
            _row("b", _ev(degraded=True)),
            _row("c", _ev(score=0.2)),
        ]
        out = ef.worst(rows, 10)
        self.assertEqual([w["id"] for w in out], ["c", "a"])

    def test_counts_unsupported_claims(self):
        claims = [
            {"text": "x", "is_numeric": True, "verdict": "supported"},
            {"text": "y", "is_numeric": True, "verdict": "unsupported"},
            {"text": "z", "is_numeric": False, "verdict": "no_source"},
        ]
        out = ef.worst([_row("a", _ev(score=0.33, claims=claims))], 10)
        self.assertEqual((out[0]["claims"], out[0]["unsupported"]), (3, 2))

    def test_limit_respected(self):
        rows = [_row(str(i), _ev(score=i / 10)) for i in range(9)]
        self.assertEqual(len(ef.worst(rows, 3)), 3)


class ProgressHttpTests(unittest.TestCase):
    """經真實 HTTP 請求驗 `/api/progress` 帶出 evaluation 區塊。"""

    _SNAPSHOT = {
        "total_reports": 1, "total_chunks": 2, "markets": [],
        "instrument_types": [], "report_types": [],
        "summary_done": 1, "summary_total": 1,
        "takeaway_done_30d": 1, "takeaway_total_30d": 2, "takeaway_latest": "2026-07-20",
        "signal_done_30d": 0, "signal_total_30d": 2, "signal_latest": None,
        "evaluation": {
            "qa": {"total": 40, "checked": 3, "degraded": 1, "below_min": 1,
                   "avg_score": 0.5634, "latest": "2026-07-28"},
            "report": None,
            "min_score": 0.9,
        },
    }

    def _get(self):
        from fastapi.testclient import TestClient

        from web.routers import monitor
        from web.server import app

        c = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        assert c.post("/login", data={"username": "tester", "password": "testpass"}).status_code == 303

        async def fake_snapshot():
            return dict(self._SNAPSHOT)

        with patch.object(monitor, "_db_stats_snapshot", fake_snapshot), \
                patch.object(monitor, "_gather_runtime", lambda: {}):
            return c.get("/api/progress")

    def test_endpoint_returns_200_with_evaluation(self):
        r = self._get()
        self.assertEqual(r.status_code, 200, r.text)
        ev = r.json()["evaluation"]
        self.assertEqual(ev["qa"]["degraded"], 1)
        self.assertEqual(ev["min_score"], 0.9)
        self.assertIsNone(ev["report"])

    def test_takeaway_and_signal_still_present(self):
        """同一個回應裡的既有區塊不得因新增而被擠掉。"""
        body = self._get().json()
        self.assertEqual(body["takeaway"]["latest"], "2026-07-20")
        self.assertIsNone(body["signal"]["latest"])


class SnapshotShapeTests(unittest.TestCase):
    """快照的 evaluation 形狀契約——前端 zod schema 依此宣告，漂了就整塊被剝除。"""

    def test_router_builds_expected_keys(self):
        src = (REPO_ROOT / "web" / "routers" / "monitor.py").read_text(encoding="utf-8")
        for key in ("degraded", "below_min", "avg_score", "min_score"):
            self.assertIn(f'"{key}"', src, f"snapshot 缺 {key}")

    def test_threshold_shared_with_report_writer(self):
        """監控頁的「待複核」門檻必須就是實際觸發修正的門檻，不能自成一套。"""
        from app.config import get_settings
        from web.routers import monitor

        self.assertEqual(
            monitor._FAITHFULNESS_MIN, get_settings().report_faithfulness_min
        )


if __name__ == "__main__":
    unittest.main()
