"""`GET /metrics`（web/metrics.py、web/routers/metrics.py）走 HTTP 層。

驗四件事：
1. **存取控制**：預設只收本機直連；代理 header 一律 404；`METRICS_ALLOWED_CIDRS` 放行指定網段；`METRICS_ENABLED=0` 關。
2. **標籤無高基數**：`route` 只會是路由模板或 `<unmatched>`；50 個不同的研報路徑只產生一個 route 值；
   LLM 的 task／provider／kind 認不得就是 `other`。
3. **無敏感資料**：查詢字串、路徑參數、帳號、session cookie、問題原文都不出現在輸出裡。
4. **訊號正確**：問答結局、LLM 呼叫（經 `llm_http` 的真 observer 收尾點）、批次新鮮度與上傳佇列（假 session）、
   斷路器標記、建置資訊；DB 查不到時 source_up=0 而 `/metrics` 照樣 200。

不連 DB（假 session factory）、不打 LLM（直接走 `llm_http._log_call` 這個收尾點，不送請求）。
"""

from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")

from fake_accounts import session_cookies  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from prometheus_client.parser import text_string_to_metric_families  # noqa: E402

import web.server as server  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.services import llm_http, monitoring_signals  # noqa: E402
from app.services.llm import LLMUnavailableError  # noqa: E402
from web import deps, metrics  # noqa: E402

LOCAL = ("127.0.0.1", 41000)


def _client(peer=LOCAL, **kw) -> TestClient:
    return TestClient(server.app, follow_redirects=False, base_url="http://127.0.0.1", client=peer, **kw)


def _scrape() -> str:
    r = _client().get("/metrics")
    assert r.status_code == 200, r.status_code
    return r.text


def _samples(text: str, name: str) -> list:
    out = []
    for fam in text_string_to_metric_families(text):
        for s in fam.samples:
            if s.name == name:
                out.append(s)
    return out


def _value(text: str, name: str, **labels) -> float:
    total = 0.0
    for s in _samples(text, name):
        if all(s.labels.get(k) == v for k, v in labels.items()):
            total += s.value
    return total


def _settings(**changes):
    return mock.patch.object(metrics, "get_settings", lambda: dataclasses.replace(get_settings(), **changes))


class AccessControlTests(unittest.TestCase):
    def test_loopback_without_login_is_served(self):
        r = _client().get("/metrics")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.headers["content-type"].startswith("text/plain"))
        self.assertIn("report_mark_http_requests_total", r.text)

    def test_other_peers_get_404_by_default(self):
        for peer in (("10.0.0.5", 1), ("203.0.113.9", 1), ("testclient", 1)):
            with self.subTest(peer=peer):
                self.assertEqual(_client(peer).get("/metrics").status_code, 404)

    def test_any_proxy_header_is_refused_even_from_loopback(self):
        for header in ("X-Forwarded-For", "X-Real-IP", "Forwarded", "CF-Connecting-IP", "X-Edge-Secret",
                       "X-Forwarded-Proto", "X-Forwarded-Host"):
            with self.subTest(header=header):
                self.assertEqual(_client().get("/metrics", headers={header: "198.51.100.7"}).status_code, 404)

    def test_configured_cidr_is_allowed(self):
        with _settings(metrics_allowed_cidrs=("172.18.0.0/16",)):
            self.assertEqual(_client(("172.18.0.9", 5)).get("/metrics").status_code, 200)
            self.assertEqual(_client(("172.19.0.9", 5)).get("/metrics").status_code, 404)
            self.assertEqual(_client().get("/metrics").status_code, 404, "設了網段就只收那個網段")

    def test_empty_allow_list_refuses_everyone(self):
        with _settings(metrics_allowed_cidrs=()):
            self.assertEqual(_client().get("/metrics").status_code, 404)

    def test_disabled(self):
        with _settings(metrics_enabled=False):
            self.assertEqual(_client().get("/metrics").status_code, 404)

    def test_invalid_cidrs_fail_closed(self):
        from app import config

        with mock.patch.dict(os.environ, {"METRICS_ALLOWED_CIDRS": "not-a-cidr, also bad"}):
            self.assertEqual(config._metrics_cidrs(), ())
        with mock.patch.dict(os.environ, {"METRICS_ALLOWED_CIDRS": ""}):
            self.assertEqual(config._metrics_cidrs(), config.DEFAULT_METRICS_CIDRS)
        with mock.patch.dict(os.environ, {"METRICS_ALLOWED_CIDRS": "10.1.2.3/8, junk"}):
            self.assertEqual(config._metrics_cidrs(), ("10.0.0.0/8",))


