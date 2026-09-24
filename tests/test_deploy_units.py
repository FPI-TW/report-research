# tests/test_deploy_units.py
"""`deploy/systemd/` 的靜態守門——把兩條「只寫在註解裡」的規約機械化。

為什麼需要這一支：2026-07-28 一次 unit 收回 repo 的部署，把 `HOME=%h` 裝上主機，
`report-mark-sync.service` 自此連續 10 輪 exit 2（uv 去建 `/root/.cache/uv` 遭
EACCES），整條 NAS 入庫管線停擺約 24 小時。rsync 那一段照常成功、檔案落到本地，
只有匯入沒發生——**看起來像什麼都沒進來，而不是像壞掉**。

`%h` 在系統層 unit 一律解析為 service manager 的家目錄（`/root`），**不受
`User=` 影響**——這是 systemd 的既定語意，不是本專案的設定失誤，所以它會一犯再犯。
同目錄另外兩支 unit 早就硬編碼 `/home/kashionz`，唯獨 sync 用 `%h`，是同一份
部署裡兩種寫法並存、其中一種壞掉。

第二條規約原本來自 `report-mark-web.service.d/path.conf` 的註解「日後用 nvm 升級 node
需同步更新此處的版本路徑」——web 的 drop-in 與 sync 的 env example 兩處的 nvm 路徑必須一致，
漏改一處的症狀是 `claude` 找不到。PR-M 移除 claude CLI 後 drop-in 刪除、sync 的 PATH 只剩 uv
（`SyncPathTests`）。
"""
import re
import unittest
from pathlib import Path

SYSTEMD_DIR = Path(__file__).resolve().parents[1] / "deploy" / "systemd"


def _units() -> list[Path]:
    return sorted(SYSTEMD_DIR.glob("*.service")) + sorted(SYSTEMD_DIR.glob("*.timer"))


def _directives(path: Path, key: str) -> list[str]:
    """取出某個 unit 內所有 `key=` 的值（略過註解行）。"""
    out: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            out.append(value.strip())
    return out


class SystemUnitHomeTests(unittest.TestCase):
    """系統層 unit 不得用 `%h` 指定 HOME。"""

    def test_no_unit_uses_percent_h_for_home(self) -> None:
        offenders = []
        for unit in _units():
            for value in _directives(unit, "Environment"):
                if value.startswith("HOME=") and "%h" in value:
                    offenders.append(f"{unit.name}: {value}")
        self.assertEqual(
            offenders,
            [],
            "系統層 unit 的 %h 解析為 /root（不受 User= 影響），會讓 uv/claude 的"
            " 快取與設定寫入失敗。請改硬編碼家目錄，與 report-mark-web.service 一致。",
        )

    def test_units_that_set_home_agree_on_one_path(self) -> None:
        """三支 unit 若各自硬編碼不同家目錄，等於埋下另一種漂移。"""
        homes = {
            unit.name: value.removeprefix("HOME=")
            for unit in _units()
            for value in _directives(unit, "Environment")
            if value.startswith("HOME=")
        }
        self.assertTrue(homes, "至少 report-mark-sync/web 應顯式設定 HOME")
        self.assertEqual(
            len(set(homes.values())),
            1,
            f"各 unit 的 HOME 不一致：{homes}",
        )


class SyncPathTests(unittest.TestCase):
    """`SYNC_PATH_EXTRA` 只為找到 uv（sync、audit、freshness、backfill、r2-reconcile 都靠它）。

    PR-M 前它還帶 nvm 的 node bin 給 claude CLI，並且必須與 web 的 PATH drop-in 逐字一致
    （`NvmPathAlignmentTests`）；CLI 移除後 drop-in 刪除、nvm 那段不再需要。
    """

    NVM_BIN = re.compile(r"/\.nvm/versions/node/")

    def _live(self, path: Path) -> str:
        return "\n".join(ln for ln in path.read_text(encoding="utf-8").splitlines() if not ln.strip().startswith("#"))

    def test_sync_path_still_finds_uv(self):
        live = self._live(SYSTEMD_DIR / "report-mark-sync.env.example")
        m = re.search(r"^SYNC_PATH_EXTRA=(.+)$", live, re.M)
        self.assertIsNotNone(m, "SYNC_PATH_EXTRA 不能刪：五支 unit 靠它找 uv")
        self.assertIn("/home/kashionz/.local/bin", m.group(1).split(":"))

    def test_no_nvm_or_claude_left_in_deploy(self):
        offenders = []
        for path in sorted(SYSTEMD_DIR.rglob("*")):
            if path.is_file() and self.NVM_BIN.search(self._live(path)):
                offenders.append(path.name)
        self.assertEqual(offenders, [], "nvm 的 node bin 只給 claude CLI 用，PR-M 後不該再出現")

    def test_web_path_dropin_is_gone(self):
        self.assertFalse((SYSTEMD_DIR / "report-mark-web.service.d").exists())


