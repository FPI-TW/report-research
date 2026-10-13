"""簡體→繁體字形正規化（app/services/zh_hant.py）。

測資全部取自全語料實際命中的字串（2026-07-31 掃描），不是造出來的例子——
這支模組的整個設計就是靠那批實測分出「真簡體」與「專有名詞」的，測試用同一批
資料才守得住。
"""

import unittest

from app.services.zh_hant import (
    MIN_SIMPLIFIED_CHARS,
    MIN_SIMPLIFIED_RATIO,
    count_han,
    count_simplified,
    is_simplified_only,
    looks_simplified,
    lookup_key,
    to_traditional,
)


class TestSimplifiedOnlyDetection(unittest.TestCase):
    """判別器：只認「不是繁體字」的字。"""

    def test_true_simplified_chars(self):
        for ch in "装测试进业务潜华为电脑软龙国际关键预计联营创历":
            self.assertTrue(is_simplified_only(ch), f"{ch} 應判為簡體字")

    def test_known_false_negatives(self):
        """已知漏網：Big5 收了「与」「厂」這類罕用繁體字形，於是它們不算簡體。

        漏判只會讓字串維持原樣（＝現況），不會改壞東西，所以刻意接受。
        釘住是為了讓下一個人知道這是已知的、不是沒想到。
        """
        for ch in "与厂么":
            self.assertFalse(is_simplified_only(ch), ch)

    def test_traditional_chars_that_opencc_would_touch(self):
        """這些字 opencc 會改（台→臺、干→幹、周→週…），但它們本身就是繁體字。

        判別器若誤收這一組，就會把「船期干擾」改成「船期幹擾」——把對的改成錯的。
        全語料實測：直接拿 s2tw 掃，10,326 篇摘要有 7,944 篇「命中」，絕大多數是
        這一類假陽性。
        """
        for ch in "台余里后干占布周面系松板表制只采谷划志么厂丰":
            self.assertFalse(is_simplified_only(ch), f"{ch} 是繁體字，不應判為簡體")

    def test_non_cjk_never_flagged(self):
        for ch in "AZ09%（），。 ":
            self.assertFalse(is_simplified_only(ch), repr(ch))

    def test_count_and_threshold(self):
        self.assertEqual(count_simplified("恒耀：2H25營運持續調整"), 1)
        self.assertFalse(looks_simplified("恒耀：2H25營運持續調整"))
        self.assertGreaterEqual(count_simplified("群联电子2026年6月营收公告"), MIN_SIMPLIFIED_CHARS)
        self.assertTrue(looks_simplified("群联电子2026年6月营收公告"))


class TestTraditionalTextUntouched(unittest.TestCase):
    """已經是繁體的字串必須一字不動——這是本模組最重要的性質。"""

    CASES = [
        "台股力拚跌深反彈行情，展覽題材以不變應萬變",
        "船期干擾導致營收下滑，市占率與布局",          # 干/占/布 都是繁體字
        "台股本周前四個交易日橫盤整理",                  # 周 是繁體字
        "新台幣90元，台積電、台達電、日月光投控",
        "高通認為市場對Android手機需求觸底的看法過於樂觀，並持續面臨市占率流失壓力。",
        "2Q26每股盈餘為新台幣1.35元，明顯低於Citi與彭博一致預期。",
        "",
    ]

    def test_unchanged(self):
        for s in self.CASES:
            self.assertEqual(to_traditional(s), s, s)


