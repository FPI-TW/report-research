"""資料健康與 LLM 用量（/api/admin/data-health、/api/admin/llm-usage，HTTP 層）。

不連 DB、不讀部署目錄：新鮮度用假 session＋tempfile 心跳，結果檔與用量檔都放 tempfile。驗：一般使用者 403、
未登入 401、沒有 `ops.read` 的管理員 403 `missing_scope`；三段的狀態判讀與 overall；沒有結果檔／DB 掛掉時
仍回 200（unknown）；TTL 快取；用量的彙總、台北時間切日、時間範圍上限、讀取上限，以及回應不含 prompt 雜湊、
file_hash、report_id。
"""

from __future__ import annotations

import dataclasses
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import batch_freshness, data_health, llm_usage
from web import auth, deps
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _NoOpsRead(FakeAccounts):
    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"ops.read"})
        return user


class _Session:
    def __init__(self, row):
        self.row = row

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt, params=None):
        row = self.row

        class _R:
            def first(self_inner):
                return row

        return _R()


class _Base(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = _NoOpsRead()
        self.store.add_user("root", ADMIN_PW, "admin")
        self.store.add_user("alice", USER_PW, "user")
        self.store.add_user("limited", ADMIN_PW, "admin")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.addCleanup(self._ctx.__exit__, None, None, None)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def login(self, username="root", password=ADMIN_PW):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client


class AuthzTests(_Base):
    def test_gates(self):
        for path in ("/api/admin/data-health", "/api/admin/llm-usage"):
            with self.subTest(path=path):
                self.assertEqual(_client().get(path).status_code, 401)
                self.assertEqual(self.login("alice", USER_PW).get(path).status_code, 403)
                r = self.login("limited").get(path)
                self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))


class DataHealthTests(_Base):
    def setUp(self):
        super().setUp()
        self.now = datetime.now(timezone.utc)
        self.health_dir = self.tmp / "health"
        env = mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": str(self.health_dir)})
        env.start()
        self.addCleanup(env.stop)
        self.heartbeat = self.tmp / "hb"
        self.heartbeat.write_text(f"epoch={int(time.time()) - 3600}\n", encoding="utf-8")
        hb = mock.patch.object(batch_freshness, "HEARTBEAT_PATH", self.heartbeat)
        hb.start()
        self.addCleanup(hb.stop)
        fresh = self.now - timedelta(hours=2)
        self.set_db(lambda: _Session((fresh, fresh, fresh, fresh)))

    def set_db(self, factory):
        orig = deps.SessionFactory
        deps.SessionFactory = factory
        self.addCleanup(setattr, deps, "SessionFactory", orig)

    def write_audit(self, findings, *, hours_ago=2.0, error=None):
        data_health.write_result(data_health.RESULT_DB_AUDIT, {
            "finished_at": (self.now - timedelta(hours=hours_ago)).isoformat(), "exit_code": 0,
            "error": error, "skipped": [], "findings": findings,
        })

    def write_reconcile(self, stats, *, exit_code=0, hours_ago=24.0, issues=()):
        data_health.write_result(data_health.RESULT_R2_RECONCILE, {
            "finished_at": (self.now - timedelta(hours=hours_ago)).isoformat(), "exit_code": exit_code,
            "mode": "r2", "dry_run": True, "kind": "all", "limit": 0, "orphan_scan": "done",
            "stats": stats, "issues": list(issues), "issues_total": len(issues),
        })

    def get(self):
        r = self.login().get("/api/admin/data-health")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_all_three_sections(self):
        self.write_audit([
            {"key": "null_embedding", "label": "缺 embedding", "severity": "error", "count": 0, "detail": "d"},
            {"key": "chunkless_report", "label": "沒有 chunk", "severity": "warn", "count": 3, "detail": "d"},
        ])
        clean = dict.fromkeys(("checked", "errors", "unkeyed", "key_mismatch", "missing", "size_mismatch",
                               "sha_mismatch", "sha_metadata_missing", "orphans"), 0)
        self.write_reconcile({**clean, "checked": 10, "orphans": 1},
                             issues=[{"type": "orphan", "ref": "originals/ff/x.pdf"}])
        body = self.get()
        self.assertEqual(body["freshness"]["status"], "ok")
        self.assertEqual([f["asset"] for f in body["freshness"]["findings"]],
                         ["pipeline", "corpus", "summary", "takeaway", "signal"])
        # warn 級的違反也算失敗（與 db_audit.py 的退出碼同一個判斷）。
        self.assertEqual(body["db_audit"]["status"], "fail")
        self.assertTrue(body["db_audit"]["available"])
        self.assertFalse(body["db_audit"]["stale"])
        self.assertEqual(body["r2_reconcile"]["status"], "warn")
        self.assertEqual(body["r2_reconcile"]["stats"]["orphans"], 1)
        self.assertEqual(body["r2_reconcile"]["issues"], [{"type": "orphan", "ref": "originals/ff/x.pdf"}])
        self.assertEqual(body["overall"], "fail")

    def test_stale_freshness_is_fail(self):
        now = self.now
        self.set_db(lambda: _Session((now, now, now - timedelta(days=8), now)))
        body = self.get()
        self.assertEqual(body["freshness"]["status"], "fail")
        self.assertEqual(body["freshness"]["exit_code"], batch_freshness.EXIT_STALE)

    def test_empty_state_is_unknown_not_error(self):
        """沒有結果檔、DB 也掛了：仍回 200，心跳那筆照樣判。"""
        def boom():
            raise OSError("connect to secret-host failed")

        self.set_db(boom)
        body = self.get()
        self.assertEqual(body["overall"], "unknown")
        self.assertEqual(body["freshness"]["status"], "unknown")
        self.assertEqual([f["asset"] for f in body["freshness"]["findings"]], ["pipeline"])
        self.assertNotIn("secret-host", json.dumps(body))
        for section in ("db_audit", "r2_reconcile"):
            self.assertFalse(body[section]["available"])
            self.assertEqual(body[section]["unavailable_reason"], "missing")
            self.assertEqual(body[section]["status"], "unknown")

    def test_old_result_is_stale_warn(self):
        self.write_audit([{"key": "k", "label": "l", "severity": "error", "count": 0, "detail": "d"}],
                         hours_ago=72)
        body = self.get()
        self.assertTrue(body["db_audit"]["stale"])
        self.assertEqual(body["db_audit"]["status"], "warn")

    def test_malformed_result_fields_do_not_500(self):
        data_health.write_result(data_health.RESULT_R2_RECONCILE, {
            "finished_at": "not-a-time", "exit_code": "x", "mode": "weird", "stats": [1, 2],
            "issues": [{"type": 1}, "x"], "issues_total": -5,
        })
        data_health.write_result(data_health.RESULT_DB_AUDIT, {"finished_at": None, "findings": [{"count": "9"}, 3]})
        body = self.get()
        self.assertTrue(body["r2_reconcile"]["available"])
        self.assertTrue(body["r2_reconcile"]["stale"])
        self.assertEqual(body["r2_reconcile"]["issues"], [])
        self.assertEqual(body["db_audit"]["findings"][0]["count"], 0)

    def test_response_is_cached(self):
        calls = []
        real = data_health.snapshot

        async def counting(factory, **kw):
            calls.append(1)
            return await real(factory, **kw)

        with mock.patch.object(deps.data_health, "snapshot", counting):
            client = self.login()
            self.assertEqual(client.get("/api/admin/data-health").status_code, 200)
            self.assertEqual(client.get("/api/admin/data-health").status_code, 200)
        self.assertEqual(len(calls), 1)


