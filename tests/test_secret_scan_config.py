# tests/test_secret_scan_config.py
"""`.gitleaks.toml` 的守門。純靜態，不需要 gitleaks 二進位。

**為什麼需要測試一個設定檔**：`[extend] useDefault = true` 這兩行**錯了完全沒有症狀**。
第一版我寫成頂層 `extends = true`（不是 gitleaks 的語法），gitleaks 不報錯、直接載入
一個**零規則**的設定，於是工作樹與全歷史掃描都印「no leaks found」——那不是乾淨，
是掃描器根本沒在看。CI 會永遠綠。

真正的行為驗證只能靠拿「內建規則一定會抓的形狀」去探（GitHub PAT `ghp_` + 36 字元
最可靠），那需要二進位、不適合放進 pytest。這裡守的是靜態面：那兩行在不在、
allowlist 有沒有寬到失去意義。
"""
import sys
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

CONFIG = REPO_ROOT / ".gitleaks.toml"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"


class ConfigLoadsDefaultRulesTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(CONFIG.exists(), ".gitleaks.toml 不見了")
        self.raw = CONFIG.read_text(encoding="utf-8")
        self.cfg = tomllib.loads(self.raw)

    def test_extends_default_ruleset(self):
        """沒有這一段，整個掃描器就是空的（而且永遠綠）。"""
        self.assertIn("extend", self.cfg, "缺 [extend] 區段 ⇒ 內建規則不會載入")
        self.assertIs(
            self.cfg["extend"].get("useDefault"), True,
            "[extend] useDefault 必須是 true",
        )

    def test_no_top_level_extends_typo(self):
        """頂層 `extends`（多個 s）是我第一版寫的錯法，gitleaks 靜默忽略它。"""
        self.assertNotIn(
            "extends", self.cfg,
            "頂層 `extends` 不是 gitleaks 的語法——gitleaks 不報錯，只是不載入規則",
        )

    def test_allowlist_is_narrow(self):
        """allowlist 只准放行已知的測試假值，不准放行整個目錄。

        放行 `tests/` 會讓「有人把真 secret 貼進測試檔」從此掃不到——而這個 repo
        的實際事故（2026-07-29）正是測試碰到生產憑證。
        """
        al = self.cfg.get("allowlist", {})
        self.assertNotIn("paths", al, "不得以路徑放行——真 secret 貼進那些路徑就掃不到了")
        self.assertNotIn("files", al, "同上")
        self.assertTrue(al.get("regexes"), "allowlist 應以具體字串 regex 放行")

    def test_allowlisted_regex_cannot_swallow_arbitrary_secrets(self):
        """放行用的 regex 不得寬到能吃下任意字串。

        `fixed-test-secret-.*` 這種寫法會讓 `fixed-test-secret-<真的 base64>` 也被
        放過。字元類要收窄，且不得出現 `.*` / `.+` / `\S+` 這類任意匹配。
        """
        for rx in self.cfg["allowlist"]["regexes"]:
            with self.subTest(regex=rx):
                for wildcard in (".*", ".+", r"\S+", r"[^\s]+"):
                    self.assertNotIn(
                        wildcard, rx,
                        f"放行 regex 含任意匹配 {wildcard!r}，會連真 secret 一起放過",
                    )
                # 反向：它必須真的匹配得到已知假值，否則 allowlist 是死的、
                # 22 個測試檔會全部誤報
                self.assertRegex("fixed-test-secret-0123456789", rx)


class DeepSeekKeyRuleTests(unittest.TestCase):
    """內建規則集抓不到 DeepSeek 金鑰（`sk-` ＋ 32 hex），靠這條自訂規則。

    樣本一律在執行期組出來：寫成字面值的話，這個測試檔本身就會被該規則掃到。
    Python `re` 與 gitleaks 的 RE2 對這個 regex（`\\b`、字元類、量詞）語意相同。
    """

    HEX32 = "0123456789abcdef" * 2

    @classmethod
    def setUpClass(cls):
        cfg = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
        rules = [r for r in cfg.get("rules", []) if r.get("id") == "deepseek-api-key"]
        cls.rule = rules[0] if rules else {}
        cls.allow = cfg.get("allowlist", {}).get("regexes", [])

    def setUp(self):
        self.assertTrue(self.rule, "缺 id=deepseek-api-key 的自訂規則")

    def test_matches_key_shape(self):
        import re

        sample = "sk-" + self.HEX32
        for text in (sample, f"DEEPSEEK_API_KEY={sample}", f'{{"key": "{sample}"}}',
                     f"Authorization: Bearer {sample}"):
            with self.subTest(text=text[:20]):
                self.assertTrue(re.search(self.rule["regex"], text))

    def test_rejects_other_lengths(self):
        """多一個或少一個字都不算——別家更長的 `sk-` 金鑰不該被歸到這條。"""
        import re

        for body in (self.HEX32[:-1], self.HEX32 + "a", self.HEX32 + "Z"):
            with self.subTest(n=len(body)):
                self.assertIsNone(re.search(self.rule["regex"], "sk-" + body))

    def test_keyword_prefilter_present(self):
        """gitleaks 先以 keywords 粗篩；缺了它規則照跑但每一行都要跑 regex。"""
        self.assertIn("sk-", self.rule.get("keywords", []))

    def test_allowlist_does_not_swallow_key(self):
        """測試假值的放行 regex 不得連真金鑰一起放過。"""
        import re

        sample = "sk-" + self.HEX32
        for rx in self.allow:
            with self.subTest(regex=rx):
                self.assertIsNone(re.search(rx, sample))


