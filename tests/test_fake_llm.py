"""deploy/demo/fake_llm.py：示範環境與 E2E 的假 LLM 回的每種內容都要過該任務**真實的**解析器。

假 LLM 的回應格式要是錯的，E2E 只會看到「摘要 0 份」這種沒有歸屬的失敗；這裡逐任務以真實的 prompt 組裝函式
餵它、再交給真實的解析器，格式漂移在單元測試就紅。最後一組以 loopback 起一個真的伺服器，經
`app/services/llm_http.py` 的 `openai_compatible` 路徑實打一次（串流、非串流、JSON 模式），驗 adapter 的
URL、認證 header 與 `max_tokens` 欄位。
"""

from __future__ import annotations

import asyncio
import json
import random
import sys
import threading
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.profile import load_profile  # noqa: E402
from app.services import judge_schema  # noqa: E402
from app.services.brief import parse_brief  # noqa: E402
from app.services.citation_filter import INVALID_CITATION, CitationStreamFilter  # noqa: E402
from app.services.faithfulness import DECOMPOSE_SYS, GROUND_ITEM_FMT, GROUND_PAYLOAD_FMT, GROUND_SYS  # noqa: E402
from app.services.followups import _parse_array  # noqa: E402
from app.services.query_planner import parse_plan_json  # noqa: E402
from app.services.reading.anchor import locate_quote  # noqa: E402
from app.services.scope_router import CORPUS_QA, parse_condense_route, parse_route  # noqa: E402
from app.services.signal_extract import build_signal_prompt, parse_signal  # noqa: E402
from app.services.tagging import parse_tags  # noqa: E402
from deploy.demo import fake_llm  # noqa: E402
from scripts import demo_corpus  # noqa: E402

FAKE_KEY = "fixed-test-secret-fakellm0"


def _report(code: str = "2330", market: str = "TW") -> demo_corpus.Report:
    r = demo_corpus.plan(date(2026, 11, 10))[0]
    if code != r.code:
        r = next(x for x in demo_corpus.plan(date(2026, 11, 10)) if x.code == code and x.market == market)
    return r


def _body(r: demo_corpus.Report) -> str:
    """與 PDF 抽回的文字同形：段落以空行分隔、段內可能斷行（抽取層的行寬換行）。"""
    paras = demo_corpus.body_paragraphs(r, random.Random(1))
    head = (f"{r.title}\n\n市場別：{demo_corpus._market_label(r)}｜類型：個股報告\n"
            f"標的：{demo_corpus._code_tag(r)}")
    wrapped = [p if i != 0 else p.replace("目標價 ", "目標價\n") for i, p in enumerate(paras)]
    return head + "\n\n" + "\n\n".join(wrapped)


def _ask(model: str, user: str, system: str | None = None) -> str:
    messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
    out = fake_llm.respond(model, messages)
    assert out is not None
    return out


