"""`scripts/_claude_cli.py`：批次共用的 LLM 呼叫層（檔名是 claude CLI 時代的歷史值）。

這一組測試釘死的是**失敗原因必須互相區分**。原缺陷是每一支批次各自寫了

    return r.stdout if r.returncode == 0 else None
    except Exception: return None

於是「claude 不在 PATH」「額度耗盡」「CLI 崩潰」「真的逾時」全部塌縮成同一句
「CLI 無回應或逾時」——2026-08-08 起連續四天 100% 失敗，事後完全無法診斷。
（signal_failures.log 累積 9,273 筆全是那一句。）

PR-M 移除 CLI backend 後，這個意圖改由 DeepSeek 路徑承接（`FailureReasonsTests`：每一種失敗的訊息
互不相同、單行、說得出 kind；帳號與設定層級的失敗整批中止）。CLI 專屬的測試（argv 旗標、errno 判定、
stderr 尾巴、CLI 認證失效）隨 CLI 一起刪除。
"""
import ast
import importlib
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm_failures as lf  # noqa: E402
from app.services import llm_http as lh  # noqa: E402
from scripts import _claude_cli as cc  # noqa: E402
from scripts import _llm_env as le  # noqa: E402

FAKE_KEY = "fixed-test-secret-deepseek0"
ENV = {"DEEPSEEK_API_KEY": FAKE_KEY, "DEEPSEEK_BASE_URL": "https://api.example.test"}


def _sse(*events, done: bool = True) -> bytes:
    out = []
    for ev in events:
        out += ["data: " + json.dumps(ev, ensure_ascii=False), ""]
    if done:
        out += ["data: [DONE]", ""]
    return ("\n".join(out) + "\n").encode("utf-8")


def _chunk(content=None, finish=None, usage=None, model="deepseek-flash"):
    delta = {} if content is None else {"content": content}
    return {"model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], "usage": usage}


def _ok(text: str) -> bytes:
    return _sse(_chunk(content=text), _chunk(content="", finish="stop"))


class _HttpCase(unittest.TestCase):
    """MockTransport＋假金鑰＋擋住任何子行程＋傳輸層退避改成記錄器（不真的等 2／6 秒）。"""

    def setUp(self):
        env = mock.patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)
        # PR-M 前擋的是 `cc.subprocess.run`；CLI 移除後本模組不再 import subprocess，改擋全域的
        spawn = mock.patch("subprocess.run", side_effect=AssertionError("PR-M 後不得 spawn 任何子行程"))
        spawn.start()
        self.addCleanup(spawn.stop)
        self.sleeps: list[float] = []
        sleeper = mock.patch.object(cc, "_http_sleep", self.sleeps.append)
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self.addCleanup(self._uninstall)
        self.requests: list[httpx.Request] = []

    def install(self, handler):
        self.requests = []
        seq = iter(handler) if isinstance(handler, list) else None

        def recording(request):
            self.requests.append(request)
            return (next(seq) if seq is not None else handler)(request)

        lh._transport = httpx.MockTransport(recording)
        lh._reset_clients()

    def _uninstall(self):
        lh._transport = None
        lh._reset_clients()

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)

    def call(self, prompt="標註這篇", model="deepseek-flash", **kw):
        kw.setdefault("max_tokens", 1024)
        kw.setdefault("meta", {"task": "tag", "file_hash": "h1", "report_id": None})
        return cc.run_claude(prompt, model, **kw)


class FailureReasonsTests(_HttpCase):
    """「失敗原因說得出口」（本模組存在的理由）在 DeepSeek 路徑上的版本。

    - 單篇失敗：各 kind 的訊息**互不相同**、`API[<kind>]` 開頭、單行、不含 TAB（*_failures.log 一列一筆）。
    - 整批中止：帳號層級（401／402／404）與白名單外的 model 拋 `LlmEnvironmentError`（被各批次 main 的
      `except CliNotFoundError` 接住 → rc=2），訊息直接指出該修什麼。
    """

    PER_FILE = {
        "content_filter": lambda req: httpx.Response(400, json={"error": {"message": "Content Exists Risk"}}),
        "bad_request": lambda req: httpx.Response(400, json={"error": {"message": "bad input"}}),
        "truncated": lambda req: httpx.Response(
            200, content=_sse(_chunk(content="半"), _chunk(content="", finish="length"))),
        "empty": lambda req: httpx.Response(200, content=_sse(_chunk(content="", finish="stop"))),
        "overloaded": lambda req: httpx.Response(503, json={"error": {"message": "busy"}}),
        "network": lambda req: (_ for _ in ()).throw(httpx.ConnectError("refused", request=req)),
    }

    def test_per_file_failures_are_mutually_distinguishable(self):
        errors = {}
        for kind, handler in self.PER_FILE.items():
            with self.subTest(kind=kind):
                cc._reset_state()  # 過載／網路會進斷路器窗，逐項清掉
                self.install(handler)
                res = self.call()
                self.assertIsNone(res.text)
                self.assertTrue(res.error.startswith(f"API[{kind}]"), res.error)
                self.assertNotIn("\n", res.error)
                self.assertNotIn("\t", res.error)
                errors[kind] = res.error
        self.assertEqual(len(set(errors.values())), len(self.PER_FILE), "失敗原因不得塌縮成同一句")

    def test_account_errors_say_what_to_fix(self):
        hints = {401: "DEEPSEEK_API_KEY", 402: "儲值", 404: "模型名"}
        for code, hint in hints.items():
            with self.subTest(code=code):
                self.install(lambda req, c=code: httpx.Response(c, json={"error": {"message": "x"}}))
                with self.assertRaises(cc.CliNotFoundError) as ctx:
                    self.call()
                self.assertIsInstance(ctx.exception, cc.LlmEnvironmentError)
                self.assertIn(hint, str(ctx.exception))

    def test_cli_symbols_are_gone(self):
        """PR-M：CLI 路徑的殘留不得回來（`build_cli_args`、`_run_cli`、認證失效樣式、errno 表……）。"""
        for name in ("build_cli_args", "_run_cli", "cli_auth_error", "CLI_AUTH_HINT", "STDERR_TAIL_CHARS",
                     "_UNRUNNABLE_ERRNOS", "_cli_kind", "subprocess"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(cc, name), name)
        self.assertNotIn("cwd", __import__("inspect").signature(cc.run_claude).parameters)

    def test_batch_sources_have_no_cli_spawn(self):
        """驗收 grep 的測試版（批次那一半；線上的在 tests/test_llm.py 的 NoCliBackendTests）。"""
        hits = []
        for path in sorted((REPO_ROOT / "scripts").rglob("*")):
            if path.suffix not in (".py", ".sh"):
                continue
            text = path.read_text(encoding="utf-8")
            for pat in ("claude -p", "claude_cli_path", "_run_cli", "build_cli_args", '"claude", "-p"'):
                if pat in text:
                    hits.append(f"{path.relative_to(REPO_ROOT)}: {pat}")
        self.assertEqual(hits, [])