class LabelHygieneTests(unittest.TestCase):
    def test_route_label_is_template_and_nothing_sensitive_leaks(self):
        query_text = "fixed-sensitive-query-台積電"
        file_hash = "ab" * 32
        cookies = session_cookies()
        # DB 不可達：handler 500，照樣要以模板計。
        _client(cookies=cookies, raise_server_exceptions=False).get(f"/api/reading/{file_hash}/text",
                                                                     params={"q": query_text})
        _client().get(f"/api/reading/{file_hash}/similar", params={"q": query_text})  # 未登入：401，不經路由
        _client().get("/no/such/path-zz9", params={"token": query_text})
        text = _scrape()

        routes = {s.labels["route"] for s in _samples(text, "report_mark_http_requests_total")}
        self.assertIn("/api/reading/{file_hash}/text", routes)
        self.assertIn("/api/reading/{file_hash}/similar", routes, "認證擋下的請求也要以模板計（不是原始路徑）")
        self.assertIn(metrics.UNMATCHED, routes)
        for route in routes:
            with self.subTest(route=route):
                self.assertTrue(route == metrics.UNMATCHED or route.startswith("/"), route)
                self.assertNotIn("?", route)
        unauth = _value(text, "report_mark_http_requests_total", route="/api/reading/{file_hash}/similar",
                        status="401")
        self.assertGreaterEqual(unauth, 1)
        for secret in (query_text, file_hash, "path-zz9", "tester", *cookies.values(), "127.0.0.1"):
            with self.subTest(secret=secret[:20]):
                self.assertNotIn(secret, text)

    def test_distinct_paths_do_not_create_distinct_series(self):
        before = _scrape()
        before_routes = {s.labels["route"] for s in _samples(before, "report_mark_http_requests_total")}
        client = _client(raise_server_exceptions=False)
        for i in range(50):
            client.get(f"/api/reading/{i:064x}")
            client.get(f"/random/{i}/x")
        after = _scrape()
        new_routes = {s.labels["route"] for s in _samples(after, "report_mark_http_requests_total")} - before_routes
        self.assertLessEqual(new_routes, {"/api/reading/{file_hash}", metrics.UNMATCHED, "/metrics"})
        self.assertGreaterEqual(
            _value(after, "report_mark_http_requests_total", route="/api/reading/{file_hash}")
            - _value(before, "report_mark_http_requests_total", route="/api/reading/{file_hash}"), 50)

    def test_method_label_is_bounded(self):
        self.assertEqual(metrics.method_label("BREW"), "OTHER")
        self.assertEqual(metrics.method_label("GET"), "GET")

    def test_llm_labels_are_bounded(self):
        self.assertEqual(metrics.task_label("ask_answer"), "ask_answer")
        self.assertEqual(metrics.task_label("evil-task-1"), metrics.OTHER)
        self.assertEqual(metrics.provider_label("openai"), "openai")
        self.assertEqual(metrics.provider_label("x" * 50), metrics.OTHER)
        self.assertEqual(metrics.kind_label("timeout"), "timeout")
        self.assertEqual(metrics.kind_label("weird"), metrics.OTHER)


def _authed_ask_client():
    return _client(cookies=session_cookies())


class QaOutcomeTests(unittest.TestCase):
    def _ask(self, gen):
        orig = deps.answer_question
        deps.answer_question = gen
        try:
            r = _authed_ask_client().post("/api/ask", json={"question": "fixed-question-text 半導體展望"})
        finally:
            deps.answer_question = orig
        self.assertEqual(r.status_code, 200)

    def test_outcomes(self):
        async def ok(question, **kw):
            yield ("sources", [])
            yield ("done", {"report_ids": []})

        async def llm_fail(question, **kw):
            yield ("sources", [])
            raise LLMUnavailableError("x", kind="quota")

        async def boom(question, **kw):
            yield ("sources", [])
            raise RuntimeError("fixed-internal-detail")

        before = _scrape()
        self._ask(ok)
        self._ask(llm_fail)
        self._ask(boom)
        after = _scrape()

        def delta(name, **labels):
            return _value(after, name, **labels) - _value(before, name, **labels)

        self.assertEqual(delta("report_mark_qa_requests_total", outcome="ok"), 1)
        self.assertEqual(delta("report_mark_qa_requests_total", outcome="llm_error"), 1)
        self.assertEqual(delta("report_mark_qa_requests_total", outcome="error"), 1)
        self.assertEqual(delta("report_mark_qa_llm_errors_total", kind="quota"), 1)
        self.assertNotIn("fixed-question-text", after)
        self.assertNotIn("fixed-internal-detail", after)
        self.assertEqual({s.labels["outcome"] for s in _samples(after, "report_mark_qa_requests_total")}
                         - set(metrics.QA_OUTCOMES), set())


