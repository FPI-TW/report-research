"""M1b 研報評測指標（eval/report_metrics.py）純函式測試。

全部指標必須確定性可算（無 LLM）；每個指標的分母與缺資料行為在此凍結。
"""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval import report_metrics as rm  # noqa: E402


class FacetCoverageTests(unittest.TestCase):
    def test_hit_via_normalized_keyword(self):
        facets = [
            {"name": "先進製程", "keywords": ["3奈米", "CoWoS"]},
            {"name": "估值", "keywords": ["目標價"]},
        ]
        # CoWoS 大小寫與空白差異需經 norm_for_match 正規化後命中
        ctx = "[1] 報告：x\n台積電 co wos 產能滿載，法人上修"
        out = rm.facet_coverage(facets, ctx)
        self.assertEqual(out["covered"], 1)
        self.assertEqual(out["total"], 2)
        self.assertAlmostEqual(out["rate"], 0.5)
        self.assertEqual(out["missing"], ["估值"])

    def test_empty_facets_returns_none(self):
        self.assertIsNone(rm.facet_coverage([], "任何脈絡"))
        self.assertIsNone(rm.facet_coverage(None, "任何脈絡"))

    def test_empty_context_zero_rate(self):
        facets = [{"name": "估值", "keywords": ["目標價"]}]
        out = rm.facet_coverage(facets, "")
        self.assertEqual(out["covered"], 0)
        self.assertAlmostEqual(out["rate"], 0.0)


class SourceDiversityTests(unittest.TestCase):
    def test_dedup_and_counts(self):
        sources = [
            {"report_id": "a", "market": "TW"},
            {"report_id": "a", "market": "TW"},
            {"report_id": "b", "market": "US"},
            {"report_id": "c", "market": None},
        ]
        out = rm.source_diversity(sources, brokers=["群益", "群益", None, "凱基"])
        self.assertEqual(out["n_reports"], 3)
        self.assertEqual(out["n_markets"], 2)
        self.assertEqual(out["n_brokers"], 2)

    def test_brokers_none_means_unknown(self):
        out = rm.source_diversity([{"report_id": "a", "market": "TW"}], brokers=None)
        self.assertIsNone(out["n_brokers"])

    def test_empty_sources(self):
        out = rm.source_diversity([], brokers=None)
        self.assertEqual(out["n_reports"], 0)
        self.assertEqual(out["n_markets"], 0)


class DateDiversityTests(unittest.TestCase):
    def test_span_months_undated(self):
        sources = [
            {"report_date": "2026-07-01"},
            {"report_date": "2026-05-15"},
            {"report_date": "2026-05-20"},
            {"report_date": None},
        ]
        out = rm.date_diversity(sources)
        self.assertEqual(out["date_span_days"], 47)
        self.assertEqual(out["n_months"], 2)
        self.assertEqual(out["n_undated"], 1)

    def test_all_undated(self):
        out = rm.date_diversity([{"report_date": None}])
        self.assertIsNone(out["date_span_days"])
        self.assertIsNone(out["n_months"])
        self.assertEqual(out["n_undated"], 1)


_FULL_MD = (
    "# 台積電深度研報\n\n"
    "## 執行摘要\n\n重點[1]。\n\n"
    "## 關鍵發現\n\n發現[2]，佐證[1][3]。\n\n"
    "## 重點分析\n\n```chart\n{\"values\":[5]}\n```\n\n分析[2]（網路）。\n\n"
    "## 風險與展望\n\n風險[9]。\n\n"
    "## 引用來源\n\n[1] 報告A\n[2] 報告B\n\n"
    "## 外部參考（網路）\n\n- [新聞](https://example.com/a)\n"
)


