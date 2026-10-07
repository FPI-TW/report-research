"""
pytest 測試環境初始化。

1. 將 repo root 加入 sys.path（pytest 預設不加），讓 `web`、`app` 等頂層套件可被匯入。
2. 固定 cookie 簽章金鑰，並把帳號服務換成記憶體假物件（見 _fake_accounts）。
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

# cookie 簽章金鑰：固定值讓 token 測試可重現（未設時 web.auth 會隨機產生並記 warning）。
# 帳號不再來自環境變數：見下方 _fake_accounts（tester／testpass 管理員）。
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
# 所以測試永遠拿到 claude_cli 預設表的值（下面強制的 provider），與誰的機器、誰的環境檔無關。這一條與
# `resolve_model` 的「空字串＝未設」寫法必須同進同退：只有前者，模組會拿到空字串的模型名。
# `LLM_ENV_FILE` 指到不存在的路徑：批次在 import 期載入 LLM 專用環境檔，測試不得讀到本機
# 真的 `/etc/default/report-mark-llm`。
# 守門：tests/test_llm_models.py 的 ConftestModelGuardTests（清單直接比對 TASK_ENV）。
# **生產預設已是 deepseek**（遷移 PR-28，`llm_models.DEFAULT_PROVIDER`），這裡刻意仍設 `claude_cli`：
# 測試不得打付費 API，既有測試的假物件（`asyncio.create_subprocess_exec`、`_claude_cli` 的 spawn）
# 都接在 CLI 路徑上；改成 deepseek 會讓漏了假物件的測試改走 HTTP 路徑（端點雖指到不可達的本機埠，
# 失敗型態卻從「假物件沒接上」變成「連線失敗被當成 LLM 不可用」，靜默走另一條路）。
# 要驗預設值的測試自己在範圍內移除這個鍵（`mock.patch.dict` 後 pop），見 test_llm_models.py。
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
# /healthz/llm 的預算幣別與門檻（app/config.py）：同上，空字串＝預設（CNY、70），部署目錄 `.env` 的值
# 不滲進測試（否則 ok／low 的邊界測試會依機器而變）。守門：tests/test_healthz_llm.py 的 KnobTests。
os.environ["LLM_BUDGET_CURRENCY"] = ""
os.environ["LLM_BALANCE_FLOOR"] = ""
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
# 監控 spool（scripts/collect_resource_usage.py 寫、scripts/load_observations.py 匯入；預設落點是 repo 根的
# data/ops_spool/，而 repo 根就是部署目錄）：同理用賦值指到 os.devnull——不是目錄，收集器開不了 spool
# （只停用觀測那一段）、loader 視為沒有東西可匯入。要驗 spool 的測試自己給 --spool-dir 或 tempfile。
os.environ["OPS_SPOOL_DIR"] = os.devnull
# 容器／主機探針的連續失敗次數（scripts/_health_streak.sh；預設 repo 根的 data/.health-streaks/）：同理用賦值指到
# 不存在的目錄——寫不進去時探針改成「每筆失敗都算確認」，不會在部署目錄留下狀態。要驗去抖的測試自己給 tempfile。
os.environ["HEALTH_STREAK_DIR"] = "/nonexistent/report-mark-health-streaks"
# 每日 schema 檢查的狀態檔（scripts/schema_baseline.py scheduled；預設 repo 根的 data/schema_check.json，
# 管理頁讀它）：同理用賦值指到不存在的目錄——寫入只警告、不建目錄，部署目錄的狀態檔不會被測試的假結果
# 蓋掉。要驗內容的測試自己給 tempfile。
os.environ["SCHEMA_CHECK_STATUS_FILE"] = "/nonexistent/report-mark-schema-check/schema_check.json"
# 資料健康結果檔（app/services/data_health.py；db_audit.py 與 reconcile_object_storage.py 跑完時寫，預設 repo 根的
# data/health/）：同理用賦值指到不存在的目錄——寫入 fail-open、讀取當作「還沒跑過」。要驗內容的測試自己給 tempfile。
os.environ["DATA_HEALTH_DIR"] = "/nonexistent/report-mark-data-health"
# 檢索回歸的基準檔（app/services/retrieval_regression.py；預設 repo 根的 data/retrieval_regression/baseline.json）：
# 同理用賦值指到不存在的路徑——讀取當作「還沒有基準」、擷取寫不進去。要驗內容的測試自己給 tempfile。
os.environ["RETRIEVAL_REGRESSION_BASELINE"] = "/nonexistent/report-mark-retrieval-regression/baseline.json"
# 研報上傳的隔離區（app/services/quarantine.py；預設 repo 根的 data/quarantine/）：同理用賦值指到不存在的路徑——
# 收檔建不出目錄就回 503 `quarantine_unavailable`，部署目錄不會留下測試寫的 .part／.bin。要寫檔的測試自己給 tempfile。
os.environ["UPLOAD_QUARANTINE_DIR"] = "/nonexistent/report-mark-quarantine"
# 上傳 worker 的整輪鎖與乾淨檔目錄（app/services/upload_worker.py；預設 repo 根的 data/.upload_worker.lock、
# data/uploads/clean/）：同理用賦值指到不存在的路徑。要跑 worker 的測試自己給 tempfile（Ctx 的路徑欄位）。
# 告警 webhook：worker 偵測到感染時會送；測試一律不得送出（要驗的測試自己給假的 runner 與 URL）。
os.environ["UPLOAD_WORKER_LOCK_FILE"] = "/nonexistent/report-mark-upload-worker/.upload_worker.lock"
os.environ["UPLOAD_CLEAN_DIR"] = "/nonexistent/report-mark-uploads-clean"
os.environ.pop("REPORT_MARK_ALERT_WEBHOOK", None)
# 維運代理（app/config.py 的 OPS_AGENT_*）：同樣用賦值。這台機器就是生產主機，代理裝上之後預設 socket
# 是真的；沒裝假代理（tests/fake_ops_agent.py）的測試必須連不到它。
os.environ["OPS_AGENT_ENVIRONMENT"] = "production"
os.environ["OPS_AGENT_SOCKET"] = "/nonexistent/report-mark-ops/agent.sock"
# 管理員 TOTP 強制（app/config.py 的 ADMIN_MFA_REQUIRED，預設關）：既有測試的管理員（tester 等）都沒開 TOTP，
# 若部署目錄 .env 或執行者 shell 設成開，每一支管理端點測試都會 403。
# **用賦值**（部署目錄 .env 或執行者 shell 裡的值都擋得住）。
# 開啟時的行為由 tests/test_admin_mfa.py 在自己的範圍內換掉 Settings 驗證。
os.environ["ADMIN_MFA_REQUIRED"] = "0"
# 用量收集（app/services/usage_events.py）：lifespan 的 flusher 會把累加器寫進 usage_daily／usage_counter／
# llm_usage_daily——測試以 `with TestClient(app)` 觸發 lifespan 時，那就是寫進 REPORT_MARK_DB_URL 指的庫
# （本機預設是生產庫）。**用賦值**關掉 flusher 與 LLM observer；middleware 仍在記憶體計數（下方 fixture 每題重設）。
# 要驗寫入的測試自己給假的 session factory（tests/test_usage_events.py）。
os.environ["USAGE_EVENTS_ENABLED"] = "0"


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


@pytest.fixture(scope="session", autouse=True)
def _fake_accounts():
    """整個 session 把 `web.deps.accounts` 換成記憶體假帳號庫（`tests/fake_accounts.py`）。

    個別帳號上線後登入要查 DB；測試不連 DB，而既有約三十支測試以 tester／testpass 走
    `/login`。預設這份放一個同名的**管理員**（待複核等管理端點的測試也照樣能打）。
    要驗一般使用者、停用、跨使用者隔離的測試用 `fake_accounts.install()` 換上自己的一份。

    session 範圍而非每題：有些測試在 setUpClass 就登入，每題換一份會讓那個 session 失效。
    **刻意不主動 import** `web.deps`（理由同下方的監控快取）；測試模組在收集階段就 import
    了 web.server，所以這裡跑的時候它已經在 sys.modules 裡。真的帳號 SQL 由
    `tests/test_accounts_db.py` 對 PostgreSQL 驗。
    """
    mod = sys.modules.get("web.deps")
    if mod is None:
        yield None
        return
    from fake_accounts import default_accounts

    orig = mod.accounts
    mod.accounts = default_accounts()
    try:
        yield mod.accounts
    finally:
        mod.accounts = orig


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
def _reset_v2_state():
    """Admin v2 的兩個模組級狀態每題前後各清一次：用量累加器（`app.services.usage_events`）與功能旗標的
    DB 覆寫快取（`app.services.feature_flags`）。旗標快取會影響行為：A 測試的假覆寫不能漏到 B。
    同樣不主動 import——模組沒載入就沒有狀態要清。"""

    def _clear() -> None:
        usage = sys.modules.get("app.services.usage_events")
        if usage is not None:
            usage.reset()
        flags = sys.modules.get("app.services.feature_flags")
        if flags is not None:
            flags.invalidate()
        security = sys.modules.get("app.services.security_ops")  # 限流彙總的記憶體計數、稽核鏈驗證快取
        if security is not None:
            security.reset()

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _stub_feature_flag_db():
    """功能旗標的讀取（`app.services.feature_flags` 沒給 session_factory 時用的 `SessionFactory`）預設讀成「沒有
    任何覆寫」：問答、上傳等既有測試在 registry 預設下跑（＝只看環境變數，與 v1 相同），而不是去讀
    `REPORT_MARK_DB_URL` 指的庫——本機預設是生產庫，那裡的覆寫會讓測試結果跟著生產設定變。
    驗旗標本身的測試明確傳 session_factory，或在範圍內用 `fake_feature_flags.flag_rows()` 換成指定的覆寫。
    旗標模組很輕（只依賴 app.config 與 app.services.db），這裡直接 import。"""
    from fake_feature_flags import NoRowsSession

    from app.services import feature_flags as flags

    orig = flags.SessionFactory
    flags.SessionFactory = NoRowsSession
    try:
        yield
    finally:
        flags.SessionFactory = orig


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


@pytest.fixture(autouse=True)
def _stub_ask_ownership_checks():
    """`/api/ask`、`/api/ask/stop` 在串流前會查「參照的對話串／回答是不是別人的」
    （`deps.conversation_is_foreign`、`deps.qa_is_foreign`，真的實作會連 DB）。

    測試不連 DB：預設 stub 成「不是別人的」，帶 conversation_id／regenerate_of 的既有
    測試照舊走到 answer_question。要驗 404 的測試在自己範圍內覆寫（tests/test_qa_isolation.py）。
    同樣不主動 import——web.deps 沒載入就沒有東西要 stub。
    """
    mod = sys.modules.get("web.deps")
    if mod is None:
        yield
        return

    async def _not_foreign(*_a, **_k):
        return False

    orig = (mod.conversation_is_foreign, mod.qa_is_foreign)
    mod.conversation_is_foreign = _not_foreign
    mod.qa_is_foreign = _not_foreign
    try:
        yield
    finally:
        mod.conversation_is_foreign, mod.qa_is_foreign = orig


@pytest.fixture(autouse=True)
def _stub_quota_charge():
    """問答與匯出每次都會計配額（`deps.quota.charge`，真的實作寫 `usage_counter`）。測試不連 DB：預設換成
    「放行、不計數」的代理，其餘屬性照舊轉給真的模組。要驗配額的測試自己把 `deps.quota` 換成假物件
    （tests/test_quota_api.py）。同樣不主動 import——web.deps 沒載入就沒有東西要 stub。"""
    mod = sys.modules.get("web.deps")
    if mod is None:
        yield
        return
    real = mod.quota

    class _AllowAll:
        async def charge(self, user, kind, **_kw):
            return real.Decision(kind=kind, allowed=True)

        def __getattr__(self, name):
            return getattr(real, name)

    mod.quota = _AllowAll()
    try:
        yield
    finally:
        mod.quota = real
