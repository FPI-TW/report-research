"""用量收集（Admin v2）：記憶體累加器 → 每 60 秒與關機時 upsert 進 `usage_daily`、`usage_counter`、`llm_usage_daily`。

三個來源、一個累加器：

- **usage middleware**（`web/server.py` 的 `UsageMiddleware`，純 ASGI、只計回 200 的請求）呼叫 `record_request`：
  `/api/reading/{hash}`（閱讀頁）、`/api/report/{id}/file`（原檔）、`/api/search`、`/api/ask`。
- **llm_http 的 observer**（web 在 lifespan 註冊 `observe_llm_call`）：線上 DeepSeek 呼叫的 metadata，
  以 `app.request_context.current_user_id()` 歸到人。
- 寫入由 `UsageFlusher`（lifespan 啟停）定期呼叫 `flush`；關機時再 flush 一次。

**隱私規則（使用者定案 2、3、4、6；寫進結構，不靠呼叫端自律）**：

- 只落兩種形狀：「主題×日」（`usage_daily`，**沒有 user_id**）與「人×日×類別」（`usage_counter`，**沒有主題**）。
  **永遠不會有「誰看了哪篇」**：主題與人只在同一個記憶體格子裡相遇一次——`usage_daily` 的不重複人數
  （`users`）用行程內的集合去重，集合本身從不落庫，flush 只送集合的大小。
- 搜尋**永不記查詢字串**：middleware 只從 query string 取 `market` 一個鍵，而且只接受 findb 市場代碼
  （`tagging.MARKETS`）；其餘任何值（含 `q`）連讀都不讀進累加器。
- LLM 只記 metadata（任務、模型、成敗、token、耗時），observer 介面上就沒有 prompt 與回答（`llm_http.call_metadata`）。
- 已刪除帳號（`app_user.deleted_at`）的個人資料在 upsert 時以 JOIN 濾掉：刪帳前一刻還在累加器裡的計數不會讓
  `usage_counter`／`llm_usage_daily` 在刪帳後復活。

**有上限**：三種格子合計最多 `USAGE_EVENTS_MAX_KEYS` 個相異鍵；滿了之後**新的鍵**一律丟棄並計數（`dropped`，
flush 時以 WARNING 說出來），已存在的鍵照常累加。flush 失敗時把那一批放回累加器（同樣受上限約束）下次再試；
DB 掛掉時 session 查驗早就回 503，這裡不會無限堆積。

**分工**：`usage_counter` 的 `ask`／`export`／`upload` 三類由配額服務（Quota lane）以單句原子 SQL 遞增，
這裡**不寫**那三類（否則重複計數）；middleware 只寫 `reading`／`report_file`／`search`。`usage_daily` 的
`ask` 只有總量（主題是空字串）。

日期一律台北時間的日曆日（與上傳配額、`llm_usage.py` 一致）。`users` 是下限：web 重啟後集合歸零，
以 GREATEST 合併（`usage_daily.users` 的註解）。
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from app import request_context
from app.services.db import SessionFactory

logger = logging.getLogger(__name__)

TZ = timezone(timedelta(hours=8))

# usage_daily.kind 與 usage_counter.kind 的詞彙（DB 只限形狀，唯一定義在這裡）。
KIND_READING = "reading"
KIND_REPORT_FILE = "report_file"
KIND_SEARCH = "search"
KIND_ASK = "ask"
DAILY_KINDS: frozenset[str] = frozenset({KIND_READING, KIND_REPORT_FILE, KIND_SEARCH, KIND_ASK})
# 本模組寫進 usage_counter 的類別。ask／export／upload 由配額服務寫（QUOTA_KINDS），不在這裡。
COUNTER_KINDS: frozenset[str] = frozenset({KIND_READING, KIND_REPORT_FILE, KIND_SEARCH})
QUOTA_KINDS: frozenset[str] = frozenset({"ask", "export", "upload"})
ALL_COUNTER_KINDS: frozenset[str] = COUNTER_KINDS | QUOTA_KINDS

_NIL_UUID = "00000000-0000-0000-0000-000000000000"
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_MAX_SUBJECT = 128
_MAX_LABEL = 64
# 一個主題格子的去重集合上限（使用者只有十幾人；只是保險）。
_MAX_USERS_PER_CELL = 10_000


def today() -> date:
    return datetime.now(TZ).date()


def _uuid_or_none(value) -> str | None:
    return str(value).lower() if value is not None and _UUID_RE.fullmatch(str(value)) else None


def _label(value) -> str:
    v = str(value or "").strip()[:_MAX_LABEL]
    return v or "(未標示)"


def _int(value) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


@dataclass
class _DailyCell:
    hits: int = 0  # 尚未 flush 的請求數
    users: set[str] = field(default_factory=set)  # 行程內去重；**從不落庫**，只送大小


@dataclass
class _LlmCell:
    calls: int = 0
    failures: int = 0
    hit: int = 0
    miss: int = 0
    completion: int = 0
    reasoning: int = 0
    without_tokens: int = 0
    total_ms: int = 0

    def add(self, other: _LlmCell) -> None:
        for name in ("calls", "failures", "hit", "miss", "completion", "reasoning", "without_tokens", "total_ms"):
            setattr(self, name, getattr(self, name) + getattr(other, name))


@dataclass
class Batch:
    """一次 flush 要寫的東西（`UsageAccumulator.drain` 的結果）。"""

    daily: list[tuple[date, str, str, int, int]] = field(default_factory=list)  # (day, kind, subject, hits, users)
    counters: list[tuple[str, date, str, int]] = field(default_factory=list)  # (user_id, day, kind, count)
    llm: list[tuple[date, str | None, str, str, _LlmCell]] = field(default_factory=list)
    dropped: int = 0

    def __bool__(self) -> bool:
        return bool(self.daily or self.counters or self.llm)


class UsageAccumulator:
    """執行緒安全的記憶體累加器（observer 可能在 `asyncio.to_thread` 的執行緒裡被呼叫）。"""

    def __init__(self, max_keys: int = 50_000) -> None:
        self.max_keys = max(1, int(max_keys))
        self._lock = threading.Lock()
        self._daily: dict[tuple[date, str, str], _DailyCell] = {}
        self._counters: dict[tuple[str, date, str], int] = {}
        self._llm: dict[tuple[date, str | None, str, str], _LlmCell] = {}
        self.dropped = 0  # 累計；drain 時回報增量
        self._reported_dropped = 0

    def _size(self) -> int:
        return len(self._daily) + len(self._counters) + len(self._llm)

    def _room(self) -> bool:
        if self._size() < self.max_keys:
            return True
        self.dropped += 1
        return False

    def record_hit(self, kind: str, subject: str | None, user_id: str | None, *, day: date | None = None) -> None:
        """一次成功的請求：主題格子（含 subject=''的總量格子）＋（有登入身分且類別屬於本模組時）個人計數。"""
        if kind not in DAILY_KINDS:
            raise ValueError(f"未知的用量類別：{kind!r}")
        d = day or today()
        uid = _uuid_or_none(user_id)
        subjects = [""]
        sub = (subject or "")[:_MAX_SUBJECT]
        if sub:
            subjects.append(sub)
        with self._lock:
            for s in subjects:
                key = (d, kind, s)
                cell = self._daily.get(key)
                if cell is None:
                    if not self._room():
                        continue
                    cell = self._daily[key] = _DailyCell()
                cell.hits += 1
                if uid is not None and len(cell.users) < _MAX_USERS_PER_CELL:
                    cell.users.add(uid)
            if uid is not None and kind in COUNTER_KINDS:
                ckey = (uid, d, kind)
                if ckey in self._counters or self._room():
                    self._counters[ckey] = self._counters.get(ckey, 0) + 1

    def record_llm(self, meta: dict, user_id: str | None, *, day: date | None = None) -> None:
        """一次 LLM 呼叫的 metadata（`llm_http.call_metadata` 的形狀）。只取計數與 token，其餘鍵不看。"""
        d = day or today()
        key = (d, _uuid_or_none(user_id), _label(meta.get("task")), _label(meta.get("model")))
        tokens = meta.get("tokens")
        add = _LlmCell(calls=1, failures=1 if meta.get("kind") else 0, total_ms=_int(meta.get("total_ms")))
        if isinstance(tokens, dict):
            add.hit, add.miss = _int(tokens.get("hit")), _int(tokens.get("miss"))
            add.completion, add.reasoning = _int(tokens.get("completion")), _int(tokens.get("reasoning"))
        else:
            add.without_tokens = 1
        with self._lock:
            cell = self._llm.get(key)
            if cell is None:
                if not self._room():
                    return
                cell = self._llm[key] = _LlmCell()
            cell.add(add)

    def drain(self) -> Batch:
        """取出所有尚未寫入的增量。主題格子的去重集合留著（同一天後續的 users 才會遞增），過了那天就釋放。"""
        cutoff = today()
        with self._lock:
            batch = Batch()
            for (d, kind, subject), cell in list(self._daily.items()):
                if cell.hits:
                    batch.daily.append((d, kind, subject, cell.hits, len(cell.users)))
                    cell.hits = 0
                if d < cutoff:
                    del self._daily[(d, kind, subject)]
            batch.counters = [(uid, d, kind, n) for (uid, d, kind), n in self._counters.items() if n]
            self._counters.clear()
            batch.llm = [(d, uid, task, model, cell) for (d, uid, task, model), cell in self._llm.items()]
            self._llm.clear()
            batch.dropped = self.dropped - self._reported_dropped
            self._reported_dropped = self.dropped
        return batch

    def restore(self, batch: Batch) -> None:
        """flush 失敗：把那一批放回去（受上限約束；放不回去的計入 dropped）。"""
        with self._lock:
            for d, kind, subject, hits, _users in batch.daily:
                cell = self._daily.get((d, kind, subject))
                if cell is None:
                    if not self._room():
                        continue
                    cell = self._daily[(d, kind, subject)] = _DailyCell()
                cell.hits += hits
            for uid, d, kind, n in batch.counters:
                key = (uid, d, kind)
                if key in self._counters or self._room():
                    self._counters[key] = self._counters.get(key, 0) + n
            for d, uid, task, model, add in batch.llm:
                key = (d, uid, task, model)
                cell = self._llm.get(key)
                if cell is None:
                    if not self._room():
                        continue
                    cell = self._llm[key] = _LlmCell()
                cell.add(add)

    def size(self) -> int:
        with self._lock:
            return self._size()


# 行程內唯一的累加器（lifespan 已保證單一 worker）。測試以 reset() 換一份新的（tests/conftest.py 每題前後）。
_ACC = UsageAccumulator()


def accumulator() -> UsageAccumulator:
    return _ACC


def reset(max_keys: int | None = None) -> UsageAccumulator:
    global _ACC
    _ACC = UsageAccumulator(max_keys if max_keys is not None else _ACC.max_keys)
    return _ACC


def configure(max_keys: int) -> None:
    """lifespan 依 `USAGE_EVENTS_MAX_KEYS` 設定上限（不清掉已累加的內容）。"""
    _ACC.max_keys = max(1, int(max_keys))


def record_request(kind: str, subject: str | None = None, user_id: str | None = None) -> None:
    """middleware 的入口：user_id 省略時取 `request_context.current_user_id()`。"""
    _ACC.record_hit(kind, subject, user_id if user_id is not None else request_context.current_user_id())


def observe_llm_call(meta: dict) -> None:
    """`llm_http.add_observer` 註冊的函式：線上 LLM 呼叫歸到這個請求的使用者（沒有就歸 NULL）。"""
    _ACC.record_llm(meta, request_context.current_user_id())


# ── 寫入 ────────────────────────────────────────────────────────────────────

_DAILY_SQL = """
INSERT INTO research.usage_daily (day, kind, subject, hits, users)
SELECT * FROM unnest(CAST(:days AS date[]), CAST(:kinds AS text[]), CAST(:subjects AS text[]),
                     CAST(:hits AS bigint[]), CAST(:users AS integer[]))
