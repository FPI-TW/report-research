"""檢索回歸檢查（GET /api/admin/retrieval-regression，HTTP 層）。

不連 DB、不跑檢索：結果檔寫 tempfile（`DATA_HEALTH_DIR`）。驗：未登入 401、一般使用者 403、沒有 `ops.read` 的
管理員 403 `missing_scope`；沒有結果檔回 200（unknown）；比對結果原樣帶出；結果檔格式不符不回 500。
"""

from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from app.services import data_health
from app.services import retrieval_regression as rr
from web import auth, deps
from web.server import app

ADMIN_PW = "root-password-1"
USER_PW = "alice-password-1"
PATH = "/api/admin/retrieval-regression"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class _NoOpsRead(FakeAccounts):
    def _user(self, row, elevated_until=None):
        user = super()._user(row, elevated_until)
        if user.username == "limited":
            return dataclasses.replace(user, scopes=user.scopes - {"ops.read"})
        return user


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
        env = mock.patch.dict(os.environ, {"DATA_HEALTH_DIR": str(Path(self._tmp.name) / "health")})
        env.start()
        self.addCleanup(env.stop)

    def login(self, username="root", password=ADMIN_PW):
        client = _client()
        r = client.post("/login", data={"username": username, "password": password})
        self.assertEqual(r.status_code, 303)
        return client

    def get(self):
        r = self.login().get(PATH)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class AuthzTests(_Base):
    def test_gates(self):
        self.assertEqual(_client().get(PATH).status_code, 401)
        self.assertEqual(self.login("alice", USER_PW).get(PATH).status_code, 403)
        r = self.login("limited").get(PATH)
        self.assertEqual((r.status_code, r.json()["code"]), (403, "missing_scope"))


class ResultTests(_Base):
    def test_no_result_yet(self):
        body = self.get()
        self.assertEqual((body["status"], body["available"], body["unavailable_reason"]), ("unknown", False, "missing"))
        self.assertIsNone(body["comparison"])

    def test_degraded_result(self):
        now = datetime.now(timezone.utc)
        h = "ab" * 32
        data_health.write_result(rr.RESULT_NAME, {
            "finished_at": now.isoformat(), "exit_code": 1, "outcome": "degraded", "reason": None,
            "message": "檢索劣化", "comparison": {
                "finished_at": now.isoformat(), "duration_s": 95.2, "dense_scan": 400, "params_changed": False,
                "baseline": {"captured_at": (now - timedelta(days=9)).isoformat(),
                             "corpus_cutoff": (now - timedelta(days=9)).isoformat(), "corpus_reports": 15060,
                             "simulated_as_of": False, "k": 10, "dense_scan": 400, "dataset_sha256": "0" * 64},
                "thresholds": {"min_mean_recall": 0.8, "min_question_recall": 0.5, "max_degraded_questions": 2},
                "summary": {"verdict": "degraded", "questions": 18, "comparable": 18, "mean_report_recall": 0.62,
                            "mean_raw_report_recall": 0.4, "mean_chunk_recall": 0.5, "mean_rbo": 0.55,
                            "degraded_questions": 4, "hidden_reports": 1, "removed_reports": 0,
                            "excluded_new_reports": 30, "degraded_ids": ["q001"]},
                "questions": [{"id": "q001", "question": "台積電最新的營運展望如何", "comparable": True,
                               "degraded": True, "report_recall": 0.2, "raw_report_recall": 0.1,
                               "chunk_recall": 0.1, "rbo": 0.15, "baseline_reports": 5, "eligible_reports": 5,
                               "current_reports": 5, "hidden_reports": 0, "removed_reports": 0,
                               "excluded_new_reports": 2, "lost_total": 4, "gained_total": 4,
                               "lost": [{"file_hash": h, "label": "某券商 台積電"}], "gained": []}],
            },
        })
        body = self.get()
        self.assertEqual((body["status"], body["outcome"], body["exit_code"]), ("fail", "degraded", 1))
        c = body["comparison"]
        self.assertEqual(c["summary"]["verdict"], "degraded")
        self.assertEqual(c["summary"]["excluded_new_reports"], 30)
        self.assertEqual(c["baseline"]["corpus_reports"], 15060)
        self.assertEqual(c["questions"][0]["lost"], [{"file_hash": h, "label": "某券商 台積電"}])
        self.assertNotIn("degraded_ids", c["summary"])  # 回應只帶宣告過的欄位

    def test_skipped_without_previous_comparison(self):
        data_health.write_result(rr.RESULT_NAME, {
            "finished_at": datetime.now(timezone.utc).isoformat(), "exit_code": 2, "outcome": "skipped",
            "reason": "db_unavailable", "message": "DB 連不上", "comparison": None,
        })
        body = self.get()
        self.assertEqual((body["status"], body["reason"], body["comparison"]), ("unknown", "db_unavailable", None))

    def test_shape_mismatch_is_not_a_500(self):
        with mock.patch.object(deps.retrieval_regression, "section",
                               return_value={"status": "ok", "available": True, "stale": "no-such-bool"}):
            body = self.get()
        self.assertEqual((body["available"], body["unavailable_reason"]), (False, "corrupt:schema"))


if __name__ == "__main__":
    unittest.main()