class HttpDispatchTests(_HttpCase):
    """白名單 model 走 `llm_http.complete_chat`；其餘整批中止（PR-M 前 spawn CLI）。"""

    def test_model_resp_is_response_model(self):
        """`model_resp` 取回應的 model 欄，不是請求的（摘錄與訊號記進 raw_payload.model，PR-15）。"""
        body = _sse(_chunk(content="ok", model="deepseek-flash-0925"),
                    _chunk(content="", finish="stop", model="deepseek-flash-0925"))
        self.install(lambda req: httpx.Response(200, content=body))
        res = self.call()
        self.assertEqual((res.text, res.model_resp), ("ok", "deepseek-flash-0925"))

    def test_http_success_request_shape(self):
        """批次只送一則 user、prompt 原樣不動、thinking 兩個開關都關、max_tokens 照呼叫點給。"""
        self.install(lambda req: httpx.Response(200, content=_ok('{"a": 1}')))
        prompt = "規則\n\n檔名：x.pdf\n內文"
        res = self.call(prompt, max_tokens=16384, meta={"task": "signal", "file_hash": "h1", "report_id": "r1"})
        self.assertEqual(res, cc.CliResult('{"a": 1}', None, "deepseek-flash"))
        body = self.body()
        self.assertEqual(body["messages"], [{"role": "user", "content": prompt}])
        self.assertEqual(body["max_tokens"], 16384)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["user_id"], "batch-signal")
        self.assertEqual(body["model"], "deepseek-flash")

    def test_every_whitelisted_name_goes_http(self):
        for model in ("deepseek-flash", "deepseek-v4-pro", "deepseek-v4-flash"):
            with self.subTest(model=model):
                self.install(lambda req: httpx.Response(200, content=_ok("ok")))
                self.assertEqual(self.call(model=model).text, "ok")
                self.assertEqual(len(self.requests), 1)

    def test_non_whitelisted_model_aborts_the_batch_without_sending(self):
        """PR-M：白名單外（含 claude-*、CLI 別名、打錯字、空字串）→ `LlmEnvironmentError`（整批 rc=2），
        不送出、不寫用量記錄、不進斷路器窗。"""
        self.install(lambda req: httpx.Response(200, content=_ok("不該送出")))
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "usage.jsonl"
            with mock.patch.dict(os.environ, {"LLM_USAGE_LOG": str(log)}):
                for model in ("claude-haiku-4-5", "claude-sonnet-5", "sonnet", "deepseek-flsh", ""):
                    with self.subTest(model=model):
                        with self.assertRaises(cc.CliNotFoundError) as ctx:
                            cc.run_claude("p", model, max_tokens=1024, meta={"task": "tag", "file_hash": "h1"})
                        self.assertIsInstance(ctx.exception, cc.LlmEnvironmentError)
                        self.assertNotIsInstance(ctx.exception, cc.BadRequestEscalation)
                        msg = str(ctx.exception)
                        self.assertIn(repr(model), msg)
                        self.assertIn("白名單", msg)
                        self.assertIn("deepseek-flash", msg)  # 說得出可以填什麼
            self.assertFalse(log.exists(), "沒送出的呼叫不記用量")
        self.assertEqual(self.requests, [])
        self.assertEqual(sum(cc._BREAKER._recent), 0)
        self.assertEqual(len(cc._BREAKER._recent), 0)

    def test_http_without_max_tokens_is_a_programming_error(self):
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        with self.assertRaises(ValueError):
            cc.run_claude("p", "deepseek-flash", meta={"task": "tag"})
        self.assertEqual(self.requests, [])

    def test_timeout_is_passed_as_total_deadline(self):
        with mock.patch.object(cc.llm_http, "complete_chat", wraps=cc.llm_http.complete_chat) as cc_call:
            self.install(lambda req: httpx.Response(200, content=_ok("ok")))
            self.call(timeout=150)
        self.assertEqual(cc_call.call_args.kwargs["timeout"], 150.0)

    def test_account_errors_raise_batch_abort(self):
        """401／402／404／模型不存在：`LlmEnvironmentError`，被各批次的 `except CliNotFoundError` 接住。"""
        cases = [
            (401, "Authentication Fails", "API[auth]"),
            (402, "Insufficient Balance", "API[quota]"),
            (404, "Not Found", "API[config]"),
            (400, "Model Not Exist", "API[config]"),
        ]
        for code, message, prefix in cases:
            with self.subTest(code=code, message=message):
                self.install(lambda req, c=code, m=message: httpx.Response(c, json={"error": {"message": m}}))
                try:
                    self.call()
                except cc.CliNotFoundError as exc:  # 各批次 main 接的就是這個型別
                    self.assertIsInstance(exc, cc.LlmEnvironmentError)
                    self.assertTrue(str(exc).startswith(prefix), str(exc))
                    self.assertNotIn("\t", str(exc))
                    self.assertNotIn("\n", str(exc))
                else:
                    self.fail("帳號層級錯誤必須拋出")
                self.assertEqual(len(self.requests), 1, "帳號錯誤不重試")

    def test_quota_hint_never_suggests_claude(self):
        self.install(lambda req: httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}))
        with self.assertRaises(cc.LlmEnvironmentError) as ctx:
            self.call()
        self.assertIn("儲值", str(ctx.exception))

    def test_per_file_failures_are_results_with_api_prefix(self):
        cases = [
            (lambda req: httpx.Response(400, json={"error": {"message": "Content Exists Risk"}}),
             "API[content_filter]"),
            (lambda req: httpx.Response(400, json={"error": {"message": "bad\tinput\nhere"}}), "API[bad_request]"),
            (lambda req: httpx.Response(200, content=_sse(_chunk(content="半"), _chunk(content="", finish="length"))),
             "API[truncated]"),
            (lambda req: httpx.Response(200, content=_sse(_chunk(content="", finish="stop"))), "API[empty]"),
        ]
        for handler, prefix in cases:
            with self.subTest(prefix=prefix):
                self.install(handler)
                res = self.call()
                self.assertIsNone(res.text)
                self.assertTrue(res.error.startswith(prefix), res.error)
                self.assertNotIn("\t", res.error)
                self.assertNotIn("\n", res.error)
                self.assertEqual(len(self.requests), 1)


