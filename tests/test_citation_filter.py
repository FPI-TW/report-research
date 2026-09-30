"""不存在的來源編號不會在串流或最終答案中冒充有效證據。"""

import unittest

from app.services.citation_filter import CitationStreamFilter, count_unknown_citations, filter_unknown_citations


class CitationFilterTests(unittest.TestCase):
    def test_only_supplied_source_numbers_survive(self):
        text = "毛利率 28%[1]，偽造來源[9]，第二份[2]。"
        self.assertEqual(
            filter_unknown_citations(text, [1, 2]),
            "毛利率 28%[1]，偽造來源（無效引用），第二份[2]。",
        )
        self.assertEqual(count_unknown_citations(text, [1, 2]), 1)

    def test_split_brackets_never_expose_unknown_number(self):
        stream = CitationStreamFilter([1])
        pieces = ["毛利率 28%[", "9", "]，實際來源[", "1]。"]
        emitted = [stream.feed(piece) for piece in pieces] + [stream.flush()]
        self.assertEqual("".join(emitted), "毛利率 28%（無效引用），實際來源[1]。")
        self.assertTrue(all("[9]" not in piece for piece in emitted))

    def test_non_citation_brackets_are_preserved(self):
        stream = CitationStreamFilter([1])
        emitted = stream.feed("範圍 [A] 與未完成 [3") + stream.flush()
        self.assertEqual(emitted, "範圍 [A] 與未完成 [3")
