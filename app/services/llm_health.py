"""`/healthz/llm` 的判定：LLM 帳號此刻能不能用（金鑰、端點、額度）。

## 為什麼需要它

問答與批次的 LLM 段全部走同一個供應商（`LLM_PROVIDER`，`app/services/llm_models.py`），沒有自動備援。
帳號層級的失效（額度用完、401 金鑰失效、連不上）會讓問答每一題失敗、sync 整批 rc=2，而 `/healthz` 只探 DB 照樣綠。
這支判定讓本機探針（`scripts/check_web_health.sh`）在幾分鐘內知道：503 一律是退出碼 8（CRITICAL）。

## 狀態（`/healthz/llm` 只回 `{"llm": state}`）

| state | HTTP | 意義 |
|---|---|---|
| `unknown` | 200 | 還沒有完成過任何一次探測（或第一次連不上） |
| `ok` | 200 | `GET /models` 回 200，或 403 但只缺 Models 讀取權限：端點與金鑰可用 |
| `exhausted` | 503 | 探測回額度用完，或本行程的真實請求碰過額度用完、之後還沒有成功的請求（見下） |
| `auth_failed` | 503 | 探測回 401 或其他 403；或沒有目前 provider 的金鑰 |
| `unreachable` | 503 | 連續 2 次連不上（網路、逾時、429／5xx）；單次不算 |
| `indeterminate` | 503 | 判斷不出來：端點設定錯（404 等）、其他未預期的回應 |
| `misconfigured` | 503 | 問答主答（`ASK_ANSWER` 任務）解析到白名單外的名稱：每題都回「設定有誤」（見下） |

- **沒有餘額可查，所以沒有「快用完」的預警**：OpenAI 與 OpenCode Go 都沒有查餘額的 API，額度用完只有真實請求碰到才
  知道。OpenAI 的預算告警請在 OpenAI 後台設定（`docs/production_resilience.md`）。
- **額度閂鎖**：本行程任何一次真實請求碰到額度用完（`llm_http.last_quota_at`），就立刻回 `exhausted`，直到之後有一次
  **真實請求成功**（`llm_http.last_ok_at`）才解除——`/models` 回 200 說明不了額度回來了沒。401 的結論優先於閂鎖
  （金鑰壞了是更直接的原因）。閂鎖期間快取以失敗 TTL 計。
- **主答設定錯誤＝`misconfigured`（503）**：判定粒度是問答主答（`ASK_ANSWER` 任務，審查 M15）。沒有其他 backend，
  主答解析到白名單外的名稱時 `llm.stream_completion` 對每一題都拋 config 錯誤——問答停擺，跟帳號壞掉一樣要開事件
  （探針 reason `llm_misconfigured`、退出碼 8）。這時**不探端點**、直接回 `misconfigured`：帳號狀態回答不了「問答能不能
  用」。**只看主答**：其他線上任務（首輪路由、續問改寫、查詢規劃、追問建議、忠實度抽查）全部 fail-open，它們解析到
  什麼都不影響這支端點；網搜沒有後端（`llm.py` 對 `allow_web=True` 直接拋 config），同樣不算。要讓線上 LLM 刻意停下來時
  把 `ASK_ANSWER_MODEL` 設成白名單外名稱，這個 503 是預期的（見 `docs/production_resilience.md`）。

## 已知限制

- **web 重啟可能閃一次 RESOLVED**（刻意不修）：狀態只在行程記憶體裡。web 重啟後回到 `unknown`（200），第一次探測若剛好
  碰上單次連不上，仍是 `unknown`（連續 2 次才算 unreachable）→ 探針回 0 → 進行中的 LLM 事件被送出 RESOLVED，下一輪若仍
  連不上才重新 FIRING。刻意不把「重啟後第一次失敗」改回 503：那會讓每一次重啟碰上單次抖動都開一個假事件。
- **批次的額度用完，web 不會知道**：閂鎖只記本行程。批次本來就以整批 rc=2 → `OnFailure` 立即告警；刻意不做跨行程的標記檔
  （要落在 repo 根的 `data/`，任何觸發額度錯誤的測試都會把部署目錄的 web 翻成 exhausted 並開事件）。

## 快取與有界

`ok` 快取 600 秒，其餘（含單次連不上）60 秒；每次最多等 4 秒，等不到就回上一次的結論（探測在背景跑完會自己更新）。
探針每 ~130 秒問一次，`GET /models` 不計費。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import NamedTuple

from app.services import llm_http, llm_models

logger = logging.getLogger(__name__)

UNKNOWN = "unknown"
OK = "ok"
EXHAUSTED = "exhausted"
AUTH_FAILED = "auth_failed"
UNREACHABLE = "unreachable"
INDETERMINATE = "indeterminate"
MISCONFIGURED = "misconfigured"
# 回 503 的狀態（探針退出碼 8）
FAILING = frozenset({EXHAUSTED, AUTH_FAILED, UNREACHABLE, INDETERMINATE, MISCONFIGURED})

OK_TTL = 600.0
FAIL_TTL = 60.0
# 三個期限必須嚴格遞增：FETCH_TIMEOUT < WAIT < 探針的 curl `HEALTH_TIMEOUT`（5 秒）。
# WAIT 比探針短，這支端點才一定在探針放棄之前回答（否則 curl 逾時＝判不出來、不開事件，停擺被靜默）；
# 探測自己的期限比 WAIT 短，逾時的那一次在同一個請求裡就有結論（計入連續失敗），而不是等 WAIT 先到、
# 回上一次的結論，再讓背景的探測事後才更新。tests/test_healthz_llm.py 釘住這個關係。
WAIT = 4.0
FETCH_TIMEOUT = 3.5
FAILS_TO_UNREACHABLE = 2

# 連不上這一類：單次不算、連續 FAILS_TO_UNREACHABLE 次才翻 unreachable
_TRANSIENT = frozenset({llm_http.NETWORK, llm_http.TIMEOUT, llm_http.OVERLOADED})


class _Snapshot(NamedTuple):
    state: str
    consecutive_failures: int
    expires_at: float   # monotonic；到期就重查
    checked_at: float   # 產生這個結論的那次探測開始的時刻（monotonic；0＝從未查過）
    detail: str         # 只進日誌


_INITIAL = _Snapshot(UNKNOWN, 0, 0.0, 0.0, "")
_snap: _Snapshot = _INITIAL
_task: asyncio.Task | None = None
_last_reported: str | None = None


def reset() -> None:
    """僅供測試：回到剛啟動的狀態。"""
    global _snap, _task, _last_reported
    _snap, _task, _last_reported = _INITIAL, None, None


def _quota_latched() -> bool:
    return llm_http.last_quota_at() > llm_http.last_ok_at()


async def _refresh() -> None:
    """探一次並更新 `_snap`。永不拋例外（它是 fire-and-forget 的 task）。"""
    global _snap
    started = time.monotonic()
    try:
        res = await llm_http.probe_endpoint(FETCH_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # probe_endpoint 本身不拋；這裡只是讓 task 永遠會更新狀態
        res = llm_http.ProbeResult(llm_http.OTHER, f"{type(exc).__name__}: {exc}")
    prev = _snap
    fails = 0
    if res.kind is None:
        state, detail = OK, res.detail or f"{llm_http.provider_label()} 端點可用"
    elif res.kind == llm_http.AUTH:
        state, detail = AUTH_FAILED, res.detail
    elif res.kind == llm_http.QUOTA:
        state, detail = EXHAUSTED, res.detail
    elif res.kind in _TRANSIENT:
        fails = prev.consecutive_failures + 1
        state = UNREACHABLE if fails >= FAILS_TO_UNREACHABLE else prev.state
        detail = f"{res.detail}（連續第 {fails} 次）"
    else:
        state, detail = INDETERMINATE, res.detail
    ttl = OK_TTL if res.kind is None else FAIL_TTL
    _snap = _Snapshot(state, fails, time.monotonic() + ttl, started, detail)


# 問答停擺與否只取決於這些任務（審查 M15 的判定粒度）：主答。其餘線上任務都 fail-open，見模組 docstring。
ASK_CRITICAL_TASKS: tuple[str, ...] = (llm_models.TASK_ASK_ANSWER,)


def misconfigured_tasks(env=None) -> dict[str, str]:
    """解析到白名單外名稱的關鍵任務 → 那個名稱（空＝主答設定正常）。其餘 fail-open 的線上任務不算。"""
    resolved = llm_models.resolve_all(ASK_CRITICAL_TASKS, env)
    return {t: m for t, m in resolved.items() if not llm_models.is_http_model(m)}


async def _account_state() -> tuple[str, str]:
    """帳號狀態與給日誌的原因（主答設定正常時才會問到這裡）。"""
    global _task
    if not llm_http.api_key_configured():
        return AUTH_FAILED, f"問答主答走 {llm_http.provider_label()} 但 {llm_models.key_env()} 為空"
    now = time.monotonic()
    if now >= _snap.expires_at or (_quota_latched() and now >= _snap.checked_at + FAIL_TTL):
        # 換過 event loop（測試每個 TestClient 一個 loop）時舊 task 永遠不會完成，不能沿用
        if _task is None or _task.done() or _task.get_loop() is not asyncio.get_running_loop():
            _task = asyncio.create_task(_refresh(), name="healthz-llm-probe")
        try:
            await asyncio.wait_for(asyncio.shield(_task), timeout=WAIT)
        except asyncio.TimeoutError:
            pass
    if _quota_latched() and _snap.state != AUTH_FAILED:
        return EXHAUSTED, f"本行程的請求碰到 {llm_http.provider_label()} 額度用完，尚無之後成功的請求"
    return _snap.state, _snap.detail


def cached_snapshot() -> dict:
    """上一次探測的**被動**讀取（給 `/api/admin/diagnostics`）：不觸發探測、不回原因字串。

    `state` 是帳號狀態，主答設定錯誤（`misconfigured`）不反映在這裡，看 `ask_uses_http`。鍵名沿用 CLI 時代的說法
    （管理頁與產生的 admin client 在讀）：現在的意思是「問答主答解析到白名單內的模型」，False＝`/healthz/llm` 會回
    `misconfigured`。
    """
    snap = _snap
    return {
        "state": snap.state,
        "key_configured": llm_http.api_key_configured(),
        "ask_uses_http": not misconfigured_tasks(),
        "consecutive_failures": snap.consecutive_failures,
        "last_check_age_s": round(time.monotonic() - snap.checked_at, 1) if snap.checked_at else None,
        "quota_latched": _quota_latched(),
    }


async def report() -> tuple[str, int]:
    """(回應的 state, HTTP 狀態碼)。狀態改變時記一行 WARNING（含原因；回應本身不含）。"""
    global _last_reported
    bad = misconfigured_tasks()
    if bad:
        state = MISCONFIGURED
        detail = "、".join(f"{t}={m!r}" for t, m in bad.items()) + " 不在模型白名單，每題都會回設定有誤"
    else:
        state, detail = await _account_state()
    if state != _last_reported:
        _last_reported = state
        logger.warning("healthz LLM 狀態：%s（%s）", state, detail or "-")
    return state, 503 if state in FAILING else 200
