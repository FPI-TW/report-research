"""批次經 `scripts/_claude_cli.run_claude` 分派到 DeepSeek（遷移 PR-12）：逐支批次走到 main。

`tests/test_claude_cli.py` 測呼叫層本身；這裡測「接上批次之後」的行為——帳號層級錯誤要從單篇
函式一路拋到各批次 main、以 rc=2 中止整批，不記跳過名單、不寫單篇失敗紀錄、不 spawn CLI。

全程不連網：HTTP 一律 `httpx.MockTransport`（`llm_http._transport`），金鑰是 gitleaks allowlist
內的假值、以 `mock.patch.dict` 限定在單一測試內；DB 一律 patch 掉。
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import llm_failures as lf  # noqa: E402
from app.services import llm_http as lh  # noqa: E402
from scripts import _claude_cli as cc  # noqa: E402

FAKE_KEY = "fixed-test-secret-deepseek0"
ENV = {"DEEPSEEK_API_KEY": FAKE_KEY, "DEEPSEEK_BASE_URL": "https://api.example.test"}
DS = "deepseek-flash"
N_ITEMS = 5  # 批次 main 的工作量：帳號錯誤時只該打到第一篇


def _load_script(name: str):
    # 先註冊進 sys.modules 再 exec：腳本內有 @dataclass（理由見 test_extract_takeaways_sql.py）
    spec = importlib.util.spec_from_file_location(f"_bhd_{name}", REPO_ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


gs = _load_script("generate_summaries")
gt = _load_script("generate_titles")
et = _load_script("extract_takeaways")
es = _load_script("extract_signals")
tac = _load_script("tag_all_cli")
snr = _load_script("sync_new_reports")
gb = _load_script("generate_brief")


def sse(*events, done: bool = True) -> bytes:
    out = []
    for ev in events:
        out += ["data: " + json.dumps(ev, ensure_ascii=False), ""]
    if done:
        out += ["data: [DONE]", ""]
    return ("\n".join(out) + "\n").encode("utf-8")


def chunk(content=None, finish=None, usage=None):
    delta = {} if content is None else {"content": content}
    return {"model": DS, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}], "usage": usage}


def ok_body(text: str) -> bytes:
    return sse(chunk(content=text), chunk(content="", finish="stop"))


def ok(text: str):
    return lambda req: httpx.Response(200, content=ok_body(text))


def status(code: int, message: str = "x"):
    return lambda req: httpx.Response(code, json={"error": {"message": message}})


# 帳號層級：每一篇都會踩到，要整批中止（第二版計畫 §4.6）
ACCOUNT_CASES = {
    "auth": status(401, "Authentication Fails"),
    "quota": status(402, "Insufficient Balance"),
    "config_404": status(404, "Not Found"),
    "config_model": status(400, "Model Not Exist"),
}


class SpyRecorder:
    def __init__(self):
        self.recorded: list = []
        self.cleared: list = []

    async def record(self, file_hash, reason):
        self.recorded.append((file_hash, reason))

    async def clear(self, file_hash):
        self.cleared.append(file_hash)


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None


class HttpMixin:
    """裝 MockTransport、給假金鑰、擋住 CLI、把傳輸層退避換成記錄器。"""

    def setUp(self):
        super().setUp()
        env = mock.patch.dict(os.environ, ENV)
        env.start()
        self.addCleanup(env.stop)
        spawn = mock.patch.object(cc.subprocess, "run", side_effect=AssertionError("不該 spawn claude"))
        spawn.start()
        self.addCleanup(spawn.stop)
        self.sleeps: list[float] = []
        sleeper = mock.patch.object(cc, "_http_sleep", self.sleeps.append)
        sleeper.start()
        self.addCleanup(sleeper.stop)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self.uninstall)
        self.requests: list[httpx.Request] = []

    def install(self, handler):
        """handler：單一函式（每次都用它），或函式清單（依序各用一次）。順便清斷路器的窗：
        同一題的多個 subTest 累積的逾時／過載不該互相影響。"""
        self.requests = []
        cc._reset_state()
        seq = iter(handler) if isinstance(handler, list) else None

        def recording(request):
            self.requests.append(request)
            return (next(seq) if seq is not None else handler)(request)

        lh._transport = httpx.MockTransport(recording)
        lh._reset_clients()

    def uninstall(self):
        lh._transport = None
        lh._reset_clients()

    def body(self, i: int = 0) -> dict:
        return json.loads(self.requests[i].content)

    # ── 各批次的單篇函式（HTTP model） ────────────────────────────────────────
    async def run_title_or_summary(self, mod, fn: str, file_hash: str = "h1", rid: str = "rid"):
        rec = SpyRecorder()
        with mock.patch.object(mod, "FAIL_LOG", self.tmp / f"{fn}.log"), \
             mock.patch.object(mod, "MODEL", DS), \
             mock.patch.object(mod, "SessionFactory", lambda: _FakeSession()):
            await getattr(mod, fn)(
                asyncio.Semaphore(1), rid, "f.pdf", "內文" * 50, 3000, 1, file_hash=file_hash, recorder=rec,
            )
        return rec

    async def run_takeaway(self, file_hash: str = "h1"):
        rec = SpyRecorder()
        item = et.WorkItem("rep-1", "f.pdf", None, "券商甲", "正典文字" * 20, "sha", file_hash=file_hash)
        with mock.patch.object(et, "FAIL_LOG", self.tmp / "takeaway.log"), \
             mock.patch.object(et, "_replace_rows", new=mock.AsyncMock()):
            await et.extract_one(asyncio.Semaphore(1), item, 24000, DS, 1, recorder=rec)
        return rec

    async def run_signal(self, file_hash: str = "h1"):
        rec = SpyRecorder()
        item = es.WorkItem("rep-1", "TW", "券商甲", None, "f.pdf", "內文" * 20, ["2330"], file_hash=file_hash)
        with mock.patch.object(es, "FAIL_LOG", self.tmp / "signal.log"), \
             mock.patch.object(es, "_upsert_rows", new=mock.AsyncMock()):
            await es.extract_one(asyncio.Semaphore(1), item, 16000, DS, 1, recorder=rec)
        return rec

    def run_tag_all(self, file_hash: str = "h1") -> str:
        rec = {"file_hash": file_hash, "file_name": "f.pdf", "text": "內文"}
        with mock.patch.object(tac, "TAGS_DIR", self.tmp), \
             mock.patch.object(tac, "FAIL_LOG", self.tmp / "tag.log"), \
             mock.patch.object(tac, "MODEL", DS):
            return tac.tag_one(rec, 10000)

    # ── 各批次的 main（整批中止要回 rc=2） ───────────────────────────────────
    def main_rc(self, name: str) -> tuple[int | None, SpyRecorder, str]:
        """跑某支批次的 main，回 (SystemExit 碼或 main 的回傳值, 跳過名單寫入端, stdout+stderr)。"""
        rec = SpyRecorder()
        out = io.StringIO()
        code = None
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(out))
            stack.enter_context(mock.patch.object(lf, "open_recorder", mock.AsyncMock(return_value=rec)))
            try:
                code = self._main(name, stack)
            except SystemExit as exc:
                code = exc.code
        return code, rec, out.getvalue()

    def _main(self, name: str, stack: contextlib.ExitStack):
        p = stack.enter_context
        if name in ("summaries", "titles"):
            mod = gs if name == "summaries" else gt
            p(mock.patch.object(mod, "MODEL", DS))
            p(mock.patch.object(mod, "FAIL_LOG", self.tmp / f"{name}.log"))
            p(mock.patch.object(mod, "fetch_candidates", mock.AsyncMock(
                return_value=[(f"rid{i}", f"{i}.pdf", "內文" * 50, f"h{i}") for i in range(1, N_ITEMS + 1)])))
            p(mock.patch.object(mod, "SessionFactory", lambda: _FakeSession()))
            return asyncio.run(mod.main(1, None, 3000))
        if name == "takeaways":
            items = [et.WorkItem(f"rep-{i}", "f.pdf", None, "券商甲", "正典文字" * 20, "sha", file_hash=f"h{i}")
                     for i in range(1, N_ITEMS + 1)]
            p(mock.patch.object(et, "FAIL_LOG", self.tmp / "takeaway.log"))
            p(mock.patch.object(et, "build_worklist", mock.AsyncMock(return_value=(2, items))))
            p(mock.patch.object(et, "_replace_rows", new=mock.AsyncMock()))
            args = argparse.Namespace(
                hashes_file=None, model=DS, retry_blocked=False, reextract=False, since_days=90,
                dry_run=False, limit=None, workers=1, excerpt=24000,
            )
            return asyncio.run(et.main(args))
        if name == "signals":
            items = [es.WorkItem(f"rep-{i}", "TW", "券商甲", None, "f.pdf", "內文" * 20, ["2330"], file_hash=f"h{i}")
                     for i in range(1, N_ITEMS + 1)]
            p(mock.patch.object(es, "FAIL_LOG", self.tmp / "signal.log"))
            p(mock.patch.object(es, "build_worklist", mock.AsyncMock(return_value=([], items))))
            p(mock.patch.object(es, "_upsert_rows", new=mock.AsyncMock()))
            args = argparse.Namespace(
                model=DS, retry_blocked=False, reextract=False, min_brokers=3, min_reports=5, top_n=50,
                dry_run=False, limit=None, workers=1, excerpt=16000,
            )
            return asyncio.run(es.main(args))
        if name == "tag_all":
            recs = [{"file_hash": f"h{i}", "file_name": f"{i}.pdf", "text": "內文"} for i in range(1, N_ITEMS + 1)]
            p(mock.patch.object(tac, "TAGS_DIR", self.tmp))
            p(mock.patch.object(tac, "FAIL_LOG", self.tmp / "tag.log"))
            p(mock.patch.object(tac, "MODEL", DS))
            p(mock.patch.object(tac.cache, "iter_records", return_value=iter(recs)))
            return tac.main(1, None, 10000)
        if name == "sync":
            async def fake_run(args):
                for h in [f"h{i}" for i in range(1, N_ITEMS + 1)]:
                    snr._tag_via_cli(f"{h}.pdf", "內文", model=DS, file_hash=h)

            p(mock.patch.object(snr, "_run", fake_run))
            p(mock.patch.object(snr, "require_llm_key"))
            p(mock.patch.object(snr, "claude_cli_lock_or_exit", lambda name: contextlib.nullcontext()))
            p(mock.patch.object(sys, "argv", ["sync_new_reports.py", "--delta", "d.txt"]))
            return snr.main()
        if name == "brief":
            async def fake_generate(args):
                raw, error = gb.call_cli("素材", args.model)
                return 1 if error else 0

            p(mock.patch.object(gb, "generate", fake_generate))
            p(mock.patch.object(gb, "require_llm_key"))
            p(mock.patch.object(gb, "record_failure"))
            p(mock.patch.object(sys, "argv", ["generate_brief.py", "--model", DS]))
            return gb.main()
        raise AssertionError(name)


BATCHES = ("summaries", "titles", "takeaways", "signals", "tag_all", "sync", "brief")
# 並行批次（asyncio.gather＋to_thread）在第一篇拋出後，下一篇可能已經被排進執行緒；中止是
# 「不再開新的」，不是「連已排進去的都收回」。所以上限是 2，不是 1。tag_all_cli 的
# ThreadPoolExecutor 在主執行緒處理到第一個例外之前，工作執行緒可能已經把佇列跑完（假物件
# 回得太快）——`pending.cancel()` 只收得回還沒開始的，這是既有行為，這裡不量它的請求數。
PARALLEL = ("summaries", "titles", "takeaways", "signals")


class AccountErrorAbortsWholeBatchTests(HttpMixin, unittest.TestCase):
    """401／402／模型不存在：`LlmEnvironmentError`（`CliNotFoundError` 子類）→ 各批次 main 整批 rc=2。

    記成 N 筆單篇失敗後 exit 0 是四天停擺的型態；記進跳過名單會讓研報以 DeepSeek 的 model 名被擋在
    外面（那不是研報的問題）；改走 Claude 等於繞過預算。三件事都不能發生。
    """

    def test_every_batch_main_exits_2(self):
        for name in BATCHES:
            for case, handler in ACCOUNT_CASES.items():
                with self.subTest(batch=name, case=case):
                    self.install(handler)
                    code, rec, out = self.main_rc(name)
                    self.assertEqual(code, 2, out)
                    self.assertEqual(rec.recorded, [], "帳號錯誤不記跳過名單")
                    limit = N_ITEMS if name == "tag_all" else 2 if name in PARALLEL else 1
                    self.assertLessEqual(len(self.requests), limit, f"{N_ITEMS} 篇只該打到第一篇就中止")
                    self.assertGreaterEqual(len(self.requests), 1)
                    self.assertIn("API[", out, "中止訊息要帶出 API[<kind>]")
                    logs = [p for p in self.tmp.glob("*.log") if p.stat().st_size]
                    self.assertEqual(logs, [], "不寫單篇失敗紀錄")

    def test_exception_type_is_the_batch_abort_type(self):
        self.assertTrue(issubclass(cc.LlmEnvironmentError, cc.CliNotFoundError))
        for mod in (gs, gt, et, es, tac, snr, gb):
            self.assertIs(mod.CliNotFoundError, cc.CliNotFoundError, mod.__name__)


class HttpSuccessThroughBatchesTests(HttpMixin, unittest.IsolatedAsyncioTestCase):
    """成功路徑：各批次拿到的是 DeepSeek 的文字，且請求帶的是該呼叫點的 max_tokens 與 task。"""

    async def test_summary_title_takeaway_signal_tag(self):
        self.install(ok('{"summary": "先進製程需求強勁，上修全年營收預估。"}'))
        rec = await self.run_title_or_summary(gs, "summarize_one")
        self.assertEqual(rec.cleared, ["h1"])
        self.assertEqual(self.body()["max_tokens"], gs.MAX_TOKENS)
        self.assertEqual(self.body()["user_id"], "batch-summary")

        self.install(ok('{"title": "台積電：先進製程需求強勁", "title_original": null, "title_source": "extracted"}'))
        rec = await self.run_title_or_summary(gt, "title_one")
        self.assertEqual(rec.cleared, ["h1"])
        self.assertEqual(self.body()["user_id"], "batch-title")

        tag = ('{"market":"TW","is_research":true,"confidence":0.9,"instrument_types":["equity"],'
               '"relates_stock":true,"relates_futures":false,"stock_targets":["2330"],"futures_targets":[]}')
        self.install(ok(tag))
        self.assertEqual(self.run_tag_all(), "ok")
        self.assertEqual(self.body()["user_id"], "batch-tag")
        self.assertEqual(self.body()["messages"], [{"role": "user", "content": self.body()["messages"][0]["content"]}])

    def test_sync_inline_tag_and_brief(self):
        tag = ('{"market":"TW","is_research":true,"confidence":0.9,"instrument_types":["equity"],'
               '"relates_stock":true,"relates_futures":false,"stock_targets":["2330"],"futures_targets":[]}')
        self.install(ok(tag))
        got, err = snr._tag_via_cli("x.pdf", "內文", model=DS, file_hash="h1")
        self.assertIsNotNone(got)
        self.assertIsNone(err)
        self.assertEqual(self.body()["max_tokens"], snr.TAG_MAX_TOKENS)

        self.install(ok("## 今日重點\n- 一"))
        raw, err = gb.call_cli("素材", DS)
        self.assertEqual((raw, err), ("## 今日重點\n- 一", None))
        self.assertEqual(self.body()["max_tokens"], gb.MAX_TOKENS)
        self.assertEqual(self.body()["user_id"], "batch-brief")


# HTTP 單篇失敗：傳輸層已處理，腳本層一律只打 1 次（第二版 §4.7）。值是跳過名單該記的 reason。
PER_FILE_FAILURES = {
    "content_filter": (status(400, "Content Exists Risk"), lf.CONTENT_FILTER),
    "truncated": (lambda req: httpx.Response(
        200, content=sse(chunk(content="{\"半"), chunk(content="", finish="length"))), lf.TRUNCATED),
    "empty": (lambda req: httpx.Response(200, content=sse(chunk(content="", finish="stop"))), lf.EMPTY),
    "bad_request": (status(400, "Invalid request: prompt too long"), lf.BAD_REQUEST),
    "overloaded": (status(503, "busy"), None),  # 傳輸層已退避重試 3 次；環境型不記
}

# 解析不了的回應（「回應成功但解析失敗」）：各批次腳本層照舊重試到 3 次
UNPARSEABLE_TEXT = "抱歉，我無法處理這份文件。"


class ScriptLevelRetryTests(HttpMixin, unittest.IsolatedAsyncioTestCase):
    """`is_retryable`：`API[...]` 錯誤在 5 支批次都只呼叫 1 次；unparseable 仍重試到 3 次。

    以 `complete_chat` 的呼叫次數量腳本層（傳輸層的重試不算在內）。
    """

    def spy(self):
        spy = mock.patch.object(cc.llm_http, "complete_chat", wraps=cc.llm_http.complete_chat)
        m = spy.start()
        self.addCleanup(spy.stop)
        return m

    async def _one(self, script: str):
        """跑某支批次的單篇函式；回 (跳過名單寫入端或 None, 失敗 log 內容)。"""
        if script == "summaries":
            rec = await self.run_title_or_summary(gs, "summarize_one")
            return rec, (self.tmp / "summarize_one.log").read_text(encoding="utf-8")
        if script == "titles":
            rec = await self.run_title_or_summary(gt, "title_one")
            return rec, (self.tmp / "title_one.log").read_text(encoding="utf-8")
        if script == "takeaways":
            rec = await self.run_takeaway()
            return rec, (self.tmp / "takeaway.log").read_text(encoding="utf-8")
        if script == "signals":
            rec = await self.run_signal()
            return rec, (self.tmp / "signal.log").read_text(encoding="utf-8")
        if script == "tag_all":
            self.assertEqual(self.run_tag_all(), "fail")
            return None, (self.tmp / "tag.log").read_text(encoding="utf-8")
        raise AssertionError(script)

    SCRIPTS = ("summaries", "titles", "takeaways", "signals", "tag_all")

    async def test_api_errors_called_once_per_script(self):
        for script in self.SCRIPTS:
            for case, (handler, reason) in PER_FILE_FAILURES.items():
                with self.subTest(script=script, case=case):
                    for f in self.tmp.glob("*.log"):
                        f.unlink()
                    self.install(handler)
                    calls = self.spy()
                    rec, log = await self._one(script)
                    self.assertEqual(calls.call_count, 1, f"{script}/{case}：API[...] 不得在腳本層重試")
                    self.assertIn(f"API[{case if case != 'overloaded' else 'overloaded'}]", log)
                    if rec is not None:
                        self.assertEqual(rec.recorded, [("h1", reason)] if reason else [])

    async def test_unparseable_still_retried_three_times(self):
        for script in self.SCRIPTS:
            with self.subTest(script=script):
                for f in self.tmp.glob("*.log"):
                    f.unlink()
                self.install(ok(UNPARSEABLE_TEXT if script != "signals" else "不是 JSON"))
                calls = self.spy()
                rec, _ = await self._one(script)
                self.assertEqual(calls.call_count, 3, f"{script}：解析失敗照舊最多 3 次")
                if rec is not None:
                    self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])

    async def test_unparseable_then_api_error_stops_and_records_last_reason(self):
        """先解析失敗、再遇到截斷：截斷那次就停，記截斷（1 次就跳過）。"""
        for script in ("summaries", "titles", "takeaways", "signals"):
            with self.subTest(script=script):
                self.install([
                    ok(UNPARSEABLE_TEXT if script != "signals" else "不是 JSON"),
                    PER_FILE_FAILURES["truncated"][0],
                ])
                calls = self.spy()
                rec, _ = await self._one(script)
                self.assertEqual(calls.call_count, 2)
                self.assertEqual(rec.recorded, [("h1", lf.TRUNCATED)])

    async def test_cli_errors_keep_script_level_retries(self):
        """CLI 路徑語意不變：`CLI 逾時` 之類照舊重試 3 次、不記跳過名單。"""
        err = cc.CliResult(None, "CLI 逾時（180s 內未回應）")
        cases = (
            (gs, lambda: self.run_title_or_summary(gs, "summarize_one")),
            (gt, lambda: self.run_title_or_summary(gt, "title_one")),
            (et, self.run_takeaway),
            (es, self.run_signal),
        )
        for mod, run in cases:
            with self.subTest(mod=mod.__name__), mock.patch.object(mod, "call_cli", return_value=err) as call:
                rec = await run()
                self.assertEqual(call.call_count, 3)
                self.assertEqual(rec.recorded, [])
        with mock.patch.object(tac, "call_cli", return_value=err) as call:
            self.assertEqual(self.run_tag_all(), "fail")
        self.assertEqual(call.call_count, 3)


class SummaryPlainTextFallbackTests(HttpMixin, unittest.IsolatedAsyncioTestCase):
    """審查 D11：純文字 fallback 只給 CLI；DeepSeek 不守 JSON 格式＝解析失敗（多半是閒聊或拒答）。"""

    def test_parse_summary_flag(self):
        self.assertEqual(gs.parse_summary("這是一段摘要。"), "這是一段摘要。")
        self.assertIsNone(gs.parse_summary("這是一段摘要。", allow_plain_text=False))
        self.assertEqual(gs.parse_summary('{"summary": "摘要"}', allow_plain_text=False), "摘要")

    async def test_http_plain_text_is_unparseable(self):
        self.install(ok(UNPARSEABLE_TEXT))
        rec = await self.run_title_or_summary(gs, "summarize_one")
        self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])
        self.assertEqual(rec.cleared, [])
        self.assertEqual(len(self.requests), 3)

    async def test_cli_plain_text_still_accepted(self):
        rec = SpyRecorder()
        with mock.patch.object(gs, "FAIL_LOG", self.tmp / "s.log"), \
             mock.patch.object(gs, "MODEL", "claude-sonnet-5"), \
             mock.patch.object(gs, "SessionFactory", lambda: _FakeSession()), \
             mock.patch.object(gs, "call_cli", return_value=cc.CliResult("先進製程需求強勁。", None)):
            await gs.summarize_one(asyncio.Semaphore(1), "rid", "f.pdf", "內文", 3000, 1, file_hash="h1", recorder=rec)
        self.assertEqual(rec.cleared, ["h1"])


class BreakerAbortsBatchTests(HttpMixin, unittest.TestCase):
    """斷路器跳脫時整批 rc=2（沿用 `except CliNotFoundError`），並寫標記。"""

    def test_summaries_and_sync_abort_on_breaker(self):
        marker = self.tmp / "data" / ".llm_breaker"
        for name in ("summaries", "sync"):
            with self.subTest(batch=name), mock.patch.dict(os.environ, {"LLM_BREAKER_FILE": str(marker)}):
                cc._reset_state()
                with contextlib.suppress(FileNotFoundError):
                    marker.unlink()
                self.install(status(503, "busy"))
                code, rec, out = self.main_rc(name)
                self.assertEqual(code, 2, out)
                self.assertIn("斷路器", out)
                self.assertTrue(marker.exists())
                self.assertEqual(rec.recorded, [], "過載不是研報的問題，不記跳過名單")


if __name__ == "__main__":
    unittest.main()