class ParserContractTests(unittest.TestCase):
    def test_every_task_knob_has_a_model(self):
        from app.services.llm_models import TASK_ENV

        # 網搜沒有後端（MODEL_DEFAULTS 刻意空）；其餘每個任務都要能指到假 LLM。
        missing = sorted(set(TASK_ENV) - set(fake_llm.MODELS) - {"ask_web"})
        self.assertEqual(missing, [])

    def test_tag(self):
        from scripts import _ingest_core

        r = _report()
        captured = {}

        def fake_run(prompt, model, **kw):
            captured["prompt"] = prompt
            return _ingest_core.CliResult(text=_ask("demo-tag", prompt), error=None)

        with mock.patch.object(_ingest_core, "run_claude", fake_run):
            tag, err = _ingest_core._tag_via_cli("a.pdf", _body(r), model="demo-tag")
        self.assertIsNone(err)
        self.assertEqual(tag.market, "TW")
        self.assertEqual(tag.stock_targets, ["2330"])
        self.assertTrue(tag.is_research)

    def test_tag_without_market_word_is_not_research(self):
        tag = parse_tags(_ask("demo-tag", "報告內文（前 10 字摘錄）：\n一段看不出市場的文字。\n\n請依上述規則"),
                         normalize_code=load_profile(REPO_ROOT / "profiles" / "demo").instruments.normalize_code)
        self.assertIsNone(tag.market)
        self.assertFalse(tag.is_research)

    def test_summary(self):
        from scripts.generate_summaries import build_prompt, parse_summary

        out = _ask("demo-summary", build_prompt("a.pdf", _body(_report()), 6000))
        summary = parse_summary(out, allow_plain_text=False)
        self.assertTrue(summary and summary.startswith(fake_llm.DEMO_MARK))

    def test_title(self):
        from scripts.generate_titles import build_prompt, parse_title

        out = _ask("demo-title", build_prompt("a.pdf", _body(_report()), 1500))
        self.assertIsNotNone(parse_title(out, "a.pdf"))

    def test_takeaways_anchor_in_canonical_text(self):
        from scripts.extract_takeaways import build_rows, build_takeaway_prompt, parse_takeaways

        canonical = _body(_report())
        out = _ask("demo-takeaway", build_takeaway_prompt("a.pdf", "2026-11-01", "aster", canonical))
        parsed = parse_takeaways(out)
        self.assertTrue(parsed.ok)
        rows = build_rows("rid", canonical, "sha", parsed)
        self.assertGreaterEqual(len(rows), 3)
        for row in rows:
            self.assertIsNotNone(locate_quote(canonical, row.quote), row.quote)

    def test_signal_carries_rating_target_and_verbatim_evidence(self):
        body = _body(_report())
        out = _ask("demo-signal", build_signal_prompt("a.pdf", "2026-11-01", "aster", ["2330"], body))
        parsed = parse_signal(out, ["2330"])
        self.assertTrue(parsed.ok)
        sig = parsed.signals["2330"]
        self.assertIn(sig["rating"]["raw"], demo_corpus.RATINGS)
        self.assertGreater(sig["target_price"]["value"], 0)
        self.assertIn(sig["target_price"]["evidence"], body)  # 段內換行原樣保留
        self.assertEqual(sig["thesis"]["outlook"]["stance"], "positive")

    def test_brief_route_condense_planner_followups(self):
        self.assertIsNotNone(parse_brief(_ask("demo-brief", "素材")))
        self.assertEqual(parse_route(_ask("demo-intent", "台積電的評等？")), CORPUS_QA)
        query, scope = parse_condense_route(_ask("demo-condense", "先前對話：\nQ\n\n追問：它的目標價？"))
        self.assertEqual((query, scope), ("它的目標價？", CORPUS_QA))
        self.assertEqual(parse_plan_json(_ask("demo-planner", "問題：x")), {"subqueries": []})
        self.assertEqual(len(_parse_array(_ask("demo-followup", "問題：x\n\n回答：y"))), 3)

    def test_answer_citations_are_filtered_in_the_stream(self):
        prompt = "[1] 報告：a.pdf（市場 TW）\n片段一\n\n[2] 報告：b.pdf\n片段二\n\n問題：台積電？"
        text = _ask("demo-answer", prompt)
        self.assertIn(f"[{fake_llm.INVALID_CITATION_NUMBER}]", text)
        pieces = fake_llm.stream_pieces(text)
        self.assertEqual("".join(pieces), text)
        self.assertTrue(any(p.endswith("[") for p in pieces))  # 引用確實被切在兩段之間
        filt = CitationStreamFilter([1, 2])
        shown = "".join(filt.feed(p) for p in pieces) + filt.flush()
        self.assertNotIn(f"[{fake_llm.INVALID_CITATION_NUMBER}]", shown)
        self.assertIn(INVALID_CITATION, shown)
        self.assertIn("[1]", shown)
        self.assertIn("[2]", shown)

    def test_answer_without_sources(self):
        self.assertNotRegex(_ask("demo-answer", "問題：x"), r"\[\d+\]")

    def test_faithfulness_judge(self):
        answer = "台積電目標價為 715 元。展望正向。毛利率估 26.1%。"
        statements = judge_schema.parse_statements(json.loads(_ask("demo-judge", answer, DECOMPOSE_SYS)))
        self.assertEqual(len(statements), 2)  # 只拆含數字的句子
        payload = GROUND_PAYLOAD_FMT.format(
            contexts="片段", claims="\n".join(GROUND_ITEM_FMT.format(i=i, s=s) for i, s in enumerate(statements)))
        verdicts, missing = judge_schema.parse_verdicts(
            json.loads(_ask("demo-judge", payload, GROUND_SYS)), len(statements), "supported")
        self.assertEqual(missing, [])
        self.assertTrue(all(verdicts.values()))

    def test_unknown_model(self):
        self.assertIsNone(fake_llm.respond("gpt-6-luna", [{"role": "user", "content": "x"}]))

    def test_deterministic(self):
        prompt = "研報內文：\n" + _body(_report()) + "\n\n請依上述 schema"
        self.assertEqual(_ask("demo-takeaway", prompt), _ask("demo-takeaway", prompt))


class OpenAICompatibleRoundTripTests(unittest.TestCase):
    """經真的 HTTP（loopback）走 `llm_http` 的 openai_compatible 路徑。"""

    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), fake_llm.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.env = {
            "LLM_PROVIDER": "openai_compatible",
            "OPENAI_COMPATIBLE_BASE_URL": f"http://127.0.0.1:{cls.server.server_address[1]}/v1",
            "OPENAI_COMPATIBLE_API_KEY": FAKE_KEY,
            "OPENAI_COMPATIBLE_MODELS": fake_llm.declared_models(),
            "FAKE_LLM_API_KEY": FAKE_KEY,
        }

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_batch_streaming_and_json_mode(self):
        from app.services import llm_http

        with mock.patch.dict("os.environ", self.env):
            res = llm_http.complete_chat("demo-intent", "台積電？", max_tokens=16, timeout=10, task="ask_intent")
            self.assertEqual((res.text, res.kind), ("CORPUS_QA", None))
            js = asyncio.run(llm_http.complete_json(
                "demo-judge", "營收 10 億。", system=DECOMPOSE_SYS, max_tokens=256, timeout=10, task="faithfulness"))
            self.assertIsNone(js.kind, js.error)
            self.assertEqual(js.data, {"statements": ["營收 10 億。"]})

            async def stream():
                parts = []
                async for item in llm_http.astream_chat(
                    "demo-answer", "[1] 報告：a.pdf\n片段", max_tokens=512, first_token_timeout=10, task="ask_answer",
                ):
                    if isinstance(item, str):
                        parts.append(item)
                    else:
                        outcome = item
                return "".join(parts), outcome

            text, outcome = asyncio.run(stream())
            self.assertIsNone(outcome.kind)
            self.assertIn("[1]", text)
            self.assertGreater(outcome.usage["completion_tokens"], 0)  # stream_options.include_usage 有回

    def test_wrong_key_is_an_auth_failure(self):
        from app.services import llm_http

        with mock.patch.dict("os.environ", {**self.env, "OPENAI_COMPATIBLE_API_KEY": "fixed-test-secret-wrong"}):
            res = llm_http.complete_chat("demo-intent", "x", max_tokens=16, timeout=10, task="ask_intent", retries=0)
        self.assertIsNone(res.text)
        self.assertEqual(res.kind, "auth")


if __name__ == "__main__":
    unittest.main()
