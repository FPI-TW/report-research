# tests/test_faithfulness_report.py
"""M8b 研報接線：report_writer 的逐節 grounding helper 與 persist evaluation。

_ground_sections/_section_needs_fix/_citation_coverage 以假物件測（零 LLM/DB）；
persist_report_doc 的 evaluation 欄以假 session 驗（含位置參數契約不破壞）。
draft_report 的 grounding 控制流（needs_fix→regen→reground→彙總）由 SectionedComposedTests
的真 draft_report 覆蓋（見 test_report_sectioned.py）。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import report_writer as rw  # noqa: E402
from app.services.evidence import EvidenceLedger, RenderedCitations  # noqa: E402
from app.services.faithfulness import ClaimVerdict, FaithfulnessResult  # noqa: E402


def _result(*, numeric_rate, claims=(), degraded=False):
    return FaithfulnessResult(
        faithfulness_score=None, numeric_support_rate=numeric_rate,
        claims=list(claims), degraded=degraded,
    )


class SectionNeedsFixTests(unittest.TestCase):
    def test_below_threshold_with_unsupported_numeric_needs_fix(self):
        r = _result(numeric_rate=0.5,
                    claims=[ClaimVerdict("毛利率 90%", True, "unsupported")])
        self.assertTrue(rw._section_needs_fix(r, 0.9))

    def test_above_threshold_no_fix(self):
        r = _result(numeric_rate=1.0,
                    claims=[ClaimVerdict("毛利率 50%", True, "supported")])
        self.assertFalse(rw._section_needs_fix(r, 0.9))

    def test_degraded_never_fixes(self):
        self.assertFalse(rw._section_needs_fix(_result(numeric_rate=None, degraded=True), 0.9))

    def test_no_numeric_claims_no_fix(self):
        self.assertFalse(rw._section_needs_fix(_result(numeric_rate=None), 0.9))

    def test_low_rate_but_no_unsupported_numeric_no_fix(self):
        # numeric_rate 低但未支持的都是非數值 → 不觸發（門檻只針對數值主張修正）
        r = _result(numeric_rate=0.5,
                    claims=[ClaimVerdict("看好前景", False, "unsupported")])
        # 此情境 numeric_rate 理論上不會是 0.5，但防禦性驗：無「未支持的數值主張」不修
        self.assertFalse(rw._section_needs_fix(r, 0.9))


class CitationCoverageTests(unittest.TestCase):
    def _rendered(self, n_ordered, n_unknown):
        return RenderedCitations(
            text="x", ordered=[object()] * n_ordered,
            number_of={}, n_unknown=n_unknown,
        )

    def test_all_resolved(self):
        self.assertEqual(rw._citation_coverage(self._rendered(4, 0)), 1.0)

    def test_partial(self):
        self.assertEqual(rw._citation_coverage(self._rendered(3, 1)), 0.75)

    def test_none_when_no_citations(self):
        self.assertIsNone(rw._citation_coverage(self._rendered(0, 0)))


class GroundSectionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_per_section_evidence_and_keying(self):
        led = EvidenceLedger()
        drafts = [
            {"position": 0, "draft": "節0內文"},
            {"position": 1, "draft": "節1內文"},
        ]
        claim_evidence = {"0": ["ev-a"], "1": ["ev-b", "ev-c"]}
        seen = {}

        async def fake_resolve(ledger, session, *, evidence_ids=None, **kw):
            seen[tuple(evidence_ids or [])] = True
            return [f"文字-{','.join(evidence_ids or [])}"]

        async def fake_check(text, context_texts, **kw):
            # 回一個把 context 記進 verdict 的結果，便於斷言節→證據對得上
            return _result(numeric_rate=1.0,
                           claims=[ClaimVerdict(text + "|" + context_texts[0], True, "supported")])

        class _S:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

        with patch.object(rw, "resolve_evidence_texts", fake_resolve), \
             patch.object(rw, "check_faithfulness", fake_check), \
             patch.object(rw, "SessionFactory", lambda: _S()):
            out = await rw._ground_sections(
                drafts, claim_evidence, led, model="m", timeout=1.0,
            )

        self.assertEqual(set(out.keys()), {0, 1})
        # 節0拿 ev-a，節1拿 ev-b,ev-c
        self.assertIn("文字-ev-a", out[0].claims[0].text)
        self.assertIn("文字-ev-b,ev-c", out[1].claims[0].text)
        self.assertIn(("ev-a",), seen)
        self.assertIn(("ev-b", "ev-c"), seen)


class PersistEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_persist_writes_evaluation_and_keeps_positional_contract(self):
        from app.services import report as rpt

        captured = {}

        class _FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False

            async def execute(self, stmt, params):
                captured["params"] = params
                captured["sql"] = str(stmt)

            async def commit(self):
                captured["committed"] = True

        ev = {"faithfulness_score": 0.8, "degraded": False}
        with patch.object(rpt, "SessionFactory", lambda: _FakeSession()):
            # 位置參數契約（…, sources, thinking_ms, evidence_manifest）不變；evaluation keyword
            await rpt.persist_report_doc(
                "rid", None, None, "q", "t", "md", "p",
                [{"n": 1}], 100, {"schema_version": 1},
                evaluation=ev,
            )
        self.assertTrue(captured.get("committed"))
        self.assertIn("evaluation", captured["sql"])
        # evaluation 以 json 字串進 bind
        import json
        self.assertEqual(json.loads(captured["params"]["eval"]), ev)

    async def test_persist_evaluation_none_binds_null(self):
        from app.services import report as rpt

        captured = {}

        class _FakeSession:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def execute(self, stmt, params): captured["params"] = params
            async def commit(self): pass

        with patch.object(rpt, "SessionFactory", lambda: _FakeSession()):
            await rpt.persist_report_doc(
                "rid", None, None, "q", "t", "md", "p", [], 0, None,
            )
        self.assertIsNone(captured["params"]["eval"])


if __name__ == "__main__":
    unittest.main()
