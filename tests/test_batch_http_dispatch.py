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
    """裝 MockTransport、給假金鑰、擋住任何子行程、把傳輸層退避換成記錄器。"""

    def setUp(self):
        super().setUp()
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
    def main_rc(self, name: str, *, no_table: bool = False) -> tuple[int | None, SpyRecorder, str]:
        """跑某支批次的 main，回 (SystemExit 碼或 main 的回傳值, 跳過名單寫入端, stdout+stderr)。

        `no_table=True`：`open_recorder` 回 None（`llm_task_failure` 表不存在，部署漏了 make schema）。
        """
        rec = SpyRecorder()
        out = io.StringIO()
        code = None
        with contextlib.ExitStack() as stack:
            stack.enter_context(contextlib.redirect_stdout(out))
            stack.enter_context(contextlib.redirect_stderr(out))
            stack.enter_context(mock.patch.object(
                lf, "open_recorder", mock.AsyncMock(return_value=None if no_table else rec)))
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


CLAUDE = "claude-haiku-4-5"


class NonWhitelistAbortsWholeBatchTests(HttpMixin, unittest.TestCase):
    """PR-M：model 不在白名單（含 claude-*；PR-M 前走 CLI）→ 每支批次（含簡報）整批 rc=2，不送出、
    不記跳過名單、不寫單篇失敗紀錄。

    這是 PR-M 前「CLI 認證失效整批 rc=2」（9/23 事故）的後繼：設定把某段解析到 Claude 時，不能變成
    逐篇記「單篇失敗」、整批 rc=0。`main_rc` 對 sync 與簡報已略過預檢（見 `_main`），所以這裡量到的是
    `run_claude` 本身的防線；預檢那一道在 tests/test_llm_env_loading.py。
    """

    def test_every_batch_main_exits_2(self):
        for name in BATCHES:
            with self.subTest(batch=name), mock.patch.object(sys.modules[__name__], "DS", CLAUDE):
                self.install(lambda req: (_ for _ in ()).throw(AssertionError("白名單外的 model 不該打 HTTP")))
                code, rec, out = self.main_rc(name)
                self.assertEqual(code, 2, out)
                self.assertEqual(rec.recorded, [], "設定錯誤不記跳過名單")
                self.assertIn("不在 DeepSeek 白名單", out)
                self.assertIn(CLAUDE, out)
                self.assertEqual(self.requests, [])
                logs = [p for p in self.tmp.glob("*.log") if p.stat().st_size]
                self.assertEqual(logs, [], "不寫單篇失敗紀錄")


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


class _OneFileMixin(HttpMixin):
    """跑各批次的單篇函式（HTTP model），回跳過名單寫入端與失敗 log。"""

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


class ScriptLevelRetryTests(_OneFileMixin, unittest.IsolatedAsyncioTestCase):
    """`is_retryable`：`API[...]` 錯誤在 5 支批次都只呼叫 1 次；unparseable 仍重試到 3 次。

    以 `complete_chat` 的呼叫次數量腳本層（傳輸層的重試不算在內）。
    """

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


def _mid_stream_error(req):
    """吐了字、再以串流中的錯誤物件結束（→ overloaded）：這個請求已經計費。"""
    body = sse(chunk(content='{"summary": "半'), {"error": {"message": "server error"}}, done=False)
    return httpx.Response(200, content=body)


