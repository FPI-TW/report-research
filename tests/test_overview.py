import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.overview import detect_overview  # noqa: E402


class DetectOverviewTests(unittest.TestCase):
    def test_enumeration_cue_is_overview(self):
        self.assertTrue(detect_overview("給我所有元大的報告種類"))
        self.assertTrue(detect_overview("台股最近一週有哪些新研報"))
        self.assertTrue(detect_overview("元大總共有多少篇研報"))

    def test_specific_question_is_not_overview(self):
        self.assertFalse(detect_overview("台積電的投資評級如何"))
        self.assertFalse(detect_overview("元大怎麼看半導體景氣"))