class SectionCoverageTests(unittest.TestCase):
    def test_full_skeleton(self):
        out = rm.section_coverage(_FULL_MD)
        self.assertEqual(out["covered"], 5)
        self.assertEqual(out["total"], 5)
        self.assertAlmostEqual(out["rate"], 1.0)

    def test_missing_sections_listed(self):
        md = "# 標題\n\n## 執行摘要\n\n內容\n"
        out = rm.section_coverage(md)
        self.assertEqual(out["covered"], 1)
        self.assertIn("關鍵發現", out["missing"])

    def test_numbered_heading_still_counts(self):
        md = "## 一、執行摘要\n\n內容\n"
        out = rm.section_coverage(md)
        self.assertEqual(out["covered"], 1)

    def test_deeper_heading_not_counted(self):
        md = "### 執行摘要\n\n內容\n"
        out = rm.section_coverage(md)
        self.assertEqual(out["covered"], 0)

    def test_empty_markdown(self):
        out = rm.section_coverage("")
        self.assertEqual(out["covered"], 0)


class CitationMetricsTests(unittest.TestCase):
    def test_valid_invalid_mix_and_reference_sections_excluded(self):
        # 正文引用 [1],[2],[1],[3],[2],[9] 共 6 個，sources 只有 3 篇 → [9] 無效；
        # 引用來源節的 [1][2] 與 chart 圍欄裡的 [5] 都不得計入。
        out = rm.citation_metrics(_FULL_MD, n_sources=3)
        self.assertEqual(out["n_citations"], 6)
        self.assertAlmostEqual(out["citation_validity"], 5 / 6)
        self.assertAlmostEqual(out["source_citation_rate"], 3 / 3)

    def test_no_citations_returns_none_validity(self):
        out = rm.citation_metrics("# 標題\n\n## 執行摘要\n\n無引用。", n_sources=3)
        self.assertEqual(out["n_citations"], 0)
        self.assertIsNone(out["citation_validity"])

    def test_fence_containing_section_heading_does_not_eat_body(self):
        """圍欄先去、節後剔：圍欄內含「## 引用來源」行時不得誤砍正文引用。"""
        md = (
            "## 重點分析\n\n```chart\n## 引用來源\n{\"values\":[5]}\n```\n\n"
            "正文論點[1][2]。\n\n## 引用來源\n\n[1] A\n[2] B\n"
        )
        out = rm.citation_metrics(md, n_sources=2)
        self.assertEqual(out["n_citations"], 2)
        self.assertAlmostEqual(out["citation_validity"], 1.0)

    def test_zero_sources_rate_none(self):
        out = rm.citation_metrics("內文[1]。", n_sources=0)
        self.assertIsNone(out["source_citation_rate"])
        self.assertAlmostEqual(out["citation_validity"], 0.0)


class ExternalLabelingTests(unittest.TestCase):
    def test_consistent_usage_scores_one(self):
        out = rm.external_labeling(_FULL_MD)
        self.assertTrue(out["applicable"])
        self.assertAlmostEqual(out["score"], 1.0)

    def test_markers_without_section_inconsistent(self):
        md = "## 執行摘要\n\n論點（網路）。\n"
        out = rm.external_labeling(md)
        self.assertTrue(out["applicable"])
        self.assertAlmostEqual(out["score"], 0.0)

    def test_section_without_markers_inconsistent(self):
        md = "## 執行摘要\n\n論點[1]。\n\n## 外部參考（網路）\n\n- [新聞](https://e.com)\n"
        out = rm.external_labeling(md)
        self.assertTrue(out["applicable"])
        self.assertAlmostEqual(out["score"], 0.0)

    def test_no_web_not_applicable(self):
        md = "## 執行摘要\n\n論點[1]。\n"
        out = rm.external_labeling(md)
        self.assertFalse(out["applicable"])
        self.assertIsNone(out["score"])

    def test_ext_section_needs_link_line(self):
        # 有節名但無任何 `- [標題](網址)` 行 → 視同沒有外部參考節
        md = "## 執行摘要\n\n論點（網路）。\n\n## 外部參考（網路）\n\n（空）\n"
        out = rm.external_labeling(md)
        self.assertAlmostEqual(out["score"], 0.0)


