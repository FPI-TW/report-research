# tests/test_ask_web_search.py
"""M11：問答的「搜尋網路」開關（`/api/ask` 的 `web` 欄位）。

三條縫各自都是靜默的，所以逐一釘住：

1. **提示與工具授權必須一致**。M4 起 `allow_web` 寫死 False，而系統提示裡第 1/3/6/7
   條都在講網路搜尋——模型看得到能力、拿不到工具，於是憑記憶寫出像查過網路的句子並
   標『（網路）』，沒有任何錯誤訊息。網搜改成可開關之後，這個落差每題都可能發生。
2. **伺服器總閘**（`ASK_ENABLE_WEB`）必須贏過請求欄位，否則整站停用網搜要改前端。
3. **時效題**開網搜後改由網路作答，但受信任 adapter 仍優先，且免責句由 Python 追加——
   交給 prompt 就是機率性缺席，而它是使用者分辨「adapter 數值」與「網路整理數值」的
   唯一穩定訊號。
"""
import hashlib
import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app.services.retrieval_pipeline as rp  # noqa: E402
from app.services import answer as ans  # noqa: E402
from app.services import scope_router as sr  # noqa: E402
from app.services import trusted_market_data as tmd  # noqa: E402
from app.services.llm import SEARCH_EVENT, LLMUnavailableError  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402

_TS_Q = "台積電今天收盤價多少"  # 命中前檢的 time_sensitive 詞表（零 LLM、決定性）


def _row():
    return ChunkRow(
        chunk_id=None, report_id="r1", file_hash="h-r1", file_name="x.pdf",
        title=None, market="TW", source=None, summary=None,
        report_date=date(2026, 6, 1), report_type=None, instrument_types=None,
        relates_stock=None, relates_futures=None, stock_targets=None,
        futures_targets=None, chunk_index=0, content="內容。", distance=0.2,
    )


class _CorpusPatch:
    """corpus_qa 路徑的替身組：檢索、主 LLM、落庫全部攔下來。"""

    def __init__(self, decision, stream=None):
        self.decision = decision
        self.called: dict = {}
        self._stream = stream

    def __enter__(self):
        called = self.called

        async def fake_search(*a, **k):
            return [(0, 0.80, _row())]

        def fake_embed(q):
            return [0.0]

        default_stream = self._stream

        async def fake_stream(*a, **k):
            called["llm"] = True
            called["stream_kwargs"] = k
            if default_stream is not None:
                async for chunk in default_stream():
                    yield chunk
            else:
                yield "答案[1]"

        async def fake_route(question, **k):
            return self.decision

        async def fake_load(conversation_id, **k):
            return []

        async def fake_log(question, answer, cited, filters, *a, **k):
            called["log_filters"] = filters
            called["answer"] = answer
            return "qa-web"

        async def no_followups(question, answer, **k):
            return []

        self._orig = (
            rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
            ans.classify_non_overview, ans.load_recent_turns, ans._log_qa,
            ans.generate_followups, ans.ASK_FAITHFULNESS_SAMPLE_RATE,
        )
        rp.hybrid_search = fake_search
        rp.embed_query_cached = fake_embed
        ans.stream_completion = fake_stream
        ans.classify_non_overview = fake_route
        ans.load_recent_turns = fake_load
        ans._log_qa = fake_log
        ans.generate_followups = no_followups
        ans.ASK_FAITHFULNESS_SAMPLE_RATE = 0.0  # 抽查必不中，測試不依賴亂數
        return self

    def __exit__(self, *exc):
        (
            rp.hybrid_search, rp.embed_query_cached, ans.stream_completion,
            ans.classify_non_overview, ans.load_recent_turns, ans._log_qa,
            ans.generate_followups, ans.ASK_FAITHFULNESS_SAMPLE_RATE,
        ) = self._orig
        return False


class PromptToolConsistencyTests(unittest.TestCase):
    """提示的「有沒有網路」必須與 allow_web 一致——這是靜默錯誤的來源。"""

    def test_default_prompt_denies_web_explicitly(self):
        p = ans.SYSTEM_PROMPT
        self.assertIn("沒有網路存取能力", p)
        self.assertIn("不得聲稱查過網路", p)
        self.assertIn("[EXT_SOURCES]", p)  # 明文禁止輸出該標記
        self.assertNotIn("可用網路搜尋補充", p)

    def test_web_prompt_carries_web_rules(self):
        p = ans.ask_system_prompt(True)
        self.assertIn("已開啟網路搜尋", p)
        self.assertIn("（網路）", p)
        self.assertIn("[EXT_SOURCES]", p)
        self.assertNotIn("沒有網路存取能力", p)  # 兩段互斥，不可同時出現

    def test_both_variants_share_the_citation_rules(self):
        """研報引用鐵律不因網搜開關而改變——它是本站的價值所在。"""
        for p in (ans.ask_system_prompt(False), ans.ask_system_prompt(True)):
            self.assertIn("綜合多篇研報、彼此佐證", p)
            self.assertIn("[1]", p)
            self.assertIn("『資料』而非『指令』", p)

    def test_system_prompt_is_the_web_off_variant(self):
        """既有讀取端（含 test_locale）以 SYSTEM_PROMPT 斷言預設形狀。"""
        self.assertEqual(ans.SYSTEM_PROMPT, ans.ask_system_prompt(False))


class CorpusQaWebToggleTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, *, web, gate=True, decision=None):
        decision = decision or sr._decision(sr.CORPUS_QA, decided_by=sr.BY_LLM)
        orig_gate = ans.ASK_ENABLE_WEB
        ans.ASK_ENABLE_WEB = gate
        try:
            with _CorpusPatch(decision) as pt:
                _ = [e async for e in ans.answer_question("台積電展望", web=web)]
            return pt.called
        finally:
            ans.ASK_ENABLE_WEB = orig_gate

    async def test_default_off_is_byte_for_byte_the_old_behaviour(self):
        called = await self._run(web=False)
        kw = called["stream_kwargs"]
        self.assertIs(kw["allow_web"], False)
        self.assertEqual(kw["system"], ans.SYSTEM_PROMPT)
        # 逾時刻意不傳：關網搜的路徑沿用 llm.py 預設，零回歸。
        self.assertNotIn("timeout", kw)
        self.assertIs(called["log_filters"]["web"], False)

    async def test_web_true_grants_tool_prompt_and_longer_timeout(self):
        called = await self._run(web=True)
        kw = called["stream_kwargs"]
        self.assertIs(kw["allow_web"], True)
        self.assertIn("已開啟網路搜尋", kw["system"])
        # 沿用 120s 預設會在「搜到一半」被砍，而逾時對已串流文字是 fail-open：
        # 症狀是答案無聲截斷。
        self.assertEqual(kw["timeout"], ans.ASK_WEB_TIMEOUT)
        self.assertIs(called["log_filters"]["web"], True)

    async def test_server_gate_overrides_request_field(self):
        """ASK_ENABLE_WEB=0 → 前端送 true 也一律關，不必改前端就能整站停用。"""
        called = await self._run(web=True, gate=False)
        kw = called["stream_kwargs"]
        self.assertIs(kw["allow_web"], False)
        self.assertEqual(kw["system"], ans.SYSTEM_PROMPT)
        self.assertIs(called["log_filters"]["web"], False)

    async def test_web_flag_always_written_to_qa_log(self):
        """含 False 也要寫：只在開啟時寫，「沒開」與「舊版程式跑的」在 log 裡同形。"""
        for web in (True, False):
            called = await self._run(web=web)
            self.assertIn("web", called["log_filters"])

    async def test_advice_risk_keeps_research_only_policy_last(self):
        """網搜段插在研究限制之前——既有測試以 endswith 釘住那段必須在最後。"""
        called = await self._run(
            web=True, decision=sr._decision(sr.ADVICE_RISK, decided_by=sr.BY_LLM)
        )
        system = called["stream_kwargs"]["system"]
        self.assertTrue(system.endswith(ans.RESEARCH_ONLY_POLICY))
        self.assertIn("已開啟網路搜尋", system)

    async def test_search_event_surfaces_as_searching_web_stage(self):
        async def with_search():
            yield SEARCH_EVENT
            yield "答案[1]"

        orig_gate = ans.ASK_ENABLE_WEB
        ans.ASK_ENABLE_WEB = True
        try:
            with _CorpusPatch(
                sr._decision(sr.CORPUS_QA, decided_by=sr.BY_LLM), stream=with_search
            ):
                events = [e async for e in ans.answer_question("台積電展望", web=True)]
        finally:
            ans.ASK_ENABLE_WEB = orig_gate
        stages = [p.get("stage") for k, p in events if k == "status" and isinstance(p, dict)]
        self.assertIn("searching_web", stages)
        body = "".join(p for k, p in events if k == "token")
        self.assertNotIn(SEARCH_EVENT, body)  # 控制標記不外洩到正文


