# tests/test_store_sql.py
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.store import _lexical_sql  # noqa: E402


class LexicalSqlTests(unittest.TestCase):
    def test_default_has_no_distinct_on(self):
        sql = _lexical_sql(2, ["r.market = :market"], per_report=False)
        self.assertNotIn("DISTINCT ON", sql)
        self.assertIn("c.content_norm LIKE :t0", sql)
        self.assertIn("c.content_norm LIKE :t1", sql)
        self.assertIn("r.market = :market", sql)
        self.assertLess(sql.index("c.content_norm LIKE :t0"), sql.index("r.market = :market"))
        self.assertIn("LIMIT :cap", sql)
        self.assertIn("LIMIT :limit", sql)

    def test_per_report_uses_distinct_on(self):
        sql = _lexical_sql(1, [], per_report=True)
        self.assertIn("DISTINCT ON (c.report_id)", sql)
        # DISTINCT ON 需以 report_id 起首排序，再依向量距離取最近 chunk
        self.assertIn("ORDER BY c.report_id", sql)

    def test_no_extra_conds_still_has_pattern_cond(self):
        sql = _lexical_sql(1, [], per_report=False)
        self.assertIn("c.content_norm LIKE :t0", sql)


if __name__ == "__main__":
    unittest.main()
