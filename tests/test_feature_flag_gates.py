"""功能旗標接上各功能（Admin v2 Flags lane）：每個被替換的常數讀取，在 DB 沒有覆寫時行為與 v1 相同、
覆寫關閉時確實降級。

旗標的 DB 讀取用假的 session factory（`fake_feature_flags.flag_rows`）：走真的 `feature_flags.policy` → 快取 →
`evaluate`，只把 `SELECT ... FROM research.feature_flag` 的結果換掉。預設（tests/conftest.py 的
`_stub_feature_flag_db`）是零列。

| 旗標 | 呼叫點 | 上限（也是既有替換點） |
|---|---|---|
| ask.web_search | `answer.answer_question` 的 `web_on`、時效婉拒的「可開網搜」提示 | `answer.ASK_ENABLE_WEB` |
| qa.agentic | `answer.answer_question` 建 `plan_task` | `get_settings().qa_agentic_enabled` |
| ask.rerank | `retrieval_pipeline.ask_rerank_top_m` → `retrieve_context` | `answer.ASK_RERANK_TOP_M` |
| qa.faithfulness | `answer.answer_question` done 之後的抽查 | `answer.ASK_FAITHFULNESS_ENABLED` |
| trusted_data | `trusted_market_data.fetch_trusted` | `trusted_market_data.TRUSTED_DATA_ENABLED` |
| uploads.intake | `POST /api/admin/uploads` | `Settings.upload_enabled` |
"""

from __future__ import annotations

import dataclasses
import os
import unittest
from unittest import mock

os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fake_feature_flags import flag_rows as _flag_rows  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from test_ask_web_search import _TS_Q, _CorpusPatch  # noqa: E402

from app import config  # noqa: E402
from app.services import answer as ans  # noqa: E402
from app.services import feature_flags as ff  # noqa: E402
from app.services import retrieval_pipeline as rp  # noqa: E402
from app.services import scope_router as sr  # noqa: E402
from app.services import trusted_market_data as tmd  # noqa: E402
from web.server import app  # noqa: E402

UID = "aaaaaaaa-0000-0000-0000-0000000000a1"


class _Saved:
    """暫時改模組屬性、離開時還原（既有測試的寫法，集中成一個）。"""

    def __init__(self, **attrs):
        self.attrs = attrs

    def __enter__(self):
        self.orig = {k: getattr(ans, k) for k in self.attrs}
        for k, v in self.attrs.items():
            setattr(ans, k, v)

    def __exit__(self, *exc):
        for k, v in self.orig.items():
            setattr(ans, k, v)


class WebSearchGateTests(unittest.IsolatedAsyncioTestCase):
    async def _allow_web(self, **kw):
        with _Saved(ASK_ENABLE_WEB=True), _CorpusPatch(sr._decision(sr.CORPUS_QA, decided_by=sr.BY_LLM)) as pt:
            _ = [e async for e in ans.answer_question("台積電展望", web=True, **kw)]
        return pt.called["stream_kwargs"]["allow_web"], pt.called["log_filters"]["web"]

    async def test_no_override_is_the_old_behaviour(self):
        self.assertEqual(await self._allow_web(), (True, True))

    async def test_override_off_closes_web_even_when_requested(self):
        with _flag_rows(("ask.web_search", False, None, None)):
            self.assertEqual(await self._allow_web(), (False, False))

    async def test_scoped_override_uses_the_asking_user(self):
        with _flag_rows(("ask.web_search", True, None, [UID])):
            self.assertEqual(await self._allow_web(user_id=UID), (True, True))
            self.assertEqual(await self._allow_web(user_id="aaaaaaaa-0000-0000-0000-0000000000ff"), (False, False))
            self.assertEqual(await self._allow_web(user_id=None), (False, False))  # 沒有身分：作用域不成立

    async def test_role_scope_looks_up_the_role_once(self):
        with _flag_rows(("ask.web_search", True, ["admin"], None), role="admin"):
            self.assertEqual(await self._allow_web(user_id=UID), (True, True))
        with _flag_rows(("ask.web_search", True, ["admin"], None), role="user"):
            self.assertEqual(await self._allow_web(user_id=UID), (False, False))

    async def test_web_false_never_consults_the_flag(self):
        calls = []

        async def spy(*a, **k):
            calls.append(a)
            return True

        with mock.patch.object(ff, "policy", spy), \
                _CorpusPatch(sr._decision(sr.CORPUS_QA, decided_by=sr.BY_LLM)):
            _ = [e async for e in ans.answer_question("台積電展望", web=False)]
        self.assertNotIn(("ask.web_search",), [c[:1] for c in calls])

    async def test_time_sensitive_hint_follows_the_flag(self):
        async def fake_log(*a, **k):
            return "qa-ts"

        async def no_turns(*a, **k):
            return []

        async def never(*a, **k):
            raise AssertionError("時效題不得檢索")
            yield  # pragma: no cover

        tmd.clear_providers()
        with _Saved(ASK_ENABLE_WEB=True, _log_qa=fake_log, load_recent_turns=no_turns, stream_completion=never):
            events = [e async for e in ans.answer_question(_TS_Q)]
            self.assertEqual(events[2][1], ans.TIME_SENSITIVE_UNAVAILABLE_WITH_HINT)
            with _flag_rows(("ask.web_search", False, None, None)):
                events = [e async for e in ans.answer_question(_TS_Q)]
            self.assertEqual(events[2][1], ans.TIME_SENSITIVE_UNAVAILABLE_MESSAGE)


