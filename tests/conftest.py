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

# LLM 付費 API：**刻意用賦值，不用上面那種 setdefault**。
# 兩道要擋的來源：
# 1. repo root 就是部署目錄，`web/server.py`、`web/deps.py` 在 import 期把真的 `.env` 灌進
#    os.environ；`web.env_loader.load_env_file` 只補「還不存在」的鍵（空字串也算存在），
#    所以這裡先佔位就擋得住——這一點 setdefault 也做得到。
# 2. **執行者的環境裡本來就有真金鑰**（shell 已 export、或先載入過部署環境檔）。這是
#    setdefault 擋不住、只有賦值擋得住的情況：漏了假物件的測試會真的打付費端點，而 CI 的
#    runner 沒有金鑰、永遠看不到這個差異。
# 端點指到不可達的本機埠是第二道防線。需要金鑰的測試用 mock.patch.dict 在自己的範圍內給假值。
# 守門：tests/test_llm_http.py 的 ConftestGuardTests（含靜態釘住「賦值而非 setdefault」）。
os.environ["DEEPSEEK_API_KEY"] = ""
os.environ["DEEPSEEK_BASE_URL"] = "http://127.0.0.1:9"

# 模型選擇：同樣**用賦值**，理由同上（部署目錄的 `.env`、執行者 shell 裡的值都擋得住）。
# 所有任務旋鈕設成 ""——`app/services/llm_models.resolve_model` 把空字串視同未設、改查預設表，
# 所以測試永遠拿到 claude_cli 預設表的值，與誰的機器、誰的環境檔無關。這一條與
# `resolve_model` 的「空字串＝未設」寫法必須同進同退：只有前者，模組會拿到空字串的模型名。
# `LLM_ENV_FILE` 指到不存在的路徑：批次在 import 期載入 LLM 專用環境檔，測試不得讀到本機
# 真的 `/etc/default/report-mark-llm`。
# 守門：tests/test_llm_models.py 的 ConftestModelGuardTests（清單直接比對 TASK_ENV）。
os.environ["LLM_PROVIDER"] = "claude_cli"
os.environ["LLM_ENV_FILE"] = "/nonexistent/report-mark-llm"
for _knob in (
    "ASK_ANSWER_MODEL", "ASK_WEB_MODEL", "ASK_INTENT_MODEL", "ASK_CONDENSE_MODEL",
    "QA_PLANNER_MODEL", "ASK_FOLLOWUP_MODEL", "FAITHFULNESS_MODEL", "EVAL_JUDGE_MODEL",
    "TAG_MODEL", "SUMMARY_MODEL", "TITLE_MODEL", "TAKEAWAY_MODEL", "SIGNAL_MODEL", "BRIEF_MODEL",
):
    os.environ[_knob] = ""
# DeepSeek 串流的牆鐘總時限：空字串＝預設 600（app/config._positive_float），部署目錄 `.env` 的值不滲進測試。
os.environ["LLM_HTTP_TOTAL_TIMEOUT"] = ""
# 批次斷路器的標記（scripts/_llm_env.breaker_path）：預設落在 repo 根的 data/，而 repo 根就是部署
# 目錄——測試讓斷路器跳脫時寫進去，生產排程會 30 分鐘拒跑。指到不存在的目錄：寫入 fail-open
# 失敗、讀取當作沒有。要驗標記的測試用 mock.patch.dict 指到自己的 tempfile。
os.environ["LLM_BREAKER_FILE"] = "/nonexistent/report-mark-llm-breaker/.llm_breaker"
# sync 輪次 id（scripts/sync_new_reports.sh 每輪 export）：斷路器標記的有效範圍依它判斷。從排程環境
# 裡跑測試時不得沾到那一輪的 id；要驗輪次行為的測試用 mock.patch.dict 自己給。
os.environ.pop("SYNC_ROUND_ID", None)
# 批次用量記錄（scripts/_claude_cli.usage_log_path）：同理不得寫進部署目錄的 data/llm_usage.jsonl
# （那是費用歸因的依據）。指到 os.devnull：寫得進去、什麼都不留；要驗內容的測試自己指到 tempfile。
os.environ["LLM_USAGE_LOG"] = os.devnull