class NoDataHandledTests(unittest.TestCase):
    def test_error_event_is_safe(self):
        self.assertTrue(rm.no_data_handled(error="找不到足夠資料生成研報",
                                           n_sources=0, markdown=None))

    def test_web_answer_with_labeling_and_no_invalid_citations(self):
        md = (
            "# 主題\n\n## 執行摘要\n\n觀點（網路）。\n\n"
            "## 外部參考（網路）\n\n- [x](https://e.com)\n"
        )
        self.assertTrue(rm.no_data_handled(error=None, n_sources=3, markdown=md))

    def test_invalid_citation_fails(self):
        md = "# 主題\n\n## 執行摘要\n\n捏造[7]。\n"
        self.assertFalse(rm.no_data_handled(error=None, n_sources=3, markdown=md))

    def test_inconsistent_labeling_fails(self):
        md = "# 主題\n\n## 執行摘要\n\n觀點（網路）。\n"
        self.assertFalse(rm.no_data_handled(error=None, n_sources=3, markdown=md))


class AggregateCasesTests(unittest.TestCase):
    def _case(self, **over):
        base = {
            "id": "r001",
            "no_data": False,
            "facet_coverage": {"covered": 3, "total": 4, "rate": 0.75, "missing": []},
            "source_diversity": {"n_reports": 10, "n_markets": 2, "n_brokers": 4},
            "date_diversity": {"date_span_days": 90, "n_months": 4, "n_undated": 0},
            "section_coverage": {"covered": 5, "total": 5, "rate": 1.0, "missing": []},
            "citation_validity": 1.0,
            "source_citation_rate": 0.6,
            "n_citations": 12,
            "external_labeling": {"applicable": True, "score": 1.0},
            "no_data_handled": None,
        }
        base.update(over)
        return base

    def test_aggregate_means_counts_and_sufficiency(self):
        cases = [self._case(id=f"r{i:03d}") for i in range(6)]
        cases.append(self._case(id="r007", error="boom"))
        cases.append(self._case(
            id="r008", no_data=True, facet_coverage=None,
            citation_validity=None, no_data_handled=True,
        ))
        s = rm.aggregate_cases(cases)
        self.assertEqual(s["n"], 8)
        self.assertEqual(s["n_errors"], 1)
        self.assertEqual(s["n_no_data"], 1)
        self.assertAlmostEqual(s["facet_coverage"]["mean"], 0.75)
        self.assertEqual(s["facet_coverage"]["n_valid"], 6)
        self.assertAlmostEqual(s["section_coverage"]["mean"], 1.0)
        self.assertAlmostEqual(s["no_data_handled"]["mean"], 1.0)
        self.assertEqual(s["no_data_handled"]["n_valid"], 1)
        self.assertTrue(s["sufficient_n"])
        self.assertEqual(s["ruleset_version"], rm.RULESET_VERSION)

    def test_insufficient_n_flagged(self):
        cases = [self._case(), self._case(error="x"), self._case(error="y")]
        s = rm.aggregate_cases(cases)
        self.assertFalse(s["sufficient_n"])

    def test_report_declines_separate_from_runner_errors(self):
        """審查 M1b-1：no_data 題的結構化婉拒（report_error）不計 n_errors、
        計入 no_data_handled 分母；正常題的婉拒計 n_report_declined 且不算有效題。"""
        cases = [self._case(id=f"r{i:03d}") for i in range(6)]
        cases.append({
            "id": "r009", "no_data": True,
            "report_error": "找不到足夠資料生成研報",
            "no_data_handled": True, "n_sources": 0, "stages": ["retrieving"],
        })
        cases.append({
            "id": "r007", "no_data": False,
            "report_error": "找不到足夠資料生成研報",
            "no_data_handled": None, "n_sources": 0, "stages": ["retrieving"],
        })
        s = rm.aggregate_cases(cases)
        self.assertEqual(s["n_errors"], 0)
        self.assertEqual(s["n_report_declined"], 2)
        self.assertEqual(s["n_no_data"], 1)
        self.assertAlmostEqual(s["no_data_handled"]["mean"], 1.0)
        self.assertEqual(s["no_data_handled"]["n_valid"], 1)
        self.assertTrue(s["sufficient_n"])  # 6 題正常有效
        # 正常題婉拒不得灌入有效題數
        cases_fewer = cases[1:]  # 只剩 5 題正常有效
        self.assertFalse(rm.aggregate_cases(cases_fewer)["sufficient_n"])


if __name__ == "__main__":
    unittest.main()
