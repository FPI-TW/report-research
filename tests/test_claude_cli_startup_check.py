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


class LifespanLlmModelCheckTests(unittest.TestCase):
    """自檢依「解析到的模型」決定要檢查什麼（判準的逐列測試在 tests/test_llm_models.py）。"""

    def test_deepseek_without_key_logs_error_but_still_starts(self):
        a, b, c = _startup()
        env = {"LLM_PROVIDER": "deepseek", "DEEPSEEK_API_KEY": ""}
        with a, b, c, patch.dict("os.environ", env), patch.object(llm, "claude_cli_path", return_value="/x"):
            with self.assertLogs("web.server", level="WARNING") as cm:
                with TestClient(server.app) as client:
                    self.assertEqual(client.get("/login").status_code, 200)
        out = "\n".join(cm.output)
        self.assertIn("DEEPSEEK_API_KEY 為空", out)
        self.assertIn("provider=deepseek", out)
        # 網搜在 DeepSeek 表裡仍是 Claude，所以 CLI 檢查照做
        self.assertIn("claude CLI：/x", out)

    def test_unknown_model_name_logs_error(self):
        a, b, c = _startup()
        with a, b, c, patch.dict("os.environ", {"ASK_INTENT_MODEL": "haiku"}), \
                patch.object(llm, "claude_cli_path", return_value="/x"):
            with self.assertLogs("web.server", level="ERROR") as cm:
                with TestClient(server.app):
                    pass
        self.assertIn("未知模型名", "\n".join(cm.output))
        self.assertIn("ask_intent=haiku", "\n".join(cm.output))



class LifespanClosesLlmHttpTests(unittest.TestCase):
    """關機時收掉 DeepSeek 的 AsyncClient 連線池（沒走過 HTTP 路徑時是 no-op）。

    pgvector 版本檢查換成立即回傳：這組只驗關機，不該花 DB 連線逾時的時間（沒有 DB 的
    機器上每次啟動會等 60 秒）。
    """

    def setUp(self):
        from unittest.mock import AsyncMock

        from app.services import db

        p = patch.object(db, "assert_pgvector_version", AsyncMock(return_value="0.8.0"))
        p.start()
        self.addCleanup(p.stop)

    def test_shutdown_awaits_llm_http_aclose(self):
        from unittest.mock import AsyncMock

        from app.services import llm_http

        a, b, c = _startup()
        closer = AsyncMock()
        with a, b, c, patch.object(llm, "claude_cli_path", return_value="/x"), \
                patch.object(llm_http, "aclose", closer):
            with TestClient(server.app):
                closer.assert_not_awaited()
        closer.assert_awaited_once()

    def test_aclose_failure_does_not_break_shutdown(self):
        from unittest.mock import AsyncMock

        from app.services import llm_http

        a, b, c = _startup()
        with a, b, c, patch.object(llm, "claude_cli_path", return_value="/x"), \
                patch.object(llm_http, "aclose", AsyncMock(side_effect=RuntimeError("boom"))):
            with self.assertLogs("web.server", level="ERROR") as cm:
                with TestClient(server.app):
                    pass
        self.assertIn("llm_http.aclose 失敗", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
