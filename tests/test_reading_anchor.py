"""錨點模組測試。

**本檔的重心是 `TestLocateChunkAgainstRealChunker`** —— 它用真的 `chunk_text()`
產生 chunk、再要求 `locate_chunk` 全數錨回，把「chunk 不是全文子字串、丟掉
overlap 尾巴才找得到」這個實測事實釘死。任何人動了 chunk.py 的 overlap 邏輯
或 anchor.py 的探針策略，這裡就會紅。
"""

from __future__ import annotations

import unittest

from app.services.chunk import CHUNK_OVERLAP, chunk_text
from app.services.reading.anchor import locate_chunk, locate_quote
from app.services.textnorm import (
    clean_extracted,
    norm_for_match,
    norm_for_match_with_map,
)

# 仿真實券商研報：CJK 間空白、破碎換行、重複的頁首樣板、段落邊界。
# 需夠長（清理後 > CHUNK_SIZE 600）才會切出多塊、才有 overlap 尾巴可測。
RAW_REPORT = """本刊載之報告為元富投顧於特定日期之分析 ， 已力求陳述內容之可靠性 。

個 股 報 告
中鋼 (2002)

投資結論
盤 價 調 漲 加 上 原 料 成 本 降 低 有 利 價 差 好 轉 ， 不 過 考 量 鋼 鐵 產 業
與 營 運 仍 在 調 整 ， 維 持 中 鋼 中 立 建 議 ， 目 標 價 24 元 。

營運概況
2026 年第二季合併營收 為 新 台 幣 892 億 元 ， 季 增 3.1% ， 年 減 1.4% 。
毛 利 率 4.2% ， 較 前 季 提 升 0.8 個 百 分 點 ， 主 要 反 映 原 料 煤 與 鐵 礦 砂
採 購 成 本 下 滑 。 稅 後 淨 利 21.4 億 元 ， 每 股 盈 餘 0.14 元 。

產 品 結 構 方 面 ， 熱 軋 佔 比 38% 、 冷 軋 22% 、 鋼 板 14% 、 線 材 11% ，
其 餘 為 電 磁 鋼 片 與 其 他 產 品 。 內 外 銷 比 重 約 為 七 三 開 ， 內 銷 仍 為
主 要 獲 利 來 源 。

產業展望
中 國 減 產 政 策 若 落 實 ， 有 機 會 帶 動 亞 洲 盤 價 落 底 回 升 。
惟 需 求 端 仍 疲 弱 ， 短 期 不 宜 過 度 樂 觀 。 東 南 亞 新 增 產 能 於 2026
下 半 年 陸 續 開 出 ， 將 對 區 域 供 需 造 成 壓 力 。

國 內 部 分 ， 公 共 工 程 與 綠 能 建 設 帶 動 鋼 板 與 型 鋼 需 求 ， 惟 建 築
用 鋼 受 房 市 交 易 量 萎 縮 影 響 而 轉 弱 。 整 體 而 言 ， 內 需 呈 現 結 構
性 分 化 。

盤價政策
公 司 針 對 第 三 季 內 銷 盤 價 採 平 盤 開 出 ， 部 分 品 項 微 幅 調 漲 ，
反 映 成 本 與 市 場 供 需 的 平 衡 考 量 。 管 理 層 表 示 ， 後 續 將 視 中 國
出 口 報 價 與 國 內 需 求 動 態 調 整 。

成本結構
原 料 煤 採 購 成 本 較 前 季 下 滑 約 12% ， 鐵 礦 砂 則 因 巴 西 出 貨 回 穩 而
微 幅 走 低 。 公 司 表 示 ， 第 三 季 原 料 成 本 仍 有 下 降 空 間 ， 惟 幅 度
將 較 第 二 季 收 斂 。 電 價 調 漲 使 單 位 製 造 成 本 增 加 約 0.3 個 百 分 點 。

轉投資與其他
中 鴻 、 中 龍 等 轉 投 資 事 業 於 本 季 合 計 貢 獻 業 外 收 益 3.2 億 元 ，
較 前 季 由 虧 轉 盈 。 中 鋼 構 受 惠 於 離 岸 風 電 水 下 基 礎 訂 單 ， 在 手
訂 單 能 見 度 延 伸 至 2027 年 上 半 年 。

資本支出與股利
2026 年 資 本 支 出 預 算 為 180 億 元 ， 主 要 投 入 高 爐 整 修 與 節 能 減 碳
設 備 。 公 司 維 持 過 往 股 利 政 策 ， 惟 考 量 獲 利 水 準 ， 現 金 股 利 預 估
將 較 前 一 年 度 縮 減 。

評價
以 2026 年 每 股 淨 值 推 估 ， 目 前 股 價 對 應 股 價 淨 值 比 約 0.78 倍 ，
位 於 近 五 年 區 間 下 緣 。 惟 獲 利 能 力 尚 未 明 確 改 善 ， 評 價 折 價
反 映 基 本 面 疑 慮 ， 暫 不 具 重 評 價 條 件 。

風險
下 行 風 險 為 中 國 出 口 持 續 高 檔 、 內 需 復 甦 不 如 預 期 、 以 及 原 料
成 本 反 彈 侵 蝕 價 差 。 上 行 風 險 則 來 自 中 國 實 質 減 產 超 乎 預 期 。

本刊載之報告為元富投顧於特定日期之分析 ， 已力求陳述內容之可靠性 。
"""


