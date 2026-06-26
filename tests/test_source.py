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

    def test_foreign_earliest_header_match_wins(self):
        txt = "Global Research Goldman Sachs Asia focus list; later cites Morgan Stanley estimates"
        self.assertEqual(self._s(txt), "goldman_sachs")

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

    # --- 期貨／金控子公司：發行機構自我指稱（「X期貨」「X金融控股」非僅「X投顧」）---
    def test_kgi_by_futures_arm(self):
        # CFTC 籌碼快報／債券雙週報由凱基期貨發行，自稱「凱基期貨」而非「凱基投顧」
        txt = (
            "重要聲明: 本簡報由凱基期貨股份有限公司編製，所載資料、意見及預測"
            "乃根據本公司認為可靠之資料來源。資料來源：CFTC 美國商品期貨交易委員會"
        )
        self.assertEqual(self._s(txt), "kgi")

    def test_kgi_by_holding_company(self):
        # 線上講座／海外債簡報自稱「凱基金融控股公司(「凱基金控」)」
        txt = "本簡報由凱基金融控股公司(「凱基金控」)所編制，所載之資料、意見及預測"
        self.assertEqual(self._s(txt), "kgi")

    def test_sinopac_by_futures_arm(self):
        # 永豐期貨專題報導／台指期盤後快訊自稱「永豐期貨股份有限公司」
        txt = "中國經濟轉好 銅價有望再迎大多頭 永豐期貨股份有限公司│台北市重慶南路一段2號"
        self.assertEqual(self._s(txt), "sinopac")

    def test_yuanta_by_futures_arm(self):
        # 元大期貨法人總經講座
        txt = "市場展望與操作工具分享 2025/6/19 2025年 元大期貨法人總經講座 川普政策搖擺"
        self.assertEqual(self._s(txt), "yuanta")

    # --- 無指紋 / 空輸入 ---
    def test_no_signal_returns_none(self):
        txt = "崇越(5434) – 20241230座談會摘要 預估2025年營收逐季成長，全年目標630億元。"
        self.assertIsNone(self._s(txt))

    def test_empty_and_none(self):
        self.assertIsNone(self._s(""))
        self.assertIsNone(self._s(None))


class IssuerVariantTests(unittest.TestCase):
    """補長尾發行機構指紋：新增本土券商與既有券商的自我指稱變體（皆自我指稱、非提及）。"""

    def _s(self, text):
        return extract_source_from_text(text)

    def test_concord_by_disclaimer(self):
        # 康和投顧個股報告頁尾自稱「以上資料為康和投顧所提供」
        self.assertEqual(self._s("以上資料為康和投顧所提供,不得轉寄"), "concord")

    def test_huanan_by_copyright(self):
        # 華南 Memo 著作權自稱
        txt = "投資人應自行負責。本研究報告的著作權為華南投顧所有，嚴禁抄襲、引用、對外傳送或轉載。"
        self.assertEqual(self._s(txt), "huanan")

    def test_fubon_sec_by_header(self):
        # 福邦投顧（福邦證券，非富邦金）股市早報報頭自稱
        self.assertEqual(self._s("福邦投顧 股市投資早報 2026/1/13 Grand Fortune Securities"), "fubon_sec")

    def test_masterlink_by_chart_credit_variant(self):
        # 元富中國經濟系列圖表自我標註「資料來源：…元富整理」
        self.assertEqual(self._s("中國GDP年增5.4% 資料來源：Wind、元富整理"), "masterlink")

    def test_mega_international_consulting_variant(self):
        txt = "不做任何保證 兆豐國際證券投資顧問股份有限公司獨立經營管理 台北"
        self.assertEqual(self._s(txt), "mega")

    def test_yuanta_by_trust_credit(self):
        self.assertEqual(self._s("投資黃金 資料來源：彭博資訊，元大投信整理，2025"), "yuanta")

    def test_president_by_trust_credit(self):
        self.assertEqual(self._s("資料來源：Factset，並經統一投信整理。"), "president")

    def test_union_federal_not_attributed(self):
        # 「聯邦快遞」=FedEx、「聯邦資金利率」=Fed funds，皆非券商，不得誤標
        self.assertIsNone(self._s("Nike(NKE) 聯邦快遞(FDX) 12/20 (五)"))
        self.assertIsNone(self._s("FOMC 以10:2通過維持利率不變，聯邦資金利率維持在 3.5%"))

    def test_new_broker_display_names(self):
        self.assertEqual(source_display("concord"), "康和")
        self.assertEqual(source_display("huanan"), "華南")
        self.assertEqual(source_display("fubon_sec"), "福邦")

    def test_ctbc_filename_token_maps_to_citic(self):
        # 中信證券（CTBC）個股報告檔名帶 -CTBC<6碼>
        self.assertEqual(parse_filename("群翊(6664,UG,B)-CTBC250724.pdf").source, "citic")


class NewBrokerMappingTests(unittest.TestCase):
    def test_masterlink_display_name(self):
        self.assertEqual(source_display("masterlink"), "元富")

    def test_hongyuan_display_name(self):
        self.assertEqual(source_display("hongyuan"), "宏遠")

    def test_yuanfu_filename_token_maps_to_masterlink(self):
        # 檔名帶「元富」應解析為 masterlink（與內文指紋一致）
        self.assertEqual(parse_filename("元富投顧0801每日股市彙報.pdf").source, "masterlink")

    def test_hongyuan_filename_token_maps_to_hongyuan(self):
        self.assertEqual(parse_filename("宏遠投顧晨報_20240625.pdf").source, "hongyuan")

    # --- 拉丁券商代碼以詞邊界比對：'MS' 不得命中 'MSCI'/'MSFT'/'EMS' 等子字串 ---
    def test_ms_substring_in_ems_not_morgan_stanley(self):
        # 「EMS」含 MS 子字串，但非 Morgan Stanley；無其他券商 → source 應為 None
        self.assertIsNone(parse_filename("中國股市-EMS產業 20240930.pdf").source)

    def test_ms_substring_in_msft_falls_through_to_cjk_token(self):
        # 「MSFT」含 MS，但檔名實帶「元富」CJK 券商 → 應解析為 masterlink（非被 MS 劫走）
        self.assertEqual(
            parse_filename("元富投顧國際重要財報簡評 0502 -- MSFT.pdf").source, "masterlink"
        )

    def test_ms_substring_in_msci_not_morgan_stanley(self):
        self.assertIsNone(parse_filename("台股-MSCI季度調整展望與預測.pdf").source)

    def test_ms_word_boundary_real_token_still_matches(self):
        # 真 Morgan Stanley 檔名（-MS- / -MS<6碼>）詞邊界仍命中
        self.assertEqual(parse_filename("5274 信驊-MS-0326.pdf").source, "morgan_stanley")
        self.assertEqual(parse_filename("金像電(2368)-MS241212.pdf").source, "morgan_stanley")

    def test_source_date_anchor_still_matches_ms(self):
        # 錨定式 -MS<8碼> 路徑不受影響
        self.assertEqual(parse_filename("鴻海(2317)-MS20240314.pdf").source, "morgan_stanley")


if __name__ == "__main__":
    unittest.main()
