"""啟動期 LLM 自檢：設定不對要說出來，但不擋啟動。

`/healthz` 只探 DB。DeepSeek 金鑰沒填、模型名不在白名單、網搜總閘開著卻沒有後端時，問答每一題都回
SSE error（或時效題一律婉拒），而健康檢查照樣 ok。這裡釘住兩件事：lifespan 真的有呼叫自檢、設定不對時
App 仍然起得來（檢索、閱讀頁、雷達、簡報的讀取都不需要 LLM）。判準的逐列測試在 tests/test_llm_models.py。

PR-M 前這支叫 test_claude_cli_startup_check.py、另外檢查 claude 在不在 PATH（2026-09-02 原生安裝路徑
漂移）；CLI backend 移除後那一段連同 `llm.claude_cli_path` 一起刪除。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from web import server  # noqa: E402


def _startup():
    return (
        patch.object(sys, "argv", ["uvicorn", "web.server:app"]),
        patch("web.deps.embed_texts", return_value=[[0.0]]),
        patch("web.deps.rerank_warmup", return_value=True),
    )


class LifespanLlmModelCheckTests(unittest.TestCase):
    """自檢依「解析到的模型」與網搜總閘決定要說什麼。"""

    def _run(self, env: dict, *, level: str = "WARNING", pop: tuple[str, ...] = ()):
        a, b, c = _startup()
        # `_SETTINGS` 換成 None：自檢讀 `get_settings().ask_enable_web`，要照這裡的環境重新載入（離開時還原快取）
        with a, b, c, patch.dict("os.environ", env), patch("app.config._SETTINGS", None):
            for key in pop:
                os.environ.pop(key, None)
            with self.assertLogs("web.server", level=level) as cm:
                with TestClient(server.app) as client:
                    self.assertEqual(client.get("/login").status_code, 200)
        return cm

    def test_deepseek_without_key_logs_error_but_still_starts(self):
        cm = self._run({"DEEPSEEK_API_KEY": ""})
        out = "\n".join(cm.output)
        self.assertIn("DEEPSEEK_API_KEY 為空", out)
        self.assertIn("provider=deepseek", out)
        for task in ("ask_answer", "ask_intent", "faithfulness"):
            self.assertIn(f"{task}=deepseek-flash", out)
        self.assertIn("ask_web=", out)  # 網搜沒有模型（空字串）
        self.assertNotIn("claude CLI：", out)  # PR-M 前的 PATH 自檢不再出現

    def test_unset_provider_with_key_is_quiet(self):
        """預設（provider 未設、有金鑰、總閘預設關）：沒有任何 ERROR。"""
        cm = self._run({"DEEPSEEK_API_KEY": "fixed-test-secret-deepseek0"}, pop=("LLM_PROVIDER", "ASK_ENABLE_WEB"))
        out = "\n".join(cm.output)
        self.assertIn("provider=deepseek", out)
        self.assertEqual([r.getMessage() for r in cm.records if r.levelname == "ERROR"], [])
        self.assertNotIn("fixed-test-secret-deepseek0", out)

    def test_claude_model_name_logs_error(self):
        """PR-M：claude-* 不再是合法的模型名（沒有 CLI backend），與打錯字同樣記 ERROR。"""
        for name in ("claude-haiku-4-5", "haiku"):
            with self.subTest(name=name):
                cm = self._run({"ASK_INTENT_MODEL": name}, level="ERROR")
                out = "\n".join(cm.output)
                self.assertIn("不在 DeepSeek 白名單", out)
                self.assertIn(f"ask_intent={name}", out)

    def test_web_gate_on_logs_error(self):
        cm = self._run({"DEEPSEEK_API_KEY": "fixed-test-secret-deepseek0", "ASK_ENABLE_WEB": "1"}, level="ERROR")
        self.assertIn("ASK_ENABLE_WEB 開著，但網搜沒有後端", "\n".join(cm.output))

    def test_retired_provider_logs_error_and_still_starts(self):
        """退役值（claude_only 等）：web 記 ERROR、照常以 deepseek 解析（線上容錯）。"""
        from app.services import llm_models

        for value in ("claude_only", "claude_cli"):
            with self.subTest(value=value):
                llm_models._LOGGED.clear()
                with self.assertLogs("app.services.llm_models", level="ERROR") as errs:
                    cm = self._run({"LLM_PROVIDER": value, "DEEPSEEK_API_KEY": "fixed-test-secret-deepseek0"})
                self.assertIn("provider=deepseek", "\n".join(cm.output))
                self.assertIn("已隨 claude CLI 退役", "\n".join(errs.output))


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
        with a, b, c, patch.object(llm_http, "aclose", closer):
            with TestClient(server.app):
                closer.assert_not_awaited()
        closer.assert_awaited_once()

    def test_aclose_failure_does_not_break_shutdown(self):
        from unittest.mock import AsyncMock

        from app.services import llm_http

        a, b, c = _startup()
        with a, b, c, patch.object(llm_http, "aclose", AsyncMock(side_effect=RuntimeError("boom"))):
            with self.assertLogs("web.server", level="ERROR") as cm:
                with TestClient(server.app):
                    pass
        self.assertIn("llm_http.aclose 失敗", "\n".join(cm.output))


if __name__ == "__main__":
    unittest.main()