class TestNormForMatchWithMap(unittest.TestCase):
    def test_agrees_with_norm_for_match(self):
        for s in [RAW_REPORT, "台 積 電", "ＡＢＣ　abc", "", "  \n\t "]:
            built = norm_for_match_with_map(s)
            self.assertIsNotNone(built, f"不應放棄: {s!r}")
            norm, idx = built
            self.assertEqual(norm, norm_for_match(s))
            self.assertEqual(len(norm), len(idx))

    def test_map_points_back_at_source_chars(self):
        s = "台 積 電 2330"
        norm, idx = norm_for_match_with_map(s)
        # 每個正規化字元都必須能從原字串對應位置推回來
        for i, c in enumerate(norm):
            src = s[idx[i]]
            self.assertEqual(norm_for_match(src), c)

    def test_map_is_monotonic(self):
        _, idx = norm_for_match_with_map(RAW_REPORT)
        self.assertEqual(idx, sorted(idx))

    def test_fullwidth_normalizes_and_maps(self):
        norm, idx = norm_for_match_with_map("ＡＢ")
        self.assertEqual(norm, "ab")
        self.assertEqual(idx, [0, 1])


class TestLocateQuote(unittest.TestCase):
    def setUp(self):
        self.text = clean_extracted(RAW_REPORT)

    def test_exact_hit(self):
        # 注意「目標價 24 元」的空白：clean_extracted 只清 CJK 之間的空白，
        # 「價 24」有一邊不是 CJK 故空白留著 —— 正典文字就長這樣。
        quote = "維持中鋼中立建議，目標價 24 元。"
        a = locate_quote(self.text, quote)
        self.assertIsNotNone(a)
        self.assertEqual(a.method, "exact")
        self.assertEqual(self.text[a.start : a.end], quote)

    def test_normalized_hit_absorbs_whitespace_drift(self):
        # LLM 常「順手」增減空白／改全形 —— 正規化層要吸收掉。
        # 這裡刻意把空白拿掉、逗號改全形，模擬 LLM 抄寫漂移。
        a = locate_quote(self.text, "維持中鋼中立建議，目標價24元。")
        self.assertIsNotNone(a)
        self.assertEqual(a.method, "normalized")
        self.assertIn("維持中鋼中立建議", self.text[a.start : a.end])

    def test_prefix_fallback_when_tail_rewritten(self):
        # 開頭逐字、尾巴被 LLM 改寫 → 前綴退讓仍應錨到開頭
        quote = "中國減產政策若落實，有機會帶動亞洲盤價落底回升，這對中鋼是重大利多"
        a = locate_quote(self.text, quote)
        self.assertIsNotNone(a)
        self.assertEqual(a.method, "prefix")
        self.assertTrue(self.text[a.start : a.end].startswith("中國減產政策若落實"))

    def test_not_found_returns_none(self):
        self.assertIsNone(locate_quote(self.text, "台積電先進封裝產能大幅擴張"))

    def test_ambiguous_quote_returns_none(self):
        # 免責樣板在頭尾各出現一次 → 無法裁決，寧缺勿錯
        self.assertIsNone(
            locate_quote(self.text, "本刊載之報告為元富投顧於特定日期之分析")
        )

    def test_too_short_quote_returns_none(self):
        self.assertIsNone(locate_quote(self.text, "中鋼"))

    def test_empty_inputs(self):
        self.assertIsNone(locate_quote("", "abc"))
        self.assertIsNone(locate_quote(self.text, ""))


