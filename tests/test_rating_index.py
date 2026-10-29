# tests/test_rating_index.py
"""`app/services/rating_index.py`：送 LLM 前剔除近期報告／評等異動索引表。

釘三件事：(1) 元大與凱基幾種實際版型的索引表會被剔除，內文留著；(2) 長得像但不是索引表的
（大和年度財測表、經濟行事曆、少於 3 列）不動，且 fail-open；(3) 標註與訊號擷取三個入口
送出的內文都先經過剔除。樣本取自測試環境語料的原文版型。
"""
import asyncio
import importlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import rating_index as R  # noqa: E402
from scripts._claude_cli import CliResult  # noqa: E402

YUANTA_COVER = """投資早報

發布日 報告 評等

10/22 3293鈊象 買進

10/22 SPCX US -

10/21 TPCA 2026 -

10/20 生技製藥產業 -

10/19 6414樺漢 買進

10/15 2049上銀 買進

2026/10/22

台股盤勢分析 ················ 2
"""
YUANTA_BODY = "鈊象 (3293 TT，買進) - 美國跨州快速成長，授權收入再擴大，推得目標價 1,000元。"
# 真實封面後面都接著內文；樣本太短會觸發 fail-open（剔除後剩不到一半就退回原文）
PADDING = "\n" + YUANTA_BODY * 6

# 凱基 Taiwan daily 封面：雙欄交錯，索引列之間夾著另一欄的片段與空行
KGI_DAILY_COVER = """近期評等更新研究報告一覽表 訊息快遞 04

日期 公司 調整前 調整後 TW 近期內召開法人說明會一覽表
06/30 環球晶 (6488 重啟評等 增加持股 現

TT) 動態更新 05

06/17 創新服務 (7828 首次評等 增加持股 數據彙整

TT) 18

TW 海悅 (2348 TT): 3Q26 集中爆發

06/16 長榮航 (2618 TT 持有 增加持股
05/25 富邦金 (2881 ) 持有 增加持股
"""

KGI_INDUSTRY_TABLE = """近期報告
9/5 嘉澤 3533 增加持股 1,930 AI推升新CPU平台滲透率與利潤率
9/3 華擎 3515 增加持股 270 伺服器為主要成長動能
8/23 奇鋐 3017 增加持股 830 液冷市場強勁需求驅動擴產
"""

DAIWA_FINANCIALS = """年度 營收 年增 營業利益 年增 每股盈餘 股利
3/22 401,138 (24) 70,821 (83) 295.4 40.00
3/23 417,549 (4) 72,362 (2) 373.7 50.00
3/24 370,511 (-11) 47,568 (-34) 225.4 40.00
12/24 E 158,952 18 128,612 18 20.60 -
12/25 E 160,000 1 130,000 1 21.00 -
12/26 E 165,000 3 133,000 2 22.00 -
"""

CALENDAR = """本週重要行事曆
10/1 美國大選副總統辯論會
9/30 聯準會主席Powell於全美商業經濟協會(NABE)演說
9/30 美國美東港口罷工談判最終日
"""


class StripTests(unittest.TestCase):
    def test_yuanta_cover_index_removed_body_kept(self):
        out = R.strip_rating_index(YUANTA_COVER + YUANTA_BODY)
        for gone in ("6414樺漢", "2049上銀", "SPCX US -", "生技製藥產業 -"):
            self.assertNotIn(gone, out)
        self.assertIn("發布日 報告 評等", out)
        self.assertIn("台股盤勢分析", out)
        self.assertIn(YUANTA_BODY, out)

    def test_interleaved_two_column_cover_removed(self):
        out = R.strip_rating_index(KGI_DAILY_COVER + PADDING)
        for gone in ("06/30", "06/17", "06/16", "05/25"):
            self.assertNotIn(gone, out)
        self.assertIn("TW 海悅 (2348 TT): 3Q26 集中爆發", out)

    def test_industry_recent_reports_table_removed(self):
        out = R.strip_rating_index(KGI_INDUSTRY_TABLE + PADDING)
        self.assertNotIn("3533", out)
        self.assertIn("近期報告", out)

    def test_dated_lines_without_ratings_untouched(self):
        for text in (DAIWA_FINANCIALS, CALENDAR):
            with self.subTest(text=text[:10]):
                self.assertEqual(R.strip_rating_index(text), text)

    def test_dash_only_calendar_untouched(self):
        # 富邦總經週報的選舉行事曆：整群都是「-」結尾，沒有一列帶評等詞
        text = "全球重要選舉\n3/10 葡萄牙 議會選舉 - \n4/10 南韓 國會選舉 - \n6/2 墨西哥 總統與國會選舉 - \n" + PADDING
        self.assertEqual(R.strip_rating_index(text), text)

    def test_fewer_than_min_rows_untouched(self):
        text = "10/22 3293鈊象 買進\n\n10/15 2049上銀 買進\n內文"
        self.assertEqual(R.strip_rating_index(text), text)

    def test_far_apart_rows_are_separate_tables(self):
        filler = "\n".join(f"內文第{i}段" for i in range(R.MAX_GAP + 1))
        text = f"10/22 3293鈊象 買進\n{filler}\n10/15 2049上銀 買進\n{filler}\n10/14 2006東和鋼鐵 買進"
        self.assertEqual(R.strip_rating_index(text), text)

    def test_invalid_dates_are_not_dates(self):
        self.assertFalse(R._is_date_line("13/40 2330台積電 買進"))
        self.assertFalse(R._is_date_line("10/22-3293 買進"))

    def test_fail_open_when_almost_everything_is_index(self):
        self.assertEqual(R.strip_rating_index(KGI_INDUSTRY_TABLE.split("\n", 1)[1]),
                         KGI_INDUSTRY_TABLE.split("\n", 1)[1])

    def test_empty_and_none(self):
        self.assertEqual(R.strip_rating_index(None), "")
        self.assertEqual(R.strip_rating_index(""), "")