class BatchCallSiteMaxTokensTests(unittest.TestCase):
    """批次各呼叫點送出的 `max_tokens`（第二版計畫 §8）與 `meta` 逐點釘住。

    值只作用在 HTTP 路徑，CLI 時代完全看不出差別；漏帶則要等某個任務切到 DeepSeek 那天才以
    `ValueError` 爆開。以該檔模組命名空間求值 `max_tokens` 的運算式，量的是真正會送出的數字。
    表格鍵是（檔案, meta 的 task 原始碼），多一個或少一個呼叫點都會紅。
    """

    EXPECTED = {
        ("scripts/generate_summaries.py", "TASK_SUMMARY"): 1024,
        ("scripts/generate_titles.py", "TASK_TITLE"): 512,
        ("scripts/extract_takeaways.py", "TASK_TAKEAWAY"): 4096,
        ("scripts/extract_signals.py", "llm_failures.TASK_SIGNAL"): 16384,
        ("scripts/tag_all_cli.py", "TASK_TAG"): 1024,
        ("scripts/sync_new_reports.py", "TASK_TAG"): 1024,
        ("scripts/generate_brief.py", "TASK_BRIEF"): 8192,
    }

    def test_values_and_meta(self):
        found: dict[tuple[str, str], int] = {}
        for base in ("app", "eval", "scripts", "web"):
            for path in sorted((REPO_ROOT / base).rglob("*.py")):
                rel = str(path.relative_to(REPO_ROOT))
                if rel == "scripts/_claude_cli.py":
                    continue
                tree = ast.parse(path.read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    fn = node.func
                    name = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else None
                    if name != "run_claude":
                        continue
                    kws = {k.arg: k.value for k in node.keywords}
                    self.assertIn("max_tokens", kws, f"{rel}:{node.lineno}")
                    self.assertIsInstance(kws.get("meta"), ast.Dict, f"{rel}:{node.lineno} 要帶 meta 字面值")
                    meta = {ast.literal_eval(k): v for k, v in zip(kws["meta"].keys, kws["meta"].values)}
                    if rel != "scripts/generate_brief.py":  # 簡報一天一次、沒有單篇身分
                        self.assertLessEqual({"task", "file_hash", "report_id"}, set(meta), rel)
                    module = importlib.import_module(rel[:-3].replace("/", "."))
                    value = eval(compile(ast.Expression(kws["max_tokens"]), rel, "eval"), vars(module))  # noqa: S307
                    key = (rel, ast.unparse(meta["task"]))
                    self.assertNotIn(key, found)
                    found[key] = value
        self.assertEqual(found, self.EXPECTED)


class RetryClassificationTests(unittest.TestCase):
    """`is_retryable`／`failure_kind`：腳本層要不要再打、跳過名單記什麼（第二版計畫 §4.7）。"""

    def test_is_retryable(self):
        R = cc.CliResult
        self.assertFalse(cc.is_retryable(R(None, lh.error_string(lh.TIMEOUT))))
        self.assertFalse(cc.is_retryable(R(None, "API[content_filter] 觸發供應商內容審查")))
        # 不是 `API[` 開頭的失敗（PR-M 前 CLI 的訊息；`run_claude` 已不會回，但函式的規則不變）與成功照舊可重試
        for err in ("非 API 前綴的訊息", None):
            with self.subTest(err=err):
                self.assertTrue(cc.is_retryable(R(None, err)))
        self.assertTrue(cc.is_retryable(R("文字", None)))

    def test_every_http_kind_is_not_retryable(self):
        for kind in (lh.CONTENT_FILTER, lh.BAD_REQUEST, lh.OVERLOADED, lh.NETWORK, lh.TIMEOUT,
                     lh.TRUNCATED, lh.TIMEOUT_STREAMED, lh.EMPTY, lh.OTHER):
            with self.subTest(kind=kind):
                self.assertFalse(cc.is_retryable(cc.CliResult(None, lh.error_string(kind, "x"))))

    def test_failure_kind_mapping(self):
        expected = {
            lh.CONTENT_FILTER: lf.CONTENT_FILTER,
            lh.TRUNCATED: lf.TRUNCATED,
            lh.TIMEOUT_STREAMED: lf.TIMEOUT_STREAMED,  # 期限型截斷：記，但不併進 truncated
            lh.EMPTY: lf.EMPTY,
            lh.BAD_REQUEST: lf.BAD_REQUEST,
            lh.TIMEOUT: None,
            lh.OVERLOADED: None,
            lh.NETWORK: None,
            lh.OTHER: None,
            lh.AUTH: None,
            lh.QUOTA: None,
            lh.CONFIG: None,
        }
        for kind, reason in expected.items():
            with self.subTest(kind=kind):
                self.assertEqual(cc.failure_kind(cc.CliResult(None, lh.error_string(kind, "d"))), reason)
                if reason is not None:
                    self.assertIn(reason, lf.REASONS)

    def test_failure_kind_ignores_non_api_errors_and_success(self):
        self.assertIsNone(cc.failure_kind(cc.CliResult(None, "非 API 前綴的訊息")))
        self.assertIsNone(cc.failure_kind(cc.CliResult("ok", None)))
        # 內容剛好長得像前綴也不算：只看失敗
        self.assertIsNone(cc.failure_kind(cc.CliResult("API[content_filter]", None)))


def _status(code, message="x", headers=None):
    return lambda req: httpx.Response(code, json={"error": {"message": message}}, headers=headers or {})


def _content_filter(req):
    return httpx.Response(400, json={"error": {"message": "Content Exists Risk"}})


def _connect_error(req):
    raise httpx.ConnectError("connection refused", request=req)


class BreakerTests(_HttpCase):
    """斷路器（第二版 §4.7、審查 L9）：最近 10 次 HTTP 呼叫中逾時／過載／網路 ≥5 → 整批 rc=2＋標記。"""

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.marker = Path(self._tmpdir.name) / "data" / ".llm_breaker"
        env = mock.patch.dict(os.environ, {"LLM_BREAKER_FILE": str(self.marker)})
        env.start()
        self.addCleanup(env.stop)

    def _calls(self, handler, n):
        """連續 n 次 run_claude；回每次的結果（跳脫時是例外物件）。"""
        self.install(handler)
        out = []
        for _ in range(n):
            try:
                out.append(self.call())
            except cc.LlmEnvironmentError as exc:
                out.append(exc)
        return out

    def test_trips_on_fifth_bad_call(self):
        res = self._calls(_status(503, "busy"), 4)
        self.assertTrue(all(isinstance(r, cc.CliResult) for r in res), res)
        self.assertFalse(self.marker.exists())
        with self.assertRaises(cc.LlmEnvironmentError) as ctx:
            self.call()
        self.assertIsInstance(ctx.exception, cc.CliNotFoundError)
        self.assertIn("斷路器", str(ctx.exception))
        self.assertTrue(self.marker.exists(), "要寫標記讓後續段預檢拒跑")
        self.assertIn("reason=", self.marker.read_text(encoding="utf-8"))

    def test_after_trip_no_more_requests(self):
        self._calls(_status(503), 5)
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        with self.assertRaises(cc.LlmEnvironmentError):
            self.call()
        self.assertEqual(self.requests, [], "跳脫後不得再送出任何請求")

    def test_every_breaker_kind_counts(self):
        cases = {
            "overloaded": _status(503),
            "rate_limited": _status(429),
            "network": _connect_error,
        }
        for name, handler in cases.items():
            with self.subTest(kind=name):
                cc._reset_state()
                res = self._calls(handler, 5)
                self.assertIsInstance(res[-1], cc.LlmEnvironmentError, name)
        with self.subTest(kind="timeout"):
            cc._reset_state()
            self.install(lambda req: httpx.Response(200, content=_ok("ok")))
            for i in range(5):
                if i < 4:
                    res = self.call(timeout=0)  # 總期限一開始就過了 → API[timeout]
                    self.assertTrue(res.error.startswith("API[timeout]"), res.error)
                else:
                    with self.assertRaises(cc.LlmEnvironmentError):
                        self.call(timeout=0)

    def test_content_failures_do_not_count(self):
        """審查、400、截斷、空回應是單篇的事，不代表供應商出問題。"""
        res = self._calls(_content_filter, 10) + self._calls(_status(400, "bad"), 10)
        self.assertTrue(all(isinstance(r, cc.CliResult) for r in res))
        self.assertFalse(self.marker.exists())

    def test_window_slides(self):
        """最近 10 次：舊的壞結局被 10 次成功推出窗外後不再算數。"""
        self._calls(_status(503), 4)
        self._calls(lambda req: httpx.Response(200, content=_ok("ok")), 10)
        res = self._calls(_status(503), 4)
        self.assertTrue(all(isinstance(r, cc.CliResult) for r in res), "窗內只有 4 次壞結局，不該跳脫")
        with self.assertRaises(cc.LlmEnvironmentError):
            self.call()

    def test_window_is_ten_calls(self):
        """窗內 10 次裡有 5 次壞就跳：壞與好交錯也算。"""
        good = lambda req: httpx.Response(200, content=_ok("ok"))  # noqa: E731
        for _ in range(4):
            self._calls(_status(503), 1)
            self._calls(good, 1)
        self.assertFalse(self.marker.exists())
        with self.assertRaises(cc.LlmEnvironmentError):
            self._calls(_status(503), 1)
            self.call()  # 第 9 次：窗內 5 壞 4 好
        self.assertTrue(self.marker.exists())

    def test_thread_safe_single_trip(self):
        self.install(_status(503))
        errors, results = [], []
        barrier = threading.Barrier(8)

        def worker():
            barrier.wait()
            try:
                results.append(self.call())
            except cc.LlmEnvironmentError as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        # 前 4 次回結果；第 5 次跳脫並拋出；其餘被擋（check）或在跳脫後照常交回結果
        self.assertGreaterEqual(len(errors), 1)
        self.assertEqual(len(results) + len(errors), 8)
        self.assertLessEqual(len(self.requests) // 3, 8)
        with self.assertRaises(cc.LlmEnvironmentError):
            self.call()

    def test_marker_blocks_later_segment(self):
        """跨段：跳脫寫的標記讓下一段預檢 rc=2。PR-M 前全用 Claude 的段照跑；CLI 移除後沒有那種段了，
        沒有模型的段（--dry-run 之類）照跑。"""
        self._calls(_status(503), 5)
        self.assertTrue(self.marker.exists())
        le._STATE.clear()
        self.addCleanup(le._STATE.clear)
        with mock.patch.object(le, "_warn_if_not_deploy_root"), \
             mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()) as err:
            with self.assertRaises(SystemExit) as ctx:
                le.require_llm_key({"summary": "deepseek-flash"})
            self.assertEqual(ctx.exception.code, 2)
            self.assertIn("斷路器", err.getvalue())
            le.require_llm_key({"summary": None})  # 不拋

    def test_marker_carries_sync_round_id(self):
        """審查中4：在 sync 輪次內跳脫，標記帶 `round=`；同一輪後段拒跑，下一輪的段放行。"""
        with mock.patch.dict(os.environ, {"SYNC_ROUND_ID": "20260924_090000"}):
            self._calls(_status(503), 5)
        self.assertIn("round=20260924_090000\n", self.marker.read_text(encoding="utf-8"))
        le._STATE.clear()
        self.addCleanup(le._STATE.clear)
        with mock.patch.object(le, "_warn_if_not_deploy_root"), \
             mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()):
            with mock.patch.dict(os.environ, {"SYNC_ROUND_ID": "20260924_090000"}), \
                 self.assertRaises(SystemExit):
                le.require_llm_key({"summary": "deepseek-flash"})
            with mock.patch.dict(os.environ, {"SYNC_ROUND_ID": "20260924_120000"}):
                le.require_llm_key({"tag": "deepseek-flash"})  # 下一輪：不拋

    def test_marker_without_round_outside_sync(self):
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("SYNC_ROUND_ID", None)
            self._calls(_status(503), 5)
        self.assertNotIn("round=", self.marker.read_text(encoding="utf-8"))

    def test_marker_write_failure_still_aborts(self):
        blocker = Path(self._tmpdir.name) / "file"
        blocker.write_text("", encoding="utf-8")
        with mock.patch.dict(os.environ, {"LLM_BREAKER_FILE": str(blocker / "x" / ".llm_breaker")}), \
             mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()):
            res = self._calls(_status(503), 5)
        self.assertIsInstance(res[-1], cc.LlmEnvironmentError)


