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
    """簽章與 `llm_failures.FailureRecorder` 同步（加參數時這裡也要加，過期的假物件拋 TypeError
    會被外層 except 吞掉）。`escalated`：以 `escalated=True` 記的 file_hash（400 升級的觸發篇）。"""

    def __init__(self):
        self.recorded: list = []
        self.cleared: list = []
        self.escalated: list = []

    async def record(self, file_hash, reason, *, escalated=False):
        self.recorded.append((file_hash, reason))
        if escalated:
            self.escalated.append(file_hash)

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


_FIELDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel")


def _unique_400():
    """每個請求一則不同的 400 訊息（單篇輸入問題的樣子）。差異刻意是英文字而不是數字：升級比對會把
    數字正規化成 `#`（`_claude_cli._escalation_key`），只差數字的訊息算同一則。"""
    n = iter(_FIELDS)
    return lambda req: httpx.Response(400, json={"error": {"message": f"Invalid request: field {next(n)}"}})


def _serde_422(req):
    """DeepSeek 反序列化錯誤的形狀：`column N` 隨請求 body 長度變（審查實驗 [2]）。"""
    return httpx.Response(422, json={"error": {"message": (
        "Failed to deserialize the JSON body into the target type: reasoning_effort: unknown variant `none`, "
        f"expected one of `low`, `medium`, `high` at line 1 column {len(req.content)}")}})


def _context_length_400(req):
    """單篇輸入太長：訊息裡的數字隨篇變，正規化後兩篇會是同一句——但它不該升級。"""
    return httpx.Response(400, json={"error": {"message": (
        "This model's maximum context length is 131072 tokens. However, you requested "
        f"{131072 + len(req.content)} tokens. Please reduce the length of the messages or completion.")}})


class BadRequestEscalationBatchTests(HttpMixin, unittest.TestCase):
    """審查 H2 接上批次：同訊息 ≥2 篇 → 整批 rc=2，**中止前**把觸發研報以 bad_request 記入跳過名單；
    不同訊息或只有 1 篇 → 單篇失敗、批次照常跑完。"""

    ASYNC_BATCHES = ("summaries", "titles", "takeaways", "signals")

    def test_same_message_on_two_files_aborts_and_records_first(self):
        for name in self.ASYNC_BATCHES:
            with self.subTest(batch=name):
                self.install(status(400, "Invalid request: unsupported parameter"))
                code, rec, out = self.main_rc(name)
                self.assertEqual(code, 2, out)
                self.assertIn("API[config]", out)
                recorded = set(rec.recorded)
                self.assertLessEqual({("h1", lf.BAD_REQUEST), ("h2", lf.BAD_REQUEST)}, recorded, out)
                self.assertEqual({r for _, r in recorded}, {lf.BAD_REQUEST})
                self.assertIn("h1, h2", out, "完整 file_hash 要印出來")
                # 觸發篇以 escalated 記（計數直接到 SKIP_AFTER_ROUNDS）：下一輪就跳過，不再連續 3 輪 rc=2
                self.assertLessEqual({"h1", "h2"}, set(rec.escalated), out)

    def test_messages_differing_only_in_numbers_escalate(self):
        """審查實驗 [2]：反序列化錯誤帶 `column N`（隨 prompt 長度變），逐字比對永遠不升級。"""
        for name in self.ASYNC_BATCHES:
            with self.subTest(batch=name):
                self.install(_serde_422)
                code, rec, out = self.main_rc(name)
                self.assertEqual(code, 2, out)
                self.assertIn("API[config]", out)
                self.assertLessEqual(len(self.requests), 3, "第二篇就升級（並行批次上限 3）")
                self.assertGreaterEqual(len(set(rec.escalated)), 2, out)

    def test_context_length_never_escalates(self):
        """單篇輸入太長：每篇都是單篇失敗，批次照常跑完、每篇只打 1 次。"""
        for name in self.ASYNC_BATCHES:
            with self.subTest(batch=name):
                self.install(_context_length_400)
                code, rec, out = self.main_rc(name)
                self.assertIsNone(code, out)
                self.assertEqual(sorted(rec.recorded), [(f"h{i}", lf.BAD_REQUEST) for i in range(1, N_ITEMS + 1)])
                self.assertEqual(rec.escalated, [])
                self.assertEqual(len(self.requests), N_ITEMS)

    def test_different_messages_do_not_escalate(self):
        for name in self.ASYNC_BATCHES:
            with self.subTest(batch=name):
                self.install(_unique_400())
                code, rec, out = self.main_rc(name)
                self.assertIsNone(code, out)
                self.assertEqual(sorted(rec.recorded), [(f"h{i}", lf.BAD_REQUEST) for i in range(1, N_ITEMS + 1)])
                self.assertEqual(len(self.requests), N_ITEMS, "每篇只打 1 次")

    def test_one_bad_file_among_successes_does_not_escalate(self):
        good = ok('{"summary": "先進製程需求強勁，上修全年營收預估。"}')
        self.install([status(400, "Invalid request: x")] + [good] * (N_ITEMS - 1))
        code, rec, out = self.main_rc("summaries")
        self.assertIsNone(code, out)
        self.assertEqual(rec.recorded, [("h1", lf.BAD_REQUEST)])
        self.assertEqual(len(rec.cleared), N_ITEMS - 1)

    def test_tag_all_logs_triggers_without_db(self):
        """全語料標註只寫 log、不接 DB（審查 L5）：觸發研報寫進 tag_failures.log。"""
        self.install(status(400, "Invalid request: unsupported parameter"))
        code, rec, out = self.main_rc("tag_all")
        self.assertEqual(code, 2, out)
        self.assertEqual(rec.recorded, [])
        log = (self.tmp / "tag.log").read_text(encoding="utf-8")
        self.assertIn("API[config]", log)
        for line in log.splitlines():
            self.assertEqual(len(line.split("\t")), 3, line)


