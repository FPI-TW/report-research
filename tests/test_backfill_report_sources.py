import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.filename import resolve_source  # noqa: E402


class ResolveSourceTests(unittest.TestCase):
    """backfill 與 sync 共用的來源決定：本土發行機構指紋 → 檔名 token → 外資指紋。"""

    def test_filename_token_when_no_issuer_fingerprint(self):
        # 內文無本土發行機構指紋 → 用檔名（外資 -MS 樣式）
        self.assertEqual(
            resolve_source("ABF substrates-MS20240506.pdf", None),
            "morgan_stanley",
        )

    def test_content_fallback_when_filename_silent(self):
        meta = "公司拜訪快報 報告著作權屬元富投顧所有 元富證券投資顧問股份有限公司"
        self.assertEqual(
            resolve_source("瑞昱(2379)-20240422-速報.pdf", meta), "masterlink"
        )

    def test_issuer_fingerprint_corrects_subject_company_misparse(self):
        # 檔名把標的公司「國泰金」誤當券商(cathay)；內文著作權證實實為元富投顧 → 校正為 masterlink
        content = (
            "本刊載之報告為元富投顧於特定日期之分析。"
            "報告著作權屬元富投顧所有。元富投顧研究部 個股報告 國泰金(2882) BUY"
        )
        self.assertEqual(
            resolve_source("2882國泰金20240530-報告.pdf", content), "masterlink"
        )

    def test_roundup_competitor_mention_does_not_override_issuer(self):
        # 凱基 Taiwan daily 提及對手「國票綜合證券」主辦之法說會；發行者仍是凱基（kgisia 在報頭）
        content = (
            "研究網站: https://investment.kgisia.com.tw/Portal/Report 近期評等更新 "
            "僑威參加國票綜合證券舉辦之法人說明會 南亞科年第三季營運狀況"
        )
        self.assertEqual(resolve_source("Taiwan daily_2025_10_09_C.pdf", content), "kgi")

    def test_none_when_neither(self):
        self.assertIsNone(
            resolve_source("研究報告-顧問會員專屬盤後日報-0331.pdf", "盤後日報 無發行機構指紋")
        )


if __name__ == "__main__":
    unittest.main()
