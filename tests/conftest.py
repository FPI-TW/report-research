"""
pytest 測試環境初始化。

1. 將 repo root 加入 sys.path（pytest 預設不加），讓 `web`、`app` 等頂層套件可被匯入。
2. 為 web.auth fail-closed env var 設定測試預設值（individual 測試可在匯入前覆蓋）。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Repo root = parent of this tests/ directory
_REPO_ROOT = str(Path(__file__).resolve().parent.parent)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

# web.auth is fail-closed: raise RuntimeError if these are unset at import time.
# These defaults are applied before any test module is imported, so per-module
# setdefault() calls (e.g. in test_auth.py) intentionally defer to these values.
os.environ.setdefault("REPORT_MARK_ACCESS_USERNAME", "tester")
os.environ.setdefault("REPORT_MARK_ACCESS_PASSWORD", "testpass")
os.environ.setdefault("REPORT_MARK_SESSION_SECRET", "fixed-test-secret-0123456789")


@pytest.fixture(autouse=True)
def _clear_trusted_providers():
    """M4a：trusted registry／快取／限流是模組級狀態。每測試後清空，防止
    忘記 tearDown 的註冊型測試讓「空 registry＝安全婉拒」的 M4 回歸誤判。"""
    yield
    from app.services.trusted_market_data import clear_providers

    clear_providers()


@pytest.fixture(autouse=True)
def _stub_followups():
    """預設關閉追問建議（M3）：主 RAG 路徑在 done 之後會呼叫 generate_followups，
    真跑會外連 claude CLI 並讓事件序尾隨 followups。除非測試明確驗追問，否則一律
    stub 成回 []（不發 followups 事件）。明確驗追問的測試在其函式內自行覆寫 + 還原。"""
    import app.services.answer as ans

    orig = getattr(ans, "generate_followups", None)

    async def _empty(*a, **k):
        return []

    ans.generate_followups = _empty
    try:
        yield
    finally:
        if orig is not None:
            ans.generate_followups = orig


@pytest.fixture(autouse=True)
def _stub_query_planner_llm():
    """預設關閉查詢規劃 LLM（M5）：qa_agentic_enabled 預設開，answer_question 會
    並行呼叫 plan_queries，真跑會外連 claude CLI。stub 成回空子查詢陣列——
    計畫為單一原問題查詢 → run_agentic 走快速路徑，事件序與 M4 完全一致。
    本 fixture 為 M5/M6 唯一共用的 planner stub（M5 spec 凍結契約 4）：M6 report
    profile 沿用同一 stub 點，不得另加第二份 planner fixture；驗規劃／評估行為
    的測試在其內部自行 patch query_planner.stream_completion。"""
    import app.services.query_planner as qp

    orig = qp.stream_completion

    async def _empty_plan(prompt, **kwargs):
        yield '{"subqueries": []}'

    qp.stream_completion = _empty_plan
    try:
        yield
    finally:
        qp.stream_completion = orig