class LlmObserverTests(unittest.TestCase):
    def setUp(self):
        llm_http.add_observer(metrics.observe_llm_call)
        self.addCleanup(llm_http.remove_observer, metrics.observe_llm_call)

    def test_calls_failures_duration_and_tokens(self):
        before = _scrape()
        usage = {"prompt_cache_hit_tokens": 100, "prompt_cache_miss_tokens": 20, "completion_tokens": 30,
                 "completion_tokens_details": {"reasoning_tokens": 5}}
        llm_http._log_call("ask_answer", "gpt-6-luna", llm_http.ChatOutcome(kind=None, usage=usage, total_ms=1500))
        llm_http._log_call("ask_answer", "gpt-6-luna", llm_http.ChatOutcome(kind=llm_http.TIMEOUT, total_ms=90000))
        llm_http._log_call("fixed-unknown-task", "gpt-6-luna", llm_http.ChatOutcome(kind="weird-kind", total_ms=10))
        after = _scrape()

        def delta(name, **labels):
            return _value(after, name, **labels) - _value(before, name, **labels)

        prov = llm_http._provider()
        self.assertEqual(delta("report_mark_llm_calls_total", task="ask_answer", provider=prov, result="ok"), 1)
        self.assertEqual(delta("report_mark_llm_calls_total", task="ask_answer", provider=prov, result="error"), 1)
        self.assertEqual(delta("report_mark_llm_call_failures_total", task="ask_answer", kind="timeout"), 1)
        self.assertEqual(delta("report_mark_llm_call_failures_total", task="other", kind="other"), 1)
        self.assertEqual(delta("report_mark_llm_tokens_total", task="ask_answer", type="prompt_cached"), 100)
        self.assertEqual(delta("report_mark_llm_tokens_total", task="ask_answer", type="prompt_uncached"), 20)
        self.assertEqual(delta("report_mark_llm_tokens_total", task="ask_answer", type="completion"), 30)
        self.assertEqual(delta("report_mark_llm_tokens_total", task="ask_answer", type="reasoning"), 5)
        self.assertEqual(delta("report_mark_llm_call_duration_seconds_count", task="ask_answer"), 2)
        self.assertNotIn("fixed-unknown-task", after)
        self.assertNotIn("weird-kind", after)

    def test_server_registers_the_observer(self):
        src = (REPO_ROOT / "web" / "server.py").read_text(encoding="utf-8")
        self.assertIn("llm_http.add_observer(metrics.observe_llm_call)", src)
        self.assertIn("llm_http.remove_observer(metrics.observe_llm_call)", src)
        self.assertIn('"/metrics"', src, "/metrics 必須在認證白名單裡")


class _Result:
    def __init__(self, first=None, rows=()):
        self._first, self._rows = first, list(rows)

    def first(self):
        return self._first

    def all(self):
        return self._rows


class _Session:
    def __init__(self, latest, upload_rows):
        self.latest, self.upload_rows = latest, upload_rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        sql = str(stmt)
        if "report_upload" in sql:
            return _Result(rows=self.upload_rows)
        return _Result(first=self.latest)


class DbSignalTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        hb = Path(self._tmp.name) / "hb"
        hb.write_text(f"epoch={int(time.time()) - 3600}\n", encoding="utf-8")
        p = mock.patch("app.services.batch_freshness.HEARTBEAT_PATH", hb)
        p.start()
        self.addCleanup(p.stop)

    def _factory(self, session=None, fail=None):
        def factory():
            if fail is not None:
                raise fail
            return session
        return factory

    def test_freshness_and_upload_queue(self):
        now = datetime.now(timezone.utc)
        latest = (now - timedelta(hours=2), now - timedelta(days=5), now - timedelta(hours=3), None)
        rows = [("clean", 2, 7200.0), ("quarantined", 1, 600.0), ("published", 40, 900000.0)]
        with mock.patch.object(metrics, "_session_factory", lambda: self._factory(_Session(latest, rows))):
            text = _scrape()
        self.assertEqual(_value(text, "report_mark_signal_source_up", source="freshness"), 1)
        self.assertEqual(_value(text, "report_mark_signal_source_up", source="uploads"), 1)
        self.assertEqual(_value(text, "report_mark_batch_freshness_stale", asset="summary"), 1, "5 天 > 門檻 3 天")
        self.assertEqual(_value(text, "report_mark_batch_freshness_stale", asset="takeaway"), 0)
        self.assertEqual(_value(text, "report_mark_batch_freshness_stale", asset="pipeline"), 0)
        self.assertAlmostEqual(_value(text, "report_mark_batch_freshness_age_seconds", asset="summary"), 5 * 86400,
                               delta=120)
        self.assertEqual(_value(text, "report_mark_batch_freshness_threshold_seconds", asset="summary"), 3 * 86400)
        self.assertEqual(_value(text, "report_mark_batch_freshness_threshold_seconds", asset="pipeline"), 9 * 3600)
        self.assertEqual(_value(text, "report_mark_batch_freshness_threshold_seconds", asset="signal"), 0)
        self.assertEqual(_value(text, "report_mark_upload_queue", state="clean"), 2)
        self.assertEqual(_value(text, "report_mark_upload_queue", state="processing"), 0, "沒有的狀態補 0")
        self.assertEqual(_value(text, "report_mark_upload_queue", state="published"), 40)
        self.assertEqual(_value(text, "report_mark_upload_oldest_in_flight_age_seconds"), 7200,
                         "只算處理中的狀態（published 不算）")

    def test_db_unavailable_still_serves(self):
        with mock.patch.object(metrics, "_session_factory", lambda: self._factory(fail=ConnectionError("x"))):
            text = _scrape()
        self.assertEqual(_value(text, "report_mark_signal_source_up", source="freshness"), 0)
        self.assertEqual(_value(text, "report_mark_signal_source_up", source="uploads"), 0)
        self.assertEqual(_samples(text, "report_mark_upload_queue"), [], "查不到時不留舊值")
        self.assertIn("report_mark_http_requests_total", text)

    def test_results_are_cached_between_scrapes(self):
        calls = []

        def factory():
            calls.append(1)
            return _Session((None, None, None, None), [])

        with mock.patch.object(metrics, "_session_factory", lambda: factory):
            _scrape()
            n = len(calls)
            _scrape()
        self.assertGreater(n, 0)
        self.assertEqual(len(calls), n, f"{metrics.DB_SIGNAL_TTL} 秒內不重查")