class BilledRequestsPerFileTests(_OneFileMixin, unittest.IsolatedAsyncioTestCase):
    """審查中3：每篇每輪最多 3 個已計費（吐了字）的請求；截斷與審查只有 1 個。

    修正前傳輸層不看 `streamed`：審查實驗 [1] 的「吐字後斷 ×2、第三次解析不了」×3 輪一篇打出 9 個
    已計費請求。這裡以實際送出的請求數量（不是 complete_chat 的呼叫次數）驗證。
    """

    ASYNC_SCRIPTS = ("summaries", "titles", "takeaways", "signals")

    def _unparseable(self, script):
        return ok(UNPARSEABLE_TEXT if script != "signals" else "不是 JSON")

    async def test_reviewer_experiment_1(self):
        for script in self.ASYNC_SCRIPTS:
            with self.subTest(script=script):
                self.install([_mid_stream_error, _mid_stream_error, self._unparseable(script)] * 3)
                rec, log = await self._one(script)
                self.assertEqual(len(self.requests), 1, "吐字後出事：傳輸層與腳本層都不重打")
                self.assertEqual(rec.recorded, [], "過載是環境型，不記跳過名單")
                self.assertIn("API[overloaded]", log)

    async def test_at_most_three_billed_requests(self):
        """解析失敗兩次、第三次吐字後斷：3 個已計費請求就停，記 unparseable（先前的原因不被環境型蓋掉）。"""
        for script in self.ASYNC_SCRIPTS:
            with self.subTest(script=script):
                self.install([self._unparseable(script)] * 2 + [_mid_stream_error] * 10)
                rec, _ = await self._one(script)
                self.assertEqual(len(self.requests), 3)
                self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])

    async def test_truncated_and_content_filter_single_request(self):
        for script in self.ASYNC_SCRIPTS:
            for case in ("truncated", "content_filter"):
                with self.subTest(script=script, case=case):
                    self.install(PER_FILE_FAILURES[case][0])
                    await self._one(script)
                    self.assertEqual(len(self.requests), 1)

    async def test_unparseable_then_timeout_records_unparseable(self):
        """N08／N09：先解析失敗、再逾時（環境型）→ 記 unparseable，不因最後一次是環境型就什麼都不記。"""
        timeout = cc.CliResult(None, lh.error_string(lh.TIMEOUT, "超過總期限"))
        seq = [cc.CliResult(UNPARSEABLE_TEXT, None), timeout]
        cases = (
            (gs, lambda: self.run_title_or_summary(gs, "summarize_one")),
            (gt, lambda: self.run_title_or_summary(gt, "title_one")),
        )
        for mod, run in cases:
            with self.subTest(mod=mod.__name__), mock.patch.object(mod, "call_cli", side_effect=list(seq)) as call:
                rec = await run()
                self.assertEqual(call.call_count, 2, "逾時（API[...]）之後不再重試")
                self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])
        for mod, run in ((et, self.run_takeaway), (es, self.run_signal)):
            with self.subTest(mod=mod.__name__), \
                    mock.patch.object(mod, "call_cli", side_effect=[cc.CliResult("不是 JSON", None), timeout]) as call:
                rec = await run()
                self.assertEqual(call.call_count, 2)
                self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])

    async def test_deadline_after_text_records_timeout_streamed(self):
        """已吐字後才到總期限＝期限型截斷：記 timeout_streamed（連續 3 輪才跳過、不是 truncated 的 1 次就跳過），
        而且**計入斷路器**——DeepSeek 暫時變慢時整段中止，而不是整批研報一輪就進跳過名單。"""
        clock = {"t": 1000.0}

        def drip(req):
            def gen():
                yield sse(chunk(content='{"summary": "很長'), done=False)
                for _ in range(400):
                    clock["t"] += 1.0
                    yield sse(chunk(content="。"), done=False)
            return httpx.Response(200, content=gen())

        fake_time = mock.Mock(monotonic=lambda: clock["t"], sleep=lambda s: None)
        with mock.patch.object(lh, "time", fake_time):
            self.install(drip)  # 只在這裡清一次斷路器的窗：接下來的呼叫要累積
            for i in range(cc.BREAKER_TRIP - 1):
                self.requests.clear()
                rec = await self.run_title_or_summary(gs, "summarize_one", file_hash=f"h{i}")
                self.assertEqual(rec.recorded, [(f"h{i}", lf.TIMEOUT_STREAMED)])
                self.assertEqual(len(self.requests), 1, "已吐字不在傳輸層重試")
                self.assertEqual(sum(cc._BREAKER._recent), i + 1, "期限型截斷計入斷路器")
                one_round = lf.FailureRecord(lf.TIMEOUT_STREAMED, DS, 1)
                self.assertFalse(lf.should_skip(one_round, DS), "1 次不跳過：可能只是暫時變慢")
            with self.assertRaises(cc.LlmEnvironmentError) as ctx:
                await self.run_title_or_summary(gs, "summarize_one", file_hash="hx")
            self.assertIn("斷路器", str(ctx.exception))


