# tests/test_extraction_layout_v4.py
"""版面層 v4 的四項修正，逐一用合成資料釘住（docs/EXTRACTION.md §4）：

1. 相鄰詞合併——右對齊填充的空白字元讓 pdfplumber 把「50,009.35」斷成「5」「0,009.35」。
2. 欄偵測——窄數值側欄併回標籤欄、單一英文字母不算分母、被段落連續穿過的溝槽剔除。
3. 表格體檢——殘缺與欄位不足的框線表以詞重建，重建不成退回文字流。
4. 圖區——曲線／bar 聚成的區域內丟掉刻度與圖例；側欄底圖（image）與色塊不算圖區。

全部不碰真 PDF：這些函式錯了不會拋例外、只會讓輸出「比較亂」，所以每一條都要有
一個看得出對錯的最小輸入。
"""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.extraction import layout as L  # noqa: E402


def _w(text: str, x0: float, x1: float, top: float, h: float = 7.56) -> dict:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + h}


class MergeTouchingWordsTests(unittest.TestCase):
    def test_padding_split_number_is_rejoined(self):
        """凱基投資早報 p3 實測座標：'5' x1=156.67、'0,009.35' x0=156.56（重疊 0.1pt）。"""
        words = [_w("5", 153.99, 156.67, 112.23), _w("0,009.35", 156.56, 174.7, 112.23)]
        out, merged = L._merge_touching_words(words)
        self.assertEqual([w["text"] for w in out], ["50,009.35"])
        self.assertEqual(merged, 1)
        self.assertAlmostEqual(out[0]["x1"], 174.7)

    def test_real_word_gap_is_kept(self):
        words = [_w("道瓊指數", 75, 110, 112.2), _w("50,009.35", 154, 175, 112.2)]
        out, merged = L._merge_touching_words(words)
        self.assertEqual(len(out), 2)
        self.assertEqual(merged, 0)

    def test_heavily_overlapping_objects_are_not_merged(self):
        """浮水印壓在正文上：重疊 30pt 的兩個物件不是同一個詞被切開。"""
        words = [_w("正文", 100, 120, 50), _w("浮水印", 90, 130, 50)]
        out, _ = L._merge_touching_words(words)
        self.assertEqual(len(out), 2)

    def test_kerning_overlap_within_a_point_is_merged(self):
        words = [_w("台", 100, 108, 50), _w("積電", 107.2, 124, 50)]
        out, merged = L._merge_touching_words(words)
        self.assertEqual(([w["text"] for w in out], merged), (["台積電"], 1))

    def test_other_line_is_not_merged(self):
        words = [_w("1.3", 100, 110, 50), _w("4.0", 110.2, 120, 62)]
        out, _ = L._merge_touching_words(words)
        self.assertEqual(len(out), 2)


class NumericTokenTests(unittest.TestCase):
    def test_units_and_ranges_count_as_numeric(self):
        for t in ("6M", "12M", "3.5x", "(1.5)", "28.9-42.8", "+886", "20%", "47.3", "2730-2869"):
            with self.subTest(t=t):
                self.assertTrue(L._is_numeric_token(t))

    def test_words_do_not(self):
        for t in ("FY22", "Idea", "買進", "May", "J"):
            with self.subTest(t=t):
                self.assertFalse(L._is_numeric_token(t))


class GroupLinesTests(unittest.TestCase):
    def test_tall_stray_glyph_does_not_split_the_row(self):
        """凱基側欄實測：每列前有一個 9.5pt 高的隱形符號，比 top 會把「12 個月目標價」與「47.3」拆成兩行。"""
        row = [
            _w("3", 161.8, 166, 179.97, h=9.48),
            _w("47.3", 179.3, 195, 183.89),
            _w("個月目標價", 58.2, 95, 184.64),
            _w("12", 47.5, 56, 184.73),
            _w("(US$)", 99.6, 118, 184.73),
        ]
        prev = [_w("3", 47.5, 52, 171.77), _w("個月目標價", 53.6, 90, 171.68), _w("37.2", 179.3, 195, 170.93)]
        lines = L._group_lines(prev + row)
        self.assertEqual(len(lines), 2, [[w["text"] for w in ln] for ln in lines])
        self.assertEqual([w["text"] for w in lines[1]], ["12", "個月目標價", "(US$)", "3", "47.3"])

    def test_adjacent_rows_stay_separate(self):
        rows = [_w("a", 10, 20, 100 + i * 12) for i in range(5)]
        self.assertEqual(len(L._group_lines(rows)), 5)


