import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.chart import render_chart_svg  # noqa: E402


class RenderChartSvgTests(unittest.TestCase):
    def test_bar_single_series_has_three_bars(self):
        spec = {
            "type": "bar", "title": "各廠營收（億元）",
            "x": ["新應材", "台特化", "中砂"],
            "series": [{"name": "營收", "values": [120, 86, 54]}],
            "unit": "億元", "source": "[3]",
        }
        svg = render_chart_svg(spec)
        self.assertTrue(svg.startswith("<svg"))
        self.assertEqual(svg.count("<rect"), 3)  # 單序列：3 長條、無圖例 rect
        self.assertIn("各廠營收", svg)

    def test_line_has_polyline_and_points(self):
        spec = {
            "type": "line", "title": "毛利率趨勢",
            "x": ["Q1", "Q2", "Q3", "Q4"],
            "series": [{"name": "毛利率", "values": [40, 42, 45, 47]}],
        }
        svg = render_chart_svg(spec)
        self.assertIn("<polyline", svg)
        self.assertEqual(svg.count("<circle"), 4)

    def test_pie_has_three_slices(self):
        spec = {
            "type": "pie", "title": "市佔",
            "x": ["A", "B", "C"],
            "series": [{"name": "市佔", "values": [50, 30, 20]}],
        }
        svg = render_chart_svg(spec)
        self.assertEqual(svg.count("<path"), 3)

    def test_multi_series_bar(self):
        spec = {
            "type": "bar", "title": "分產品線營收",
            "x": ["Q1", "Q2"],
            "series": [
                {"name": "A", "values": [10, 12]},
                {"name": "B", "values": [5, 7]},
            ],
        }
        svg = render_chart_svg(spec)
        # 2 序列 × 2 類別 = 4 長條 rect + 2 圖例 rect
        self.assertEqual(svg.count("<rect"), 6)

    def test_invalid_specs_return_empty(self):
        self.assertEqual(render_chart_svg({"type": "scatter", "x": ["a"], "series": [{"values": [1]}]}), "")
        self.assertEqual(render_chart_svg({"type": "bar", "x": [], "series": [{"values": [1]}]}), "")
        self.assertEqual(render_chart_svg({"type": "bar", "x": ["a"], "series": []}), "")
        self.assertEqual(render_chart_svg({"type": "bar", "x": ["a"], "series": [{"values": ["x"]}]}), "")
        self.assertEqual(render_chart_svg("not a dict"), "")

    def test_bar_negative_values_no_invalid_height(self):
        spec = {"type": "bar", "title": "YoY 變動",
                "x": ["Q1", "Q2", "Q3"],
                "series": [{"name": "YoY", "values": [10, -5, 8]}]}
        svg = render_chart_svg(spec)
        self.assertTrue(svg.startswith("<svg"))
        self.assertEqual(svg.count("<rect"), 3)
        self.assertNotIn('height="-', svg)  # 無負高度（無效 SVG）

    def test_line_negative_stays_on_canvas(self):
        spec = {"type": "line", "title": "淨利率",
                "x": ["a", "b", "c"],
                "series": [{"name": "s", "values": [5, -3, 2]}]}
        svg = render_chart_svg(spec)
        self.assertIn("<polyline", svg)
        self.assertNotIn('cy="-', svg)  # 點不出界（無負 y）

    def test_pie_negative_value_returns_empty(self):
        spec = {"type": "pie", "title": "x",
                "x": ["A", "B"], "series": [{"name": "s", "values": [50, -10]}]}
        self.assertEqual(render_chart_svg(spec), "")

    def test_pie_single_slice_is_circle(self):
        spec = {"type": "pie", "title": "獨佔",
                "x": ["A"], "series": [{"name": "s", "values": [100]}]}
        svg = render_chart_svg(spec)
        self.assertIn("<circle", svg)


if __name__ == "__main__":
    unittest.main()
