"""app/services/doc_types.py：類型片段的形狀、呼叫時依 profile 渲染、入庫閘，以及廷豐 profile 下與改動前等價。

不連 DB。SQL 片段在真的 PostgreSQL 上是否把類型關掉的文件從各路徑排除，由 `tests/test_doc_type_db.py` 驗；
沒有漏帶片段由 `tests/test_doc_type_guard.py` 守。
"""

import itertools
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import profile as P  # noqa: E402
from app.services import doc_types  # noqa: E402
from app.services.tagging import MarketTag, doc_type_for, parse_tags  # noqa: E402

_PROFILE = """
[institution]
name = "測試機構"
product_name = "測試研報平台"

[doc_types.research]
features = ["search", "reading", "tagging_markets", "summary"]

[doc_types.non_research]
features = []

[doc_types.meeting_note]
features = ["reading", "summary"]
"""


class _ProfileCase(unittest.TestCase):
    """在暫存目錄建一份 profile，以 PROFILE_DIR 指過去；結束時清快取（conftest 也會清）。"""

    toml = _PROFILE

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / "acme"
        root.mkdir()
        (root / "profile.toml").write_text(textwrap.dedent(self.toml), encoding="utf-8")
        self._env = patch.dict(os.environ, {"PROFILE_DIR": str(root)})
        self._env.start()
        P.reset_profile_cache()

    def tearDown(self):
        self._env.stop()
        P.reset_profile_cache()
        self._tmp.cleanup()


class TingfongEquivalenceTests(unittest.TestCase):
    """廷豐 profile（預設）：每個功能都只開給 research，片段等同改動前的 `is_research IS NOT FALSE`。

    等價的理由：revision 0012 之後 `is_research` NOT NULL 且由 CHECK 綁成 `is_research = (doc_type = 'research')`，
    所以 `doc_type IN ('research')` ⇔ `is_research` ⇔ `is_research IS NOT FALSE`。
    """

    def test_every_feature_renders_research_only(self):
        for feature in P.DOC_FEATURES:
            with self.subTest(feature=feature):
                self.assertEqual(doc_types.enabled_types(feature), ("research",))
                self.assertEqual(doc_types.doc_type_enabled_sql("r", feature), "r.doc_type IN ('research')")

    def test_tingfong_declares_both_tagger_types(self):
        features = dict(P.get_profile().doc_types.features)
        self.assertEqual(features, {"research": frozenset(P.DOC_FEATURES), "non_research": frozenset()})

    def test_ingest_gate_matches_old_rule(self):
        """改動前：`tag.is_research and tag.market`。"""
        for is_research, market in itertools.product((True, False), ("TW", None, "")):
            with self.subTest(is_research=is_research, market=market):
                self.assertEqual(
                    doc_types.ingest_gate(doc_type_for(is_research), market),
                    bool(is_research and market),
                )

    def test_section_absent_is_the_same_as_tingfong(self):
        """沒寫 [doc_types] 的 profile 走框架預設，與廷豐的明確設定相同。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "bare"
            root.mkdir()
            (root / "profile.toml").write_text('[institution]\nname = "x"\nproduct_name = "y"\n', encoding="utf-8")
            bare = P.load_profile(root)
        self.assertEqual(dict(bare.doc_types.features), dict(P.get_profile().doc_types.features))


class FragmentShapeTests(unittest.TestCase):
    def test_alias_and_id_variants(self):
        self.assertEqual(doc_types.doc_type_enabled_sql("rn", "radar"), "rn.doc_type IN ('research')")
        by_id = doc_types.doc_type_enabled_id_sql("report_signal.report_id", "brief")
        self.assertEqual(
            by_id,
            "EXISTS (SELECT 1 FROM research.research_report dt_r "
            "WHERE dt_r.id = report_signal.report_id AND dt_r.doc_type IN ('research'))",
        )

    def test_rejects_bad_alias_expr_and_feature(self):
        for bad in ("", "r; DROP TABLE x", "r.doc_type", "1r", "r)"):
            with self.subTest(alias=bad), self.assertRaises(ValueError):
                doc_types.doc_type_enabled_sql(bad, "search")
        for bad in ("", "x; --", "a.b.c", "s.report_id)"):
            with self.subTest(expr=bad), self.assertRaises(ValueError):
                doc_types.doc_type_enabled_id_sql(bad, "search")
        for bad in ((), ("ask",), ("search", "nope")):
            with self.subTest(features=bad), self.assertRaises(ValueError):
                doc_types.doc_type_enabled_sql("r", *bad)
        with self.assertRaises(ValueError):
            doc_types.is_enabled("research", "nope")

    def test_legacy_is_research(self):
        self.assertTrue(doc_types.legacy_is_research("research"))
        self.assertFalse(doc_types.legacy_is_research("non_research"))
        self.assertFalse(doc_types.legacy_is_research("meeting_note"))


class TaggingMappingTests(unittest.TestCase):
    def test_is_research_maps_to_doc_type(self):
        self.assertEqual(doc_type_for(True), "research")
        self.assertEqual(doc_type_for(False), "non_research")
        self.assertEqual(MarketTag(market="TW", is_research=True).doc_type, "research")
        self.assertEqual(MarketTag(market=None, is_research=False).doc_type, "non_research")

    def test_parse_tags_unchanged_and_mapped(self):
        tag = parse_tags('{"market": "TW", "is_research": false, "confidence": 0.4}')
        assert tag is not None
        self.assertFalse(tag.is_research)
        self.assertEqual(tag.doc_type, "non_research")


class CustomProfileTests(_ProfileCase):
    """片段在呼叫時依目前的 profile 渲染：換 profile 就換類型清單（不是 import 期定值）。"""

    def test_fragments_follow_the_profile(self):
        self.assertEqual(doc_types.doc_type_enabled_sql("r", "search"), "r.doc_type IN ('research')")
        self.assertEqual(
            doc_types.doc_type_enabled_sql("r", "reading"), "r.doc_type IN ('research', 'meeting_note')"
        )
        self.assertEqual(doc_types.doc_type_enabled_sql("r", "radar"), "FALSE")
        self.assertEqual(
            doc_types.doc_type_enabled_sql("r", "radar", "summary"), "r.doc_type IN ('research', 'meeting_note')"
        )
        self.assertIn("AND FALSE)", doc_types.doc_type_enabled_id_sql("s.report_id", "brief"))

    def test_module_sql_is_rendered_per_call(self):
        from app.services import brief
        from app.services.reading import queries as reading

        self.assertIn("r.doc_type IN ('research', 'meeting_note')", str(reading._doc_sql()))
        self.assertIn("AND FALSE", str(brief._window_reports_sql()))

    def test_ingest_gate(self):
        self.assertTrue(doc_types.ingest_gate("research", "TW"))
        self.assertFalse(doc_types.ingest_gate("research", None))  # tagging_markets 開著：market 必填
        self.assertTrue(doc_types.ingest_gate("meeting_note", None))  # 沒開 tagging_markets：不要求 market
        self.assertFalse(doc_types.ingest_gate("non_research", "TW"))  # 全關：不入庫
        self.assertFalse(doc_types.ingest_gate("unknown_type", "TW"))  # 沒宣告：等同全關
        self.assertTrue(doc_types.is_enabled("meeting_note", "reading"))
        self.assertFalse(doc_types.is_enabled("meeting_note", "search"))
        self.assertFalse(doc_types.is_enabled(None, "search"))


if __name__ == "__main__":
    unittest.main()