class _FakeClock:
    """`llm_http` 的 monotonic／sleep：伺服器延遲與退避都只推進假時鐘，測試不必真的等。"""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, s):
        self.now += s


class DeadlineTests(_HttpCase):
    """批次的 `timeout` 是涵蓋傳輸層重試的總期限（第二版 §4.5）：逐行檢查，不靠 httpx 的 read 逾時。"""

    def test_keepalive_cannot_extend_deadline(self):
        def queued():
            for _ in range(150):
                yield b": keep-alive\n\n"
                time.sleep(0.02)

        self.install(lambda req: httpx.Response(200, content=queued()))
        t0 = time.monotonic()
        res = self.call(timeout=0.3)
        self.assertLess(time.monotonic() - t0, 2.0)
        self.assertTrue(res.error.startswith("API[timeout]"), res.error)

    def test_deadline_covers_transport_retries(self):
        """期限從第一次嘗試起算：第二次失敗後，再退避就會超過期限 → 不打第三次。"""
        clock = _FakeClock()

        def slow_503(req):
            clock.now += 0.3  # 伺服器花 0.3 秒才回 503
            return httpx.Response(503, text="busy", headers={"Retry-After": "0.3"})

        self.install(slow_503)
        with mock.patch.object(lh, "time", SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep)), \
             mock.patch.object(cc, "_http_sleep", clock.sleep):
            res = self.call(timeout=1.0)
        self.assertTrue(res.error.startswith("API[overloaded]"), res.error)
        self.assertEqual(len(self.requests), 2, "t=0.9 時再退避 0.3 就到期：只能打兩次")

    def test_batch_call_sites_keep_their_timeouts(self):
        """沿用各批次現行 timeout（摘要／標題／摘錄／訊號 180、標註 150、簡報 300）當總期限。"""
        from test_batch_http_dispatch import es, et, gb, gs, gt, snr, tac

        seen = {}

        def fake(model, prompt, **kw):
            seen[kw["task"]] = kw["timeout"]
            return lh.ChatResult(text="{}", error=None, kind=None)

        with mock.patch.object(cc.llm_http, "complete_chat", side_effect=fake), \
             mock.patch.object(gs, "MODEL", "deepseek-flash"), mock.patch.object(gt, "MODEL", "deepseek-flash"), \
             mock.patch.object(tac, "MODEL", "deepseek-flash"):
            gs.call_cli("p")
            gt.call_cli("p")
            et.call_cli("p", "deepseek-flash")
            es.call_cli("p", "deepseek-flash")
            tac.call_cli("p")
            snr._tag_via_cli("x.pdf", "t", model="deepseek-flash")
            gb.call_cli("p", "deepseek-flash")
        self.assertEqual(seen, {
            "summary": 180.0, "title": 180.0, "takeaway": 180.0, "signal": 180.0, "tag": 150.0, "brief": 300.0,
        })