class PageLinesTests(unittest.TestCase):
    """先分欄再分行：不同欄字級不同時，整頁一起分行會把側欄兩列絞進主文的一行（高盛首頁實測）。"""

    def test_small_sidebar_rows_are_not_swallowed_by_big_main_text_line(self):
        main = [_w(f"m{i}", 40 + i * 30, 60 + i * 30, 322.8, h=10.0) for i in range(8)]  # 主文 10pt，x 40–290
        row1 = [
            _w("Market", 466, 488, 320.0, h=6.7), _w("cap:", 490, 503, 320.0, h=6.7),
            _w("NT$64.0tr", 506, 536, 320.0, h=6.7),
        ]
        row2 = [
            _w("Enterprise", 452, 483, 328.2, h=6.7), _w("value:", 485, 503, 328.2, h=6.7),
            _w("NT$61.3tr", 506, 536, 328.2, h=6.7),
        ]
        lines = L._page_lines(main + row1 + row2, [380.0], min_gap=6.0)
        texts = sorted(" ".join(w["text"] for w in ln) for ln in lines)
        self.assertIn("Market cap: NT$64.0tr", texts)
        self.assertIn("Enterprise value: NT$61.3tr", texts)
        self.assertEqual(len(lines), 3, texts)

    def test_cross_column_title_is_rejoined(self):
        title = [_w("跨欄大標", 100, 300, 50, h=14.0), _w("續", 304, 330, 50, h=14.0)]  # 溝槽 302，字距 4pt
        body = [_w("左", 40, 250, 100 + i * 12) for i in range(5)]
        body += [_w("右", 320, 550, 100 + i * 12) for i in range(5)]
        lines = L._page_lines(title + body, [302.0], min_gap=6.0)
        joined = [" ".join(w["text"] for w in ln) for ln in lines]
        self.assertIn("跨欄大標 續", joined)
        self.assertEqual(L._run_column(title, [302.0]), 0, "接回的大標是跨欄元素")

    def test_long_left_line_intruding_into_gutter_stays_in_its_column(self):
        run = [_w("左欄長行", 40, 305, 100)]  # x1 越過溝槽 302 三個 pt
        self.assertEqual(L._run_column(run, [302.0]), 0)
        self.assertFalse(L._spans(40, 305, [302.0], slack=6.0))
        self.assertTrue(L._spans(40, 330, [302.0], slack=6.0))
        right = [_w("右欄", 300, 400, 100)]  # x0 侵入溝槽左側 2pt，中點仍在右邊
        self.assertEqual(L._run_column(right, [302.0]), 1)


class DetectColumnsV4Tests(unittest.TestCase):
    @staticmethod
    def _plain(x0: float, x1: float, top: float) -> dict:
        return {"x0": x0, "x1": x1, "top": top, "bottom": top + 10}

    def test_axis_letters_do_not_dilute_the_numeric_share(self):
        """凱基美股個股頁：數值欄裡混著圖表座標軸的「J a n」散字，數值占比 0.69 差一點就過不了門檻。"""
        labels = [self._plain(50, 130 if i % 2 else 100, 240 + i * 14) for i in range(40)]
        values = [dict(self._plain(160, 200, 240 + i * 14), text=f"{i * 1.5:.1f}") for i in range(28)]
        letters = [dict(self._plain(165, 170, 640 + i * 6), text=ch) for i, ch in enumerate("JanMarMayJul")]
        main = [self._plain(230, 560, 230 + i * 13) for i in range(60)]
        gutters = L.detect_columns(labels + values + letters + main, 595.0, 842.0)
        self.assertEqual(len(gutters), 1, gutters)
        self.assertTrue(200 < gutters[0] < 230, gutters)

    def test_narrow_half_numeric_sidebar_merges_left(self):
        labels = [self._plain(50, 130 if i % 2 else 100, 240 + i * 14) for i in range(40)]
        values = [dict(self._plain(160, 200, 240 + i * 14), text=(f"{i}.0" if i % 2 else "6M")) for i in range(40)]
        main = [self._plain(230, 560, 230 + i * 13) for i in range(60)]
        gutters = L.detect_columns(labels + values + main, 595.0, 842.0)
        self.assertEqual(len(gutters), 1, gutters)
        self.assertTrue(200 < gutters[0] < 230, gutters)

    def test_gutter_crossed_by_prose_is_rejected(self):
        """大摩首頁：右上角只有幾行的段落在自己內部投影出一條假溝槽，段落被腰斬成兩欄。"""
        lines = []
        for i in range(12):
            top = 200 + i * 12
            # 一行連續的英文散文：詞距 3pt，其中一個詞距剛好落在 x=420 附近
            xs = [(300, 340), (343, 380), (383, 418), (421, 460), (463, 500), (503, 540)]
            lines.append([dict(self._plain(a, b, top), text="w") for a, b in xs])
        words = [w for ln in lines for w in ln]
        min_gap = 10.0
        self.assertEqual(L._reject_crossed_gutters([419.5], lines, min_gap), [])
        # 真雙欄：兩側同一 y、溝槽處留白 40pt
        two_col = [[self._plain(40, 250, 200 + i * 12), self._plain(340, 550, 200 + i * 12)] for i in range(40)]
        self.assertEqual(L._reject_crossed_gutters([295.0], two_col, min_gap), [295.0])
        del words


