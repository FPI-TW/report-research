"""app/profile.py：profile 的載入、嚴格驗證（fail-closed）與 section 登記表。

驗證錯誤的每一類都要說出檔案、鍵與原因（ProfileError 的訊息就是維運看到的唯一線索）；
測試用的 profile 一律在 tempfile 建立，不碰 repo 裡的 profiles/。
"""

import ast
import os
import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import profile as P  # noqa: E402
from app.services.tagging import MARKET_DISPLAY, MARKETS  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16

MINIMAL = """
[institution]
name = "測試機構"
product_name = "測試研報平台"
"""


def make_profile(root: Path, toml: str, assets: dict[str, bytes] | None = None) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "profile.toml").write_text(textwrap.dedent(toml), encoding="utf-8")
    if assets:
        (root / "assets").mkdir(exist_ok=True)
        for name, data in assets.items():
            (root / "assets" / name).write_bytes(data)
    return root


class _TmpCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "acme"

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, toml: str, assets: dict[str, bytes] | None = None) -> P.Profile:
        return P.load_profile(make_profile(self.root, toml, assets))

    def assertRejected(self, toml: str, *needles: str, assets: dict[str, bytes] | None = None) -> str:
        with self.assertRaises(P.ProfileError) as cm:
            self.load(toml, assets)
        msg = str(cm.exception)
        self.assertIn(str(self.root.resolve() / "profile.toml"), msg)  # 說出是哪個檔
        for needle in needles:
            self.assertIn(needle, msg)
        return msg