class BadRequestEscalationTests(_HttpCase):
    """審查 H2：只有「≥2 個不同 file_hash 收到相同的 400 訊息」才升級成 config 中止。"""

    def _bad(self, message="Invalid request: bad field"):
        self.install(lambda req: httpx.Response(400, json={"error": {"message": message}}))

    def call_for(self, file_hash, task="summary"):
        return self.call(meta={"task": task, "file_hash": file_hash, "report_id": None})

    def test_single_file_is_a_per_file_failure(self):
        self._bad()
        res = self.call_for("h1")
        self.assertTrue(res.error.startswith("API[bad_request]"), res.error)

    def test_two_files_same_message_escalates(self):
        self._bad()
        self.call_for("h2")
        with self.assertRaises(cc.BadRequestEscalation) as ctx:
            self.call_for("h1")
        exc = ctx.exception
        self.assertIsInstance(exc, cc.LlmEnvironmentError)
        self.assertIsInstance(exc, cc.CliNotFoundError)  # 各批次 main 的 rc=2 接法
        self.assertEqual(exc.file_hashes, ("h1", "h2"))
        self.assertTrue(str(exc).startswith("API[config]"), str(exc))
        self.assertNotIn("\t", str(exc))
        self.assertNotIn("\n", str(exc))

    def test_two_files_different_messages_do_not_escalate(self):
        self._bad("Invalid request: field a")
        self.call_for("h1")
        self._bad("Invalid request: field b")
        res = self.call_for("h2")
        self.assertTrue(res.error.startswith("API[bad_request]"))

    def test_serde_column_differs_per_request_still_escalates(self):
        """審查實驗 [2]：6 篇收到只差 `column N` 的 422 → 第二篇就升級（修正前 6 篇都不升級）。"""
        self.install(lambda req: httpx.Response(422, json={"error": {"message": (
            "Failed to deserialize the JSON body into the target type: reasoning_effort: unknown variant "
            f"`none`, expected one of `low`, `medium`, `high` at line 1 column {len(req.content)}")}}))
        results, esc = [], None
        for i in range(6):
            try:
                results.append(self.call(prompt="內文" * (100 + i),
                                         meta={"task": "title", "file_hash": f"h{i}", "report_id": None}))
            except cc.BadRequestEscalation as exc:
                esc = exc
                break
        self.assertIsNotNone(esc, [r.error for r in results])
        self.assertEqual(esc.file_hashes, ("h0", "h1"))
        self.assertEqual(len({json.loads(r.content)["messages"][0]["content"] for r in self.requests}), 2,
                         "兩篇 prompt 長度不同（column 不同）")

    def test_context_length_on_two_files_does_not_escalate(self):
        """兩篇超長研報：正規化後同一句，但它是單篇輸入問題，不得中止整批。"""
        self.install(lambda req: httpx.Response(400, json={"error": {"message": (
            "This model's maximum context length is 131072 tokens. However, you requested "
            f"{131072 + len(req.content)} tokens. Please reduce the length of the messages.")}}))
        for i in range(2):
            meta = {"task": "summary", "file_hash": f"h{i}", "report_id": None}
            res = self.call(prompt="內文" * (100 + i), meta=meta)
            self.assertTrue(res.error.startswith("API[bad_request]"), res.error)

    def test_same_file_twice_does_not_escalate(self):
        self._bad()
        self.call_for("h1")
        res = self.call_for("h1")
        self.assertTrue(res.error.startswith("API[bad_request]"))

    def test_no_escalation_once_any_call_succeeded(self):
        """本行程內有任何一次 HTTP 呼叫成功過：請求格式與設定沒問題，之後相同的 400 是單篇問題，不升級。"""
        bad = lambda req: httpx.Response(400, json={"error": {"message": "Invalid request: bad field"}})  # noqa: E731
        self.install([lambda req: httpx.Response(200, content=_ok("ok")), bad, bad, bad])
        self.assertEqual(self.call_for("h0").text, "ok")
        for h in ("h1", "h2", "h3"):
            res = self.call_for(h)
            self.assertTrue(res.error.startswith("API[bad_request]"), res.error)

    def test_success_flag_is_reset_between_processes(self):
        """`_reset_state`（測試用；等同新行程）連成功紀錄一起清：清掉後照常升級。"""
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        self.call_for("h0")
        cc._reset_state()
        self._bad()
        self.call_for("h1")
        with self.assertRaises(cc.BadRequestEscalation):
            self.call_for("h2")

    def test_failed_calls_do_not_count_as_success(self):
        """審查、截斷之類「有回應但失敗」不算成功：之後的相同 400 照樣升級。"""
        bad = lambda req: httpx.Response(400, json={"error": {"message": "Invalid request: bad field"}})  # noqa: E731
        self.install([_content_filter, bad, bad])
        self.call_for("h0")
        self.call_for("h1")
        with self.assertRaises(cc.BadRequestEscalation):
            self.call_for("h2")

    def test_local_encoding_failure_never_escalates(self):
        """本機編碼就失敗（孤立代理字元繞過 sanitize）：單篇 bad_request，status 為 None、不參與升級、不中止。"""
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        with mock.patch.object(lh, "sanitize", lambda text: text):
            for h in ("h1", "h2", "h3"):
                res = self.call(prompt="孤立\ud800代理", meta={"task": "summary", "file_hash": h, "report_id": None})
                self.assertTrue(res.error.startswith("API[bad_request]"), res.error)
                self.assertEqual(cc.failure_kind(res), lf.BAD_REQUEST)
        self.assertEqual(self.requests, [], "沒送出任何請求")

    def test_calls_without_file_hash_never_escalate(self):
        """簡報一天一次、沒有單篇身分：不參與升級。"""
        self._bad()
        for _ in range(3):
            res = self.call(meta={"task": "brief"})
            self.assertTrue(res.error.startswith("API[bad_request]"))

    def test_other_kinds_do_not_escalate(self):
        self.install(lambda req: httpx.Response(400, json={"error": {"message": "Content Exists Risk"}}))
        for h in ("h1", "h2", "h3"):
            self.assertTrue(self.call_for(h).error.startswith("API[content_filter]"))

    def test_record_escalation_records_every_trigger_once(self):
        """升級前先把觸發的研報以 bad_request 記入跳過名單，而且直接記到 SKIP_AFTER_ROUNDS（審查中2）：
        單篇路徑本輪已記過的那篇也要補上升級那一筆（inc=0，不重複累加），否則它停在 1、下一輪照打。"""
        import asyncio

        calls: list = []

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def execute(self, stmt, params=None):
                calls.append((str(stmt), params))

            async def commit(self):
                pass

        rec = lf.FailureRecorder("summary", "deepseek-flash", _Session)
        exc = cc.BadRequestEscalation("API[config] x", ("h1", "h2"))

        async def go():
            await rec.record("h1", lf.BAD_REQUEST)  # 單篇路徑已記過 h1
            await cc.record_escalation(exc, rec)
            await cc.record_escalation(cc.LlmEnvironmentError("API[quota] x"), rec)  # 其他中止：不記
            await rec.record("h2", lf.BAD_REQUEST)  # 升級後同一輪再記：去重

        with mock.patch("sys.stdout", new_callable=lambda: __import__("io").StringIO()):
            asyncio.run(go())
        self.assertEqual([p["file_hash"] for _, p in calls], ["h1", "h1", "h2"])
        self.assertEqual({p["reason"] for _, p in calls}, {lf.BAD_REQUEST})
        self.assertEqual(calls[0][0], lf.RECORD_SQL)
        self.assertEqual([sql for sql, _ in calls[1:]], [lf.ESCALATED_RECORD_SQL] * 2)
        self.assertEqual([p["inc"] for _, p in calls[1:]], [0, 1], "本輪已記過的不再 +1")

    def test_record_escalation_without_recorder_does_not_crash(self):
        """表不存在（recorder 為 None）時只印出觸發研報，不拋——中止碼要由原本的例外決定。"""
        import asyncio
        import io

        out = io.StringIO()
        with mock.patch("sys.stdout", out):
            asyncio.run(cc.record_escalation(cc.BadRequestEscalation("API[config] x", ("h1", "h2")), None))
        self.assertIn("h1, h2", out.getvalue())