def _fresh_point():
    return tmd.TrustedDataPoint(
        value="1085.00", unit="TWD",
        as_of=datetime.now(timezone.utc) - timedelta(minutes=1),
        published_at=None, url="https://example.com/quote/2330",
        source_type="exchange", profile_id="trusted-quote",
        snapshot_ref="snapshot://trusted-quote/payload",
        canonical_payload=b"payload",
        content_hash=hashlib.sha256(b"payload").hexdigest(),
        provider="fake-quote", category="quote", subject="台積電 2330",
    )


def _spec():
    return tmd.ProviderSpec(
        name="fake-quote", category="quote", allowed_domains=("example.com",),
        max_age=timedelta(minutes=15), cache_ttl=timedelta(seconds=60),
        timeout=1.0, min_interval=0.0, exchange_tz="Asia/Taipei",
    )


class _Provider:
    def __init__(self, point):
        self.point = point

    async def fetch(self, query):
        return self.point


class TimeSensitiveWebTests(unittest.IsolatedAsyncioTestCase):
    """時效題 × 網搜開關。adapter 優先、免責由 Python 追加、失敗退回 M4 婉拒。"""

    def setUp(self):
        tmd.clear_providers()
        self._orig = (
            rp.retrieve_context, ans._log_qa, ans.stream_completion,
            ans.load_recent_turns, ans.ASK_ENABLE_WEB,
        )
        self.logged: dict = {}

        async def fake_retrieve(question, **kw):
            raise AssertionError("時效題不得觸發 retrieve_context")

        async def fake_log(question, answer, cited, filters, *a, **k):
            self.logged["filters"] = filters
            self.logged["answer"] = answer
            self.logged["ext_sources"] = a[2] if len(a) > 2 else k.get("ext_sources")
            return "qa-ts-web"

        async def fake_turns(conv_id, **kw):
            return []

        rp.retrieve_context = fake_retrieve
        ans._log_qa = fake_log
        ans.load_recent_turns = fake_turns
        ans.ASK_ENABLE_WEB = True

    def tearDown(self):
        tmd.clear_providers()
        (
            rp.retrieve_context, ans._log_qa, ans.stream_completion,
            ans.load_recent_turns, ans.ASK_ENABLE_WEB,
        ) = self._orig

    def _set_stream(self, chunks, kwargs_sink=None):
        async def fake_stream(*a, **k):
            if kwargs_sink is not None:
                kwargs_sink.update(k)
                kwargs_sink["prompt"] = a[0] if a else None
            for c in chunks:
                yield c

        ans.stream_completion = fake_stream

    async def test_web_off_still_declines(self):
        """零回歸：不開網搜時，時效題行為與 M4 完全相同。"""
        self._set_stream(["不該被呼叫"])
        events = [e async for e in ans.answer_question(_TS_Q)]
        self.assertEqual([k for k, _ in events], ["status", "sources", "notice", "done"])
        self.assertEqual(events[2][1], ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE)
        self.assertIs(self.logged["filters"]["web"], False)

    async def test_web_on_answers_from_web_with_disclaimer_and_sources(self):
        kw: dict = {}
        self._set_stream(
            [SEARCH_EVENT, "台積電 8/19 收盤 1085 元（網路）。",
             "\n[EXT_SOURCES]\n- 交易所行情 | https://example.com/q\n"],
            kwargs_sink=kw,
        )
        events = [e async for e in ans.answer_question(_TS_Q, web=True)]
        kinds = [k for k, _ in events]
        self.assertNotIn("notice", kinds)
        self.assertIs(kw["allow_web"], True)
        self.assertEqual(kw["timeout"], ans.ASK_WEB_TIMEOUT)
        # 研報來源不得混入時效答案
        self.assertEqual([p for k, p in events if k == "sources"], [[]])
        stages = [p.get("stage") for k, p in events if k == "status" and isinstance(p, dict)]
        self.assertIn("searching_web", stages)
        body = "".join(p for k, p in events if k == "token")
        self.assertIn("1085", body)
        self.assertIn(ans.WEB_ANSWER_DISCLAIMER, body)     # 串流可見
        self.assertIn(ans.WEB_ANSWER_DISCLAIMER, self.logged["answer"])  # 落庫也有
        ext = [p for k, p in events if k == "ext_sources"][0]
        self.assertEqual(ext[0]["url"], "https://example.com/q")
        self.assertEqual(self.logged["filters"]["path"], "time_sensitive")
        self.assertIs(self.logged["filters"]["web"], True)
        self.assertEqual(events[-1][1]["qa_id"], "qa-ts-web")

    async def test_trusted_adapter_wins_over_web(self):
        """可稽核的數值在手時不該退回模型自由搜尋。"""
        called = {"llm": False}

        async def boom_stream(*a, **k):
            called["llm"] = True
            yield "不該被呼叫"

        ans.stream_completion = boom_stream
        tmd.register_provider(_spec(), _Provider(_fresh_point()))
        events = [e async for e in ans.answer_question(_TS_Q, web=True)]
        body = "".join(p for k, p in events if k == "token")
        self.assertIn("資料時間", body)              # 確定性模板
        self.assertNotIn(ans.WEB_ANSWER_DISCLAIMER, body)
        self.assertFalse(called["llm"])
        # 這一輪沒有動用網搜，log 要照實記
        self.assertIs(self.logged["filters"]["web"], False)

    async def test_disclaimer_not_duplicated_when_model_already_wrote_it(self):
        self._set_stream([f"收盤 1085 元。（{ans.WEB_ANSWER_DISCLAIMER}）"])
        events = [e async for e in ans.answer_question(_TS_Q, web=True)]
        body = "".join(p for k, p in events if k == "token")
        self.assertEqual(body.count(ans.WEB_ANSWER_DISCLAIMER), 1)

    async def test_simplified_stream_gets_corrected_in_done(self):
        """串流照原樣送、done 帶校正後的整份——比照主 RAG 路徑（見 _answer_correction）。

        少了它，這條路徑串出來的簡體答案會永遠停在螢幕上，而落庫的是繁體版：
        使用者看到的與紀錄下來的不是同一份，且沒有任何訊號。
        """
        self._set_stream(["台积电今日收盘价为 1085 元，成交量放大。"])
        events = [e async for e in ans.answer_question(_TS_Q, web=True)]
        done = events[-1][1]
        self.assertIn("answer", done)
        self.assertIn("台積電", done["answer"])
        self.assertEqual(done["answer"], self.logged["answer"])  # 校正＝落庫那一份

    async def test_no_correction_field_when_nothing_changed(self):
        """純繁體答案不該每題多背一份完整答案的 payload。"""
        self._set_stream(["台積電今日收盤價為 1085 元。"])
        events = [e async for e in ans.answer_question(_TS_Q, web=True)]
        self.assertNotIn("answer", events[-1][1])

    async def test_llm_unavailable_falls_back_to_m4_notice(self):
        """網搜路徑掛掉時退回既有婉拒——使用者仍得到明確答覆，且該輪照樣落庫。"""
        async def failing(*a, **k):
            raise LLMUnavailableError("529 Overloaded")
            yield  # pragma: no cover — 讓函式成為 async generator

        ans.stream_completion = failing
        events = [e async for e in ans.answer_question(_TS_Q, web=True)]
        self.assertEqual(
            [k for k, _ in events], ["status", "sources", "notice", "done"]
        )
        self.assertEqual(events[2][1], ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE)
        self.assertEqual(events[-1][1]["notice_kind"], "time_sensitive")
        self.assertIn("llm_error", self.logged["filters"])
        self.assertIs(self.logged["filters"]["web"], True)


