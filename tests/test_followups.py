import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import followups as fu  # noqa: E402


class GenerateFollowupsTests(unittest.IsolatedAsyncioTestCase):
    async def test_passes_explicit_timeout_to_llm(self):
        seen = {}

        async def fake_stream(*a, **k):
            seen.update(k)
            yield '[]'

        orig = fu.stream_completion
        fu.stream_completion = fake_stream
        try:
            await fu.generate_followups("q", "a", timeout=7)
        finally:
            fu.stream_completion = orig

        self.assertEqual(seen["timeout"], 7)

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


class FollowupTraditionalTests(unittest.IsolatedAsyncioTestCase):
    async def test_normalizes_simplified_output_before_returning(self):
        async def fake_stream(*args, **kwargs):
            yield '["台积电的资本'
            yield '开支如何影响营收？", "联发科的竞争优势是什么？", "先进封装产能如何？", "第四題"]'

        with patch.object(fu, "stream_completion", fake_stream):
            result = await fu.generate_followups("展望", "答案")

        self.assertEqual(result, [
            "台積電的資本開支如何影響營收？",
            "聯發科的競爭優勢是什麼？",
            "先進封裝產能如何？",
        ])

    async def test_preserves_traditional_wording_and_proper_names(self):
        questions = ["恒耀的營收展望？", "台積電如何布局先進封裝？", "船期干擾有何影響？"]

        async def fake_stream(*args, **kwargs):
            yield json.dumps(questions, ensure_ascii=False)

        with patch.object(fu, "stream_completion", fake_stream):
            self.assertEqual(await fu.generate_followups("展望", "答案"), questions)

    async def test_preserves_english_with_original_proper_names(self):
        questions = ["What is 江苏长电's outlook?", "What is TSMC's capex?", "How will NVDA grow?"]

        async def fake_stream(*args, **kwargs):
            yield json.dumps(questions, ensure_ascii=False)

        with patch.object(fu, "stream_completion", fake_stream):
            self.assertEqual(await fu.generate_followups("Outlook", "Answer", locale="en"), questions)

    async def test_unknown_locale_falls_back_to_traditional(self):
        async def fake_stream(*args, **kwargs):
            yield '["台积电的资本开支如何影响营收？"]'

        with patch.object(fu, "stream_completion", fake_stream):
            result = await fu.generate_followups("展望", "答案", locale="unknown")

        self.assertEqual(result, ["台積電的資本開支如何影響營收？"])

    async def test_normalization_failure_is_fail_open(self):
        async def fake_stream(*args, **kwargs):
            yield '["台积电的资本开支如何影响营收？"]'

        with (
            patch.object(fu, "stream_completion", fake_stream),
            patch("app.services.zh_hant.to_traditional", side_effect=RuntimeError("conversion failed")),
        ):
            self.assertEqual(await fu.generate_followups("展望", "答案"), [])