class EvidenceFromIndexTests(unittest.TestCase):
    """修復批次靠它挑出要刪的訊號：LLM 回的證據會補字、會把下一行的代碼併進來。"""

    LINES = R.index_lines(KGI_DAILY_COVER + PADDING) + ["03/31 台新新光金 持有 增加持股"]

    def test_matches_despite_llm_rewording(self):
        self.assertTrue(R.evidence_from_index("06/30 環球晶 (6488 TT) 重啟評等 增加持股", "6488", self.LINES))

    def test_matches_code_on_next_line_by_first_word(self):
        self.assertTrue(R.evidence_from_index("03/31 台新新光金 (2887) 持有 增加持股", "2887", self.LINES))

    def test_body_evidence_and_other_dates_do_not_match(self):
        self.assertFalse(R.evidence_from_index("維持增加持股評等，目標價 1,350 元", "6488", self.LINES))
        self.assertFalse(R.evidence_from_index("07/01 環球晶 (6488) 增加持股", "6488", self.LINES))
        self.assertFalse(R.evidence_from_index(None, "6488", self.LINES))


def _load_script(name: str):
    spec = importlib.util.spec_from_file_location(f"_ri_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class EntryPointTests(unittest.TestCase):
    """三個入口送出的內文都不含索引列（漏接一個，那條路徑照舊把舊評等當本篇）。"""

    TEXT = YUANTA_COVER + YUANTA_BODY

    def test_sync_inline_tagging(self):
        ic = importlib.import_module("scripts._ingest_core")
        sent = []

        def fake_run_claude(prompt, model, **kw):
            sent.append(prompt)
            return CliResult(None, "測試不送出")

        with mock.patch.object(ic, "run_claude", fake_run_claude):
            ic._tag_via_cli("f.pdf", self.TEXT, file_hash="h1")
        self.assertNotIn("2049上銀", sent[0])
        self.assertIn(YUANTA_BODY, sent[0])

    def test_tag_all_cli(self):
        tac = _load_script("tag_all_cli")
        prompt = tac.build_prompt("f.pdf", self.TEXT, 10000)
        self.assertNotIn("2049上銀", prompt)
        self.assertIn(YUANTA_BODY, prompt)

    def test_signal_extraction(self):
        es = _load_script("extract_signals")
        sent = []

        class _Stop(Exception):
            pass

        def fake_build(file_name, report_date, broker, codes, body_excerpt):
            sent.append(body_excerpt)
            raise _Stop

        item = es.WorkItem("rep-1", "TW", "yuanta", None, "f.pdf", self.TEXT, ["3293"], file_hash="h1")
        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(es, "FAIL_LOG", Path(tmp) / "signal.log"), \
             mock.patch.object(es, "build_signal_prompt", fake_build):
            with self.assertRaises(_Stop):
                asyncio.run(es.extract_one(asyncio.Semaphore(1), item, 16000, "deepseek-flash", 1))
        self.assertNotIn("2049上銀", sent[0])
        self.assertIn(YUANTA_BODY, sent[0])


if __name__ == "__main__":
    unittest.main()