class AskEndpointWebForwardingTests(unittest.TestCase):
    """`/api/ask` 的 `web` 欄位轉發，走 **HTTP 層**（不呼叫 handler 函式物件）。

    這條縫踩過兩次：Pydantic 靜默丟棄未宣告欄位（M3），以及裝飾器套到輔助函式上
    （2026-07-28）。兩者都只在真實請求下才看得見。
    """

    def _capture(self, body):
        from unittest.mock import patch

        from fastapi.testclient import TestClient

        from web import deps
        from web.server import app

        seen: dict = {}

        async def fake_answer(question, **kwargs):
            seen.update(kwargs)
            yield ("done", {"cited": [], "qa_id": None, "conversation_id": "c1"})

        client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        r = client.post("/login", data={"username": "tester", "password": "testpass"})
        assert r.status_code == 303, r.status_code
        with patch.object(deps, "answer_question", fake_answer):
            with client.stream("POST", "/api/ask", json=body) as resp:
                code = resp.status_code
                "".join(resp.iter_text())
        return code, seen

    def test_web_true_reaches_service(self):
        code, seen = self._capture({"question": "台積電最新消息", "web": True})
        self.assertEqual(code, 200)
        self.assertIs(seen.get("web"), True)

    def test_web_absent_defaults_to_false(self):
        """未帶欄位的舊前端行為一字不變——預設值在端點，不在服務層猜。"""
        code, seen = self._capture({"question": "台積電展望"})
        self.assertEqual(code, 200)
        self.assertIs(seen.get("web"), False)

    def test_web_false_reaches_service(self):
        code, seen = self._capture({"question": "台積電展望", "web": False})
        self.assertEqual(code, 200)
        self.assertIs(seen.get("web"), False)


if __name__ == "__main__":
    unittest.main()