class EscalationKeyTests(unittest.TestCase):
    """審查中1：400 升級的比對鍵要正規化（數字、id），單篇輸入造成的訊息不參與。"""

    def test_numbers_and_ids_are_normalized(self):
        same = [
            ("HTTP 422 Failed to deserialize: unknown variant `none` at line 1 column 1834",
             "HTTP 422 Failed to deserialize: unknown variant `none` at line 1 column 52"),
            ("HTTP 400 Invalid request (request id: 9f3a2b7c-1d2e-4f50-8a9b-0c1d2e3f4a5b)",
             "HTTP 400 Invalid request (request id: 0a1b2c3d-4e5f-4a6b-8c7d-9e8f7a6b5c4d)"),
            ("HTTP 400 Invalid request, trace 7c1f0e9a2b3d4c5e6f708192a3b4c5d6",
             "HTTP 400 Invalid request, trace 00ff11ee22dd33cc44bb55aa66997788"),
            ("HTTP 400 Bad param req_id=Ab12Cd34Ef56Gh78", "HTTP 400 Bad param req_id=Zz98Yy76Xx54Ww32"),
            ("HTTP 400 INVALID Request", "HTTP 400 invalid request"),
        ]
        for a, b in same:
            with self.subTest(a=a):
                self.assertIsNotNone(cc._escalation_key(a))
                self.assertEqual(cc._escalation_key(a), cc._escalation_key(b))

    def test_different_wording_stays_different(self):
        self.assertNotEqual(cc._escalation_key("HTTP 400 Invalid request: field alpha"),
                            cc._escalation_key("HTTP 400 Invalid request: field bravo"))
        self.assertNotEqual(cc._escalation_key("HTTP 400 reasoning_effort unknown"),
                            cc._escalation_key("HTTP 400 thinking unknown"))

    def test_input_specific_messages_are_excluded(self):
        for msg in (
            "HTTP 400 This model's maximum context length is 131072 tokens. However, you requested 140000 tokens.",
            "HTTP 400 context_length_exceeded",
            "HTTP 400 Please reduce the length of the messages or completion.",
            "HTTP 400 Invalid request: prompt too long",
            "HTTP 400 Input exceeds the model limit",
            "HTTP 400 too many tokens",
        ):
            with self.subTest(msg=msg):
                self.assertIsNone(cc._escalation_key(msg))

    def test_global_parameter_errors_are_not_excluded(self):
        """`max_tokens` 參數本身不合法是呼叫點的程式錯、每篇都會踩到：要能升級。"""
        for msg in (
            "HTTP 400 Invalid max_tokens value, the valid range of max_tokens is [1, 8192]",
            "HTTP 422 Failed to deserialize the JSON body into the target type: reasoning_effort: unknown variant",
        ):
            with self.subTest(msg=msg):
                self.assertIsNotNone(cc._escalation_key(msg))


