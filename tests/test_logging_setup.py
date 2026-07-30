# tests/test_logging_setup.py
"""`app/logging_setup.py` 的行為，以及 `web/server.py` 對它的接線。

守的是一個**靜默**缺陷：沒有 root logger 設定時，`app.services.*` 的
`logger.info(...)` 在呼叫點就被丟棄（root 無 handler、effective level 為
WARNING，落到 `logging.lastResort`）。沒有任何錯誤、沒有任何訊號——2026-07-29
查生產 journald，近 14 天 10 次 `/api/ask` 對應 `qa_timing` **0 筆**。

接線順序用靜態驗（比照 `tests/test_env_loading.py` 的 ServerWiringTests）：
真的 import 一次 `web.server` 會連帶載入整組服務模組，慢且會動到全域 logging
狀態，而順序這件事靜態就看得出來。
"""
import logging
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.logging_setup import (  # noqa: E402
    _reset_for_tests,
    configure_logging,
    logging_config,
)

_SERVER_SRC = (REPO_ROOT / "web" / "server.py").read_text(encoding="utf-8")


class _LoggingSandbox(unittest.TestCase):
    """dictConfig 會改動整個行程的 logging 狀態，測完必須完整還原。"""

    def setUp(self):
        root = logging.getLogger()
        self._handlers = root.handlers[:]
        self._level = root.level
        self._disabled = root.manager.disable
        _reset_for_tests()

    def tearDown(self):
        root = logging.getLogger()
        root.handlers[:] = self._handlers
        root.setLevel(self._level)
        root.manager.disable = self._disabled
        _reset_for_tests()


class ConfigShapeTests(unittest.TestCase):
    def test_declares_root_logger(self):
        """關鍵在 root：uvicorn 的 LOGGING_CONFIG 沒有 root 鍵，所以 app 的
        logger 才會落到 lastResort。補個別 logger 是治標。"""
        cfg = logging_config("INFO")
        self.assertIn("root", cfg)
        self.assertEqual(cfg["root"]["level"], "INFO")
        self.assertEqual(cfg["root"]["handlers"], ["stderr"])

    def test_does_not_declare_uvicorn_loggers(self):
        """宣告它們會蓋掉 uvicorn 自己的設定——access log 是目前唯一還能用的日誌，
        不能在修 app 日誌的同時把它弄壞。"""
        cfg = logging_config("INFO")
        self.assertNotIn("loggers", cfg)

    def test_keeps_existing_loggers_enabled(self):
        """uvicorn 在 import app 之前就建好自己的 logger；True 會把它們全部關掉。"""
        self.assertIs(logging_config("INFO")["disable_existing_loggers"], False)

    def test_format_carries_level_and_logger_name(self):
        """現況缺的正是這兩者：lastResort 只印裸訊息，journald 裡看到的是
        `uv[535]: ask failed`——不知道等級、也不知道哪個模組發的。"""
        fmt = logging_config("INFO")["formatters"]["standard"]["format"]
        self.assertIn("%(levelname)s", fmt)
        self.assertIn("%(name)s", fmt)


class ConfigureLoggingTests(_LoggingSandbox):
    def test_app_logger_info_is_emitted_after_configure(self):
        """這一題就是缺陷本體的反面：設定前 INFO 被丟棄，設定後收得到。"""
        logger = logging.getLogger("app.services.answer")

        before = logger.isEnabledFor(logging.INFO)
        configure_logging("INFO")
        after = logger.isEnabledFor(logging.INFO)

        self.assertTrue(after, "設定後 app.services.* 的 INFO 必須送得出去")
        # before 在乾淨環境下是 False；pytest 等外部可能已裝過 handler，
        # 故不硬性斷言 before，只確保設定後為真並留下對照。
        self.assertIsInstance(before, bool)

    def test_records_reach_a_handler(self):
        configure_logging("INFO")
        root = logging.getLogger()
        self.assertTrue(root.handlers, "root 必須有 handler，否則走 lastResort")
        captured: list[logging.LogRecord] = []

        class _Capture(logging.Handler):
            def emit(self, record):
                captured.append(record)

        cap = _Capture()
        root.addHandler(cap)
        try:
            logging.getLogger("app.services.answer").info("qa_timing total_ms=1")
        finally:
            root.removeHandler(cap)
        self.assertEqual([r.getMessage() for r in captured], ["qa_timing total_ms=1"])

    def test_level_is_honoured(self):
        configure_logging("WARNING")
        self.assertFalse(
            logging.getLogger("app.services.answer").isEnabledFor(logging.INFO)
        )

    def test_is_idempotent(self):
        configure_logging("INFO")
        n = len(logging.getLogger().handlers)
        configure_logging("INFO")
        self.assertEqual(len(logging.getLogger().handlers), n)


class LevelValidationTests(unittest.TestCase):
    def test_unknown_level_falls_back_instead_of_crashing_import(self):
        """打錯的 LOG_LEVEL 若原樣進 dictConfig 會 ValueError，而那發生在
        web/server.py 的 import 期——app 直接起不來且訊息晦澀。"""
        import os

        from app.config import _log_level

        key = "TF_LOGTEST_LEVEL"
        old = os.environ.get(key)
        try:
            os.environ[key] = "info0"
            self.assertEqual(_log_level(key, "INFO"), "INFO")
            os.environ[key] = "debug"
            self.assertEqual(_log_level(key, "INFO"), "DEBUG")  # 大小寫不敏感
        finally:
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


class ServerWiringTests(unittest.TestCase):
    def test_server_configures_logging(self):
        self.assertIn("from app.logging_setup import configure_logging", _SERVER_SRC)
        self.assertIn("configure_logging()", _SERVER_SRC)

    def test_configure_runs_after_env_load_and_before_service_imports(self):
        """順序是硬需求：晚於 .env（要讀 LOG_LEVEL），早於任何會 getLogger 的模組。

        **走 AST 而非字面比對**：原本用 `_SERVER_SRC.index("from web import deps")`，
        而 ruff 的 isort 會把同模組的 from-import 併成一行
        （`from web import auth, concurrency, deps, report_runs`），於是那個字面
        消失、測試紅掉——紅的是格式，不是順序。順序這件事 AST 看得更準也更穩。
        """
        import ast

        tree = ast.parse(_SERVER_SRC)

        def _call_line(func_name: str) -> int:
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == func_name
                ):
                    return node.lineno
            self.fail(f"web/server.py 找不到 {func_name}(...) 呼叫")

        i_env = _call_line("load_env_file")
        i_cfg = _call_line("configure_logging")
        self.assertLess(i_env, i_cfg, "LOG_LEVEL 來自 .env，設定必須晚於載入")

        # `web.env_loader` 刻意在最前面（它就是載 .env 的那支），不算服務模組。
        service_imports = [
            (node.lineno, node.module)
            for node in tree.body
            if isinstance(node, ast.ImportFrom)
            and node.module
            and (node.module == "web" or node.module.startswith("web."))
            and node.module != "web.env_loader"
        ]
        self.assertTrue(service_imports, "web/server.py 竟然沒有任何 web.* import？")
        for lineno, mod in service_imports:
            with self.subTest(mod=mod):
                self.assertLess(
                    i_cfg,
                    lineno,
                    f"from {mod} import ... 會連帶載入服務模組，logging 設定必須更早",
                )


if __name__ == "__main__":
    unittest.main()
