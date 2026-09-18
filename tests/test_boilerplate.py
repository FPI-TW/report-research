# tests/test_boilerplate.py
"""`app/services/boilerplate.py`：跨文件樣板段落的字典與剔除。

釘三件事：(1) 門檻——夠多篇、且跨標的才算樣板，單一公司的簡介不算；(2) 剔除只動要切塊
的文字、且 fail-open（沒字典、壞字典、剔太多都退回原文）；(3) 字典寫讀往返決定性。
全部用 tempfile，不碰 data/。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import boilerplate as B  # noqa: E402

DISCLAIMER = (
    "免責聲明 本報告之內容皆來自本公司認可之資料來源，但不保證其完整性及精確性。"
    "投資人應審慎考量本身之投資風險。"
)
PROFILE = "台積電為全球最大晶圓代工廠，先進製程市占率領先，主要客戶涵蓋高效能運算與智慧型手機。"
BODY = "第三季營收優於預期，主因先進製程需求強勁；我們上調目標價至 1,200 元。"


def _rec(text: str, source: str = "kgi", code: str | None = "2330") -> dict:
    return {"text": text, "source": source, "stock_code": code}


class BuildIndexTests(unittest.TestCase):
    def test_paragraph_repeated_across_codes_is_boilerplate(self):
        recs = [_rec(f"{BODY} {i}\n\n{DISCLAIMER}", code=str(1000 + i)) for i in range(10)]
        idx = B.build_index(recs, min_docs=8)
        keys = idx["kgi"]["keys"]
        self.assertIn(B.paragraph_key(DISCLAIMER), keys)
        self.assertNotIn(B.paragraph_key(BODY + " 1"), keys)

    def test_single_company_profile_is_not_boilerplate(self):
        """同一檔股票的公司簡介會在每篇重複，但只跨一個 stock_code——不是樣板。"""
        recs = [_rec(f"{PROFILE}\n\n{BODY} {i}", code="2330") for i in range(20)]
        idx = B.build_index(recs, min_docs=8)
        self.assertNotIn(B.paragraph_key(PROFILE), idx["kgi"]["keys"])

    def test_macro_reports_without_code_count_by_themselves(self):
        recs = [_rec(f"{DISCLAIMER}\n\n{BODY} {i}", code=None) for i in range(10)]
        idx = B.build_index(recs, min_docs=8)
        self.assertIn(B.paragraph_key(DISCLAIMER), idx["kgi"]["keys"])

    def test_below_threshold_is_not_boilerplate(self):
        recs = [_rec(f"{DISCLAIMER}\n\n{BODY} {i}", code=str(i)) for i in range(5)]
        idx = B.build_index(recs, min_docs=8)
        self.assertEqual(idx["kgi"]["keys"], {})

    def test_sources_are_separate(self):
        recs = [_rec(DISCLAIMER, source="kgi", code=str(i)) for i in range(10)]
        recs += [_rec(DISCLAIMER, source="yuanta", code=str(i)) for i in range(3)]
        idx = B.build_index(recs, min_docs=8)
        self.assertIn(B.paragraph_key(DISCLAIMER), idx["kgi"]["keys"])
        self.assertNotIn(B.paragraph_key(DISCLAIMER), idx["yuanta"]["keys"])

    def test_short_paragraphs_never_count(self):
        self.assertIsNone(B.paragraph_key("資料來源：凱基"))

    def test_same_paragraph_twice_in_one_doc_counts_once(self):
        recs = [_rec(f"{DISCLAIMER}\n\n{DISCLAIMER}", code=str(i)) for i in range(4)]
        idx = B.build_index(recs, min_docs=8)
        self.assertEqual(idx["kgi"]["keys"], {})


class StripTests(unittest.TestCase):
    def setUp(self):
        self.keys = frozenset({B.paragraph_key(DISCLAIMER)})

    def test_strips_boilerplate_paragraph_only(self):
        text = f"{BODY}\n\n{DISCLAIMER}\n\n{PROFILE}"
        out, dropped = B.strip_boilerplate(text, "kgi", keys=self.keys)
        self.assertEqual(dropped, 1)
        self.assertEqual(out, f"{BODY}\n\n{PROFILE}")

    def test_no_keys_returns_input_unchanged(self):
        text = f"{BODY}\n\n{DISCLAIMER}"
        self.assertEqual(B.strip_boilerplate(text, "kgi", keys=frozenset()), (text, 0))

    def test_all_boilerplate_falls_back_to_original(self):
        """一篇幾乎全是樣板的檔不可以被剔到空——那會讓入庫端當它是掃描檔跳過。"""
        text = f"{DISCLAIMER}\n\n短。"
        out, dropped = B.strip_boilerplate(text, "kgi", keys=self.keys)
        self.assertEqual((out, dropped), (text, 0))

    def test_missing_index_is_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            B.load_keys.cache_clear()
            self.assertEqual(B.load_keys("nobody", d), frozenset())

    def test_corrupt_index_is_fail_open(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "kgi.json").write_text("{not json", encoding="utf-8")
            B.load_keys.cache_clear()
            self.assertEqual(B.load_keys("kgi", d), frozenset())


class RoundTripTests(unittest.TestCase):
    def test_write_then_load(self):
        recs = [_rec(f"{DISCLAIMER}\n\n{BODY} {i}", code=str(i)) for i in range(10)]
        idx = B.build_index(recs, min_docs=8)
        with tempfile.TemporaryDirectory() as d:
            paths = B.write_index(idx, Path(d))
            self.assertEqual([p.name for p in paths], ["kgi.json"])
            payload = json.loads(paths[0].read_text(encoding="utf-8"))
            self.assertEqual(payload["version"], B.RECORD_VERSION)
            B.load_keys.cache_clear()
            self.assertEqual(B.load_keys("kgi", d), frozenset(idx["kgi"]["keys"]))
            self.assertEqual(B.load_keys("KGI", d), B.load_keys("kgi", d), "source 大小寫不敏感")


if __name__ == "__main__":
    unittest.main()