class TestProperNounsBelowThreshold(unittest.TestCase):
    """單一簡體字＝券商自己的寫法，不動。

    恒耀（8349）在 49 篇原文寫「恒」、0 篇寫「恆」；新美齊建案「画世代」4 篇原文
    全寫「画」。opencc 會把它們改成恆耀／畫世代，那是改壞專有名詞。
    """

    CASES = [
        "恒耀：2H25營運持續調整，靜待2026年復甦",
        "新美齊法說會重點摘要：画世代待交屋 91 戶、儲備建案延至 2027 年公開銷售",
        "台股重挫逾700點跌破月線，資金持續外流；三檔個股（中華、裕隆、恒耀）與工商銀行點評",
    ]

    def test_unchanged(self):
        for s in self.CASES:
            self.assertEqual(to_traditional(s), s, s)

    def test_each_case_has_exactly_one_simplified_char(self):
        """釘住「門檻＝2」擋住的正是這些：各只有 1 個簡體字。"""
        for s in self.CASES:
            self.assertEqual(count_simplified(s), 1, s)

    def test_same_char_converts_inside_genuinely_simplified_text(self):
        """同一個「恒」在真的簡體句子裡照樣會被轉——門檻是唯一的分界，不是白名單。

        這條把取捨講白：保住專有名詞的代價，就是它出現在簡體文字裡時也會被改。
        """
        out = to_traditional("恒耀营收创同期新高，产能扩充进度符合预期")
        self.assertIn("恆耀", out)


class TestLongDocumentDensityGate(unittest.TestCase):
    """長文件另靠密度把關——字數門檻是拿標題／摘錄校準的，放到研報上形同虛設。"""

    TRAD_PARAGRAPH = (
        "本報告針對台灣半導體供應鏈的產能配置與資本支出節奏進行分析，"
        "並就先進封裝、測試設備與載板三個環節的供需缺口提出觀察。"
    )

    def _long_traditional_report(self) -> str:
        """幾千字的純繁體研報，中間提到兩次「恒耀」（券商寫法）。"""
        body = "\n\n".join(self.TRAD_PARAGRAPH for _ in range(20))
        return f"{body}\n\n個股方面，恒耀營運持續調整；恒耀的目標價維持不變。"

    def test_two_proper_nouns_in_a_long_report_do_not_trigger(self):
        doc = self._long_traditional_report()
        self.assertGreaterEqual(count_simplified(doc), MIN_SIMPLIFIED_CHARS)  # 字數門檻擋不住
        self.assertLess(count_simplified(doc) / count_han(doc), MIN_SIMPLIFIED_RATIO)
        self.assertEqual(to_traditional(doc), doc)  # 密度門檻擋住了

    def test_reversal_without_density_gate_the_long_report_would_be_rewritten(self):
        """反轉實驗：只用字數門檻，上面那份純繁體研報會被整份改寫。"""
        doc = self._long_traditional_report()
        self.assertTrue(count_simplified(doc) >= MIN_SIMPLIFIED_CHARS)  # 舊規則會放行
        self.assertFalse(looks_simplified(doc))                          # 新規則不放行

    def test_a_genuinely_simplified_report_still_converts(self):
        doc = "\n\n".join([
            "本报告分析台湾半导体供应链的产能配置与资本支出节奏。",
            "先进封装、测试设备与载板三个环节的供需缺口持续扩大。",
            "个股方面，群联电子营收创同期新高，产能扩充进度符合预期。",
        ] * 8)
        self.assertTrue(looks_simplified(doc))
        out = to_traditional(doc)
        self.assertIn("本報告分析台灣半導體供應鏈", out)
        self.assertIn("群聯電子營收創同期新高", out)
        self.assertNotIn("报告", out)


class TestSimplifiedTextConverted(unittest.TestCase):
    """真正的簡體字串要整串轉成台灣標準字形。"""

    def test_the_headline_that_started_this(self):
        """2026-07-31 的台股頭條（title_source=translated，模型翻成了簡體）。"""
        self.assertEqual(
            to_traditional("封装测试进展如期，2027年LEAP业务有望翻倍，年化每股盈余潜力超170元"),
            "封裝測試進展如期，2027年LEAP業務有望翻倍，年化每股盈餘潛力超170元",
        )

    def test_phrase_table_resolves_ambiguous_chars(self):
        """一對多的字要靠詞組表，逐字轉會挑錯：盈余→盈餘（不是留著「余」）。

        「余」「后」本身是繁體字，判別器不會標記它們；是整串達門檻後由詞組表
        一起解掉的——這正是「整串轉、不逐字轉」的理由。
        """
        self.assertIn("盈餘", to_traditional("每股盈余与营业收入"))
        self.assertIn("後天", to_traditional("后天发布财报"))

    def test_taiwan_variants_not_opencc_standard(self):
        """s2tw 而不是 s2t：群聯不是羣聯、為不是爲、裡不是裏。"""
        out = to_traditional("群联认为里面的营收结构")
        self.assertIn("群聯", out)
        self.assertNotIn("羣", out)
        self.assertIn("為", out)
        self.assertNotIn("爲", out)
        self.assertNotIn("裏", out)

    def test_tai_stays_as_corpus_convention(self):
        """轉完把「臺」收斂回「台」：語料標題含「台」1,068 篇、含「臺」2 篇。"""
        out = to_traditional("台积电与新台币汇率")
        self.assertEqual(out, "台積電與新台幣匯率")
        self.assertNotIn("臺", out)

    def test_idempotent(self):
        for s in ["封装测试进展如期，2027年LEAP业务有望翻倍", "群联电子2026年6月营收公告"]:
            once = to_traditional(s)
            self.assertEqual(to_traditional(once), once, s)