ON CONFLICT (day, kind, subject) DO UPDATE
SET hits = research.usage_daily.hits + EXCLUDED.hits,
    users = GREATEST(research.usage_daily.users, EXCLUDED.users),
    updated_at = now()
"""

# 已刪除帳號的計數以 JOIN 濾掉（模組 docstring）。
_COUNTER_SQL = """
INSERT INTO research.usage_counter (user_id, day, kind, count)
SELECT x.user_id, x.day, x.kind, x.count
FROM unnest(CAST(:uids AS uuid[]), CAST(:days AS date[]), CAST(:kinds AS text[]), CAST(:counts AS integer[]))
     AS x(user_id, day, kind, count)
JOIN research.app_user a ON a.id = x.user_id AND a.deleted_at IS NULL
ON CONFLICT (user_id, day, kind) DO UPDATE
SET count = research.usage_counter.count + EXCLUDED.count, updated_at = now()
"""

_LLM_SQL = f"""
INSERT INTO research.llm_usage_daily (day, user_id, task, model, calls, failures, prompt_hit_tokens,
    prompt_miss_tokens, completion_tokens, reasoning_tokens, calls_without_tokens, total_ms)
SELECT x.day, x.user_id, x.task, x.model, x.calls, x.failures, x.hit, x.miss, x.completion, x.reasoning,
       x.without_tokens, x.total_ms