class TestLocateChunkAgainstRealChunker(unittest.TestCase):
    """用真的 chunk_text() 產 chunk，要求全數錨回正典文字。

    這是本功能的地基測試：它同時證明「chunk 不是全文的子字串」與
    「丟掉 overlap 尾巴後找得到」。
    """

    def setUp(self):
        self.canonical = clean_extracted(RAW_REPORT)
        self.chunks = chunk_text(self.canonical)

    def test_chunker_produced_chunks(self):
        self.assertGreater(len(self.chunks), 1, "測資需大到會產生多塊才有 overlap")

    def test_naive_find_fails_for_overlapped_chunks(self):
        """釘住問題本身：天真的 find() 對帶 overlap 尾巴的塊會失敗。

        若這條變綠（find 全中），代表 chunk.py 的 overlap 行為改了 —— 屆時
        anchor.py 的丟尾巴策略也要重新評估，不要只是把這條測試刪掉。
        """
        misses = [c for c in self.chunks if self.canonical.find(c) == -1]
        self.assertTrue(
            misses, "預期至少一塊無法用天真 find 定位（overlap 尾巴造成）"
        )

    def test_locate_chunk_finds_every_chunk(self):
        for i, c in enumerate(self.chunks):
            with self.subTest(chunk=i):
                a = locate_chunk(self.canonical, c)
                self.assertIsNotNone(a, f"第 {i} 塊錨不到")
                self.assertGreaterEqual(a.start, 0)
                self.assertLessEqual(a.end, len(self.canonical))
                self.assertGreater(a.end, a.start)

    def test_anchor_span_overlaps_chunk_body(self):
        """錨到的區間必須真的落在該塊的正文上（而不是隨便一段）。"""
        for i, c in enumerate(self.chunks):
            with self.subTest(chunk=i):
                a = locate_chunk(self.canonical, c)
                span_norm = norm_for_match(self.canonical[a.start : a.end])
                chunk_norm = norm_for_match(c)
                self.assertIn(span_norm, chunk_norm)

    def test_first_chunk_has_no_injected_tail(self):
        # 第一塊不帶 overlap 尾巴，應可直接定位
        a = locate_chunk(self.canonical, self.chunks[0])
        self.assertIsNotNone(a)

    def test_short_chunk_falls_back_to_whole(self):
        # 比 overlap 還短的塊不可被切光
        a = locate_chunk(self.canonical, "營運概況", overlap=CHUNK_OVERLAP)
        self.assertIsNotNone(a)
        self.assertEqual(self.canonical[a.start : a.end], "營運概況")

    def test_empty_inputs(self):
        self.assertIsNone(locate_chunk("", "abc"))
        self.assertIsNone(locate_chunk(self.canonical, ""))


class TestCanonicalTextInvariant(unittest.TestCase):
    """釘住「正典文字＝clean_extracted(full_text)」這個不變量。"""

    def test_clean_extracted_removes_cjk_gaps_but_keeps_paragraphs(self):
        out = clean_extracted(RAW_REPORT)
        self.assertIn("維持中鋼中立建議", out)  # CJK 間空白已清
        self.assertNotIn("維 持 中 鋼", out)
        self.assertIn("\n\n", out)  # 段落邊界保留（chunk_text 依此切段）

    def test_clean_extracted_is_idempotent(self):
        once = clean_extracted(RAW_REPORT)
        self.assertEqual(once, clean_extracted(once))

    def test_raw_full_text_is_not_readable(self):
        """證明為什麼不能直接把 full_text 丟上頁面。"""
        self.assertIn("維 持 中 鋼 中 立 建 議", RAW_REPORT)
        self.assertNotIn("維持中鋼中立建議", RAW_REPORT)


if __name__ == "__main__":
    unittest.main()
