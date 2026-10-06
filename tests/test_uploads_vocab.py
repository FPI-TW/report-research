"""上傳狀態詞彙（app/services/uploads.py）與 revision 0008 的 SQL 逐字一致（不連 DB）。

狀態字串同時活在兩處：DB 的 `state` CHECK／partial unique index 條件，以及 Python 的常數。
兩邊漂移的後果都是靜默的——CHECK 少一個值，worker 寫入就在半路失敗；index 條件少一個值，
同一個 file_hash 就能有兩筆進行中的上傳。
"""

from __future__ import annotations

import re
import unittest

from app.services import schema_migrations as sm
from app.services import uploads


def _upgrade_sql() -> str:
    for rev in sm.revision_chain():
        if rev.revision == "0008":
            return rev.module.UPGRADE_SQL
    raise AssertionError("找不到 revision 0008")


def _quoted(fragment: str) -> list[str]:
    return re.findall(r"'([a-z_]+)'", fragment)


class UploadVocabTests(unittest.TestCase):
    def setUp(self):
        self.sql = _upgrade_sql()

    def test_states_match_check(self):
        m = re.search(r"state\s+text NOT NULL CHECK \(state IN \((.*?)\)\)", self.sql, re.S)
        self.assertIsNotNone(m, "0008 找不到 state 的 CHECK")
        self.assertEqual(tuple(_quoted(m.group(1))), uploads.STATES)

    def test_active_states_match_unique_index(self):
        m = re.search(r"idx_report_upload_active_hash.*?WHERE state IN \((.*?)\)", self.sql, re.S)
        self.assertIsNotNone(m, "0008 找不到 idx_report_upload_active_hash 的條件")
        self.assertEqual(tuple(_quoted(m.group(1))), uploads.ACTIVE_STATES)

    def test_failure_kind_has_no_check(self):
        """詞彙刻意只在 Python：新增失敗類別不必寫 revision。"""
        line = next(ln for ln in self.sql.splitlines() if ln.strip().startswith("failure_kind "))
        self.assertNotIn("CHECK", line)

    def test_subsets_are_declared_states(self):
        for group in (uploads.ACTIVE_STATES, uploads.TERMINAL_STATES, uploads.REJECTABLE_STATES):
            with self.subTest(group=group):
                self.assertLessEqual(set(group), set(uploads.STATES))
        self.assertLessEqual(set(uploads.RETRYABLE_FAILURE_KINDS), set(uploads.FAILURE_KINDS))
        # 進行中與終態互斥；draft 是進行中（發布前同 hash 不能再傳一份）。
        self.assertEqual(set(uploads.ACTIVE_STATES) & set(uploads.TERMINAL_STATES), set())
        self.assertTrue(uploads.is_active(uploads.STATE_DRAFT))
        self.assertFalse(uploads.is_active(uploads.STATE_PUBLISHED))

    def test_vocab_has_no_duplicates(self):
        self.assertEqual(len(uploads.STATES), len(set(uploads.STATES)))
        self.assertEqual(len(uploads.FAILURE_KINDS), len(set(uploads.FAILURE_KINDS)))

    def test_retryable(self):
        self.assertTrue(uploads.is_retryable_failure(uploads.FAILURE_TAG_FAILED))
        self.assertFalse(uploads.is_retryable_failure(uploads.FAILURE_NOT_RESEARCH))
        self.assertFalse(uploads.is_retryable_failure(None))


if __name__ == "__main__":
    unittest.main()
