# tests/test_db_audit.py
"""`scripts/db_audit.py`：純函式與 SQL 形狀，全部不需要 DB。

**為什麼不用真 DB 驗**：稽核器的價值在「查到有問題時會紅」，而在測試庫裡製造出
每一種違反（孤兒列、重複 chunk_index、市場不一致）要先寫入資料——那就變成在驗
「我造得出違反」而不是「稽核器判得對」。判斷邏輯全在 Python（count > 0 ⇒ 失敗、
退出碼、排序），SQL 則以形狀驗（每條回單一整數、掃對表、方向沒寫反）。

真 DB 那一半靠實跑：2026-07-30 對生產跑過，抓到 2 列孤兒（當時的研報成品表，該表已
隨功能移除；與獨立查詢的數字相符），退出碼 1。
"""
import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import db_audit  # noqa: E402


class CheckShapeTests(unittest.TestCase):
    def test_keys_unique(self):
        keys = [c.key for c in db_audit.CHECKS]
        self.assertEqual(len(keys), len(set(keys)))

    def test_every_check_has_a_detail_saying_what_breaks(self):
        """detail 是「發現之後要做什麼」的唯一載體。

        空的 detail 等於「數字紅了但沒人知道那代表什麼」——那種告警兩週內會被
        當成背景噪音（本專案已有這個教訓）。
        """
        for c in db_audit.CHECKS:
            with self.subTest(key=c.key):
                self.assertGreater(len(c.detail), 40, f"{c.key} 的 detail 太短")

    def test_severity_is_one_of_two(self):
        for c in db_audit.CHECKS:
            with self.subTest(key=c.key):
                self.assertIn(c.severity, (db_audit.SEVERITY_ERROR, db_audit.SEVERITY_WARN))

    def test_every_sql_is_a_single_count(self):
        """回單一整數是 run_audit 的契約（它做 `int(row[0])`）。

        多回一欄不會拋錯——只會被靜默忽略，而那一欄很可能正是有用的樣本。
        """
        for c in db_audit.CHECKS:
            with self.subTest(key=c.key):
                self.assertIn("count(*)", c.sql)
                self.assertTrue(c.sql.strip().upper().startswith("SELECT COUNT(*)"))

    def test_every_table_reference_is_research_qualified(self):
        """每個 `FROM`／`JOIN` 的對象只能是 `research.<表>` 或子查詢 `(`。

        這條取代了原本的 `assertIn("research.", sql)`。那個寫法對「完全不碰表」的
        檢查是誤判——`durability_off` 查的是 `current_setting('fsync')`，一張表都不
        需要，要求它提到 `research.` 等於逼它去 join 一張用不到的表。

        **改寫的過程中我的第一版 regex 也錯了兩次，兩次都是假命中**，記在這裡因為
        下一個人很可能踩同一個：

        - `IS DISTINCT FROM r.market` 裡的 `FROM r.` 被當成「schema 叫 r」。`FROM`
          是 SQL 關鍵字但也出現在 `IS DISTINCT FROM` 這個運算子裡，所以要先剔掉。
        - `FROM ( ... ) d`（子查詢包裝）被當成「這條在讀表」。

        所以現在的判準是白名單而不是黑名單：剔掉運算子形式的 FROM，剩下的每一個
        `FROM`／`JOIN` 後面都必須接 `research.` 或 `(`。
        """
        # `IS DISTINCT FROM` / `IS NOT DISTINCT FROM` 的 FROM 是運算子的一部分。
        operator_from = re.compile(r"IS\s+(?:NOT\s+)?DISTINCT\s+FROM", re.IGNORECASE)
        target = re.compile(r"\b(?:FROM|JOIN)\s+(\S+)", re.IGNORECASE)
        for c in db_audit.CHECKS:
            with self.subTest(key=c.key):
                sql = operator_from.sub(" __DISTINCT_OP__ ", c.sql)
                for hit in target.findall(sql):
                    with self.subTest(target=hit):
                        self.assertTrue(
                            hit.startswith("research.") or hit.startswith("("),
                            f"{c.key}: `FROM/JOIN {hit}` 既不是 research.<表> 也不是子查詢"
                            "——不寫 schema 就是在賭 search_path，那不是契約",
                        )

    def test_durability_check_reads_settings_not_tables(self):
        """耐久性那條刻意不碰表：`current_setting()` 是即時的、零成本的。

        釘住它是因為「改成查 pg_settings 表」是很自然的重寫，而那條路會讓這個
        檢查跟著 `statement_timeout` 與全表掃描的命運綁在一起。
        """
        c = next(x for x in db_audit.CHECKS if x.key == "durability_off")
        self.assertIn("current_setting(", c.sql)
        self.assertNotIn(" FROM research.", c.sql)
        for guc in ("fsync", "full_page_writes", "synchronous_commit"):
            with self.subTest(guc=guc):
                self.assertIn(f"current_setting('{guc}')", c.sql)

    def test_durability_is_the_first_check(self):
        """它是唯一「不修會失去全部資料」的一條，必須排在輸出最前面。

        `run_audit` 只按 severity 排序、同級維持宣告順序，所以「最前面」就是
        `CHECKS` 的第一筆。
        """
        self.assertEqual(db_audit.CHECKS[0].key, "durability_off")
        self.assertEqual(db_audit.CHECKS[0].severity, db_audit.SEVERITY_ERROR)

    def test_no_check_mutates(self):
        """唯讀是硬約束：稽核器自己去修等於在無人監督下改生產資料。"""
        forbidden = ("UPDATE ", "DELETE ", "INSERT ", "ALTER ", "DROP ", "TRUNCATE ")
        for c in db_audit.CHECKS:
            up = c.sql.upper()
            for kw in forbidden:
                with self.subTest(key=c.key, kw=kw.strip()):
                    self.assertNotIn(kw, up)

    def test_orphan_checks_use_not_exists_not_inner_join(self):
        """孤兒的定義是「對不到」，方向寫反會回報成「總數」而不是「孤兒數」。

        這是這組 SQL 裡最容易寫反的一種——`JOIN` 回的是有對到的，`NOT EXISTS`
        回的才是孤兒，而兩者都是合法 SQL、都回一個看起來合理的數字。
        """
        for c in db_audit.CHECKS:
            if not c.key.startswith("orphan_"):
                continue
            with self.subTest(key=c.key):
                self.assertIn("NOT EXISTS", c.sql.upper())

    def test_signal_mismatch_uses_is_distinct_from(self):
        """`!=` 對 NULL 回 NULL＝不算命中，會把「一邊有市場一邊沒有」全部漏掉。"""
        c = next(x for x in db_audit.CHECKS if x.key == "signal_market_mismatch")
        self.assertIn("IS DISTINCT FROM", c.sql.upper())
        self.assertNotIn(" != ", c.sql)


