# tests/test_env_loading.py
import os
import subprocess
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = REPO_ROOT / ".env"


class EnvLoadingTests(unittest.TestCase):
    def test_server_loads_dotenv_without_shell_expansion(self):
        original = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else None
        try:
            ENV_PATH.write_text(
                "\n".join(
                    [
                        "REPORT_MARK_ACCESS_USERNAME=tester",
                        'REPORT_MARK_ACCESS_PASSWORD="prefix-$HOME-suffix"',
                        "REPORT_MARK_SESSION_SECRET=test-secret",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.pop("REPORT_MARK_ACCESS_USERNAME", None)
            env.pop("REPORT_MARK_ACCESS_PASSWORD", None)
            env.pop("REPORT_MARK_SESSION_SECRET", None)
            env["HOME"] = "/should-not-expand"
            proc = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os; import web.server; "
                        "print(os.environ['REPORT_MARK_ACCESS_PASSWORD'])"
                    ),
                ],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=5,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertEqual(proc.stdout.strip(), "prefix-$HOME-suffix")
        finally:
            if original is None:
                ENV_PATH.unlink(missing_ok=True)
            else:
                ENV_PATH.write_text(original, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