class SnapshotTests(unittest.TestCase):
    def test_breaker_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / ".llm_breaker"
            with mock.patch.dict(os.environ, {"LLM_BREAKER_FILE": str(marker)}):
                self.assertEqual(_value(_scrape(), "report_mark_llm_breaker_active"), 0)
                marker.write_text("ts=x\nreason=y\n", encoding="utf-8")
                self.assertEqual(_value(_scrape(), "report_mark_llm_breaker_active"), 1)
                old = time.time() - monitoring_signals.BREAKER_TTL_S - 5
                os.utime(marker, (old, old))
                self.assertEqual(_value(_scrape(), "report_mark_llm_breaker_active"), 0)

    def test_breaker_constants_match_batch_side(self):
        from scripts import _llm_env

        self.assertEqual(monitoring_signals.BREAKER_TTL_S, _llm_env.BREAKER_TTL_S)
        with mock.patch.dict(os.environ, {"LLM_BREAKER_FILE": ""}):
            self.assertEqual(monitoring_signals.breaker_path(), _llm_env.breaker_path())

    def test_pool_gates_and_build_info(self):
        text = _scrape()
        self.assertEqual(_value(text, "report_mark_db_pool_size"), get_settings().db_pool_size)
        self.assertEqual(_value(text, "report_mark_gate_capacity", gate="ask"), 3)
        build = _samples(text, "report_mark_build_info")
        self.assertEqual(len(build), 1)
        self.assertEqual(set(build[0].labels), {"commit", "version", "tree_clean", "source"})



class ContractTests(unittest.TestCase):
    def test_every_metric_is_documented(self):
        """文件「監控對接」的指標表與實際輸出對帳：新增指標要寫進文件，文件也不留不存在的指標。"""
        import re

        doc = (REPO_ROOT / "docs" / "CONTAINER_DEPLOYMENT.md").read_text(encoding="utf-8")
        section = doc.split("## 監控對接", 1)[1].split("\n## ", 1)[0]
        documented = set()
        for cell in re.findall(r"^\| (`report_mark_[^|]+) \|", section, re.M):
            documented.update(re.findall(r"`(report_mark_\w+)`", cell))
        exposed = set()
        for fam in text_string_to_metric_families(_scrape()):
            if fam.name.startswith("report_mark_"):
                exposed.add(fam.name + ("_total" if fam.type == "counter" else ""))
        self.assertEqual(exposed - documented, set(), "指標沒寫進 docs/CONTAINER_DEPLOYMENT.md「監控對接」")
        self.assertEqual(documented - exposed, set(), "文件列了不存在的指標")

    def test_bundled_reverse_proxies_always_forward_x_forwarded_for(self):
        """`/metrics` 靠「帶代理 header 就拒絕」擋經反向代理進來的請求；repo 裡的 nginx 每個 proxy_pass 都必須帶它。"""
        import re

        for name in ("nginx.conf", "nginx-origin.conf"):
            text = (REPO_ROOT / "deploy" / name).read_text(encoding="utf-8")
            blocks = re.findall(r"location[^{]*\{(.*?)\n    \}", text, re.S)
            proxied = [b for b in blocks if "proxy_pass" in b]
            self.assertTrue(proxied, name)
            for block in proxied:
                with self.subTest(conf=name, block=block.strip().splitlines()[0]):
                    self.assertIn("proxy_set_header X-Forwarded-For", block)


if __name__ == "__main__":
    unittest.main()
