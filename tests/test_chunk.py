# tests/test_chunk.py
"""`app/services/chunk.py`：段落聚合 + 滑動視窗 + overlap 合併。

**為什麼這支測試存在**：`chunk.py` 先前只被 `test_reading_anchor.py` 當 fixture
產生器用（import `chunk_text` 造出待錨定的文字），它自己的不變量一條都沒被釘住。

而那個 overlap 合併正是本專案最有名的陷阱的成因——`f"{tail}{cur}"` 把前一塊的末
`CHUNK_OVERLAP` 字元複製到下一塊的頭，所以：

    **`report_chunk.content` 不是 `full_text` 的子字串**

400 樣本實測（記錄在 CLAUDE.md 與 `reading/anchor.py`）：raw 3/300、normalized
168/400、normalized ＋ head-drop 400/400。更糟的是拿原始 `full_text` 去錨定**不會**
回 None——它會回一個看起來很合理但座標系錯誤的 Anchor。

實測數字寫在文件裡，但**沒有任何東西阻止有人把 `f"{tail}{cur}"` 改掉**。這支測試
就是那道牆：改了合併方式，這裡會紅，而不是等到閱讀頁的引文跳到錯的位置。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.chunk import CHUNK_OVERLAP, CHUNK_SIZE, chunk_text  # noqa: E402


class DefaultsTests(unittest.TestCase):
    """兩個常數是跨模組契約：改了就得重跑全語料 ingest（數十小時 CPU）。"""

    def test_pinned(self):
        self.assertEqual(CHUNK_SIZE, 600)
        self.assertEqual(CHUNK_OVERLAP, 80)

    def test_overlap_smaller_than_size(self):
        """滑動視窗的步進是 `size - overlap`；相等或更大會讓 while 迴圈永不前進。"""
        self.assertLess(CHUNK_OVERLAP, CHUNK_SIZE)


class EmptyAndTrivialTests(unittest.TestCase):
    def test_empty_inputs_return_empty_list(self):
        for raw in ("", "   ", "\n\n\t\n"):
            with self.subTest(raw=repr(raw)):
                self.assertEqual(chunk_text(raw), [])

    def test_single_short_paragraph_is_one_chunk_unchanged(self):
        self.assertEqual(chunk_text("台積電先進封裝產能吃緊。"), ["台積電先進封裝產能吃緊。"])


class ParagraphAggregationTests(unittest.TestCase):
    def test_short_paragraphs_merge_with_single_newline(self):
        """段落之間用**單一換行**接起來——不是空行，也不是空格。

        這件事有兩個下游後果：`make normalize` 那個陷阱（`clean_text` 把換行折成
        空格 ⇒ 實測 95.95%／98.66% 的 chunk 被改動）；以及 `clean_extracted` 也不行
        （`textnorm._RE_CJK_GAP` 的 `(?<=[CJK])\\s+(?=[CJK])` 同樣吃掉這個換行，
        實測仍破壞 51%）。所以這條測試同時是那兩條「刻意不做」的守門。
        """
        out = chunk_text("第一段。\n\n第二段。", size=600, overlap=0)
        self.assertEqual(out, ["第一段。\n第二段。"])

    def test_aggregation_stops_before_exceeding_size(self):
        a, b = "甲" * 50, "乙" * 50
        out = chunk_text(f"{a}\n\n{b}", size=60, overlap=0)
        self.assertEqual(out, [a, b], "兩段合起來超過 size，不可硬塞成一塊")

    def test_blank_line_variants_all_count_as_paragraph_breaks(self):
        for sep in ("\n\n", "\n \n", "\n\t\n", "\n\n\n"):
            with self.subTest(sep=repr(sep)):
                self.assertEqual(chunk_text(f"甲。{sep}乙。", size=600, overlap=0),
                                 ["甲。\n乙。"])


class OversizeParagraphTests(unittest.TestCase):
    """超長單一段落走滑動視窗——PDF 抽字常產出整頁無空行的巨型段落。"""

    def test_window_step_is_size_minus_overlap(self):
        para = "".join(str(i % 10) for i in range(250))
        out = chunk_text(para, size=100, overlap=20)
        # 步進 80：起點 0, 80, 160, 240
        self.assertEqual(len(out), 4)
        self.assertTrue(out[0].startswith("0123456789"))

    def test_every_character_is_covered(self):
        """視窗不得漏字——漏掉的內容從此完全搜不到，且沒有任何症狀。"""
        para = "".join(chr(ord("a") + i % 26) for i in range(500))
        out = chunk_text(para, size=100, overlap=0)
        self.assertEqual("".join(out), para)

    def test_buffered_text_is_flushed_before_the_oversize_paragraph(self):
        """短段落緩衝要先送出，否則它會被排到超長段落之後、順序錯亂。"""
        out = chunk_text("短段。\n\n" + "長" * 300, size=100, overlap=0)
        self.assertEqual(out[0], "短段。")


class OverlapMergeTests(unittest.TestCase):
    """**`content` 不是 `full_text` 子字串**的成因就在這裡。"""

    def test_each_chunk_after_the_first_is_prefixed_with_previous_tail(self):
        a, b = "甲" * 50, "乙" * 50
        out = chunk_text(f"{a}\n\n{b}", size=60, overlap=10)
        self.assertEqual(out[0], a)
        self.assertEqual(out[1], "甲" * 10 + b, "第二塊的頭應是第一塊的末 10 字")

    def test_first_chunk_is_never_prefixed(self):
        out = chunk_text("甲" * 50 + "\n\n" + "乙" * 50, size=60, overlap=10)
        self.assertFalse(out[0].startswith("乙"))

    def test_overlap_zero_disables_merging(self):
        a, b = "甲" * 50, "乙" * 50
        self.assertEqual(chunk_text(f"{a}\n\n{b}", size=60, overlap=0), [a, b])

    def test_single_chunk_is_not_merged(self):
        self.assertEqual(chunk_text("只有一段。", overlap=80), ["只有一段。"])

    def test_naive_substring_search_fails_by_design(self):
        """把「陷阱本身」寫成測試：直接 `full_text.find(chunk)` 必須失敗。

        這條刻意斷言**失敗**，因為它是 `reading/anchor.py` 存在的全部理由。若哪天
        它變成通過，代表 overlap 合併被移除了——那時 `anchor.py` 的 head-drop 補償
        就會變成**多減一段**，錨點整體偏移，而閱讀頁不會報錯、只會跳到錯的位置。
        """
        full_text = "甲" * 50 + "\n\n" + "乙" * 50
        chunks = chunk_text(full_text, size=60, overlap=10)
        self.assertEqual(
            full_text.find(chunks[1]), -1,
            "第二塊竟然是原文的子字串——overlap 合併可能被移除了，"
            "請同時檢查 app/services/reading/anchor.py 的 head-drop 補償",
        )

    def test_head_drop_recovers_the_original_substring(self):
        """扣掉複製過來的頭之後就找得到——這正是 anchor.py 的補償邏輯。"""
        full_text = "甲" * 50 + "\n\n" + "乙" * 50
        chunks = chunk_text(full_text, size=60, overlap=10)
        self.assertGreaterEqual(full_text.find(chunks[1][10:]), 0)


class StripAndFilterTests(unittest.TestCase):
    def test_result_has_no_leading_or_trailing_whitespace(self):
        out = chunk_text("  甲。  \n\n  乙。  ", size=600, overlap=0)
        for c in out:
            with self.subTest(chunk=c):
                self.assertEqual(c, c.strip())

    def test_no_empty_chunks_survive(self):
        out = chunk_text("甲。\n\n   \n\n乙。", size=600, overlap=0)
        self.assertTrue(all(c for c in out))


class RealisticShapeTests(unittest.TestCase):
    """用貼近真實研報的形狀跑一遍，確認預設參數下沒有退化。"""

    def test_mixed_paragraph_lengths_with_production_defaults(self):
        paras = [
            "投資結論：維持買進評等，目標價上調至 1200 元。",
            "營運展望。" * 120,   # 超長段落 → 走滑動視窗
            "風險：先進封裝良率不如預期。",
        ]
        out = chunk_text("\n\n".join(paras))
        self.assertGreater(len(out), 1)
        for c in out:
            with self.subTest(chunk=c[:20]):
                # 每塊上界＝size + overlap（合併會把前一塊的尾接上來）
                self.assertLessEqual(len(c), CHUNK_SIZE + CHUNK_OVERLAP)


if __name__ == "__main__":
    unittest.main()