USAGE_FIELDS = {
    "ts", "task", "file_hash", "report_id", "backend", "model_req", "model_resp", "prompt_sha256",
    "tokens", "finish_reason", "kind", "attempts", "ttft_ms", "total_ms",
}


class InputSanitizeTests(_HttpCase):
    """HTTP 路徑的輸入清理（低1、低2）：NUL 去掉、孤立代理字元換 U+FFFD，與 CLI 路徑（argv 去 NUL）一致；
    清理不到的編碼錯誤也只是單篇失敗，不是 CONFIG 整批中止。"""

    def test_nul_and_lone_surrogate_are_cleaned_before_sending(self):
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        res = self.call(prompt="研報\x00內文\ud800結尾")
        self.assertEqual(res.text, "ok")
        self.assertNotIn(b"\x00", self.requests[0].content)
        self.assertNotIn(b"\\u0000", self.requests[0].content)
        self.assertEqual(self.body()["messages"][-1]["content"], "研報內文\ufffd結尾")

    def test_unicode_error_is_bad_request_not_config(self):
        """`UnicodeError` 歸 bad_request（單篇、記跳過名單），不是 CONFIG（整批 rc=2、不留紀錄）。"""
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        with mock.patch.object(lh, "sanitize", lambda text: text):
            res = self.call(prompt="孤立\ud800代理")
        self.assertIsNone(res.text)
        self.assertTrue(res.error.startswith("API[bad_request]"), res.error)
        self.assertIn("UnicodeEncodeError", res.error)

    def test_invalid_url_is_still_config(self):
        """組請求時的 InvalidURL 仍是設定錯（CONFIG、整批中止）：只有 UnicodeError 改歸單篇。"""
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        with mock.patch.object(httpx.Client, "build_request", side_effect=httpx.InvalidURL("bad url")):
            with self.assertRaises(cc.LlmEnvironmentError) as ctx:
                self.call()
        self.assertTrue(str(ctx.exception).startswith("API[config]"), str(ctx.exception))


