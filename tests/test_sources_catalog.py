"""發行機構名單由 profile 提供（`[sources].catalog`）：廷豐逐字不變、其他 profile 不吃廷豐的名單。

- 廷豐：`profiles/tingfong/sources.toml` 必須與框架化之前寫死在 `app/services/filename.py` 的名單逐字等價
  （`tests/fixtures/tingfong_sources_golden.json` 是改動前由舊常數產生的快照；順序也釘住，因為檔名 token 依序
  比對、第一個命中者勝）。比對規則沒有動，名單相同＝判定結果相同；`tests/test_source.py` 另有逐案的行為斷言。
- 沒有名單的 profile：不從檔名或內文推斷發行機構（`20251001_birch_JPM_Chase.pdf` 不會變成 jpmorgan），顯示名稱
  就是代碼本身。
- 自己的名單：檔名 token、內文指紋、顯示名稱、總覽題的券商查找都換成那一份。
"""

import json
import os
import sys
import tempfile
import textwrap
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from app import profile as P  # noqa: E402
from app.services import filename as F  # noqa: E402
from app.services import overview  # noqa: E402

GOLDEN = json.loads((REPO / "tests" / "fixtures" / "tingfong_sources_golden.json").read_text(encoding="utf-8"))


class TingfongCatalogGoldenTests(unittest.TestCase):
    def setUp(self):
        self.src = P.load_profile(REPO / "profiles" / "tingfong").sources

    def test_filename_tokens_in_order(self):
        self.assertEqual([list(t) for t in self.src.filename_tokens], GOLDEN["filename_tokens"])

    def test_display_names(self):
        self.assertEqual(dict(self.src.display), GOLDEN["display"])

    def test_text_signatures_in_order(self):
        self.assertEqual([[s, list(m)] for s, m in self.src.text_signatures], GOLDEN["text_signatures"])
        self.assertEqual([[s, list(m)] for s, m in self.src.fallback_signatures], GOLDEN["fallback_signatures"])

    def test_default_profile_is_tingfong_catalog(self):
        self.assertEqual(P.get_profile().sources, self.src)

    def test_known_tingfong_resolutions(self):
        """幾個代表性案例（完整的逐案斷言在 tests/test_source.py）。"""
        self.assertEqual(F.parse_filename("2330台積電-MS20240314.pdf").source, "morgan_stanley")
        self.assertEqual(F.parse_filename("20251001_birch_JPM_Chase.pdf").source, "jpmorgan")
        self.assertEqual(F.resolve_source("x.pdf", "本報告由凱基投顧編製"), "kgi")
        self.assertEqual(F.source_display("goldman_sachs"), "高盛")
        self.assertEqual(overview.resolve_filters("高盛的研報有哪些", date(2026, 1, 1)).source, "goldman_sachs")


class _ProfileCase(unittest.TestCase):
    """以 PROFILE_DIR 指向暫存 profile（conftest 每題前後清 profile 快取）。"""

    TOML = """
        [institution]
        name = "示範"
        product_name = "示範研報"
    """
    CATALOG: str | None = None

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "demo"
        root.mkdir()
        toml = textwrap.dedent(self.TOML)
        if self.CATALOG is not None:
            toml += '\n[sources]\ncatalog = "sources.toml"\n'
            (root / "sources.toml").write_text(textwrap.dedent(self.CATALOG), encoding="utf-8")
        (root / "profile.toml").write_text(toml, encoding="utf-8")
        self._env = patch.dict(os.environ, {"PROFILE_DIR": str(root)})
        self._env.start()
        P.reset_profile_cache()

    def tearDown(self):
        self._env.stop()
        P.reset_profile_cache()
        self._tmp.cleanup()


class NoCatalogTests(_ProfileCase):
    def test_filename_does_not_infer_issuer(self):
        for name in ("20251001_birch_JPM_Chase.pdf", "2330台積電-MS20240314.pdf", "GS_outlook.pdf", "凱基 週報.pdf"):
            with self.subTest(name=name):
                meta = F.parse_filename(name)
                self.assertIsNone(meta.source)
                self.assertNotIn("BROKER_TOKEN", meta.matched_patterns)
        # 檔名的其他欄位照舊（日期、代碼不受名單影響）。
        self.assertEqual(F.parse_filename("2330台積電-MS20240314.pdf").report_date, date(2024, 3, 14))

    def test_content_does_not_infer_issuer(self):
        self.assertIsNone(F.resolve_source("x.pdf", "本報告由凱基投顧編製。J.P. Morgan Securities LLC"))
        self.assertIsNone(F.extract_source_from_text("Morgan Stanley Research"))

    def test_display_is_the_code_itself(self):
        self.assertEqual(F.source_display("goldman_sachs"), "goldman_sachs")
        self.assertEqual(F.source_display("aster"), "aster")
        self.assertIsNone(F.source_display(None))

    def test_overview_has_no_issuer_lookup(self):
        self.assertIsNone(overview.resolve_filters("高盛的研報有哪些", date(2026, 1, 1)).source)


class OwnCatalogTests(_ProfileCase):
    CATALOG = """
        filename_tokens = [{ token = "AST", source = "aster" }, { token = "北辰", source = "birch" }]
        text_signatures = [{ source = "birch", markers = ["北辰投顧"] }]
        fallback_signatures = [{ source = "aster", markers = ["aster research"] }]
        [display]
        aster = "星辰證券"
        birch = "北辰投顧"
    """

    def test_filename_tokens(self):
        self.assertEqual(F.parse_filename("20251001_ast_JPM_Chase.pdf").source, "aster")
        self.assertEqual(F.parse_filename("北辰 週報 20251001.pdf").source, "birch")
        self.assertEqual(F.parse_filename("x-AST20251001.pdf").matched_patterns, ["SOURCE_DATE"])
        self.assertIsNone(F.parse_filename("20251001_JPM_Chase.pdf").source)  # 廷豐的 token 不再生效

    def test_content_priority_matches_tingfong_rules(self):
        # 本土指紋先於檔名、檔名先於 fallback 指紋（規則與廷豐相同，只是名單不同）。
        self.assertEqual(F.resolve_source("x_AST.pdf", "北辰投顧 研究報告"), "birch")
        self.assertEqual(F.resolve_source("x.pdf", "Aster Research, Taipei"), "aster")
        self.assertEqual(F.resolve_source("x.pdf", "asteroid research"), None)  # 詞邊界

    def test_display_and_overview(self):
        self.assertEqual(F.source_display("aster"), "星辰證券")
        self.assertEqual(F.source_display("goldman_sachs"), "goldman_sachs")
        self.assertEqual(overview.resolve_filters("星辰證券的研報有哪些", date(2026, 1, 1)).source, "aster")


if __name__ == "__main__":
    unittest.main()
