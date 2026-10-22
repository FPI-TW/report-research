"""`scripts/ensure_backup_mount.sh`：寫 NAS 備份落點的 unit 在執行前自己把落點掛上。

2026-10-22 辦公室主機的 WSL 重開後，NAS 備份落點要等到 03:30 備份才會再掛上，刪帳排程每小時以 rc=2 告警。
這支以假的 `mountpoint`／`sudo`／掛載輔助程式真的跑一次腳本：不需要 NAS、不需要 root。
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "ensure_backup_mount.sh"
SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
NAS_UNITS = ("report-mark-delete-accounts.service", "report-mark-replay-deletions.service",
             "report-mark-audit-anchor.service")


def _write_exec(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8")
    path.chmod(0o755)


class EnsureBackupMountRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.mounted = self.root / "mounted"   # 存在＝落點已掛
        self.sudo_log = self.root / "sudo.log"
        _write_exec(self.bin / "mountpoint", f'[ -e "{self.mounted}" ]\n')
        _write_exec(self.bin / "sudo", f'printf "%s\\n" "$*" >> "{self.sudo_log}"\n[ "$1" = -n ] && shift\nexec "$@"\n')
        self.helper = self.root / "mount-nas-backup"

    def _run(self) -> subprocess.CompletedProcess:
        env = {**os.environ, "PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
               "REPORT_MARK_BACKUP_MOUNT_HELPER": str(self.helper), "REPORT_MARK_BACKUP_MOUNT": "/mnt/nas-backup"}
        return subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, timeout=30, env=env)

    def _sudo_calls(self) -> list[str]:
        return self.sudo_log.read_text(encoding="utf-8").splitlines() if self.sudo_log.exists() else []

    def test_host_without_helper_skips(self) -> None:
        """EC2：沒有 NAS，也沒裝掛載輔助程式（tombstone 寫本機磁碟）。"""
        got = self._run()
        self.assertEqual(got.returncode, 0)
        self.assertIn("略過", got.stdout)
        self.assertEqual(self._sudo_calls(), [])

    def test_already_mounted_does_nothing(self) -> None:
        _write_exec(self.helper, "exit 1\n")
        self.mounted.touch()
        got = self._run()
        self.assertEqual((got.returncode, got.stdout, got.stderr), (0, "", ""))
        self.assertEqual(self._sudo_calls(), [])

    def test_mounts_via_sudo_without_password(self) -> None:
        _write_exec(self.helper, f'touch "{self.mounted}"\n')
        got = self._run()
        self.assertEqual(got.returncode, 0)
        self.assertEqual(self._sudo_calls(), [f"-n {self.helper}"])
        self.assertTrue(self.mounted.exists())
        self.assertEqual(got.stderr, "")

    def test_mount_failure_warns_but_does_not_block_the_main_script(self) -> None:
        """失敗交給主程式：落點不存在時它以 rc=2 告警，訊息比「ExecStartPre 失敗」清楚。"""
        for body in ("exit 32\n", "exit 0\n"):  # 輔助程式失敗；或回 0 卻沒有真的掛上
            with self.subTest(body=body):
                _write_exec(self.helper, body)
                got = self._run()
                self.assertEqual(got.returncode, 0)
                self.assertIn("掛載 /mnt/nas-backup 失敗", got.stderr)


class NasUnitsMountFirstTests(unittest.TestCase):
    def test_units_writing_the_nas_destination_mount_it_first(self) -> None:
        for name in NAS_UNITS:
            with self.subTest(unit=name):
                text = (SYSTEMD_DIR / name).read_text(encoding="utf-8")
                pre = re.search(r"^ExecStartPre=(.*)$", text, re.M)
                assert pre is not None, f"{name}：落點在 NAS 的 unit 要先掛上落點"
                self.assertIn("scripts/ensure_backup_mount.sh", pre.group(1))
                self.assertNotIn("ExecStartPre=-", text)  # 腳本自己 exit 0；不用 `-` 吞掉它找不到的錯
                self.assertNotIn("ExecStartPre=+", text)  # 不提權：沿用備份的 sudoers
                self.assertLess(text.index("ExecStartPre="), text.index("\nExecStart="))

    def test_backup_keeps_mounting_in_its_own_script(self) -> None:
        """備份腳本自己掛（含 EROFS／EACCES 的診斷），不重複掛。"""
        self.assertNotIn("ExecStartPre", (SYSTEMD_DIR / "report-mark-backup.service").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