FROM unnest(CAST(:days AS date[]), CAST(:uids AS uuid[]), CAST(:tasks AS text[]), CAST(:models AS text[]),
            CAST(:calls AS integer[]), CAST(:failures AS integer[]), CAST(:hit AS bigint[]), CAST(:miss AS bigint[]),
            CAST(:completion AS bigint[]), CAST(:reasoning AS bigint[]), CAST(:without AS integer[]),
            CAST(:total_ms AS bigint[]))
     AS x(day, user_id, task, model, calls, failures, hit, miss, completion, reasoning, without_tokens, total_ms)
WHERE x.user_id IS NULL
   OR EXISTS (SELECT 1 FROM research.app_user a WHERE a.id = x.user_id AND a.deleted_at IS NULL)
ON CONFLICT (day, (COALESCE(user_id, '{_NIL_UUID}'::uuid)), task, model) DO UPDATE
SET calls = research.llm_usage_daily.calls + EXCLUDED.calls,
    failures = research.llm_usage_daily.failures + EXCLUDED.failures,
    prompt_hit_tokens = research.llm_usage_daily.prompt_hit_tokens + EXCLUDED.prompt_hit_tokens,
    prompt_miss_tokens = research.llm_usage_daily.prompt_miss_tokens + EXCLUDED.prompt_miss_tokens,
    completion_tokens = research.llm_usage_daily.completion_tokens + EXCLUDED.completion_tokens,
    reasoning_tokens = research.llm_usage_daily.reasoning_tokens + EXCLUDED.reasoning_tokens,
    calls_without_tokens = research.llm_usage_daily.calls_without_tokens + EXCLUDED.calls_without_tokens,
    total_ms = research.llm_usage_daily.total_ms + EXCLUDED.total_ms,
    updated_at = now()
