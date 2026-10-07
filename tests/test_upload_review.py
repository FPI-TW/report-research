"""研報上傳審核的純函式與 SQL 片段（不連 DB）：app/services/upload_review.py。

DB 行為（條件式 UPDATE、並發、稽核同交易、檢索可見性）在 tests/test_upload_review_db.py。
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app import config
from app.services import upload_review as ur
from app.services import uploads


class RestoreStateTests(unittest.TestCase):
    """撤銷退回時回到哪個狀態：沒有欄位記退回前的狀態，依事實推導。"""

    def test_full_table(self):
        cases = [
            # (corpus_draft, failure_kind, scanned) → state
            ((True, None, True), "draft"),
            ((True, "tag_failed", True), "draft"),  # 語料有草稿最優先
            ((True, None, False), "draft"),
            ((False, "tag_failed", True), "failed"),
            ((False, "not_research", True), "failed"),
            ((False, "extract_timeout", False), "failed"),
            ((False, "llm_breaker", True), "clean"),  # 延後標記不是失敗
            ((False, "llm_breaker", False), "quarantined"),
            ((False, None, True), "clean"),
            ((False, "", True), "clean"),
            ((False, None, False), "quarantined"),
        ]
        for (corpus_draft, kind, scanned), expected in cases:
            with self.subTest(corpus_draft=corpus_draft, kind=kind, scanned=scanned):
                got = ur.restore_state_after_unreject(corpus_draft=corpus_draft, failure_kind=kind, scanned=scanned)
                self.assertEqual(got, expected)

    def test_results_are_states_a_worker_can_continue_from(self):
        allowed = {uploads.STATE_DRAFT, uploads.STATE_FAILED, uploads.STATE_CLEAN, uploads.STATE_QUARANTINED}
        for corpus_draft in (True, False):
            for kind in (None, *uploads.FAILURE_KINDS):
                for scanned in (True, False):
                    got = ur.restore_state_after_unreject(corpus_draft=corpus_draft, failure_kind=kind,
                                                          scanned=scanned)
                    self.assertIn(got, allowed)
                    # 推導出的每一個狀態都必須是當初可以被退回的狀態（不會撤銷到 worker 握著的狀態）。
                    self.assertIn(got, uploads.REJECTABLE_STATES)

    def test_deferral_kinds_are_known_failure_kinds(self):
        self.assertTrue(set(ur.DEFERRAL_FAILURE_KINDS) <= set(uploads.FAILURE_KINDS))
        self.assertFalse(set(ur.DEFERRAL_FAILURE_KINDS) & set(uploads.RETRYABLE_FAILURE_KINDS))


class ReasonTests(unittest.TestCase):
    def test_trims_and_limits(self):
        self.assertEqual(ur.normalize_reason("  重複上傳  "), "重複上傳")
        self.assertEqual(ur.normalize_reason("字" * 500), "字" * 500)
        for bad in (None, "", "   ", "\n\t", "字" * 501):
            with self.subTest(bad=bad):
                with self.assertRaises(ur.InvalidReasonError):
                    ur.normalize_reason(bad)

    def test_limit_matches_db_check(self):
        from pathlib import Path

        versions = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
        sql = (versions / "0008_report_upload.py").read_text()
        self.assertIn(f"char_length(decision_reason) <= {ur.REASON_MAX_CHARS}", sql)


class StateSetTests(unittest.TestCase):
    def test_gates_are_disjoint_and_cover_the_reject_table(self):
        self.assertEqual(set(ur.PREVIEWABLE_STATES), {"draft", "published"})
        self.assertEqual(set(ur.BUSY_STATES), {"scanning", "processing"})
        self.assertFalse(set(ur.BUSY_STATES) & set(uploads.REJECTABLE_STATES))
        self.assertNotIn(uploads.STATE_PUBLISHED, uploads.REJECTABLE_STATES)
        self.assertTrue(set(ur.PREVIEWABLE_STATES) <= set(uploads.STATES))


class SqlFragmentTests(unittest.TestCase):
    def test_rejects_unsafe_expressions(self):
        for bad in ("u.file_hash; DROP", "1=1", "u.file_hash OR true", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    ur.corpus_never_published_sql(bad)
                with self.assertRaises(ValueError):
                    ur.corpus_purgeable_sql(bad)

    def test_purge_guard_keeps_active_and_published_uploads(self):
        sql = ur.corpus_purgeable_sql("u.file_hash")
        self.assertIn(ur.corpus_never_published_sql("u.file_hash"), sql)
        for state in (*uploads.ACTIVE_STATES, uploads.STATE_PUBLISHED):
            self.assertIn(f"'{state}'", sql)
        self.assertNotIn("'rejected'", sql)

    def test_never_published_checks_both_markers(self):
        sql = ur.corpus_never_published_sql("x.h")
        self.assertIn("published_at IS NULL", sql)
        self.assertIn("published_at IS NOT NULL", sql)
        self.assertIn("research.research_report", sql)


class GraceKnobTests(unittest.TestCase):
    def test_default_and_bad_values(self):
        with mock.patch.dict(os.environ, {"UPLOAD_REJECT_GRACE_HOURS": ""}):
            self.assertEqual(config._load().upload_reject_grace_hours, 24)
        with mock.patch.dict(os.environ, {"UPLOAD_REJECT_GRACE_HOURS": "48"}):
            self.assertEqual(config._load().upload_reject_grace_hours, 48)
        for bad in ("0", "-1", "x"):
            with self.subTest(bad=bad), mock.patch.dict(os.environ, {"UPLOAD_REJECT_GRACE_HOURS": bad}):
                self.assertEqual(config._load().upload_reject_grace_hours, 24)


class TakeawayStateTests(unittest.TestCase):
    """摘錄的 pending／ready／none 與錨點收回（驗章不過、落在截斷範圍外）。"""

    def _row(self, ordinal=1, status="valid", sha="s", qs=0, qe=5):
        return (ordinal, "論點", "引文", qs, qe, "exact", status, sha)

    def test_states(self):
        self.assertEqual(ur._takeaways([], "s", 100)[0], "pending")
        self.assertEqual(ur._takeaways([self._row(status="pending")], "s", 100)[0], "pending")
        self.assertEqual(ur._takeaways([self._row(status="rejected")], "s", 100), ("none", []))
        state, items = ur._takeaways([self._row(), self._row(2, status="rejected")], "s", 100)
        self.assertEqual((state, len(items)), ("ready", 1))
        self.assertEqual((items[0].quote_start, items[0].quote_end, items[0].anchor_method), (0, 5, "exact"))

    def test_anchor_dropped_when_stale_or_clipped(self):
        for sha, visible in (("other", 100), (None, 100), ("s", 4)):
            with self.subTest(sha=sha, visible=visible):
                _, items = ur._takeaways([self._row()], sha, visible)
                self.assertEqual((items[0].quote_start, items[0].quote_end, items[0].anchor_method),
                                 (None, None, None))
                self.assertEqual(items[0].quote, "引文")  # 條目與引文照常顯示


if __name__ == "__main__":
    unittest.main()
