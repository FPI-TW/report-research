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

第二條規約來自 `report-mark-web.service.d/path.conf` 的註解「日後用 nvm 升級 node
需同步更新此處的版本路徑」——它有兩處（web 的 drop-in 與 sync 的 env example），
漏改一處的症狀是 `claude` 找不到；而 sync 那一側的兩段批次掛在 `|| log` 之後，
只會留下一行「best-effort，已略過」，unit 不會變紅（無聲漏跑）。
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


class NvmPathAlignmentTests(unittest.TestCase):
    """`claude` 的 nvm bin 路徑在兩處出現，必須逐字一致。"""

    NVM_BIN = re.compile(r"/home/[^:\s'\"]+/\.nvm/versions/node/[^:\s'\"]+/bin")

    def _nvm_paths(self, path: Path) -> set[str]:
        text = path.read_text(encoding="utf-8")
        # 只看實際生效的設定行，註解裡的示例不算
        live = "\n".join(
            ln for ln in text.splitlines() if not ln.strip().startswith("#")
        )
        return set(self.NVM_BIN.findall(live))

    def test_web_dropin_and_sync_env_use_same_node_bin(self) -> None:
        web = self._nvm_paths(SYSTEMD_DIR / "report-mark-web.service.d" / "path.conf")
        sync = self._nvm_paths(SYSTEMD_DIR / "report-mark-sync.env.example")
        self.assertTrue(web, "path.conf 應含 nvm node bin（claude 所在）")
        self.assertTrue(sync, "sync env example 應含 nvm node bin（claude 所在）")
        self.assertEqual(
            web,
            sync,
            "web drop-in 與 sync 的 SYNC_PATH_EXTRA 指向不同 node 版本；"
            "升級 nvm node 時兩處必須一起改，否則 sync 那側的摘要／摘錄會"
            "以 FileNotFoundError 無聲略過。",
        )

    def test_no_nvm_current_symlink_assumed(self) -> None:
        """nvm 沒有 `current` 這個符號連結（那是 n / nodenv 的慣例）。"""
        offenders = []
        for path in [
            SYSTEMD_DIR / "report-mark-web.service.d" / "path.conf",
            SYSTEMD_DIR / "report-mark-sync.env.example",
        ]:
            if "/node/current/" in "\n".join(
                ln for ln in path.read_text(encoding="utf-8").splitlines()
                if not ln.strip().startswith("#")
            ):
                offenders.append(path.name)
        self.assertEqual(
            offenders,
            [],
            "nvm 路徑寫成 node/current/bin 會整段落空，claude 將找不到。",
        )


if __name__ == "__main__":
    unittest.main()