def _line(ts: str, **kw) -> str:
    row = {"ts": ts, "task": "summary", "file_hash": "f" * 64, "report_id": "11111111-2222-3333-4444-555555555555",
           "backend": "http", "model_req": "deepseek-flash", "model_resp": "deepseek-chat",
           "prompt_sha256": "c0ffee" * 10 + "abcd", "tokens": {"hit": 10, "miss": 90, "completion": 20, "reasoning": 0},
           "finish_reason": "stop", "kind": None, "attempts": 1, "ttft_ms": 100, "total_ms": 1000}
    row.update(kw)
    return json.dumps(row, ensure_ascii=False)


class LlmUsageApiTests(_Base):
    def setUp(self):
        super().setUp()
        self.log = self.tmp / "llm_usage.jsonl"
        env = mock.patch.dict(os.environ, {"LLM_USAGE_LOG": str(self.log)})
        env.start()
        self.addCleanup(env.stop)

    def get(self, **params):
        return self.login().get("/api/admin/llm-usage", params=params)

    def test_missing_file_is_empty_result(self):
        r = self.get()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["source"]["exists"])
        self.assertEqual(body["totals"]["calls"], 0)
        self.assertEqual(body["by_day"], [])
        self.assertFalse(body["cost_available"])

    def test_aggregates_without_leaking_identifiers(self):
        self.log.write_text("\n".join([
            # 台北時間 10/06 01:00（UTC 10/05 17:00）→ 算 10/06。
            _line("2026-10-05T17:00:00.000+00:00"),
            _line("2026-10-05T18:00:00.000+00:00", task="title", kind="timeout", tokens=None, total_ms=5000),
            _line("2026-10-05T15:00:00.000+00:00", backend="cli", model_req="claude-haiku-4-5", tokens=None),
            "{not json",
            _line("2026-09-01T00:00:00.000+00:00"),  # 範圍外
        ]) + "\n", encoding="utf-8")
        r = self.get(since="2026-10-05T00:00:00Z", until="2026-10-07T00:00:00Z")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["timezone"], "Asia/Taipei")
        self.assertEqual(body["source"]["lines_invalid"], 1)
        self.assertEqual(body["source"]["lines_in_range"], 3)
        t = body["totals"]
        self.assertEqual((t["calls"], t["failures"], t["calls_without_tokens"]), (3, 1, 2))
        self.assertEqual((t["prompt_hit_tokens"], t["prompt_miss_tokens"], t["completion_tokens"]), (10, 90, 20))
        self.assertEqual([(d["day"], d["calls"]) for d in body["by_day"]], [("2026-10-05", 1), ("2026-10-06", 2)])
        self.assertEqual({x["task"]: x["calls"] for x in body["by_task"]}, {"summary": 2, "title": 1})
        self.assertEqual({x["model"]: x["calls"] for x in body["by_model"]},
                         {"deepseek-flash": 2, "claude-haiku-4-5": 1})
        self.assertEqual(len(body["rows"]), 3)
        text = r.text
        for secret in ("f" * 64, "11111111-2222", "c0ffee", "prompt_sha256", "file_hash", "report_id"):
            self.assertNotIn(secret, text)

    def test_cost_is_summed_when_present(self):
        self.log.write_text(_line("2026-10-05T01:00:00+00:00", cost=0.25) + "\n"
                            + _line("2026-10-05T02:00:00+00:00", cost=0.5) + "\n", encoding="utf-8")
        body = self.get(since="2026-10-05T00:00:00Z", until="2026-10-06T00:00:00Z").json()
        self.assertTrue(body["cost_available"])
        self.assertAlmostEqual(body["totals"]["cost"], 0.75)

    def test_window_limits(self):
        r = self.get(since="2026-10-06T00:00:00Z", until="2026-10-01T00:00:00Z")
        self.assertEqual((r.status_code, r.json()["code"]), (400, "invalid_params"))
        r = self.get(since="2024-01-01T00:00:00Z", until="2026-01-01T00:00:00Z")
        self.assertEqual((r.status_code, r.json()["code"]), (400, "invalid_params"))

    def test_cache_key_follows_file_changes(self):
        self.log.write_text(_line("2026-10-05T01:00:00+00:00") + "\n", encoding="utf-8")
        params = {"since": "2026-10-05T00:00:00Z", "until": "2026-10-06T00:00:00Z"}
        client = self.login()
        self.assertEqual(client.get("/api/admin/llm-usage", params=params).json()["totals"]["calls"], 1)
        with self.log.open("a", encoding="utf-8") as f:
            f.write(_line("2026-10-05T02:00:00+00:00") + "\n")
        self.assertEqual(client.get("/api/admin/llm-usage", params=params).json()["totals"]["calls"], 2)


