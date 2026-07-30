import os

os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

import threading
import unittest
import warnings
from unittest.mock import patch

from starlette.exceptions import StarletteDeprecationWarning

warnings.filterwarnings(
    "ignore",
    category=StarletteDeprecationWarning,
)

from fastapi.testclient import TestClient

from web.server import app


class ServerStartupTests(unittest.TestCase):
    def test_markets_endpoint_is_available_while_warmup_runs(self):
        warmup_started = threading.Event()
        allow_warmup_finish = threading.Event()
        request_finished = threading.Event()
        response_data: dict[str, int] = {}
        errors: list[BaseException] = []

        def slow_warmup(_texts: list[str]) -> list[list[float]]:
            warmup_started.set()
            if not allow_warmup_finish.wait(timeout=5):
                raise TimeoutError("warmup was never released by the test")
            return [[0.0]]

        def run_client() -> None:
            try:
                with TestClient(app) as client:
                    response = client.get("/login")
                    response_data["status_code"] = response.status_code
                    request_finished.set()
                    allow_warmup_finish.set()
            except BaseException as exc:  # pragma: no cover - surfaced by assertions
                errors.append(exc)
                request_finished.set()
                allow_warmup_finish.set()

        with patch("web.deps.embed_texts", side_effect=slow_warmup), \
             patch("web.deps.rerank_warmup", return_value=True):
            thread = threading.Thread(target=run_client)
            thread.start()
            try:
                self.assertTrue(warmup_started.wait(timeout=1))
                self.assertTrue(
                    request_finished.wait(timeout=0.5),
                    "startup blocked on warmup, so the app never became reachable",
                )
            finally:
                allow_warmup_finish.set()
                thread.join(timeout=2)

        self.assertFalse(thread.is_alive(), "test client thread did not shut down")
        self.assertFalse(errors, errors)
        self.assertEqual(response_data.get("status_code"), 200)

    def test_rerank_warmup_runs_in_background_without_blocking(self):
        # rerank 模型冷載入 prod 實測 44-52s；不預載則首個請求的 rerank 必逾時。
        # 暖載必須在背景執行，且不得阻塞啟動（socket 須先可連）。
        warmup_started = threading.Event()
        allow_warmup_finish = threading.Event()
        request_finished = threading.Event()
        response_data: dict[str, int] = {}
        errors: list[BaseException] = []

        def slow_rerank_warmup() -> bool:
            warmup_started.set()
            if not allow_warmup_finish.wait(timeout=5):
                raise TimeoutError("rerank warmup was never released by the test")
            return True

        def run_client() -> None:
            try:
                with TestClient(app) as client:
                    response = client.get("/login")
                    response_data["status_code"] = response.status_code
                    request_finished.set()
                    allow_warmup_finish.set()
            except BaseException as exc:  # pragma: no cover - surfaced by assertions
                errors.append(exc)
                request_finished.set()
                allow_warmup_finish.set()

        with patch("web.deps.embed_texts", return_value=[[0.0]]), \
             patch("web.deps.rerank_warmup", side_effect=slow_rerank_warmup):
            thread = threading.Thread(target=run_client)
            thread.start()
            try:
                self.assertTrue(warmup_started.wait(timeout=1))
                self.assertTrue(
                    request_finished.wait(timeout=0.5),
                    "startup blocked on rerank warmup, so the app never became reachable",
                )
            finally:
                allow_warmup_finish.set()
                thread.join(timeout=2)

        self.assertFalse(thread.is_alive(), "test client thread did not shut down")
        self.assertFalse(errors, errors)
        self.assertEqual(response_data.get("status_code"), 200)

    def test_rerank_warmup_waits_for_embed_warmup(self):
        # transformers 首次 import 是 lazy-module 初始化，embed（經 FlagEmbedding）與
        # rerank（直接 import）兩執行緒同時首次 import 會競態出
        # ImportError: cannot import name 'is_torch_npu_available'，暖機每次開機全滅
        # （prod 7/15-7/16 三連炸實錄）。迴歸保證：rerank 暖機須等 embed 暖機完成。
        embed_started = threading.Event()
        allow_embed_finish = threading.Event()
        rerank_called = threading.Event()
        test_done = threading.Event()
        errors: list[BaseException] = []

        def blocking_embed(_texts: list[str]) -> list[list[float]]:
            embed_started.set()
            if not allow_embed_finish.wait(timeout=5):
                raise TimeoutError("embed warmup was never released by the test")
            return [[0.0]]

        def recording_rerank() -> bool:
            rerank_called.set()
            return True

        def run_client() -> None:
            try:
                with TestClient(app):
                    if not test_done.wait(timeout=5):
                        raise TimeoutError("test never finished while app was alive")
            except BaseException as exc:  # pragma: no cover - surfaced by assertions
                errors.append(exc)

        with patch("web.deps.embed_texts", side_effect=blocking_embed), \
             patch("web.deps.rerank_warmup", side_effect=recording_rerank):
            thread = threading.Thread(target=run_client)
            thread.start()
            try:
                self.assertTrue(embed_started.wait(timeout=1))
                # embed 暖機未完成前，rerank 暖機不得開始（0.3s 寬限：並行實作會即刻觸發）
                self.assertFalse(rerank_called.wait(timeout=0.3))
                allow_embed_finish.set()
                self.assertTrue(
                    rerank_called.wait(timeout=2),
                    "rerank warmup never ran after embed warmup finished",
                )
            finally:
                allow_embed_finish.set()
                test_done.set()
                thread.join(timeout=2)

        self.assertFalse(thread.is_alive(), "test client thread did not shut down")
        self.assertFalse(errors, errors)


if __name__ == "__main__":
    unittest.main()