class NormDriftFindingTests(unittest.TestCase):
    """`content_norm` 的判準在 Python 端（SQL 沒有 norm_for_match）。"""

    def test_matching_rows_count_zero(self):
        from app.services.textnorm import norm_for_match

        rows = [(s, norm_for_match(s)) for s in ("台 積 電", "AI伺服器", "ＡＩ Server")]
        f = db_audit._norm_drift_finding(rows)
        self.assertEqual(f.count, 0)
        self.assertEqual(f.severity, db_audit.SEVERITY_ERROR)

    def test_drifted_row_is_counted(self):
        rows = [("台 積 電", "台 積 電")]  # 存的還帶空白＝舊定義算的
        self.assertEqual(db_audit._norm_drift_finding(rows).count, 1)

    def test_sample_size_appears_in_label(self):
        """標籤要說「取樣幾列」——0/500 與 0/3 是完全不同的信心水準。"""
        from app.services.textnorm import norm_for_match

        rows = [(s, norm_for_match(s)) for s in ("a", "b")]
        self.assertIn("2", db_audit._norm_drift_finding(rows).label)

    def test_empty_sample_reports_zero_of_zero(self):
        f = db_audit._norm_drift_finding([])
        self.assertEqual(f.count, 0)
        self.assertIn("0", f.label)


class RenderTests(unittest.TestCase):
    @staticmethod
    def _f(key, count, severity=db_audit.SEVERITY_WARN):
        return db_audit.Finding(
            key=key, label=f"標籤-{key}", severity=severity, count=count,
            detail="一段足夠長的說明文字，交代這條發現代表什麼、以及該怎麼處置它。",
        )

    def test_clean_run_says_all_clean(self):
        out = db_audit.render([self._f("a", 0), self._f("b", 0)])
        self.assertIn("全部乾淨", out)
        self.assertNotIn("有發現", out)

    def test_findings_are_marked_and_detail_shown(self):
        out = db_audit.render([self._f("a", 3)])
        self.assertIn("標籤-a：3", out)
        self.assertIn("處置", out)

    def test_zero_count_check_still_printed(self):
        """通過的檢查也要印出來——只印違反者會讓「被 skip」與「通過」長得一樣。"""
        out = db_audit.render([self._f("passed", 0), self._f("failed", 1)])
        self.assertIn("標籤-passed", out)

    def test_warn_only_still_counted_as_findings(self):
        """warn 也算失敗。「warn 不算失敗」會讓 warn 區永遠有東西、從此無人閱讀。"""
        out = db_audit.render([self._f("w", 1, db_audit.SEVERITY_WARN)])
        self.assertIn("1 項有發現", out)
        self.assertIn("錯誤 0", out)


class CliContractTests(unittest.TestCase):
    """退出碼是這支腳本的對外契約（timer / OnFailure 依它分流）。"""

    def test_exit_codes_are_distinct(self):
        self.assertEqual(
            len({db_audit.EXIT_OK, db_audit.EXIT_FINDINGS, db_audit.EXIT_UNKNOWN}), 3
        )

    def test_help_lists_every_skippable_key(self):
        """`--skip` 打錯字目前是靜默忽略，所以 help 必須列全可用值。"""
        out = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "db_audit.py"), "--help"],
            capture_output=True, text=True, timeout=120,
        ).stdout
        for key in db_audit.CHECK_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, out)
        self.assertIn("norm_drift", out)


class MakefileTargetTests(unittest.TestCase):
    """README 從 P3 起就寫著 `make freshness`，而那個 target 一直不存在。

    生產一直是好的（systemd unit 直接呼叫腳本），只有照文件操作的人會撞牆。
    這條測試把「文件承諾的 target 必須真的存在」釘住。
    """

    def setUp(self):
        self.mk = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")

    def test_freshness_target_exists(self):
        self.assertIn("\nfreshness:", self.mk)
        self.assertIn("scripts/check_batch_freshness.py", self.mk)

    def test_db_audit_target_exists(self):
        self.assertIn("\ndb-audit:", self.mk)
        self.assertIn("scripts/db_audit.py", self.mk)

    def test_both_targets_are_self_documenting(self):
        """`make help` 靠 `## ` 註解列出 target；漏了就等於這個入口不存在。"""
        for line in self.mk.splitlines():
            if line.startswith(("freshness:", "db-audit:")):
                with self.subTest(target=line.split(":")[0]):
                    self.assertIn("## ", line, f"{line!r} 缺少 make help 用的 ## 說明")


if __name__ == "__main__":
    unittest.main()