class LlmUsageLimitTests(unittest.TestCase):
    SINCE = datetime(2026, 10, 1, tzinfo=timezone.utc)
    UNTIL = datetime(2026, 10, 10, tzinfo=timezone.utc)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.log = Path(self._tmp.name) / "llm_usage.jsonl"
        lines = [_line(f"2026-10-0{1 + i % 9}T0{i % 10}:00:00+00:00", task=f"t{i % 7}") for i in range(300)]
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_byte_cap_reads_only_the_tail(self):
        out = llm_usage.summarize(self.SINCE, self.UNTIL, path=self.log, max_bytes=4096)
        self.assertTrue(out["source"]["truncated"])
        self.assertLessEqual(out["source"]["scanned_bytes"], 4096)
        self.assertEqual(out["source"]["lines_invalid"], 0, "殘段那一行必須丟掉，不能算成壞行")
        self.assertLess(out["totals"]["calls"], 300)

    def test_line_cap_keeps_newest(self):
        out = llm_usage.summarize(self.SINCE, self.UNTIL, path=self.log, max_lines=50)
        self.assertTrue(out["source"]["truncated"])
        self.assertEqual(out["source"]["lines_scanned"], 50)

    def test_group_cap_folds_into_other(self):
        with mock.patch.object(llm_usage, "MAX_GROUPS", 3):
            out = llm_usage.summarize(self.SINCE, self.UNTIL, path=self.log)
        tasks = {x["task"] for x in out["by_task"]}
        self.assertIn(llm_usage.OTHER, tasks)
        self.assertEqual(len(tasks), 4)
        self.assertEqual(sum(x["calls"] for x in out["by_task"]), 300)

    def test_row_cap(self):
        with mock.patch.object(llm_usage, "MAX_ROWS", 5):
            out = llm_usage.summarize(self.SINCE, self.UNTIL, path=self.log)
        self.assertEqual(len(out["rows"]), 5)
        self.assertTrue(out["rows_truncated"])

    def test_devnull_is_empty(self):
        out = llm_usage.summarize(self.SINCE, self.UNTIL, path=Path(os.devnull))
        self.assertEqual(out["totals"]["calls"], 0)
        self.assertFalse(out["source"]["exists"])

    def test_batch_writer_uses_the_same_path(self):
        from scripts import _claude_cli

        with mock.patch.dict(os.environ, {"LLM_USAGE_LOG": str(self.log)}):
            self.assertEqual(_claude_cli.usage_log_path(), llm_usage.usage_log_path())
        with mock.patch.dict(os.environ, {"LLM_USAGE_LOG": ""}):
            self.assertEqual(_claude_cli.usage_log_path(), llm_usage.REPO_ROOT / "data" / "llm_usage.jsonl")


if __name__ == "__main__":
    unittest.main()