class TableHealthTests(unittest.TestCase):
    def test_numeric_words_beside_detects_a_missing_column(self):
        bbox = (75.0, 99.0, 244.0, 294.0)
        beside = [_w(f"{i}.0", 250, 262, 105 + i * 9) for i in range(20)]  # YTD 欄掉在框外 6pt
        far = [_w("9.9", 400, 412, 150)]  # 太遠，不算
        inside = [_w("1.3", 200, 210, 150)]
        found = L._numeric_words_beside(bbox, beside + far + inside)
        self.assertEqual(len(found), 20)

    def test_underseg_fraction(self):
        rows = (("GDP", "3.4 6.6 2.6 1.3"), ("CPI", "0.2 2.0 3.0 2.5"), ("失業率", "3.8"))
        self.assertAlmostEqual(L._underseg_cell_frac(rows), 2 / 6)
        self.assertEqual(L._underseg_cell_frac((("a", "b"),)), 0.0)

    def test_words_table_rebuilds_columns_without_splitting_numbers(self):
        words = []
        header = [("重要指數", 75), ("收盤", 150), ("漲跌", 200), ("YTD", 250)]
        for text, x in header:
            words.append(_w(text, x, x + 20, 100))
        rows = [
            ("道瓊指數", "50,009.35", "645.5", "4.0"),
            ("那斯達克", "26,270.36", "399.6", "13.0"),
            ("S&P500", "7,432.97", "79.4", "8.6"),
        ]
        for r, cells in enumerate(rows):
            top = 112 + r * 10
            for (text, x) in zip(cells, (75, 150, 200, 250)):
                words.append(_w(text, x, x + 30, top))
        table = L._words_table(words, (70.0, 95.0, 290.0, 150.0))
        self.assertIsNotNone(table)
        self.assertEqual(max(len(r) for r in table), 4)
        self.assertEqual(table[1], ("道瓊指數", "50,009.35", "645.5", "4.0"))

    def test_words_table_gives_up_on_a_single_line(self):
        words = [_w("a", 10, 20, 100), _w("b", 40, 50, 100)]
        self.assertIsNone(L._words_table(words, (0.0, 90.0, 100.0, 120.0)))


class _FakePage:
    def __init__(self, curves=(), lines=(), rects=(), images=(), width=595.0, height=842.0):
        self.width, self.height = width, height
        self.bbox = (0.0, 0.0, width, height)
        self.curves, self.lines, self.rects, self.images = list(curves), list(lines), list(rects), list(images)


def _prim(x0, top, x1, bottom) -> dict:
    return {"x0": x0, "top": top, "x1": x1, "bottom": bottom}


class FigureRegionTests(unittest.TestCase):
    def test_polyline_segments_form_a_region(self):
        segs = [_prim(100 + i * 8, 500 + (i % 3) * 15, 108 + i * 8, 508 + (i % 3) * 15) for i in range(20)]
        regions = L._figure_regions(_FakePage(curves=segs), [])
        self.assertEqual(len(regions), 1)
        x0, top, x1, bottom = regions[0]
        self.assertTrue(x0 <= 100 and x1 >= 260, regions)

    def test_background_image_is_not_a_region(self):
        """凱基美股個股頁：整個側欄墊著一張點陣圖，把它當圖區會丟掉目標價那一整排。"""
        regions = L._figure_regions(_FakePage(images=[_prim(42, 146, 200, 450)]), [])
        self.assertEqual(regions, [])

    def test_wide_colour_blocks_are_not_a_region(self):
        blocks = [_prim(42, 146 + i * 60, 300, 170 + i * 60) for i in range(10)]  # 側欄標題色塊：寬 258pt
        self.assertEqual(L._figure_regions(_FakePage(curves=blocks), []), [])

    def test_off_page_clip_paths_are_ignored(self):
        segs = [_prim(-189 + i * 3, 146, -180 + i * 3, 150) for i in range(20)]
        self.assertEqual(L._figure_regions(_FakePage(curves=segs), []), [])

    def test_bars_with_varying_heights_form_a_region_but_uniform_row_shading_does_not(self):
        bars = [_prim(100 + i * 15, 600 - i * 12, 110 + i * 15, 650) for i in range(8)]
        self.assertEqual(len(L._figure_regions(_FakePage(rects=bars), [])), 1)
        shading = [_prim(100, 300 + i * 12, 118, 310 + i * 12) for i in range(8)]  # 窄欄逐列底色，等高
        self.assertEqual(L._figure_regions(_FakePage(rects=shading), []), [])

    def test_primitives_inside_a_detected_table_are_excluded(self):
        segs = [_prim(100 + i * 8, 500, 108 + i * 8, 505) for i in range(20)]
        self.assertEqual(L._figure_regions(_FakePage(curves=segs), [(90.0, 490.0, 300.0, 520.0)]), [])

    def test_chart_noise_words(self):
        cases = (
            ("50", True), ("Nov-24", True), ("女裝", True), ("FY22", True),
            ("資料來源：公司資料；凱基投顧", False),
        )
        for t, noise in cases:
            with self.subTest(t=t):
                self.assertEqual(L._is_chart_noise({"text": t}), noise)


if __name__ == "__main__":
    unittest.main()