class TingfongProfileTests(unittest.TestCase):
    def test_default_profile_is_tingfong(self):
        self.assertEqual(P.resolve_profile_dir(""), REPO / "profiles" / "tingfong")
        prof = P.get_profile()
        self.assertEqual(prof.root, (REPO / "profiles" / "tingfong").resolve())
        self.assertEqual(
            (prof.institution.name, prof.institution.product_name, prof.institution.short_name),
            ("廷豐", "廷豐智能研報", "廷豐研報"),
        )
        self.assertEqual((prof.prompt.system_name, prof.prompt.system_name_en), ("廷豐研報", "廷豐研報"))
        self.assertEqual(prof.security.totp_issuer, "廷豐智能研報")
        # 全部標準市場啟用；顯示名稱就是標準名稱（零回歸）。
        self.assertEqual(set(prof.markets.enabled), set(MARKETS))
        self.assertEqual(dict(prof.markets.display_names), MARKET_DISPLAY)
        # 強調色不覆寫（tokens.css 的鎏金即廷豐色票）。
        self.assertEqual((dict(prof.branding.colors_light), dict(prof.branding.colors_dark)), ({}, {}))
        self.assertEqual(sorted(prof.branding.assets()), ["favicon.svg", "logo.png"])

    def test_get_profile_caches_until_reset(self):
        first = P.get_profile()
        self.assertIs(P.get_profile(), first)
        P.reset_profile_cache()
        self.assertIsNot(P.get_profile(), first)

    def test_relative_dir_is_resolved_against_repo_root(self):
        self.assertEqual(P.resolve_profile_dir("profiles/x"), REPO / "profiles" / "x")
        self.assertEqual(P.resolve_profile_dir("/abs/x"), Path("/abs/x"))

    def test_profile_dir_env_is_honoured(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = make_profile(Path(tmp) / "acme", MINIMAL)
            with patch.dict(os.environ, {"PROFILE_DIR": str(root)}):
                P.reset_profile_cache()
                self.assertEqual(P.get_profile().institution.product_name, "測試研報平台")


class DefaultsTests(_TmpCase):
    def test_minimal_profile_gets_defaults(self):
        prof = self.load(MINIMAL)
        self.assertEqual(prof.institution.short_name, "測試研報平台")
        self.assertEqual((prof.prompt.system_name, prof.prompt.system_name_en), ("測試研報平台", "測試研報平台"))
        self.assertEqual(prof.security.totp_issuer, "測試研報平台")
        self.assertEqual(prof.markets.enabled, tuple(MARKETS))
        self.assertEqual(prof.branding.assets(), {})
        self.assertIsNone(prof.branding.logo_light)

    def test_full_profile(self):
        prof = self.load(MINIMAL + """
            short_name = "測試"
            [branding]
            logo_light = "logo.png"
            favicon = "icon.svg"
            [branding.colors.light]
            accent_strong = "#123456"
            [branding.colors.dark]
            accent_tint = "rgba(1, 2, 3, 0.14)"
            [prompt]
            system_name = "ACME 研報"
            system_name_en = "ACME Research"
            [markets]
            enabled = ["US", "TW"]
            [markets.display_names]
            TW = "臺股"
            [security]
            totp_issuer = "ACME"
        """, assets={"logo.png": PNG, "icon.svg": b"<svg/>"})
        self.assertEqual(prof.branding.logo_dark, prof.branding.logo_light)  # 暗色沒設就沿用亮色
        self.assertEqual(prof.branding.logo_light.content_type, "image/png")
        self.assertEqual(prof.branding.favicon.content_type, "image/svg+xml")
        self.assertEqual(dict(prof.branding.colors_light), {"accent_strong": "#123456"})
        self.assertEqual(prof.markets.enabled, ("US", "TW"))
        self.assertEqual(prof.markets.label("TW"), "臺股")
        self.assertEqual(prof.markets.label("HK"), MARKET_DISPLAY["HK"])  # 未覆寫的取標準名稱
        self.assertFalse(prof.markets.is_enabled("HK"))
        self.assertEqual(prof.prompt.system_name_en, "ACME Research")


class ValidationTests(_TmpCase):
    def test_missing_directory(self):
        missing = Path(self._tmp.name) / "nope"
        with self.assertRaises(P.ProfileError) as cm:
            P.load_profile(missing)
        self.assertIn("PROFILE_DIR", str(cm.exception))
        self.assertIn(str(missing), str(cm.exception))

    def test_missing_profile_toml(self):
        self.root.mkdir(parents=True)
        with self.assertRaises(P.ProfileError) as cm:
            P.load_profile(self.root)
        self.assertIn("profile.toml", str(cm.exception))

    def test_toml_syntax_error(self):
        self.assertRejected("[institution\n", "TOML 語法錯誤")

    def test_unknown_section(self):
        self.assertRejected(MINIMAL + "[brandingg]\nx = 1\n", "brandingg", "未知的 section")

    def test_unknown_key(self):
        self.assertRejected(MINIMAL + "slogan = \"x\"\n", "[institution].slogan", "未知的鍵")

    def test_unknown_nested_key(self):
        self.assertRejected(MINIMAL + "[branding.colors.light]\nprimary = \"#000000\"\n",
                            "[branding].colors.light.primary", "未知的鍵")

    def test_missing_required_section(self):
        self.assertRejected("[prompt]\nsystem_name = \"x\"\n", "institution", "缺少必填的 section")

    def test_missing_required_key(self):
        self.assertRejected("[institution]\nname = \"x\"\n", "[institution].product_name", "必填")

    def test_wrong_types(self):
        self.assertRejected("[institution]\nname = 1\nproduct_name = \"x\"\n", "[institution].name", "必須是字串")
        self.assertRejected(MINIMAL + "[markets]\nenabled = \"TW\"\n", "[markets].enabled", "必須是字串陣列")
        self.assertRejected(MINIMAL + "[markets]\nenabled = [1]\n", "[markets].enabled[0]", "必須是字串")
        self.assertRejected("institution = \"x\"\n", "institution", "必須是 section")
        self.assertRejected(MINIMAL + "[branding]\ncolors = \"x\"\n", "[branding].colors", "必須是表格")

    def test_empty_string(self):
        self.assertRejected("[institution]\nname = \"  \"\nproduct_name = \"x\"\n", "[institution].name", "空字串")

    def test_non_standard_market(self):
        self.assertRejected(MINIMAL + "[markets]\nenabled = [\"TW\", \"JP\"]\n",
                            "[markets].enabled[1]", "不是標準市場代碼", "'JP'")
        self.assertRejected(MINIMAL + "[markets]\nenabled = [\"tw\"]\n", "不是標準市場代碼")
        self.assertRejected(MINIMAL + "[markets.display_names]\nJP = \"日股\"\n",
                            "[markets].display_names.JP", "不是標準市場代碼")

    def test_market_subset_rules(self):
        self.assertRejected(MINIMAL + "[markets]\nenabled = []\n", "至少要啟用一個市場")
        self.assertRejected(MINIMAL + "[markets]\nenabled = [\"TW\", \"TW\"]\n", "[markets].enabled[1]", "重複")

    def test_missing_asset(self):
        self.assertRejected(MINIMAL + "[branding]\nlogo_light = \"logo.png\"\n",
                            "[branding].logo_light", "asset 不存在", "logo.png")

    def test_asset_must_be_plain_name(self):
        for name in ("../profile.toml", "sub/logo.png", "/etc/passwd", ".hidden.png"):
            with self.subTest(name=name):
                self.assertRejected(MINIMAL + f"[branding]\nlogo_light = \"{name}\"\n",
                                    "[branding].logo_light", assets={"logo.png": PNG})

    def test_asset_symlink_escaping_dir_is_rejected(self):
        make_profile(self.root, MINIMAL + "[branding]\nlogo_light = \"link.png\"\n", {"logo.png": PNG})
        outside = Path(self._tmp.name) / "outside.png"
        outside.write_bytes(PNG)
        (self.root / "assets" / "link.png").symlink_to(outside)
        with self.assertRaises(P.ProfileError) as cm:
            P.load_profile(self.root)
        self.assertIn("asset 不存在", str(cm.exception))

    def test_asset_type_and_empty(self):
        self.assertRejected(MINIMAL + "[branding]\nlogo_light = \"logo.exe\"\n", "不支援的 asset 類型",
                            assets={"logo.exe": PNG})
        self.assertRejected(MINIMAL + "[branding]\nlogo_light = \"logo.png\"\n", "空檔", assets={"logo.png": b""})

    def test_color_values_are_strict(self):
        for value in ("red", "#12345", "#1234567", "var(--x)", "#000000; } body { display:none",
                      "rgb(1,2,3);x", "url(javascript:1)"):
            with self.subTest(value=value):
                self.assertRejected(MINIMAL + f"[branding.colors.dark]\naccent_text = '{value}'\n",
                                    "[branding].colors.dark.accent_text")
        for value in ("#a1b2c3", "#A1B2C3FF", "rgb(1, 2, 3)", "rgba(212, 166, 74, 0.4)", "rgba(0,0,0,1)"):
            with self.subTest(value=value):
                self.load(MINIMAL + f"[branding.colors.dark]\naccent_text = '{value}'\n")

    def test_totp_issuer_rejects_colon(self):
        self.assertRejected(MINIMAL + "[security]\ntotp_issuer = \"A:B\"\n", "[security].totp_issuer", "冒號")

    def test_control_characters_rejected(self):
        self.assertRejected("[institution]\nname = \"a\\nb\"\nproduct_name = \"x\"\n", "控制字元")


class DocTypesSectionTests(_TmpCase):
    """[doc_types]：類型名稱、功能詞彙、必填與標註器類型都要宣告（fail-closed）。"""

    OK = MINIMAL + "[doc_types.research]\nfeatures = [\"search\"]\n[doc_types.non_research]\nfeatures = []\n"

    def test_parses_declared_types_in_order(self):
        prof = self.load(self.OK + "[doc_types.memo]\nfeatures = [\"reading\", \"summary\"]\n")
        self.assertEqual(list(prof.doc_types.features), ["research", "non_research", "memo"])
        self.assertEqual(prof.doc_types.features["memo"], frozenset({"reading", "summary"}))
        self.assertEqual(prof.doc_types.types_with("summary"), ("memo",))
        self.assertTrue(prof.doc_types.enabled("research", "search"))
        self.assertFalse(prof.doc_types.enabled("non_research", "search"))
        self.assertFalse(prof.doc_types.enabled(None, "search"))

    def test_default_when_absent(self):
        prof = self.load(MINIMAL)
        self.assertEqual(dict(prof.doc_types.features),
                         {"research": frozenset(P.DOC_FEATURES), "non_research": frozenset()})

    def test_rejections(self):
        self.assertRejected(self.OK + "[doc_types.Memo]\nfeatures = []\n", "[doc_types].Memo", "類型名稱")
        self.assertRejected(self.OK + "[doc_types.memo]\nfeatures = [\"ask\"]\n",
                            "[doc_types].memo.features[0]", "未知的功能")
        self.assertRejected(self.OK + "[doc_types.memo]\nfeatures = [\"search\", \"search\"]\n",
                            "[doc_types].memo.features[1]", "重複")
        self.assertRejected(self.OK + "[doc_types.memo]\nlabel = \"x\"\nfeatures = []\n",
                            "[doc_types].memo.label", "未知的鍵")
        self.assertRejected(self.OK + "[doc_types.memo]\n", "[doc_types].memo.features", "必填")
        self.assertRejected(self.OK + "[doc_types.memo]\nfeatures = \"search\"\n",
                            "[doc_types].memo.features", "必須是字串陣列")
        self.assertRejected(self.OK + "[doc_types.memo]\nfeatures = [1]\n",
                            "[doc_types].memo.features[0]", "必須是字串")
        self.assertRejected(MINIMAL + "[doc_types]\nmemo = 1\n", "[doc_types].memo", "必須是表格")
        # 標註器會產生的類型漏宣告：那類文件會靜默消失，所以拒絕啟動。
        self.assertRejected(MINIMAL + "[doc_types.research]\nfeatures = []\n", "[doc_types].non_research", "必須宣告")


class BackfillSectionTests(_TmpCase):
    """[backfill]：分層回填的窗期與背景補齊上限（scripts/backfill_tiers.py）。"""

    def test_defaults(self):
        bf = self.load(MINIMAL).backfill
        self.assertEqual((bf.recent_months, bf.background_enabled, bf.daily_token_limit, bf.max_reports_per_round),
                         (12, False, 2_000_000, 100))

    def test_tingfong_keeps_background_off(self):
        """廷豐已全量跑完派生：背景補齊關（scheduler 的 backfill-tiers 是 no-op），sync 鏈照舊。"""
        bf = P.get_profile().backfill
        self.assertEqual((bf.recent_months, bf.background_enabled), (12, False))

    def test_values(self):
        bf = self.load(MINIMAL + "[backfill]\nrecent_months = 6\nbackground_enabled = true\n"
                                 "daily_token_limit = 500000\nmax_reports_per_round = 20\n").backfill
        self.assertEqual((bf.recent_months, bf.background_enabled, bf.daily_token_limit, bf.max_reports_per_round),
                         (6, True, 500_000, 20))

    def test_rejections(self):
        self.assertRejected(MINIMAL + "[backfill]\nrecent_month = 6\n", "[backfill].recent_month", "未知的鍵")
        self.assertRejected(MINIMAL + "[backfill]\nrecent_months = 0\n", "[backfill].recent_months", "介於")
        self.assertRejected(MINIMAL + "[backfill]\nrecent_months = \"12\"\n", "[backfill].recent_months", "整數")
        self.assertRejected(MINIMAL + "[backfill]\nbackground_enabled = \"yes\"\n",
                            "[backfill].background_enabled", "布林")
        self.assertRejected(MINIMAL + "[backfill]\ndaily_token_limit = 0\n", "[backfill].daily_token_limit", "介於")
        self.assertRejected(MINIMAL + "[backfill]\nmax_reports_per_round = true\n",
                            "[backfill].max_reports_per_round", "整數")


class ContractTests(unittest.TestCase):
    def test_every_registered_section_has_a_profile_field(self):
        """登記表與 Profile 欄位一一對應：後續 lane 加 section 漏了任何一邊都會在這裡紅。"""
        self.assertEqual(P.registered_sections(), P.section_fields())

    def test_accent_tokens_match_frontend(self):
        """後端允許的強調色鍵＝前端 ACCENT_CSS_VARS 的鍵（多一個是靜默無效，少一個是拒絕合法設定）。"""
        src = (REPO / "frontend" / "src" / "lib" / "branding.ts").read_text(encoding="utf-8")
        block = re.search(r"ACCENT_CSS_VARS[^=]*=\s*\{(.*?)\n\}", src, re.S)
        self.assertIsNotNone(block, "frontend/src/lib/branding.ts 應定義 ACCENT_CSS_VARS")
        keys = set(re.findall(r"^\s*(\w+):", block.group(1), re.M))
        self.assertEqual(keys, set(P.ACCENT_TOKENS))

    def test_profile_module_imports_only_stdlib_and_tagging(self):
        """葉模組：totp、批次與評測入口都會讀 profile，它不能牽動 Settings 或任何會連 DB 的模組。"""
        tree = ast.parse((REPO / "app" / "profile.py").read_text(encoding="utf-8"))
        project = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] == "app":
                project.add(node.module)
            elif isinstance(node, ast.Import):
                project.update(a.name for a in node.names if a.name.split(".")[0] == "app")
        self.assertEqual(project, {"app.services.tagging"})
        tagging = ast.parse((REPO / "app" / "services" / "tagging.py").read_text(encoding="utf-8"))
        self.assertFalse([n for n in ast.walk(tagging) if isinstance(n, ast.ImportFrom)
                          and (n.module or "").startswith("app")])


if __name__ == "__main__":
    unittest.main()
