"""啟動期 claude CLI 自檢：找不到要說出來，但不擋啟動。

`/healthz` 只探 DB。claude 不在 PATH 上時每一題問答都回 SSE error，而健康檢查照樣 ok
（2026-09-02 原生安裝路徑漂移即此型態）。這裡釘住兩件事：lifespan 真的有呼叫自檢、
找不到時 App 仍然起得來（檢索、閱讀頁、雷達、簡報的讀取都不需要 CLI）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.services import llm  # noqa: E402
from web import server  # noqa: E402


def _startup():
    return (
        patch.object(sys, "argv", ["uvicorn", "web.server:app"]),
        patch("web.deps.embed_texts", return_value=[[0.0]]),
        patch("web.deps.rerank_warmup", return_value=True),
    )


class ClaudeCliPathTests(unittest.TestCase):
    def test_build_cmd_and_probe_share_one_binary_name(self):
        """探的與實際 spawn 的必須是同一個名字，否則自檢綠、問答照樣壞。"""
        self.assertEqual(llm._build_cmd("m", None, False)[0], llm.CLAUDE_BIN)
        with patch("app.services.llm.shutil.which", return_value="/x/claude") as which:
            self.assertEqual(llm.claude_cli_path(), "/x/claude")
        which.assert_called_once_with(llm.CLAUDE_BIN)


class LifespanClaudeCheckTests(unittest.TestCase):
    def test_missing_cli_logs_error_but_still_starts(self):
        a, b, c = _startup()
        with a, b, c, patch.object(llm, "claude_cli_path", return_value=None):
            with self.assertLogs("web.server", level="ERROR") as cm:
                with TestClient(server.app) as client:
                    self.assertEqual(client.get("/login").status_code, 200)
        self.assertIn("claude CLI 不在 PATH 上", "\n".join(cm.output))

    def test_present_cli_reports_resolved_path(self):
        a, b, c = _startup()
        with a, b, c, patch.object(llm, "claude_cli_path", return_value="/opt/bin/claude"):
            with self.assertLogs("web.server", level="WARNING") as cm:
                with TestClient(server.app):
                    pass
        out = "\n".join(cm.output)
        self.assertIn("/opt/bin/claude", out)
        self.assertNotIn("不在 PATH 上", out)


if __name__ == "__main__":
    unittest.main()
