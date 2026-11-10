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
        self.assertEqual(prof.institution.website, "https://research.tingfong.com")
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

    def test_website_is_optional_and_strict(self):
        """`[institution].website` 原樣進 HTTP header：只收 https 網址，不收空白、括號、引號與其他 scheme。"""
        self.assertEqual(self.load(MINIMAL).institution.website, "")
        ok = "[institution]\nname = \"a\"\nproduct_name = \"x\"\nwebsite = \"{}\"\n"
        for value in ("https://research.example.com", "https://a.example:8443/path/x"):
            with self.subTest(value=value):
                self.assertEqual(self.load(ok.format(value)).institution.website, value)
        for value in ("http://a.example", "https://a.example/x y", "https://a.example/(x)", "javascript:1",
                      "https://a.example/\\\"x"):
            with self.subTest(value=value):
                self.assertRejected(ok.format(value), "[institution].website")

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


class LocaleTests(_TmpCase):
    """[locale] 與 [institution] 的英文名稱：介面語系的 profile 設定（fail-closed）。"""

    def test_default_when_absent(self):
        prof = self.load(MINIMAL)
        self.assertEqual((prof.locale.default, prof.locale.available), ("zh-Hant", ("zh-Hant", "en")))
        # 沒給英文名稱：英文介面沿用原名。
        self.assertEqual(prof.institution.localized("en"), ("測試機構", "測試研報平台", "測試研報平台"))
        self.assertEqual(prof.institution.localized("zh-Hant"), ("測試機構", "測試研報平台", "測試研報平台"))

    def test_english_names(self):
        prof = self.load(MINIMAL + 'name_en = "Test Inst."\nproduct_name_en = "Test Research"\n')
        self.assertEqual(prof.institution.localized("en"), ("Test Inst.", "Test Research", "Test Research"))
        prof = self.load(MINIMAL + 'short_name = "測試"\nshort_name_en = "TR"\n')
        self.assertEqual(prof.institution.localized("en"), ("測試機構", "測試研報平台", "TR"))
        self.assertEqual(prof.institution.localized("zh-Hant")[2], "測試")

    def test_parses_locale(self):
        prof = self.load(MINIMAL + '[locale]\ndefault = "en"\navailable = ["en", "zh-Hant"]\n')
        self.assertEqual((prof.locale.default, prof.locale.available), ("en", ("en", "zh-Hant")))
        prof = self.load(MINIMAL + '[locale]\navailable = ["en"]\n')
        self.assertEqual((prof.locale.default, prof.locale.available), ("en", ("en",)))  # 預設取第一個

    def test_rejections(self):
        self.assertRejected(MINIMAL + '[locale]\navailable = ["fr"]\n', "[locale].available[0]", "不支援的語系")
        self.assertRejected(MINIMAL + '[locale]\navailable = ["en", "en"]\n', "[locale].available[1]", "重複")
        self.assertRejected(MINIMAL + '[locale]\navailable = []\n', "[locale].available", "至少")
        self.assertRejected(MINIMAL + '[locale]\ndefault = "en"\navailable = ["zh-Hant"]\n', "[locale].default", "不在")
        self.assertRejected(MINIMAL + '[locale]\nfallback = "en"\n', "[locale].fallback", "未知的鍵")
        self.assertRejected(MINIMAL + 'name_en = ""\n', "[institution].name_en", "空字串")

    def test_tingfong_keeps_chinese_default(self):
        """廷豐 profile：預設仍是中文（零回歸），兩種語系都可選；英文介面沿用原名。"""
        prof = P.load_profile(REPO / "profiles" / "tingfong")
        self.assertEqual(prof.locale.default, "zh-Hant")
        self.assertEqual(set(prof.locale.available), {"zh-Hant", "en"})


