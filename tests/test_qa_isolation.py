"""問答紀錄的每人隔離——路由接線（不連 DB）。

- `/api/ask`、`/api/ask/stop` 把**目前登入者**的 id 傳進服務層；參照別人的對話串或回答時
  在串流前就 404（`answer_question`／`log_stopped_qa` 根本不會被呼叫）；擁有權查不到時 503。
- 靜態守門：`app/services/answer.py` 與 `web/routers/` 裡每一個 qa_log 讀寫函式的呼叫都要
  明確帶 `user_id=`。漏帶的後果是靜默的（寫成 NULL 擁有者＝自己看不到；讀不帶條件＝看到別人的），
  而這些呼叫點大多被測試用 `**kwargs` 的假物件接住，行為測試抓不到。

真的 SQL（別人的列看不到、改不到、刪不到）由 `tests/test_qa_isolation_db.py` 驗。
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from fake_accounts import FakeAccounts, install
from fastapi.testclient import TestClient

from web import auth, deps, dev_mode
from web.server import app

REPO_ROOT = Path(__file__).resolve().parents[1]
CONV = "13c3af97-b458-4108-836d-654a89e76fb7"
QA = "8d1f0a3c-4b2e-4c6f-9a7d-2e5b6c7d8e9f"


def _client():
    return TestClient(app, follow_redirects=False, base_url="http://127.0.0.1")


class AskOwnershipTests(unittest.TestCase):
    def setUp(self):
        auth._FAILS.clear()
        self.store = FakeAccounts()
        self.alice = self.store.add_user("alice", "alice-password")
        self._ctx = install(self.store)
        self._ctx.__enter__()
        self.calls: list = []
        self.foreign: set[str] = set()
        self.check_error: Exception | None = None
        self._orig = (deps.answer_question, deps.log_stopped_qa, deps.conversation_is_foreign, deps.qa_is_foreign)

        async def fake_answer(question, **kwargs):
            self.calls.append(("ask", kwargs))
            yield ("done", {"qa_id": QA, "conversation_id": CONV})

        async def fake_stop(question, partial, **kwargs):
            self.calls.append(("stop", kwargs))
            return QA

        async def fake_check(ref, *, user_id):
            self.calls.append(("check", ref, user_id))
            if self.check_error is not None:
                raise self.check_error
            return ref in self.foreign

        deps.answer_question = fake_answer
        deps.log_stopped_qa = fake_stop
        deps.conversation_is_foreign = fake_check
        deps.qa_is_foreign = fake_check

    def tearDown(self):
        deps.answer_question, deps.log_stopped_qa, deps.conversation_is_foreign, deps.qa_is_foreign = self._orig
        self._ctx.__exit__(None, None, None)
        auth._FAILS.clear()

    def _login(self):
        c = _client()
        r = c.post("/login", data={"username": "alice", "password": "alice-password"})
        self.assertEqual(r.status_code, 303)
        return c

    def _service_calls(self, kind):
        return [c[1] for c in self.calls if c[0] == kind]

    def test_ask_writes_as_current_user(self):
        r = self._login().post("/api/ask", json={"question": "台積電展望", "conversation_id": CONV})
        self.assertEqual(r.status_code, 200)
        (kwargs,) = self._service_calls("ask")
        self.assertEqual(kwargs["user_id"], self.alice)
        self.assertIn(("check", CONV, self.alice), self.calls)

    def test_stop_writes_as_current_user(self):
        r = self._login().post("/api/ask/stop", json={"question": "q", "partial_answer": "部分"})
        self.assertEqual(r.status_code, 200)
        (kwargs,) = self._service_calls("stop")
        self.assertEqual(kwargs["user_id"], self.alice)

    def test_foreign_refs_are_404_before_streaming(self):
        self.foreign = {CONV, QA}
        c = self._login()
        cases = [
            ("/api/ask", {"question": "續問", "conversation_id": CONV}),
            ("/api/ask", {"question": "重生", "regenerate_of": QA}),
            ("/api/ask", {"question": "編輯", "edit_of": QA}),
            ("/api/ask/stop", {"question": "停", "conversation_id": CONV}),
            ("/api/ask/stop", {"question": "停", "regenerate_of": QA}),
            ("/api/ask/stop", {"question": "停", "edit_of": QA}),
        ]
        for path, body in cases:
            with self.subTest(path=path, body=body):
                r = c.post(path, json=body)
                self.assertEqual(r.status_code, 404, r.text)
                self.assertEqual(r.json()["detail"], "not found")
                self.assertEqual(r.json()["code"], "not_found")
        self.assertEqual(self._service_calls("ask") + self._service_calls("stop"), [])

    def test_ownership_check_failure_is_503_not_fail_open(self):
        self.check_error = RuntimeError("db down")
        c = self._login()
        with self.assertLogs("web.routers.ask", level="ERROR"):
            r = c.post("/api/ask", json={"question": "續問", "conversation_id": CONV})
        self.assertEqual(r.status_code, 503)
        self.assertEqual(self._service_calls("ask"), [])

    def test_malformed_conversation_id_is_400_on_ask(self):
        r = self._login().post("/api/ask", json={"question": "續問", "conversation_id": "not-a-uuid"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_no_refs_means_no_check(self):
        self._login().post("/api/ask", json={"question": "新題"})
        self.assertEqual([c for c in self.calls if c[0] == "check"], [])

    def test_dev_mode_asks_as_null_owner(self):
        orig = dev_mode.bypass_allowed
        dev_mode.bypass_allowed = lambda request: True
        try:
            r = _client().post("/api/ask", json={"question": "台積電展望"})
        finally:
            dev_mode.bypass_allowed = orig
        self.assertEqual(r.status_code, 200)
        (kwargs,) = self._service_calls("ask")
        self.assertIsNone(kwargs["user_id"])


# 讀寫 research.qa_log、而且要帶擁有者條件的函式。新增這類函式時加進來。
_OWNED = {
    "_log_qa", "log_stopped_qa", "_load_qa_meta", "_count_versions", "load_recent_turns",
    "list_conversations", "get_conversation", "list_qa_versions", "delete_conversation",
    "record_feedback", "delete_qa", "qa_is_foreign", "conversation_is_foreign", "answer_question",
}
_SCANNED = [REPO_ROOT / "app" / "services" / "answer.py", *sorted((REPO_ROOT / "web" / "routers").glob("*.py"))]


def _callee(node: ast.Call) -> str | None:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


class UserIdIsAlwaysPassedTests(unittest.TestCase):
    def test_every_owned_call_passes_user_id(self):
        missing = []
        seen = 0
        for path in _SCANNED:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or _callee(node) not in _OWNED:
                    continue
                seen += 1
                if not any(kw.arg == "user_id" for kw in node.keywords):
                    missing.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {_callee(node)}(...)")
        self.assertGreater(seen, 25, "抓到的呼叫點太少，掃描規則八成失效了")
        self.assertEqual(missing, [], f"這些 qa_log 呼叫沒有明確帶 user_id：{missing}")

    def test_read_functions_require_user_id(self):
        """讀取／變更類函式的 user_id 沒有預設值：漏傳在呼叫當下就 TypeError，不會靜默查全庫。"""
        import inspect

        from app.services import answer as ans

        for name in ("_log_qa", "log_stopped_qa", "_load_qa_meta", "_count_versions", "load_recent_turns",
                     "list_conversations", "get_conversation", "list_qa_versions", "delete_conversation",
                     "record_feedback", "delete_qa", "qa_is_foreign", "conversation_is_foreign"):
            with self.subTest(name):
                param = inspect.signature(getattr(ans, name)).parameters["user_id"]
                self.assertIs(param.default, inspect.Parameter.empty)
                self.assertEqual(param.kind, inspect.Parameter.KEYWORD_ONLY)


if __name__ == "__main__":
    unittest.main()
