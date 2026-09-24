"""`/healthz/llm` 的判定：DeepSeek 帳號此刻能不能用（餘額、金鑰、連線）。

## 為什麼需要它

Claude CLI 已於 2026-09-23 永久放棄（OAuth 過期、D-C），問答與批次的 LLM 段全部走 DeepSeek，沒有
備援。帳號層級的失效（402 餘額不足、401 金鑰失效、連不上）會讓問答每一題失敗、sync 整批 rc=2，
而 `/healthz` 只探 DB 照樣綠。這支判定讓本機探針（`scripts/check_web_health.sh` 退出碼 7）在幾分鐘
內知道，並在**用罄之前**就告警（餘額低於門檻）。

## 狀態（`/healthz/llm` 只回 `{"llm": state}`，**不回任何金額**）

| state | HTTP | 意義 |
|---|---|---|
| `disabled` | 200 | 沒有線上任務解析到 DeepSeek，而且沒有金鑰 |
| `unknown` | 200 | 還沒有完成過任何一次查詢（或第一次連不上） |
| `ok` | 200 | 預算幣別的餘額 ≥ 門檻，`is_available=true` |
| `low` | 503 | 餘額 > 0 但低於門檻（預設 ¥70，約兩週用量）：還能用，但要儲值 |
| `exhausted` | 503 | 餘額查詢回 402、`is_available=false`、餘額 ≤ 0，或本行程的真實請求收過 402（見下） |
| `auth_failed` | 503 | 餘額查詢回 401；或線上任務走 DeepSeek 卻沒有金鑰 |
| `unreachable` | 503 | 連續 2 次連不上（網路、逾時、429／5xx）；單次不算 |
| `indeterminate` | 503 | 判斷不出來：缺預算幣別那一筆、其他幣別有非零餘額（幣別不符）、金額不是數字、 |
| | | 同幣別重複、端點設定錯（404 等）、回應不是 JSON 物件 |

- **幣別只看一筆**（D-O）：`balance_infos` 的順序不固定（9/24 實測 USD、CNY 兩筆會對調），一律依
  `currency` 取值，不靠索引。其他幣別出現非零餘額代表帳戶計價與假設不符，這時「CNY 夠不夠」回答不了
  帳戶夠不夠，所以不猜、直接告警。
- **M15（審查）**：只有「有線上任務解析到 DeepSeek 模型」時上面的 503 才生效；否則回 200，state 加
  `_unused` 後綴（例如 `exhausted_unused`），只進日誌。理由：線上沒用到 DeepSeek 時，問答不受影響，
  讓它開事件等於用一則不實的「問答停擺」蓋掉其他故障。仍然照常查詢（只要有金鑰），批次用得到。
- **402 閂鎖**：本行程任何一次真實請求收到 402（`llm_http.last_quota_at`），就立刻回 `exhausted`，
  直到一次**開始於那次 402 之後**、成功（HTTP 200）且判定不是 exhausted 的餘額查詢才解除。閂鎖期間
  快取以失敗 TTL 計，所以最慢 60 秒就會重查。401 的結論優先於閂鎖（金鑰壞了是更直接的原因）。

## 快取與有界

- `ok` 快取 600 秒，其餘（含單次連不上）60 秒；每次最多等 4 秒，等不到就回上一次的結論（查詢在背景
  跑完會自己更新）。探針每 ~130 秒問一次，一天約 700 次查詢以內（官方查詢不扣費，9/24 實測）。
- **已知延遲（審查 L15）**：批次行程碰到 402 時，web 要等自己的快取到期（ok 最長 600 秒）才看得到。
  刻意不做跨行程的標記檔：批次的 402 本來就以整批 rc=2 → `OnFailure` 立即告警；而標記檔要落在 repo 根
  的 `data/`——這台機器上那就是部署目錄，任何觸發 402 的測試都會把生產 web 翻成 exhausted 並開事件
  （`LLM_BREAKER_FILE`／`LLM_USAGE_LOG` 都得在 conftest 另外封住，就是同一類風險）。低餘額門檻會在
  用罄前幾週就告警，402 正常情況不該是第一個訊號。
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from app.services import llm_http, llm_models

logger = logging.getLogger(__name__)

DISABLED = "disabled"
UNKNOWN = "unknown"
OK = "ok"
LOW = "low"
EXHAUSTED = "exhausted"
AUTH_FAILED = "auth_failed"
UNREACHABLE = "unreachable"
INDETERMINATE = "indeterminate"
# 回 503 的狀態（只在線上任務用到 DeepSeek 時；否則加 `_unused` 回 200）
FAILING = frozenset({LOW, EXHAUSTED, AUTH_FAILED, UNREACHABLE, INDETERMINATE})
UNUSED_SUFFIX = "_unused"

OK_TTL = 600.0
FAIL_TTL = 60.0
WAIT = 4.0
# 查詢自己的期限比 WAIT 短：逾時的那一次在同一個請求裡就有結論（計入連續失敗），而不是等 WAIT 先到、
# 回上一次的結論，再讓背景的查詢事後才更新。
FETCH_TIMEOUT = 3.5
FAILS_TO_UNREACHABLE = 2

# 連不上這一類：單次不算、連續 FAILS_TO_UNREACHABLE 次才翻 unreachable
_TRANSIENT = frozenset({llm_http.NETWORK, llm_http.TIMEOUT, llm_http.OVERLOADED})


class _Snapshot(NamedTuple):
    state: str
    consecutive_failures: int
    expires_at: float   # monotonic；到期就重查
    checked_at: float   # 產生這個結論的那次查詢開始的時刻（monotonic；0＝從未查過）
    cleared_at: float   # 最近一次「成功且不是 exhausted」的查詢開始的時刻：402 閂鎖以它為界
    detail: str         # 只進日誌


_INITIAL = _Snapshot(UNKNOWN, 0, 0.0, 0.0, 0.0, "")
_snap: _Snapshot = _INITIAL
_task: asyncio.Task | None = None
_last_reported: str | None = None


def reset() -> None:
    """僅供測試：回到剛啟動的狀態。"""
    global _snap, _task, _last_reported
    _snap, _task, _last_reported = _INITIAL, None, None


def judge_balance(body: dict, *, currency: str, floor: float) -> tuple[str, str]:
    """HTTP 200 的餘額本體 → (state, 給日誌的原因)。純函式。

    判斷順序：`is_available=false` → exhausted（帳戶層級說不能用，金額不必看）；結構或金額讀不懂、缺預算
    幣別、其他幣別非零 → indeterminate；預算幣別 ≤ 0 → exhausted；< 門檻 → low；其餘 ok。
    """
    available = body.get("is_available")
    if available is False:
        return EXHAUSTED, "is_available=false"
    if available is not True:
        return INDETERMINATE, f"is_available 不是布林值：{available!r}"
    infos = body.get("balance_infos")
    if not isinstance(infos, list):
        return INDETERMINATE, "缺 balance_infos"
    totals: dict[str, Decimal] = {}
    for info in infos:
        if not isinstance(info, dict) or not isinstance(info.get("currency"), str):
            return INDETERMINATE, "balance_infos 有無法辨識的項目"
        cur = info["currency"].strip().upper()
        raw = info.get("total_balance")
        try:
            amount = Decimal(str(raw).strip()) if isinstance(raw, (str, int, float)) else None
        except InvalidOperation:
            amount = None
        if amount is None or not amount.is_finite():
            return INDETERMINATE, f"{cur} 的 total_balance 不是數字：{raw!r}"
        if cur in totals:
            return INDETERMINATE, f"{cur} 出現不只一筆"
        totals[cur] = amount
    if currency not in totals:
        return INDETERMINATE, f"缺 {currency} 那一筆（幣別：{'、'.join(sorted(totals)) or '無'}）"
    others = sorted(c for c, v in totals.items() if c != currency and v != 0)
    if others:
        return INDETERMINATE, f"{'、'.join(others)} 有非零餘額，與預算幣別 {currency} 不符"
    amount = totals[currency]
    if amount <= 0:
        return EXHAUSTED, f"{currency} 餘額 {amount} ≤ 0"
    if amount < Decimal(str(floor)):
        return LOW, f"{currency} 餘額 {amount} 低於門檻 {floor:g}"
    return OK, f"{currency} 餘額 {amount}"


async def _refresh(currency: str, floor: float) -> None:
    """查一次並更新 `_snap`。永不拋例外（它是 fire-and-forget 的 task）。"""
    global _snap
    started = time.monotonic()
    try:
        res = await llm_http.fetch_balance(FETCH_TIMEOUT)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # fetch_balance 本身不拋；這裡只是讓 task 永遠會更新狀態
        res = llm_http.BalanceResult(llm_http.OTHER, f"{type(exc).__name__}: {exc}")
    prev = _snap
    fails = 0
    cleared = prev.cleared_at
    if res.kind is None:
        state, detail = judge_balance(res.body or {}, currency=currency, floor=floor)
        if state != EXHAUSTED:
            cleared = started
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
    ttl = OK_TTL if res.kind is None and state == OK else FAIL_TTL
    _snap = _Snapshot(state, fails, time.monotonic() + ttl, started, cleared, detail)


def online_uses_http(env=None) -> bool:
    """web 行程的線上任務有沒有任何一個解析到 DeepSeek 白名單模型。"""
    return any(llm_models.is_http_model(m) for m in llm_models.resolve_all(llm_models.ONLINE_TASKS, env).values())


async def _account_state(*, online: bool, currency: str, floor: float) -> tuple[str, str]:
    """未套 M15 的原始狀態與給日誌的原因。"""
    global _task
    if not llm_http.api_key_configured():
        return (AUTH_FAILED, "線上任務走 DeepSeek 但 DEEPSEEK_API_KEY 為空") if online else (DISABLED, "")
    now = time.monotonic()
    latched = llm_http.last_quota_at() > _snap.cleared_at
    if now >= _snap.expires_at or (latched and now >= _snap.checked_at + FAIL_TTL):
        # 換過 event loop（測試每個 TestClient 一個 loop）時舊 task 永遠不會完成，不能沿用
        if _task is None or _task.done() or _task.get_loop() is not asyncio.get_running_loop():
            _task = asyncio.create_task(_refresh(currency, floor), name="healthz-llm-balance")
        try:
            await asyncio.wait_for(asyncio.shield(_task), timeout=WAIT)
        except asyncio.TimeoutError:
            pass
    if llm_http.last_quota_at() > _snap.cleared_at and _snap.state != AUTH_FAILED:
        return EXHAUSTED, "本行程的請求收到 402，尚無之後開始的成功餘額查詢"
    return _snap.state, _snap.detail


async def report(*, currency: str, floor: float) -> tuple[str, int]:
    """(回應的 state, HTTP 狀態碼)。狀態改變時記一行 WARNING（含原因與金額；回應本身不含）。"""
    global _last_reported
    online = online_uses_http()
    state, detail = await _account_state(online=online, currency=currency, floor=floor)
    failing = state in FAILING
    if failing and not online:
        state += UNUSED_SUFFIX
    if state != _last_reported:
        _last_reported = state
        logger.warning("healthz LLM 狀態：%s（%s）", state, detail or "-")
    return state, 503 if failing and online else 200