class InstrumentsSectionTests(_TmpCase):
    """[instruments]：個股代號格式（標註結果 stock_targets 的驗證規則）。"""

    def test_default_when_absent_is_four_digits(self):
        rule = self.load(MINIMAL).instruments
        self.assertEqual(rule.code_formats, (P.CodeFormat(market="TW", kind="digits", min_length=4, max_length=4),))
        self.assertEqual([rule.normalize_code(v) for v in ("2330", "23300", "NVDA")], ["2330", None, None])

    def test_tingfong_declares_the_pre_framework_rule(self):
        prof = P.load_profile(REPO / "profiles" / "tingfong")
        self.assertEqual(prof.instruments, P.Instruments())
        self.assertEqual(prof.instruments.digit_lengths(), (4, 4))

    def test_parses_formats(self):
        rule = self.load(MINIMAL + """
            [instruments.code_formats.TW]
            kind = "digits"
            min_length = 4
            max_length = 6
            [instruments.code_formats.US]
            kind = "letters"
            max_length = 5
            share_class = true
        """).instruments
        self.assertEqual([f.market for f in rule.code_formats], ["TW", "US"])
        self.assertEqual(rule.code_formats[1].min_length, 1)
        self.assertEqual([rule.normalize_code(v) for v in ("00878", "nvda", "BRK.B", "NVIDIA", "1234567")],
                         ["00878", "NVDA", "BRK.B", None, None])
        self.assertEqual(rule.digit_lengths(), (4, 6))

    def test_letters_only_has_no_digit_lengths(self):
        rule = self.load(MINIMAL + '[instruments.code_formats.US]\nkind = "letters"\nmax_length = 5\n').instruments
        self.assertIsNone(rule.digit_lengths())
        self.assertIsNone(rule.normalize_code("2330"))

    def test_rejections(self):
        base = MINIMAL + "[instruments.code_formats.{m}]\n{body}\n"

        def reject(body, *needles, m="TW"):
            self.assertRejected(base.format(m=m, body=body), *needles)

        reject('kind = "digits"\nmax_length = 4', "code_formats.JP", "標準市場", m="JP")
        reject('kind = "regex"\nmax_length = 4', "code_formats.TW.kind", "不支援")
        reject('kind = "digits"', "code_formats.TW.max_length", "必填")
        reject('kind = "digits"\nmin_length = 5\nmax_length = 4', "code_formats.TW.max_length", "不可小於")
        reject('kind = "digits"\nmax_length = 13', "code_formats.TW.max_length", "介於")
        reject('kind = "digits"\nmax_length = 4\nshare_class = true', "code_formats.TW.share_class", "未知的鍵")
        reject('kind = "letters"\nmax_length = 4\nshare_class = 1', "code_formats.TW.share_class", "布林")
        reject('kind = "digits"\nmax_length = 4\npattern = "x"', "code_formats.TW.pattern", "未知的鍵")
        self.assertRejected(MINIMAL + "[instruments]\ncode_formats = {}\n", "[instruments].code_formats", "至少")
        self.assertRejected(MINIMAL + "[instruments]\nformats = {}\n", "[instruments].formats", "未知的鍵")