class AgenticGateTests(unittest.IsolatedAsyncioTestCase):
    async def _planned(self) -> bool:
        from app.services import query_planner

        planned = []
        real = query_planner.plan_queries

        async def spy(*a, **k):
            planned.append(a)
            return await real(*a, **k)

        settings = dataclasses.replace(config.get_settings(), qa_agentic_enabled=True)
        with mock.patch.object(query_planner, "plan_queries", spy), \
                mock.patch.object(config, "_SETTINGS", settings), \
                _CorpusPatch(sr._decision(sr.CORPUS_QA, decided_by=sr.BY_LLM)):
            _ = [e async for e in ans.answer_question("台積電展望")]
        return bool(planned)

    async def test_no_override_plans_as_before(self):
        self.assertTrue(await self._planned())

    async def test_override_off_skips_the_planner(self):
        with _flag_rows(("qa.agentic", False, None, None)):
            self.assertFalse(await self._planned())


class RerankGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_helper_is_ceiling_and_policy(self):
        self.assertEqual(await rp.ask_rerank_top_m(16), 16)
        self.assertEqual(await rp.ask_rerank_top_m(0), 0)  # 上限關（ASK_RERANK_ENABLED=0 → 常數 0）
        with _flag_rows(("ask.rerank", False, None, None)):
            self.assertEqual(await rp.ask_rerank_top_m(16), 0)
            self.assertEqual(await rp.ask_rerank_top_m(0), 0)

    async def _forwarded(self) -> int:
        seen = {}

        async def fake_retrieve(question, **kw):
            seen.update(kw)
            return [], ""

        with _Saved(ASK_RERANK_TOP_M=16), \
                mock.patch.object(rp, "retrieve_context", fake_retrieve), \
                _CorpusPatch(sr._decision(sr.CORPUS_QA, decided_by=sr.BY_LLM)):
            _ = [e async for e in ans.answer_question("台積電展望")]
        return seen["rerank_top_m"]

    async def test_answer_forwards_the_gated_value(self):
        self.assertEqual(await self._forwarded(), 16)
        with _flag_rows(("ask.rerank", False, None, None)):
            self.assertEqual(await self._forwarded(), 0)


class FaithfulnessGateTests(unittest.IsolatedAsyncioTestCase):
    async def _spawned(self) -> list:
        spawned = []

        def fake_spawn(coro, *, name):
            spawned.append(name)
            coro.close()

        async def numeric():
            yield "營收年增 25%[1]"

        with _CorpusPatch(sr._decision(sr.CORPUS_QA, decided_by=sr.BY_LLM), stream=numeric), \
                _Saved(ASK_FAITHFULNESS_ENABLED=True, _spawn_background=fake_spawn):
            ans.ASK_FAITHFULNESS_SAMPLE_RATE = 1.0  # _CorpusPatch 設成 0；離開時由它還原
            _ = [e async for e in ans.answer_question("台積電營收")]
        return spawned

    async def test_no_override_spot_checks_as_before(self):
        self.assertEqual(len(await self._spawned()), 1)

    async def test_override_off_skips_the_spot_check(self):
        with _flag_rows(("qa.faithfulness", False, None, None)):
            self.assertEqual(await self._spawned(), [])


class TrustedDataGateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from test_trusted_market_data import NOW, _point, _Provider, _spec

        tmd.clear_providers()
        tmd.register_provider(_spec(), _Provider(point=_point()))
        self.now = NOW

    def tearDown(self):
        tmd.clear_providers()

    async def test_no_override_fetches_as_before(self):
        point = await tmd.fetch_trusted("quote", "台積電收盤價", now=self.now)
        self.assertEqual(point.value, "1085.00")

    async def test_override_off_is_the_same_safe_refusal(self):
        with _flag_rows(("trusted_data", False, None, None)):
            with self.assertRaises(tmd.TrustedDataUnavailable) as ctx:
                await tmd.fetch_trusted("quote", "台積電收盤價", now=self.now)
        self.assertEqual(str(ctx.exception), "trusted data paused")


class UploadIntakeGateTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")
        r = self.client.post("/login", data={"username": "tester", "password": "testpass"})
        self.assertEqual(r.status_code, 303)

    def _post(self, **settings):
        s = dataclasses.replace(config.get_settings(), **settings)
        with mock.patch.object(config, "_SETTINGS", s):
            return self.client.post("/api/admin/uploads?filename=a.pdf", content=b"x",
                                    headers={"Content-Type": "text/plain", "Origin": "http://127.0.0.1"})

    def test_ceiling_off_is_the_old_503(self):
        r = self._post(upload_enabled=False)
        self.assertEqual((r.status_code, r.json()["code"]), (503, "uploads_disabled"))
        self.assertEqual(r.json()["detail"], "上傳功能尚未開放")

    def test_no_override_passes_the_gate(self):
        # 過了旗標閘門就輪到 Content-Type 檢查（text/plain → 415），證明沒有被旗標擋下
        self.assertEqual(self._post(upload_enabled=True).status_code, 415)

    def test_override_off_pauses_intake(self):
        with _flag_rows(("uploads.intake", False, None, None)):
            r = self._post(upload_enabled=True)
        self.assertEqual((r.status_code, r.json()["code"]), (503, "uploads_disabled"))
        self.assertIn("暫停", r.json()["detail"])


if __name__ == "__main__":
    unittest.main()
