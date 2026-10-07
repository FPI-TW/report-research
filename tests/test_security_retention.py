"""`scripts/security_retention.py`（每日保留期清除）：退出碼、unit 契約、不載入檢索／嵌入／LLM 模組。

「只刪超過 365 天」的 SQL 在 tests/test_security_ops_db.py；這裡不連 DB（purge 以假函式替換）。
"""
from __future__ import annotations

import ast
import asyncio
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import security_ops  # noqa: E402
from scripts import security_retention as sr  # noqa: E402

SYSTEMD_DIR = REPO_ROOT / "deploy" / "systemd"
SERVICE = SYSTEMD_DIR / "report-mark-security-retention.service"
TIMER = SYSTEMD_DIR / "report-mark-security-retention.timer"
# 每日／每次登入都會走到的路徑：不得把 torch、檢索、嵌入或 LLM 模組拉進來。
LIGHT_FILES = ("scripts/security_retention.py", "scripts/audit_anchor.py", "app/services/security_ops.py")
HEAVY_PREFIXES = ("torch", "FlagEmbedding", "transformers", "app.services.retrieval", "app.services.embed",
                  "app.services.answer", "app.services.llm", "app.services.rerank", "scripts._llm_env")


def _directives(path: Path, key: str) -> list[str]:
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, _, value = stripped.partition("=")
        if name.strip() == key:
            out.append(value.strip())
    return out


class RunTests(unittest.TestCase):
    def test_ok_prints_counts(self):
        async def purge():
            return security_ops.PurgeResult(auth_event_days=365, session_days=90, auth_events=12, sessions=3)

        self.assertEqual(asyncio.run(sr.run(purge)), sr.EXIT_OK)

    def test_db_failure_is_exit_2(self):
        async def purge():
            raise OSError("connection refused")

        self.assertEqual(asyncio.run(sr.run(purge)), sr.EXIT_ERROR)


class ImportHygieneTests(unittest.TestCase):
    def test_no_heavy_imports_in_source(self):
        for rel in LIGHT_FILES:
            tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
            names = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.append(node.module)
                    names += [f"{node.module}.{a.name}" for a in node.names]
            with self.subTest(file=rel):
                bad = [n for n in names if n.startswith(HEAVY_PREFIXES)]
                self.assertEqual(bad, [])

    def test_importing_does_not_load_torch_or_retrieval(self):
        code = (
            "import sys; sys.path.insert(0, %r)\n"
            "import scripts.security_retention, scripts.audit_anchor\n"
            "heavy = [m for m in sys.modules if m.split('.')[0] in ('torch', 'FlagEmbedding', 'transformers') "
            "or m.startswith(('app.services.retrieval', 'app.services.embed', 'app.services.answer'))]\n"
            "print(','.join(sorted(heavy)))\n"
        ) % str(REPO_ROOT)
        env = {"PATH": "/usr/bin:/bin", "REPORT_MARK_DB_URL": "postgresql+asyncpg://nobody:x@127.0.0.1:45999/none",
               "HF_HUB_OFFLINE": "1", "HOME": str(Path.home())}
        p = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, env=env,
                           cwd=str(REPO_ROOT))
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "")


class RetentionUnitTests(unittest.TestCase):
    def test_service(self):
        self.assertEqual(_directives(SERVICE, "Type"), ["oneshot"])
        self.assertEqual(_directives(SERVICE, "OnFailure"), ["report-mark-alert@%n.service"])
        self.assertIn("scripts/security_retention.py", _directives(SERVICE, "ExecStart")[0])

    def test_timer_daily_after_backup_and_anchor(self):
        self.assertEqual(_directives(TIMER, "OnCalendar"), ["*-*-* 04:45:00"])
        self.assertEqual(_directives(TIMER, "Persistent"), ["true"])
        backup = _directives(SYSTEMD_DIR / "report-mark-backup.timer", "OnCalendar")[0]
        anchor = _directives(SYSTEMD_DIR / "report-mark-audit-anchor.timer", "OnCalendar")[0]
        self.assertLess(backup.split()[-1], "04:45:00")
        self.assertLess(anchor.split()[-1], "04:45:00")


if __name__ == "__main__":
    unittest.main()