@pytest.fixture(scope="session", autouse=True)
def _protect_repo_dotenv():
    """執行期安全網：任何測試都不得改動 repo 根的 `.env`。

    這台機器上 repo root **就是部署目錄**（systemd 的 WorkingDirectory，且
    `web/server.py` 從模組自身路徑解析 `.env`）。2026-07-29 有測試覆寫它之後
    沒還原，生產帳密與 `REPORT_MARK_SESSION_SECRET` 被換成測試值，重啟後生效。

    `test_env_loading.py` 的 AST 掃描擋的是**已知的程式碼形態**；這裡擋的是
    行為本身——不管用什麼寫法動到它，session 結束時都會被抓出來並**自動還原**，
    把「靜默毀掉生產憑證」降級成「一條紅色測試」。

    限制講明：行程被 SIGKILL（OOM、逾時強殺）時 teardown 不會執行，這層網就
    失效。所以它是第二道防線，第一道仍是「測試根本不要碰真實檔案」。
    """
    env_path = Path(__file__).resolve().parent.parent / ".env"
    original = env_path.read_bytes() if env_path.is_file() else None
    yield
    now = env_path.read_bytes() if env_path.is_file() else None
    if now == original:
        return
    if original is None:
        env_path.unlink(missing_ok=True)
    else:
        env_path.write_bytes(original)
    raise AssertionError(
        f"有測試改動了 repo 根的 {env_path.name}（已自動還原）。"
        "測試不得改動真實部署檔——見 tests/test_env_loading.py 的模組 docstring。"
    )


@pytest.fixture(autouse=True)
def _reset_monitor_caches():
    """`web.routers.monitor` 的三個模組級 TTL 快取每題前後各清一次。

    沒有這層的話：A 測試 patch 掉 `_gather_runtime` 後打一次 `/api/progress`，
    假值就進了 `_RUNTIME_CACHE`，B 測試即使 patch 成別的值也拿得到 A 的——兩邊
    形狀相同時完全看不出來，只會在某個排序下莫名失敗。DB 快照那一份原本靠各測試
    自己在 setUp 裡清，那是「記得寫才有效」的防線。

    **刻意不主動 import** `web.routers.monitor`：純單元測試（textnorm、chunk 之類）
    不該為了清一個快取去拉起 web 相依鏈。模組沒被載入就等於沒有快取要清。
    """

    def _clear() -> None:
        mod = sys.modules.get("web.routers.monitor")
        if mod is not None and hasattr(mod, "reset_caches"):
            mod.reset_caches()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _reset_llm_batch_state():
    """`scripts._claude_cli` 的行程範圍狀態（斷路器的滑動窗與跳脫旗標）每題前後各清一次。

    沒有這層的話，前一題留下的幾次逾時會讓下一題莫名跳脫（或反過來，把該跳脫的推回門檻下）。
    同樣不主動 import——模組沒載入就沒有狀態要清。
    """

    def _clear() -> None:
        mod = sys.modules.get("scripts._claude_cli")
        if mod is not None and hasattr(mod, "_reset_state"):
            mod._reset_state()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _reset_ttl_caches():
    """`web.ttl_cache` 的所有快取每題前後各清一次（理由同上面的監控快取）。

    雷達目錄快取的 key 是查詢參數：兩個測試用同一組參數、不同的假查詢函式打同一支端點時，
    後者會拿到前者的回應。同樣不主動 import——模組沒載入就沒有快取要清。
    """

    def _clear() -> None:
        mod = sys.modules.get("web.ttl_cache")
        if mod is not None:
            mod.reset_all()

    _clear()
    yield
    _clear()


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