class TestWriteSiteWiring(unittest.TestCase):
    """四個寫入點確實接上了——只測純函式，不碰 DB 也不呼叫 LLM。"""

    def test_title_parser_converts(self):
        from scripts.generate_titles import parse_title

        r = parse_title('{"title": "群联电子2026年6月营收公告", "title_source": "extracted"}')
        self.assertEqual(r.title, "群聯電子2026年6月營收公告")

    def test_title_original_kept_verbatim(self):
        """title_original 是原文，刻意不轉。"""
        from scripts.generate_titles import parse_title

        r = parse_title(
            '{"title": "封装测试进展如期，业务有望翻倍", '
            '"title_original": "Packaging Test On Track", "title_source": "translated"}'
        )
        self.assertEqual(r.title, "封裝測試進展如期，業務有望翻倍")
        self.assertEqual(r.title_original, "Packaging Test On Track")

    def test_summary_parser_converts(self):
        from scripts.generate_summaries import parse_summary

        self.assertEqual(
            parse_summary('{"summary": "群联电子公布营收"}'), "群聯電子公佈營收"
        )

    def test_takeaway_claim_converts_but_quote_does_not(self):
        """claim 轉、quote 不轉——quote 是 locate_quote 的錨定基準。"""
        from scripts.extract_takeaways import parse_takeaways

        parsed = parse_takeaways(
            '{"takeaways": [{"claim": "群联营收创同期新高", "quote": "群联营收创同期新高"}]}'
        )
        self.assertTrue(parsed.ok)
        self.assertEqual(parsed.takeaways[0]["claim"], "群聯營收創同期新高")
        self.assertEqual(parsed.takeaways[0]["quote"], "群联营收创同期新高")

    def test_signal_thesis_summary_converts_but_evidence_does_not(self):
        from app.services.signal_extract import _normalize_thesis

        out, _ = _normalize_thesis(
            {
                "outlook": {
                    "stance": "positive",
                    "summary": "群联营收创同期新高",
                    "evidence": "群联营收创同期新高",
                }
            }
        )
        self.assertEqual(out["outlook"]["summary"], "群聯營收創同期新高")
        self.assertEqual(out["outlook"]["evidence"], "群联营收创同期新高")



class TestLookupKey(unittest.TestCase):
    """查表鍵（遷移 PR-15）：評等詞只有 1 個簡體字也要轉，但純繁體一個字都不動。"""

    def test_single_simplified_char_converts(self):
        """「买入」只有 1 個簡體字：`to_traditional` 的門檻刻意不轉它，查表鍵要轉。"""
        self.assertEqual(to_traditional("买入"), "买入")
        self.assertEqual(lookup_key("买入"), "買入")
        self.assertEqual(lookup_key("减持"), "減持")
        self.assertEqual(lookup_key("人民币"), "人民幣")

    def test_pure_traditional_untouched(self):
        """沒有簡體字就不過 opencc：台→臺、占→佔那一類改動不會發生，既有查表結果逐字不變。"""
        for s in ("區間操作", "優於大盤", "占比提升", "船期干擾", "Buy", ""):
            self.assertEqual(lookup_key(s), s)

    def test_tai_folded_back(self):
        self.assertEqual(lookup_key("台湾"), "台灣")


if __name__ == "__main__":
    unittest.main()