class SourcesSectionTests(_TmpCase):
    """[sources]：發行機構名單檔（檔名 token、內文指紋、顯示名稱）。"""

    CATALOG = """
        filename_tokens = [{ token = "AST", source = "aster" }, { token = "北辰", source = "birch" }]
        text_signatures = [{ source = "aster", markers = ["aster research"] }]
        fallback_signatures = [{ source = "birch", markers = ["birch securities ltd"] }]
        [display]
        aster = "星辰證券"
        birch = "北辰投顧"
    """

    def load_catalog(self, catalog: str) -> P.Profile:
        make_profile(self.root, MINIMAL + '[sources]\ncatalog = "sources.toml"\n')
        (self.root / "sources.toml").write_text(textwrap.dedent(catalog), encoding="utf-8")
        return P.load_profile(self.root)

    def assertCatalogRejected(self, catalog: str, *needles: str) -> None:
        with self.assertRaises(P.ProfileError) as cm:
            self.load_catalog(catalog)
        msg = str(cm.exception)
        self.assertIn(str(self.root.resolve() / "sources.toml"), msg)  # 指向名單檔本身
        for needle in needles:
            self.assertIn(needle, msg)

    def test_absent_means_empty(self):
        self.assertEqual(self.load(MINIMAL).sources, P.Sources())

    def test_parses_catalog_in_order(self):
        src = self.load_catalog(self.CATALOG).sources
        self.assertEqual(src.filename_tokens, (("AST", "aster"), ("北辰", "birch")))
        self.assertEqual(src.text_signatures, (("aster", ("aster research",)),))
        self.assertEqual(src.fallback_signatures, (("birch", ("birch securities ltd",)),))
        self.assertEqual(dict(src.display), {"aster": "星辰證券", "birch": "北辰投顧"})

    def test_catalog_reference_rejections(self):
        self.assertRejected(MINIMAL + '[sources]\ncatalog = "missing.toml"\n', "[sources].catalog", "不存在")
        self.assertRejected(MINIMAL + '[sources]\ncatalog = "../x.toml"\n', "[sources].catalog", "檔名")
        self.assertRejected(MINIMAL + '[sources]\ncatalog = "sources.json"\n', "[sources].catalog", ".toml")
        self.assertRejected(MINIMAL + '[sources]\nfile = "sources.toml"\n', "[sources].file", "未知的鍵")

    def test_catalog_content_rejections(self):
        self.assertCatalogRejected("[display]\nAster = \"x\"\n", "display.Aster", "小寫")
        self.assertCatalogRejected('filename_tokens = [{ token = "AST", source = "nobody" }]\n[display]\n',
                                   "filename_tokens[0].source", "[display]")
        self.assertCatalogRejected('filename_tokens = [{ token = "ast", source = "a" }]\n[display]\na = "A"\n',
                                   "filename_tokens[0].token", "大寫")
        self.assertCatalogRejected('filename_tokens = [{ token = "A", source = "a" }]\n[display]\na = "A"\n',
                                   "filename_tokens[0].token", "2 個以上")
        self.assertCatalogRejected('filename_tokens = [{ token = "AB", source = "a" }, { token = "AB", source = "a" }]'
                                   '\n[display]\na = "A"\n', "filename_tokens[1].token", "重複")
        self.assertCatalogRejected('text_signatures = [{ source = "a", markers = ["Aster Research"] }]\n'
                                   '[display]\na = "A"\n', "text_signatures[0].markers[0]", "小寫")
        self.assertCatalogRejected('text_signatures = [{ source = "a", markers = [] }]\n[display]\na = "A"\n',
                                   "text_signatures[0].markers", "非空")
        self.assertCatalogRejected('fallback_signatures = [{ source = "a", marker = ["x"] }]\n[display]\na = "A"\n',
                                   "fallback_signatures[0].marker", "未知的鍵")
        self.assertCatalogRejected('aliases = []\n', "aliases", "未知的鍵")
        self.assertCatalogRejected('filename_tokens = [\n', "TOML 語法錯誤")


class BriefSectionTests(_TmpCase):
    """[brief]：每日簡報的作息（scripts/generate_brief.py）。"""

    def test_defaults_and_tingfong(self):
        self.assertEqual(self.load(MINIMAL).brief,
                         P.Brief(after_hour=9, default_lookback_hours=24, max_lookback_days=7))
        self.assertEqual(P.load_profile(REPO / "profiles" / "tingfong").brief, P.Brief())

    def test_values_and_rejections(self):
        brief = self.load(MINIMAL + "[brief]\nafter_hour = 0\ndefault_lookback_hours = 12\nmax_lookback_days = 3\n")
        self.assertEqual(brief.brief, P.Brief(after_hour=0, default_lookback_hours=12, max_lookback_days=3))
        self.assertRejected(MINIMAL + "[brief]\nafter_hour = 24\n", "[brief].after_hour", "介於")
        self.assertRejected(MINIMAL + "[brief]\nafter_hour = \"9\"\n", "[brief].after_hour", "整數")
        self.assertRejected(MINIMAL + "[brief]\nmax_lookback_days = 0\n", "[brief].max_lookback_days", "介於")
        self.assertRejected(MINIMAL + "[brief]\ntimezone = \"UTC\"\n", "[brief].timezone", "未知的鍵")


class ContractTests(unittest.TestCase):
    def test_locales_match_output_language_and_frontend(self):
        """profile 的語系詞彙＝問答輸出語言（app/services/locale.py）＝前端字典的語系（frontend/src/i18n）。"""
        from app.services.locale import SUPPORTED_LOCALES

        self.assertEqual(P.LOCALES, SUPPORTED_LOCALES)
        src = (REPO / "frontend" / "src" / "i18n" / "index.ts").read_text(encoding="utf-8")
        m = re.search(r"export const LOCALES: readonly Locale\[\] = \[([^\]]*)\]", src)
        self.assertIsNotNone(m, "frontend/src/i18n/index.ts 應定義 LOCALES")
        self.assertEqual(tuple(re.findall(r"'([^']+)'", m.group(1))), P.LOCALES)

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