class _Sess:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, *a, **k):
        return None

    async def commit(self):
        return None

    async def rollback(self):
        return None


class SyncInlineTagTests(HttpMixin, unittest.IsolatedAsyncioTestCase):
    """行內標註（`sync_new_reports._run`）：內容審查 → skip_blocked；400 升級 → 記表、寫保留檔、中止。

    `_run` 的重型相依（抽字、DB、R2、嵌入）全部換成假物件；只走到標註後的分流，不入庫。
    """

    def _files(self, n=2):
        src = self.tmp / "src"
        src.mkdir(exist_ok=True)
        names = [f"20260924_券商甲_{i}.pdf" for i in range(1, n + 1)]
        for name in names:
            (src / name).write_bytes(b"%PDF-1.4\n")
        delta = self.tmp / "delta.txt"
        delta.write_text("\n".join(names) + "\n", encoding="utf-8")
        return src, delta

    async def _run(self, n=2, model=DS):
        from app.services.extract import ExtractResult

        src, delta = self._files(n)
        hashes = {}

        def fake_extract(path, *a, **k):
            h = hashes.setdefault(path.name, f"{len(hashes) + 1:064d}")
            return ExtractResult(h, "本報告討論產業前景。" * 20, 200, False, "zh")

        rec = SpyRecorder()
        self.hashes_out = self.tmp / "hashes_out.txt"
        args = argparse.Namespace(
            delta=str(delta), all_local=False, dry_run=False, limit=None, batch_size=32,
            hashes_out=str(self.hashes_out),
        )
        real_tag = snr._tag_via_cli
        with contextlib.ExitStack() as st:
            p = st.enter_context
            p(mock.patch.object(snr, "SRC_LOCAL", src))
            p(mock.patch.object(snr, "TAGS_DIR", self.tmp / "tags"))
            p(mock.patch.object(snr, "FAIL_LOG", self.tmp / "sync_failures.log"))
            p(mock.patch.object(snr, "STATS_FILE", self.tmp / "stats"))
            p(mock.patch.object(snr, "_tag_via_cli", lambda *a, **k: real_tag(*a, model=model, **k)))
            p(mock.patch("app.services.extract.extract_text", fake_extract))
            p(mock.patch("app.services.db.SessionFactory", lambda: _Sess()))
            p(mock.patch("app.services.store.report_exists", mock.AsyncMock(return_value=False)))
            p(mock.patch("app.services.store.upsert_extraction_log", mock.AsyncMock()))
            p(mock.patch("app.services.object_storage.get_object_storage",
                         lambda: argparse.Namespace(enabled=False)))
            p(mock.patch.object(lf, "open_recorder", mock.AsyncMock(return_value=rec)))
            p(contextlib.redirect_stdout(io.StringIO()))
            await snr._run(args)
        return rec

    def stats(self) -> dict:
        return dict(ln.split("=", 1) for ln in (self.tmp / "stats").read_text(encoding="utf-8").splitlines())

    async def test_content_filter_is_skip_blocked(self):
        self.install(status(400, "Content Exists Risk"))
        rec = await self._run()
        st = self.stats()
        self.assertEqual(st["skip_blocked"], "2")
        self.assertEqual(st["skip_untagged"], "0")
        self.assertEqual(st["abnormal"], "2", "skip_blocked 算異常（本該入庫卻沒進 DB）")
        self.assertEqual(sorted(rec.recorded), [(f"{i:064d}", lf.CONTENT_FILTER) for i in (1, 2)])
        log = (self.tmp / "sync_failures.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(log), 2)
        for line in log:
            path, stage, reason = line.split("\t")
            self.assertEqual(stage, "tag_blocked")
            self.assertTrue(Path(path).is_file())
            self.assertIn("file_hash=", reason)
        self.assertEqual(self.hashes_out.read_text(encoding="utf-8"), "", "不入庫")

    async def test_other_http_content_failures_stay_untagged_but_recorded(self):
        self.install(_unique_400())
        rec = await self._run()
        st = self.stats()
        self.assertEqual((st["skip_untagged"], st["skip_blocked"]), ("2", "0"))
        self.assertEqual(sorted(rec.recorded), [(f"{i:064d}", lf.BAD_REQUEST) for i in (1, 2)])

    async def test_bad_request_escalation_records_retains_and_aborts(self):
        self.install(status(400, "Invalid request: unsupported parameter"))
        with self.assertRaises(cc.BadRequestEscalation):
            await self._run(n=3)
        retained = snr.bad_request_path(self.hashes_out)
        lines = retained.read_text(encoding="utf-8").splitlines()
        self.assertEqual([ln.split("\t")[0] for ln in lines], [f"{i:064d}" for i in (1, 2)])
        for ln in lines:
            self.assertTrue(Path(ln.split("\t")[1]).is_file(), "第二欄要是能拿來改 delta 的路徑")
        self.assertEqual(len(self.requests), 2, "第三篇不再送出")

    async def test_bad_request_escalation_records_before_abort(self):
        rec_holder = {}
        orig = SpyRecorder

        class Capture(orig):
            def __init__(self):
                super().__init__()
                rec_holder["rec"] = self

        self.install(status(400, "Invalid request: unsupported parameter"))
        with mock.patch(f"{__name__}.SpyRecorder", Capture), self.assertRaises(cc.BadRequestEscalation):
            await self._run(n=3)
        recorded = set(rec_holder["rec"].recorded)
        self.assertEqual(recorded, {(f"{i:064d}", lf.BAD_REQUEST) for i in (1, 2)})
        self.assertEqual(sorted(rec_holder["rec"].escalated), [f"{i:064d}" for i in (1, 2)])

    async def test_claude_cli_path_unchanged(self):
        """預設 claude_cli：CLI 失敗照舊是 skip_untagged、不記跳過名單、skip_blocked 計數存在但為 0。"""
        import subprocess as sp

        fail = sp.CompletedProcess([], 1, "", "Content Exists Risk")  # CLI 的 stderr 長得像也不算
        with mock.patch.object(cc.subprocess, "run", return_value=fail):
            rec = await self._run(n=1, model="claude-haiku-4-5")
        st = self.stats()
        self.assertEqual((st["skip_untagged"], st["skip_blocked"], st["abnormal"]), ("1", "0", "1"))
        self.assertEqual(rec.recorded, [])
        self.assertEqual(self.requests, [])
        stage = (self.tmp / "sync_failures.log").read_text(encoding="utf-8").split("\t")[1]
        self.assertEqual(stage, "tag")

class BriefContentFilterTests(HttpMixin, unittest.TestCase):
    """簡報被內容審查擋下：該次跳過、不寫列、rc 非 0（走 OnFailure 告警鏈），並記在輸出與失敗紀錄。"""

    def _generate(self):
        upsert = mock.AsyncMock()
        record = mock.MagicMock()
        svc = gb.brief_service
        args = argparse.Namespace(date=None, force=True, after_hour=0, dry_run=False, model=DS, max_lookback_days=7)
        err = io.StringIO()
        with contextlib.ExitStack() as st:
            p = st.enter_context
            p(mock.patch.object(gb, "SessionFactory", lambda: _FakeSession()))
            p(mock.patch.object(svc, "fetch_by_date", mock.AsyncMock(return_value=None)))
            p(mock.patch.object(svc, "fetch_latest", mock.AsyncMock(return_value=None)))
            p(mock.patch.object(svc, "fetch_window_reports",
                                mock.AsyncMock(return_value=[argparse.Namespace(report_id="r1")])))
            p(mock.patch.object(svc, "count_window_reports", mock.AsyncMock(return_value=1)))
            p(mock.patch.object(svc, "fetch_signal_changes", mock.AsyncMock(return_value=[])))
            p(mock.patch.object(svc, "build_material", return_value="素材"))
            p(mock.patch.object(svc, "build_prompt", return_value="簡報提示詞"))
            p(mock.patch.object(svc, "upsert_brief", upsert))
            p(mock.patch.object(gb, "record_failure", record))
            p(mock.patch.object(gb, "claude_cli_lock_or_exit", lambda name: contextlib.nullcontext()))
            p(contextlib.redirect_stdout(io.StringIO()))
            p(contextlib.redirect_stderr(err))
            rc = asyncio.run(gb.generate(args))
        return rc, upsert, record, err.getvalue()

    def test_content_filter_skips_with_nonzero_rc(self):
        self.install(status(400, "Content Exists Risk"))
        rc, upsert, record, err = self._generate()
        self.assertNotEqual(rc, 0)
        upsert.assert_not_awaited()
        self.assertIn("內容審查", err)
        self.assertTrue(record.call_args.args[1].startswith("API[content_filter]"))
        self.assertEqual(len(self.requests), 1, "審查不重打")

    def test_success_records_the_model_actually_used(self):
        self.install(ok("## 今日重點\n" + "- 台積電上修目標價，先進製程需求強勁。\n" * 10))
        rc, upsert, record, _ = self._generate()
        self.assertEqual(rc, 0)
        self.assertEqual(upsert.await_args.kwargs["model"], DS)
        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
