# tests/test_c_small_defects.py
"""C 組三顆小缺陷（2026-07-28 盤點）。三者獨立，合在一檔便於對照。

1. 追問 chips 沒跟 locale：英文模式下主答案、婉拒、總覽模板都已英文化，唯獨三顆
   追問是中文，且會落 qa_log 一直跟著歷史重播。
2. 三款 .typ 未設 `set table`：pandoc 產出裸 `#table(...)`，Typst 用預設**黑**框；
   深色模板上對 #12181f 底的對比僅約 1.19:1。既有 fixture 無表格 → 這條路徑
   從未被編譯過，所以缺陷能潛伏。
3. 總覽把券商動作動詞當公司名：「有哪些券商看鴻海」→ stock_name='看鴻海'
   → company_name ILIKE '%看鴻海%' → 0 列 → 回「找不到」。
"""
import io
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import followups  # noqa: E402
from app.services.overview import _extract_stock_name  # noqa: E402


class FollowupsLocaleTests(unittest.TestCase):
    def test_english_system_prompt_is_full_variant(self):
        """整份英文變體,非「中文底稿＋尾部覆寫」——後者在逐節研報上實測會機率性失守。"""
        s = followups._system_for("en")
        cjk = sum(1 for c in s if "一" <= c <= "鿿")
        self.assertEqual(cjk, 0, f"英文追問提示不該含中文（實得 {cjk} 字）")
        self.assertIn("JSON array of strings", s)
        # 專有名詞保留原文（與 M10 全域慣例一致:證據不翻譯）
        self.assertIn("proper nouns in their original language", s)

    def test_zh_default_unchanged(self):
        self.assertIs(followups._system_for("zh-Hant"), followups._SYSTEM)

    def test_unknown_locale_fails_open_to_zh(self):
        self.assertIs(followups._system_for("fr"), followups._SYSTEM)

    def test_signature_accepts_locale(self):
        """契約防護:呼叫端（answer.py）靠這個 kwarg 傳遞。

        本專案已四度踩到「測試 fake 或呼叫端簽章漂移 → TypeError 被吞 → 靜默走錯
        路徑」,故對新增的貫穿參數一律加簽章測試。
        """
        import inspect

        self.assertIn("locale", inspect.signature(followups.generate_followups).parameters)

    def test_answer_passes_locale(self):
        src = (REPO_ROOT / "app" / "services" / "answer.py").read_text(encoding="utf-8")
        self.assertIn("generate_followups(question, body, locale=locale)", src)


class OverviewVerbStopwordTests(unittest.TestCase):
    """券商動作動詞不得黏進公司名。"""

    def test_verbs_stripped(self):
        """三字以上的標的名:動詞剝除後應留下乾淨公司名。"""
        for q, want in [
            ("有哪些券商看台積電", "台積電"),
            ("有哪些券商提到聯發科", "聯發科"),
            ("哪些券商寫過台達電", "台達電"),
            ("有哪些券商看好日月光", "日月光"),
        ]:
            with self.subTest(q=q):
                self.assertEqual(_extract_stock_name(q), want)

    def test_pure_enumeration_still_none(self):
        """沒有標的的純枚舉題不得憑空生出公司名。"""
        for q in ("有哪些券商", "有哪些市場", "目前有哪些商品類型"):
            with self.subTest(q=q):
                self.assertIsNone(_extract_stock_name(q))

    def test_real_company_names_not_damaged(self):
        """安全性:新增的停用詞不得剝掉真實公司名的字。

        對**實際被過濾的欄位** research_report.company_name（454 個相異值）驗證過;
        「推薦」因會咬到真實值「年第二季推薦個股」而刻意未列入停用詞。
        """
        for q, want in [
            ("台積電相關報告", "台積電"),
            ("聯發科最新研究", "聯發科"),
        ]:
            with self.subTest(q=q):
                self.assertEqual(_extract_stock_name(q), want)

    def test_recommend_not_a_stopword(self):
        """「推薦」不得成為停用詞:company_name 實際存在「年第二季推薦個股」等值。"""
        from app.services.overview import _NAME_STOPWORDS

        self.assertNotIn("推薦", _NAME_STOPWORDS)

    def test_known_limitation_two_char_names_not_extracted(self):
        """**已知限制（本次刻意不修）**:正則是 [一-鿿]{3,8}，2 字公司名抽不出來。

        實測 research_report.company_name 的 454 個相異值中 **293 個（64.5%）是 2 字**
        （上品／上銀／中砂…），所以「有哪些券商看鴻海」在動詞修好後仍得到 None。

        但 None **優於**修復前的 '看鴻海':後者會套成
        `company_name ILIKE '%看鴻海%'` → 0 列 → 回「找不到」（明確錯誤答案）;
        None 則是不套個股過濾、照常列出券商（答得較寬但不是錯的）。

        下修為 {2,8} 會大幅提高把雜訊當公司名的風險，需要另以既有 company_name 值
        做白名單比對，屬獨立變更、不併入這顆小缺陷修復。
        """
        self.assertIsNone(_extract_stock_name("有哪些券商看鴻海"))
        self.assertIsNone(_extract_stock_name("鴻海的研報"))


_MD_WITH_TABLE = """# 表格編譯驗證

## 執行摘要

綜述[1]。

## 重點分析

| 項目 | 2025 | 2026F |
| --- | --- | --- |
| 營收 | 100 | 140 |
| 毛利率 | 55% | 58% |

分析內文[1]。

## 風險與展望

風險。

## 引用來源
[1] a.pdf（TW·2026-01-01）
"""


class TableStyleTests(unittest.TestCase):
    def test_all_templates_declare_set_table(self):
        """未宣告時 Typst 用預設黑框——深色模板上對比僅約 1.19:1。"""
        for name in ("ib-classic", "broker-modern", "privatebank-dark"):
            with self.subTest(template=name):
                src = (REPO_ROOT / "app" / "templates" / f"{name}.typ").read_text(
                    encoding="utf-8"
                )
                self.assertIn("set table(", src)
                self.assertIn("stroke:", src)

    def test_no_hardcoded_black_stroke(self):
        for name in ("ib-classic", "broker-modern", "privatebank-dark"):
            with self.subTest(template=name):
                src = (REPO_ROOT / "app" / "templates" / f"{name}.typ").read_text(
                    encoding="utf-8"
                )
                self.assertNotIn('rgb("#000000")', src)
                self.assertNotIn("+ black", src)

    def test_table_compiles_in_every_template_and_locale(self):
        """**先前完全沒被測過的路徑**：既有 fixture 無表格，所以 pandoc 的
        `#table(...)` 從未經過編譯，樣式問題自然潛伏。"""
        import pypdf

        from app.services.typst_render import render_report_pdf
        from app.templates import manifest

        meta = {"date": "2026-07-28", "question": "表格"}
        for spec in manifest.list_templates():
            for loc in ("zh-Hant", "en"):
                with self.subTest(template=spec.id, locale=loc):
                    pdf = render_report_pdf(
                        _MD_WITH_TABLE, title="表格驗證", meta=meta,
                        template_id=spec.id, locale=loc,
                    )
                    self.assertEqual(pdf[:4], b"%PDF")
                    pages = len(pypdf.PdfReader(io.BytesIO(pdf)).pages)
                    self.assertGreaterEqual(pages, 1)
                    self.assertLessEqual(pages, 4, f"{spec.id} 爆頁：{pages}")


if __name__ == "__main__":
    unittest.main()