class UsageLogTests(_HttpCase):
    """`data/llm_usage.jsonl`（第二版計畫 §4.8 批次部分）：HTTP 與 CLI 每次呼叫一行。"""

    def setUp(self):
        super().setUp()
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.log = Path(self._tmpdir.name) / "data" / "llm_usage.jsonl"
        env = mock.patch.dict(os.environ, {"LLM_USAGE_LOG": str(self.log)})
        env.start()
        self.addCleanup(env.stop)

    def rows(self) -> list[dict]:
        return [json.loads(ln) for ln in self.log.read_text(encoding="utf-8").splitlines()]

    META = {"task": "summary", "file_hash": "h1", "report_id": "r1"}

    def test_http_success_row(self):
        usage = {"prompt_tokens": 12, "completion_tokens": 3, "prompt_cache_hit_tokens": 2,
                 "prompt_cache_miss_tokens": 10}  # thinking 關：沒有 completion_tokens_details（9/24 實測）
        self.install(lambda req: httpx.Response(200, content=_sse(
            _chunk(content="好", model="deepseek-v4.1-flash"),
            _chunk(content="", finish="stop", usage=usage, model="deepseek-v4.1-flash"))))
        self.call("提示詞", meta=self.META)
        (row,) = self.rows()
        self.assertEqual(set(row), USAGE_FIELDS)
        self.assertEqual(row["backend"], "http")
        self.assertEqual((row["task"], row["file_hash"], row["report_id"]), ("summary", "h1", "r1"))
        self.assertEqual((row["model_req"], row["model_resp"]), ("deepseek-flash", "deepseek-v4.1-flash"))
        self.assertEqual(row["prompt_sha256"], __import__("hashlib").sha256("提示詞".encode()).hexdigest())
        self.assertEqual(row["tokens"], {"hit": 2, "miss": 10, "completion": 3, "reasoning": 0})
        self.assertEqual((row["finish_reason"], row["kind"], row["attempts"]), ("stop", None, 1))
        self.assertIsInstance(row["ttft_ms"], int)
        self.assertIsInstance(row["total_ms"], int)
        self.assertNotIn("提示詞", self.log.read_text(encoding="utf-8"), "不記 prompt 本身")

    def test_reasoning_tokens_when_present(self):
        usage = {"completion_tokens": 9, "completion_tokens_details": {"reasoning_tokens": 7}}
        self.install(lambda req: httpx.Response(200, content=_sse(
            _chunk(content="好"), _chunk(content="", finish="stop", usage=usage))))
        self.call(meta=self.META)
        self.assertEqual(self.rows()[0]["tokens"]["reasoning"], 7)

    def test_http_failures_are_logged_with_kind_and_attempts(self):
        self.install(lambda req: httpx.Response(400, json={"error": {"message": "Content Exists Risk"}}))
        self.call(meta=self.META)
        self.install(lambda req: httpx.Response(503, text="busy"))
        self.call(meta=self.META)
        first, second = self.rows()
        self.assertEqual((first["kind"], first["tokens"], first["attempts"]), ("content_filter", None, 1))
        self.assertEqual((second["kind"], second["attempts"]), ("overloaded", 3))

    def test_account_error_is_logged_before_abort(self):
        self.install(lambda req: httpx.Response(402, json={"error": {"message": "Insufficient Balance"}}))
        with self.assertRaises(cc.LlmEnvironmentError):
            self.call(meta=self.META)
        self.assertEqual(self.rows()[0]["kind"], "quota")

    def test_threads_write_whole_lines(self):
        def worker(i):
            for j in range(25):
                cc.record_usage(meta={"task": "tag", "file_hash": f"{i}-{j}"}, backend="http",
                                model_req="deepseek-flash", prompt="x" * 5000, kind=None, total_ms=1)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        rows = self.rows()  # 任何一行被交錯寫壞，json.loads 就會拋
        self.assertEqual(len(rows), 200)
        self.assertEqual(len({r["file_hash"] for r in rows}), 200)

    def test_write_failure_is_fail_open(self):
        blocker = Path(self._tmpdir.name) / "file"
        blocker.write_text("", encoding="utf-8")
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        with mock.patch.dict(os.environ, {"LLM_USAGE_LOG": str(blocker / "llm_usage.jsonl")}), \
             mock.patch.object(cc, "_usage_warned", False), \
             mock.patch("sys.stderr", new_callable=lambda: __import__("io").StringIO()) as err:
            self.assertEqual(self.call().text, "ok")
            self.assertEqual(self.call().text, "ok")
        self.assertEqual(err.getvalue().count("用量記錄寫入失敗"), 1, "只警告一次")

    def test_lone_surrogate_prompt_does_not_break_logging(self):
        self.install(lambda req: httpx.Response(200, content=_ok("ok")))
        self.assertEqual(self.call("abc\ud800def").text, "ok")
        self.assertEqual(len(self.rows()), 1)

    def test_default_path_and_conftest_guard(self):
        with mock.patch.dict(os.environ, {"LLM_USAGE_LOG": ""}):
            self.assertEqual(cc.usage_log_path(), le.ROOT / "data" / "llm_usage.jsonl")
        self.assertIn('os.environ["LLM_USAGE_LOG"]', (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