"""


def batch_params(batch: Batch) -> list[tuple[str, dict]]:
    """把一批增量轉成 (SQL, 參數) 清單（純函式；測試據此斷言寫入參數裡沒有搜尋字串）。"""
    out: list[tuple[str, dict]] = []
    if batch.daily:
        out.append((_DAILY_SQL, {
            "days": [r[0] for r in batch.daily], "kinds": [r[1] for r in batch.daily],
            "subjects": [r[2] for r in batch.daily], "hits": [r[3] for r in batch.daily],
            "users": [r[4] for r in batch.daily],
        }))
    if batch.counters:
        out.append((_COUNTER_SQL, {
            "uids": [r[0] for r in batch.counters], "days": [r[1] for r in batch.counters],
            "kinds": [r[2] for r in batch.counters], "counts": [r[3] for r in batch.counters],
        }))
    if batch.llm:
        cells = [r[4] for r in batch.llm]
        out.append((_LLM_SQL, {
            "days": [r[0] for r in batch.llm], "uids": [r[1] for r in batch.llm],
            "tasks": [r[2] for r in batch.llm], "models": [r[3] for r in batch.llm],
            "calls": [c.calls for c in cells], "failures": [c.failures for c in cells],
            "hit": [c.hit for c in cells], "miss": [c.miss for c in cells],
            "completion": [c.completion for c in cells], "reasoning": [c.reasoning for c in cells],
            "without": [c.without_tokens for c in cells], "total_ms": [c.total_ms for c in cells],
        }))
    return out


async def flush(acc: UsageAccumulator | None = None, *, session_factory=None) -> bool:
    """把累加器的增量寫進 DB（同一筆交易）。成功或沒有東西要寫回 True；失敗把那一批放回去、記 WARNING、回 False。"""
    acc = acc or _ACC
    batch = acc.drain()
    if batch.dropped:
        logger.warning("用量收集：累加器已滿（上限 %d 個鍵），丟棄了 %d 筆新鍵的計數", acc.max_keys, batch.dropped)
    if not batch:
        return True
    factory = session_factory or SessionFactory
    try:
        async with factory() as session:
            for sql, params in batch_params(batch):
                await session.execute(text(sql), params)
            await session.commit()
    except asyncio.CancelledError:
        acc.restore(batch)
        raise
    except Exception:
        acc.restore(batch)
        logger.warning("用量收集：寫入 DB 失敗，下次再試", exc_info=True)
        return False
    return True


class UsageFlusher:
    """每 interval 秒呼叫一次 `flush`；`stop()` 取消背景任務並做最後一次 flush（lifespan 關機時）。"""

    def __init__(self, interval: float = 60.0, *, acc: UsageAccumulator | None = None, session_factory=None) -> None:
        self.interval = max(0.01, float(interval))
        self._acc = acc
        self._factory = session_factory
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="usage-events-flusher")

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.interval)
            await flush(self._acc, session_factory=self._factory)

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        await flush(self._acc, session_factory=self._factory)