LLM_ENV_EXAMPLE = SYSTEMD_DIR / "report-mark-llm.env.example"
SYNC_ENV_EXAMPLE = SYSTEMD_DIR / "report-mark-sync.env.example"
LLM_ENV_PATH = "-/etc/default/report-mark-llm"


def _live_lines(path: Path) -> list[str]:
    return [
        ln for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]


class LlmEnvFileTests(unittest.TestCase):
    """DeepSeek 金鑰檔（PR-10）：只給 sync unit、格式兩種讀者都讀得懂、範例檔不帶金鑰。"""

    def test_only_sync_unit_loads_llm_env(self) -> None:
        """環境變數裡有金鑰的行程越少越好：其他 unit 都不呼叫 LLM。"""
        loaders = sorted(
            unit.name for unit in _units()
            if any("report-mark-llm" in v for v in _directives(unit, "EnvironmentFile"))
        )
        self.assertEqual(loaders, ["report-mark-sync.service"])

    def test_sync_loads_llm_env_after_shared_file(self) -> None:
        files = _directives(SYSTEMD_DIR / "report-mark-sync.service", "EnvironmentFile")
        self.assertIn(LLM_ENV_PATH, files, "要用 `-` 前綴：金鑰檔還沒安裝時 unit 仍要能啟動")
        self.assertIn("-/etc/default/report-mark-sync", files)
        self.assertLess(files.index("-/etc/default/report-mark-sync"), files.index(LLM_ENV_PATH))

    def test_llm_example_key_is_empty(self) -> None:
        keys = [ln.partition("=")[2] for ln in _live_lines(LLM_ENV_EXAMPLE) if ln.startswith("DEEPSEEK_API_KEY=")]
        self.assertEqual(keys, [""], "範例檔的金鑰必須存在且為空")

    def test_llm_example_has_no_inline_comment_quotes_or_duplicates(self) -> None:
        """systemd 不剝行尾註解（# 之後會成為值），引號兩種讀者的處理也不同；重複鍵兩者取值相反。"""
        keys = []
        for ln in _live_lines(LLM_ENV_EXAMPLE):
            key, sep, value = ln.partition("=")
            self.assertTrue(sep, ln)
            self.assertNotIn("#", value, ln)
            self.assertFalse(any(q in value for q in "\"'"), ln)
            self.assertEqual(key, key.strip(), ln)
            keys.append(key)
        self.assertEqual(len(keys), len(set(keys)))

    def test_llm_example_has_no_retired_fallback_knob(self) -> None:
        self.assertNotIn("LLM_CONTENT_FALLBACK", LLM_ENV_EXAMPLE.read_text(encoding="utf-8"))

    def test_llm_example_header_has_install_instructions(self) -> None:
        text = LLM_ENV_EXAMPLE.read_text(encoding="utf-8")
        self.assertIn(
            "sudo install -m 0640 -o root -g kashionz deploy/systemd/report-mark-llm.env.example"
            " /etc/default/report-mark-llm",
            text,
        )
        self.assertIn("sudoedit", text)
        self.assertIn("source", text)

    def test_sync_example_has_no_llm_keys(self) -> None:
        """金鑰與模型旋鈕只放 llm 檔：共用檔是其他 unit 也讀、`db_backup.sh` 會逐鍵讀的檔。"""
        offenders = [
            ln for ln in _live_lines(SYNC_ENV_EXAMPLE)
            if re.match(r"(DEEPSEEK_|LLM_|[A-Z_]+_MODEL=)", ln)
        ]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
