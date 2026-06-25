# tests/test_source.py
"""內文券商來源偵測（extract_source_from_text）測試。

語料中 ~67% 研報的 source 為 NULL：檔名沒帶券商 token，但內文一定載明發行機構
（自有網站 URL、著作權／免責聲明、圖表「資料來源：…投顧」自我標註）。本函式以
這些高精度指紋補 source，與檔名解析互補（見 parse_filename）。範例字串取自真實語料。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.filename import (  # noqa: E402
    extract_source_from_text,
    parse_filename,
    source_display,
)


class ExtractSourceFromTextTests(unittest.TestCase):
    def _s(self, text):
        return extract_source_from_text(text)

    # --- 本土券商：自有網站 URL / 著作權 / 自我標註指紋 ---
    def test_kgi_by_research_url(self):
        # 凱基 daily story / Takeaway / Taiwan daily 皆帶此研究網站
        txt = "六月 14, 2026 研究網站: https://investment.kgisia.com.tw/Portal/Report 動態更新"
        self.assertEqual(self._s(txt), "kgi")

    def test_masterlink_by_copyright(self):
        txt = (
            "本刊載之報告為元富投顧於特定日期之分析，已力求陳述內容之可靠性。"
            "報告著作權屬元富投顧所有，禁止任何形式之抄襲、引用或轉載。"
        )
        self.assertEqual(self._s(txt), "masterlink")

    def test_sinopac_by_morning_brief(self):
        txt = "永豐晨訊 2024/6/26 永豐證券投資顧問股份有限公司 1"
        self.assertEqual(self._s(txt), "sinopac")

    def test_fubon_by_chart_credit(self):
        # 圖表「資料來源：…富邦投顧整理」是發行機構自我標註，屬高精度指紋
        txt = "美國貨幣政策 圖：聯邦基金利率 資料來源：Bloomberg、富邦投顧整理 9/18 聯準會"
        self.assertEqual(self._s(txt), "fubon")

    def test_hongyuan_by_disclaimer(self):
        txt = "Company Report 公司研究報告 本報告僅供宏遠投顧內部及客戶參考，雖已力求正確與完整"
        self.assertEqual(self._s(txt), "hongyuan")

    # --- 外資券商：詞邊界比對（避免 'ubs' 命中 'substrates'）---
    def test_morgan_stanley_foreign(self):
        txt = "M Global Idea Global Technology Morgan Stanley Taiwan Limited+ Charlie Chan"
        self.assertEqual(self._s(txt), "morgan_stanley")

    def test_ubs_word_boundary_no_false_positive(self):
        # 'substrates' 內含 ubs，但非 UBS；不得誤判
        txt = "Technology Hardware Asia Pacific ABF substrates read-across from AT&S"
        self.assertIsNone(self._s(txt))

    def test_ubs_real_match(self):
        txt = "Global Research UBS Securities Asia Limited equity strategy note"
        self.assertEqual(self._s(txt), "ubs")

    # --- 排序：本土發行機構（CJK）優先於外資「提及」 ---
    def test_local_issuer_wins_over_foreign_mention(self):
        # 元大投顧自家投資早報內文提及 Morgan Stanley 的預估，來源仍應是 yuanta
        txt = (
            "投資早報 元大投顧 2024/12/6 台股盤勢分析。"
            "國際機構看法：Morgan Stanley 預估明年營收成長。"
        )
        self.assertEqual(self._s(txt), "yuanta")

    # --- 視窗：外資提及只在表頭算數；過深的提及不採（防 body-mention 誤判）---
    def test_foreign_mention_beyond_window_ignored(self):
        txt = "x" * 3000 + " Morgan Stanley equity research note deep in body"
        self.assertIsNone(self._s(txt))

    def test_cjk_issuer_credit_deep_still_detected(self):
        # 國際金融市場焦點：圖表自我標註「元大投顧」常落在第 2~3 頁（char ~1800-3500）
        txt = "國際金融市場焦點 " + "y" * 3300 + " 資料來源：Bloomberg、元大投顧 交易量"
        self.assertEqual(self._s(txt), "yuanta")

    # --- 無指紋 / 空輸入 ---
    def test_no_signal_returns_none(self):
        txt = "崇越(5434) – 20241230座談會摘要 預估2025年營收逐季成長，全年目標630億元。"
        self.assertIsNone(self._s(txt))

    def test_empty_and_none(self):
        self.assertIsNone(self._s(""))
        self.assertIsNone(self._s(None))


class NewBrokerMappingTests(unittest.TestCase):
    def test_masterlink_display_name(self):
        self.assertEqual(source_display("masterlink"), "元富")

    def test_hongyuan_display_name(self):
        self.assertEqual(source_display("hongyuan"), "宏遠")

    def test_yuanfu_filename_token_maps_to_masterlink(self):
        # 檔名帶「元富」應解析為 masterlink（與內文指紋一致）
        self.assertEqual(parse_filename("元富投顧0801每日股市彙報.pdf").source, "masterlink")


if __name__ == "__main__":
    unittest.main()
