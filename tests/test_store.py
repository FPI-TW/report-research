import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import store  # noqa: E402


class BulkInsertTests(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_report_batches_chunk_inserts(self):
        calls = []

        class FakeSession:
            async def execute(self, stmt, params=None):
                calls.append((str(stmt), params))

            async def commit(self):
                calls.append(("COMMIT", None))

        report = store.ReportRow(
            file_hash="hash-1",
            file_name="report.pdf",
            file_path="/tmp/report.pdf",
            market="TW",
            is_research=True,
            confidence=0.9,
        )

        report_id = await store.upsert_report(
            FakeSession(),
            report,
            ["第一段", "第二段", "第三段"],
            [[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]],
        )

        self.assertTrue(report_id)
        exec_calls = [c for c in calls if c[0] != "COMMIT"]
        self.assertEqual(len(exec_calls), 3)
        chunk_stmt, chunk_params = exec_calls[-1]
        self.assertIn("INSERT INTO research.report_chunk", chunk_stmt)
        self.assertIsInstance(chunk_params, list)
        self.assertEqual(len(chunk_params), 3)


if __name__ == "__main__":
    unittest.main()