class SummaryPlainTextFallbackTests(HttpMixin, unittest.IsolatedAsyncioTestCase):
    """審查 D11：DeepSeek 不守 JSON 格式＝解析失敗（多半是閒聊或拒答）。純文字 fallback 原本只給 CLI，
    PR-M 起批次一律不用（解析器的預設值留給直接呼叫的工具）。"""

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

    async def test_plain_text_is_unparseable_whatever_the_model(self):
        """PR-M 前 claude-* 的純文字會被當成摘要收下；現在批次不看 model，一律嚴格解析。"""
        rec = SpyRecorder()
        with mock.patch.object(gs, "FAIL_LOG", self.tmp / "s.log"), \
             mock.patch.object(gs, "MODEL", "claude-sonnet-5"), \
             mock.patch.object(gs, "SessionFactory", lambda: _FakeSession()), \
             mock.patch.object(gs, "call_cli", return_value=cc.CliResult("先進製程需求強勁。", None)):
            await gs.summarize_one(asyncio.Semaphore(1), "rid", "f.pdf", "內文", 3000, 1, file_hash="h1", recorder=rec)
        self.assertEqual(rec.cleared, [])
        self.assertEqual(rec.recorded, [("h1", lf.UNPARSEABLE)])


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

    def test_escalation_without_failure_table_still_aborts_with_rc_2(self):
        """N18：表不存在（recorder 為 None）時升級照樣中止、rc=2，觸發研報照樣印出，不因 None 崩潰。"""
        for name in self.ASYNC_BATCHES:
            with self.subTest(batch=name):
                self.install(status(400, "Invalid request: unsupported parameter"))
                code, rec, out = self.main_rc(name, no_table=True)
                self.assertEqual(code, 2, out)
                self.assertIn("API[config]", out)
                self.assertIn("h1, h2", out)
                self.assertNotIn("AttributeError", out)
                self.assertEqual(rec.recorded, [])

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

    def test_lone_surrogate_prompts_do_not_abort_the_batch(self):
        """低2：編碼錯誤（孤立代理字元繞過 sanitize）只是單篇 bad_request：整批不中止、每篇記入跳過名單。
        修正前歸 CONFIG → 第一篇就整批 rc=2、不留任何紀錄，下一輪同一篇再中止一次。"""
        for name in self.ASYNC_BATCHES:
            with self.subTest(batch=name), mock.patch.object(lh, "sanitize", lambda text: text + "\ud800"):
                self.install(ok("{}"))
                code, rec, out = self.main_rc(name)
                self.assertNotEqual(code, 2, out)
                self.assertEqual(sorted(rec.recorded), [(f"h{i}", lf.BAD_REQUEST) for i in range(1, N_ITEMS + 1)])
                self.assertEqual(self.requests, [])

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

    async def _run(self, n=2, model=DS, no_table=False):
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
            self.open_recorder = mock.AsyncMock(return_value=None if no_table else rec)
            p(mock.patch.object(lf, "open_recorder", self.open_recorder))
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

    async def test_recorder_is_opened_for_task_tag(self):
        """N11：行內標註的跳過名單要記在 task=tag、以行內標註的模型為鍵（記到別的 task 會擋錯批次）。"""
        self.install(status(400, "Content Exists Risk"))
        await self._run(n=1)
        self.open_recorder.assert_awaited_once()
        self.assertEqual(self.open_recorder.await_args.args[:2], (lf.TASK_TAG, snr.TAG_MODEL))

    async def test_no_failure_table_keeps_classifying(self):
        """表不存在（recorder 為 None）：照樣分流計數、寫 FAIL_LOG，只是不記跳過名單。"""
        self.install(status(400, "Content Exists Risk"))
        rec = await self._run(no_table=True)
        self.assertEqual(self.stats()["skip_blocked"], "2")
        self.assertEqual(rec.recorded, [])

    async def test_escalation_without_failure_table_still_raises_escalation(self):
        """N18（匯入段）：表不存在時 400 升級仍寫保留檔、以 BadRequestEscalation 中止，不因 None 崩潰。"""
        self.install(status(400, "Invalid request: unsupported parameter"))
        with self.assertRaises(cc.BadRequestEscalation):
            await self._run(n=3, no_table=True)
        self.assertTrue(snr.bad_request_path(self.hashes_out).is_file())

    async def test_truncated_is_skip_truncated_not_replayed(self):
        """審查低1：截斷另立 skip_truncated／tag_truncated（failures_to_delta 預設不撈），記 truncated。"""
        self.install(PER_FILE_FAILURES["truncated"][0])
        rec = await self._run()
        st = self.stats()
        self.assertEqual((st["skip_truncated"], st["skip_untagged"], st["skip_blocked"]), ("2", "0", "0"))
        self.assertEqual(st["abnormal"], "2")
        self.assertEqual(sorted(rec.recorded), [(f"{i:064d}", lf.TRUNCATED) for i in (1, 2)])
        lines = (self.tmp / "sync_failures.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual([ln.split("\t")[1] for ln in lines], ["tag_truncated"] * 2)
        self.assertIn("file_hash=", lines[0])
        ftd = _load_script("failures_to_delta")
        ok, _ = ftd.parse_failures(lines, self.tmp / "src")
        self.assertEqual(ok, [], "補救指令不得把截斷的再送一次")

    async def test_deadline_truncation_stays_untagged_and_replayable(self):
        """期限型截斷（timeout_streamed）不是 skip_truncated：記 skip_untagged、階段 tag（補救指令會撈），
        跳過名單記 timeout_streamed。歸 tag_truncated 的話 DeepSeek 暫時變慢一次，那幾篇就永遠不重放。"""
        timed_out = cc.CliResult(None, lh.error_string(lh.TIMEOUT_STREAMED, "已吐字 12 字後超過總期限"))
        with mock.patch.object(snr, "run_claude", return_value=timed_out):
            rec = await self._run()
        st = self.stats()
        self.assertEqual((st["skip_untagged"], st["skip_truncated"]), ("2", "0"))
        self.assertEqual(sorted(rec.recorded), [(f"{i:064d}", lf.TIMEOUT_STREAMED) for i in (1, 2)])
        lines = (self.tmp / "sync_failures.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual([ln.split("\t")[1] for ln in lines], ["tag"] * 2)
        ftd = _load_script("failures_to_delta")
        ok, _ = ftd.parse_failures(lines, self.tmp / "src")
        self.assertEqual(len(ok), 2, "補救指令要撈得到")

    async def test_empty_stays_untagged_and_replayable(self):
        """空回應維持 skip_untagged（階段 tag）：多半是供應商端偶發，補救指令會重送。"""
        self.install(PER_FILE_FAILURES["empty"][0])
        rec = await self._run()
        st = self.stats()
        self.assertEqual((st["skip_untagged"], st["skip_truncated"]), ("2", "0"))
        self.assertEqual(sorted(rec.recorded), [(f"{i:064d}", lf.EMPTY) for i in (1, 2)])
        lines = (self.tmp / "sync_failures.log").read_text(encoding="utf-8").splitlines()
        self.assertEqual([ln.split("\t")[1] for ln in lines], ["tag"] * 2)
        ftd = _load_script("failures_to_delta")
        ok, _ = ftd.parse_failures(lines, self.tmp / "src")
        self.assertEqual(len(ok), 2)

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

    async def test_non_whitelisted_tag_model_aborts_instead_of_skip_untagged(self):
        """PR-M：行內標註解析到 claude-*（PR-M 前走 CLI、失敗記 skip_untagged）→ 整批中止（main rc=2），
        不是逐篇 skip_untagged——後者正是 9/23、9/24 兩天零入庫而排程殼看起來正常的型態。"""
        self.install(lambda req: (_ for _ in ()).throw(AssertionError("白名單外的 model 不該打 HTTP")))
        with self.assertRaises(cc.LlmEnvironmentError):
            await self._run(n=2, model="claude-haiku-4-5")
        self.assertEqual(self.requests, [])


class BriefUsageRowTests(HttpMixin, unittest.TestCase):
    """簡報交給 `run_claude` 後，用量記錄由呼叫層寫、恰好一行（PR-M 前簡報自己的 CLI 版另寫一行
    `backend=cli`，N01、N02）。"""

    def test_http_call_writes_exactly_one_usage_row(self):
        usage = self.tmp / "usage.jsonl"
        self.install(ok("## 今日重點\n- 一"))
        with mock.patch.dict(os.environ, {"LLM_USAGE_LOG": str(usage)}):
            raw, error = gb.call_cli("素材", DS)
        self.assertIsNone(error)
        rows = [json.loads(ln) for ln in usage.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual((rows[0]["task"], rows[0]["backend"], rows[0]["model_req"], rows[0]["kind"]),
                         ("brief", "http", DS, None))


class BriefContentFilterTests(HttpMixin, unittest.TestCase):
    """簡報被內容審查擋下：該次跳過、不寫列、rc 非 0（走 OnFailure 告警鏈），並記在輸出與失敗紀錄。"""

    def _generate(self, *, force=True, model=DS, real_log=False, date=None):
        """跑一次 generate。`real_log=True`：不 patch record_failure，失敗紀錄寫進 tmp 的 brief_failures.log
        （同一個測試裡多次呼叫共用，驗「今日已被擋」）；FAIL_LOG 一律指到 tmp，不讀部署目錄的檔。"""
        upsert = mock.AsyncMock()
        record = mock.MagicMock(wraps=gb.record_failure) if real_log else mock.MagicMock()
        svc = gb.brief_service
        args = argparse.Namespace(date=date, force=force, after_hour=0, dry_run=False, model=model,
                                  max_lookback_days=7)
        err = io.StringIO()
        with contextlib.ExitStack() as st:
            p = st.enter_context
            p(mock.patch.object(gb, "FAIL_LOG", self.tmp / "brief_failures.log"))
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

    def test_blocked_today_is_not_called_again(self):
        """審查低4：同一天同一個 model 已被審查擋過 → 之後的輪次不再呼叫、rc 仍非 0、訊息說明。"""
        self.install(status(400, "Content Exists Risk"))
        rc, _, _, _ = self._generate(force=False, real_log=True)
        self.assertEqual((rc, len(self.requests)), (1, 1))
        line = (self.tmp / "brief_failures.log").read_text(encoding="utf-8").splitlines()[0].split("\t")
        self.assertEqual(len(line), 4, line)
        self.assertEqual(line[3], DS)
        rc, upsert, _, err = self._generate(force=False, real_log=True)
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.requests), 1, "今日已被擋：不再送出請求")
        self.assertIn("今日已被", err)
        upsert.assert_not_awaited()

    def test_blocked_today_scope(self):
        """只擋「同一天、同一個 model、內容審查」：換 model、--force、別天、別種失敗都照打。"""
        self.install(status(400, "Content Exists Risk"))
        self._generate(force=False, real_log=True)
        self.assertEqual(len(self.requests), 1)
        self._generate(force=True, real_log=True)
        self.assertEqual(len(self.requests), 2, "--force 照打")
        self._generate(force=False, real_log=True, model="deepseek-v4-pro")
        self.assertEqual(len(self.requests), 3, "換 model 照打")
        self._generate(force=False, real_log=True, date="2020-01-01")
        self.assertEqual(len(self.requests), 4, "別天照打")
        (self.tmp / "brief_failures.log").unlink()
        self.install(status(503, "busy"))
        self._generate(force=False, real_log=True)
        sent = len(self.requests)
        self._generate(force=False, real_log=True)
        self.assertEqual(len(self.requests), 2 * sent, "過載不是審查，下一輪照打")

    def test_truncated_is_not_called_again_same_day(self):
        """max_tokens 截斷（finish_reason=length）同審查：該次跳過、rc=1、同一天同一個 model 不再重打。"""
        self.install(lambda req: httpx.Response(200, content=sse(chunk(content="## 今日重點\n- 半"),
                                                                  chunk(content="", finish="length"))))
        rc, upsert, _, err = self._generate(force=False, real_log=True)
        self.assertEqual((rc, len(self.requests)), (1, 1))
        self.assertIn("MAX_TOKENS=", err)
        self.assertIn("今天不再重試", err)
        upsert.assert_not_awaited()
        rc, upsert, _, err = self._generate(force=False, real_log=True)
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.requests), 1, "今日已截斷過：不再送出請求")
        self.assertIn("今日已被", err)
        self._generate(force=True, real_log=True)
        self.assertEqual(len(self.requests), 2, "--force 照打")

    def test_blocked_today_kinds(self):
        """算「今日已擋」的只有審查與 max_tokens 截斷；期限型截斷（timeout_streamed）與逾時可能只是暫時變慢。"""
        log = self.tmp / "brief_failures.log"
        target = gb.date_cls.today()
        cases = {
            lh.CONTENT_FILTER: True, lh.TRUNCATED: True,
            lh.TIMEOUT_STREAMED: False, lh.TIMEOUT: False, lh.OVERLOADED: False, lh.EMPTY: False,
        }
        for kind, expected in cases.items():
            with self.subTest(kind=kind):
                log.write_text(f"2026-09-24T00:00:00+00:00\t{target}\t{lh.error_string(kind, 'x')}\t{DS}\n",
                               encoding="utf-8")
                with mock.patch.object(gb, "FAIL_LOG", log):
                    self.assertIs(gb.blocked_today(target, DS), expected)

    def test_blocked_today_ignores_unreadable_and_old_format(self):
        log = self.tmp / "brief_failures.log"
        target = gb.date_cls.today()
        log.write_text(f"2026-09-24T00:00:00+00:00\t{target}\tAPI[content_filter] 觸發供應商內容審查\n",
                       encoding="utf-8")
        with mock.patch.object(gb, "FAIL_LOG", log):
            self.assertFalse(gb.blocked_today(target, DS), "三欄舊格式沒有 model，不算")
        with mock.patch.object(gb, "FAIL_LOG", self.tmp / "missing" / "x.log"):
            self.assertFalse(gb.blocked_today(target, DS))


if __name__ == "__main__":
    unittest.main()