class WorkflowWiringTests(unittest.TestCase):
    """比對**解析後的 YAML**，不是原始文字。

    第一版拿 `self.wf`（原始文字）做 `assertIn` / `count`，於是兩條當場誤判：
    註解裡為了解釋理由而提到的 `--redact` 與 `gitleaks/gitleaks-action` 都被算進去。
    「寫在註解裡」與「真的會執行」是兩件事，而這種測試要驗的顯然是後者。
    """

    @classmethod
    def setUpClass(cls):
        import yaml

        wf = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.job = wf["jobs"]["secrets"]
        cls.steps = cls.job["steps"]
        cls.runs = [st["run"] for st in cls.steps if "run" in st]
        cls.uses = [st["uses"] for st in cls.steps if "uses" in st]

    def _gitleaks_cmds(self) -> list[str]:
        """真正會執行的 gitleaks 呼叫行（排除安裝步驟的 `gitleaks version`）。"""
        out = []
        for run in self.runs:
            for line in run.splitlines():
                ln = line.strip()
                if ln.startswith("gitleaks ") and not ln.startswith("gitleaks version"):
                    out.append(ln)
        return out

    def test_job_name_is_stable(self):
        """job 的 `name` 就是分支保護要指定的 check 名稱——改它等於讓保護指向空的。"""
        self.assertEqual(self.job["name"], "secret 掃描（gitleaks）")

    def test_uses_the_repo_config(self):
        cmds = self._gitleaks_cmds()
        self.assertTrue(cmds, "找不到任何 gitleaks 呼叫")
        for c in cmds:
            with self.subTest(cmd=c):
                self.assertIn("-c .gitleaks.toml", c)

    def test_scans_both_history_and_worktree(self):
        """兩步都要：全歷史抓「先 commit 再移除」，工作樹抓未追蹤檔。"""
        cmds = " ".join(self._gitleaks_cmds())
        self.assertIn("gitleaks git .", cmds)
        self.assertIn("gitleaks dir .", cmds)

    def test_history_scan_needs_full_clone(self):
        """`fetch-depth: 0`——淺 clone 只看得到最終樹，掃歷史等於沒掃。"""
        checkouts = [
            st for st in self.steps
            if str(st.get("uses", "")).startswith("actions/checkout")
        ]
        self.assertTrue(checkouts, "缺 checkout 步驟")
        self.assertEqual(checkouts[0].get("with", {}).get("fetch-depth"), 0)

    def test_every_scan_redacts(self):
        """CI log 對整個組織可見且保存 90 天，印出 secret 等於又洩漏一次。"""
        for c in self._gitleaks_cmds():
            with self.subTest(cmd=c):
                self.assertIn("--redact", c)

    def test_version_is_pinned(self):
        """規則集隨版本變動；浮動版本＝某天 CI 突然紅了而 diff 是空的。"""
        versions = [
            st.get("env", {}).get("GITLEAKS_VERSION")
            for st in self.steps
            if st.get("env", {}).get("GITLEAKS_VERSION")
        ]
        self.assertTrue(versions, "gitleaks 版本必須釘死在 env 裡")
        self.assertRegex(str(versions[0]), r"^[0-9]+\.[0-9]+\.[0-9]+$")

    def test_does_not_use_the_marketplace_action(self):
        """`gitleaks/gitleaks-action` 對 organization repo 要求 GITLEAKS_LICENSE，
        而本 repo 在 FPI-TW 組織下——用它會直接失敗。

        只看 `uses:`：註解裡提到它是刻意的（解釋為何不用）。
        """
        for u in self.uses:
            with self.subTest(uses=u):
                self.assertNotIn("gitleaks-action", u)

    def test_job_has_no_needs(self):
        """20 秒的掃描沒有理由排在 3 分鐘的前端 job 後面。"""
        self.assertNotIn("needs", self.job)


if __name__ == "__main__":
    unittest.main()
