import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import followups as fu  # noqa: E402


class GenerateFollowupsTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_json_array(self):
        async def fake_stream(*a, **k):
            yield '["台積電資本支出？", "先進封裝進度？", "競爭對手比較？"]'

        orig = fu.stream_completion
        fu.stream_completion = fake_stream
        try:
            out = await fu.generate_followups("台積電展望", "答案內容")
        finally:
            fu.stream_completion = orig

        self.assertEqual(len(out), 3)
        self.assertIn("台積電資本支出？", out)

    async def test_caps_at_three(self):
        async def fake_stream(*a, **k):
            yield '["a", "b", "c", "d", "e"]'

        orig = fu.stream_completion
        fu.stream_completion = fake_stream
        try:
            out = await fu.generate_followups("q", "a")
        finally:
            fu.stream_completion = orig
        self.assertEqual(len(out), 3)

    async def test_fail_open_on_garbage(self):
        async def fake_stream(*a, **k):
            yield "這不是 JSON"

        orig = fu.stream_completion
        fu.stream_completion = fake_stream
        try:
            out = await fu.generate_followups("q", "a")
        finally:
            fu.stream_completion = orig
        self.assertEqual(out, [])

    async def test_fail_open_on_exception(self):
        async def boom(*a, **k):
            raise RuntimeError("529")
            yield  # pragma: no cover

        orig = fu.stream_completion
        fu.stream_completion = boom
        try:
            out = await fu.generate_followups("q", "a")
        finally:
            fu.stream_completion = orig
        self.assertEqual(out, [])
