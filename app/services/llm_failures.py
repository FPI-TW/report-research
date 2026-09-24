"""LLM 批次任務的內容型失敗紀錄（`research.llm_task_failure`）與跳過規則。

存在理由：同一篇研報每輪都失敗、每輪都重打。2026-09 的實測是 6 篇標題的 LLM
回應每次都解析不出可採信的標題，sync 每 3 小時重打一次、每次腳本層再重試 3 次，
一天約 144 次白打——佔當時批次呼叫量的八成以上。訂閱制下它只是噪音；改成按量
計費後，它是會一直長的帳單。

**只記內容型失敗**（`REASONS`）：LLM 確實回了東西，但那個東西不能用——解析不了、
被供應商內容審查擋下、被 `max_tokens` 截斷、內容為空、請求被判為不合法。
環境型失敗（逾時、CLI 非零退出、網路、帳號、額度）**刻意不記**：那不是這篇研報的
問題，記了會讓一次停機把整批研報永久打入跳過名單。

跳過規則（以同一個 model 計）：
- `content_filter`、`truncated`：1 次就跳過。同樣的輸入再送一次，結果不會變。
- 其他原因：連續 `SKIP_AFTER_ROUNDS` 輪才跳過。「連續」由「成功就刪列」保證。
- 換 model 會重試：紀錄的 model 與這次要用的不同時不跳過；再失敗時計數歸 1。
- 手動解除：各批次的 `--retry-blocked`，或直接 DELETE 該列。

**跳過鍵只看 model，不看 prompt 或 `EXTRACTION_VERSION`。** 改了 prompt（或解析規則）
之後，舊 prompt 下累計的失敗仍會把研報擋在外面——**改 prompt 後要加 `--retry-blocked`**
重跑一次。摘錄與訊號的 `--reextract` 隱含 `--retry-blocked`（強制重跑本來就是要重打）。

以 `file_hash` 為鍵，不用 `report_id`：重新 ingest 後 `report_id` 會換，行內標註時
研報也還沒有 `report_id`。

刻意不設 CHECK 約束：既有庫上改 CHECK 是 no-op（見 CLAUDE.md 的 schema 條目），
詞彙改由本模組的常數守住，`record()` 收到未知值直接拋 `ValueError`。

`should_skip()`（Python 端判斷，摘錄與訊號用）與 `skip_clause_sql()`（SQL 端判斷，
標題與摘要用，因為它們掃全表 NULL）必須等價，由 `tests/test_llm_failures.py` 以參數化
案例在 sqlite 上逐一比對。改規則時兩邊一起改。

寫入一律 fail-open：這張表寫不進去時只印警告，不影響批次主流程——它是費用防線，
不是正確性的一部分。表還沒建（部署時漏了 `make schema`）時 `open_recorder()` 回
None、各批次也不套跳過條件，行為退回沒有這張表之前。
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import text

TABLE = "research.llm_task_failure"

TASK_TAG = "tag"
TASK_SUMMARY = "summary"
TASK_TITLE = "title"
TASK_TAKEAWAY = "takeaway"
TASK_SIGNAL = "signal"
TASKS = frozenset({TASK_TAG, TASK_SUMMARY, TASK_TITLE, TASK_TAKEAWAY, TASK_SIGNAL})

UNPARSEABLE = "unparseable"        # 有回應，但解析不出可用的結果
CONTENT_FILTER = "content_filter"  # 供應商內容審查（400 Content Exists Risk、finish_reason=content_filter）
TRUNCATED = "truncated"            # finish_reason=length
EMPTY = "empty"                    # 成功結束卻沒有 content
BAD_REQUEST = "bad_request"        # 其他 400／422，多半是單篇輸入造成
REASONS = frozenset({UNPARSEABLE, CONTENT_FILTER, TRUNCATED, EMPTY, BAD_REQUEST})

SKIP_IMMEDIATELY = frozenset({CONTENT_FILTER, TRUNCATED})
SKIP_AFTER_ROUNDS = 3


@dataclass(frozen=True)
class FailureRecord:
    reason: str
    model: str
    fail_count: int


def should_skip(rec: Optional[FailureRecord], model: str) -> bool:
    """這篇這次要不要跳過。與 `skip_clause_sql()` 等價。"""
    if rec is None or rec.model != model:
        return False
    if rec.reason in SKIP_IMMEDIATELY:
        return True
    return rec.fail_count >= SKIP_AFTER_ROUNDS


def skip_clause_sql(alias: str = "r") -> str:
    """WHERE 用的片段：排除該跳過的研報。參數由 `skip_params()` 提供。

    `alias` 是外層查詢裡 research_report 的別名，要有 `file_hash` 欄。
    IN 清單由本模組的常數產生，不是外部輸入。
    """
    immediate = ", ".join(f"'{r}'" for r in sorted(SKIP_IMMEDIATELY))
    return (
        f"NOT EXISTS (SELECT 1 FROM {TABLE} f "
        f"WHERE f.file_hash = {alias}.file_hash "
        "AND f.task = :llm_skip_task AND f.model = :llm_skip_model "
        f"AND (f.reason IN ({immediate}) OR f.fail_count >= {SKIP_AFTER_ROUNDS}))"
    )


def skip_params(task: str, model: str) -> dict:
    return {"llm_skip_task": task, "llm_skip_model": model}


TABLE_READY_SQL = f"SELECT to_regclass('{TABLE}') IS NOT NULL"

FETCH_SQL = (
    f"SELECT file_hash, reason, model, fail_count FROM {TABLE} "
    "WHERE task = :task AND file_hash = ANY(:hashes)"
)

# 同一個 model 再失敗就累加；換了 model 就從 1 重新算、first_at 也重設——
# 「換 model 會重試」靠的就是這個 CASE。
RECORD_SQL = (
    f"INSERT INTO {TABLE} AS f (file_hash, task, reason, model) "
    "VALUES (:file_hash, :task, :reason, :model) "
    "ON CONFLICT (file_hash, task) DO UPDATE SET "
    "fail_count = CASE WHEN f.model = EXCLUDED.model THEN f.fail_count + 1 ELSE 1 END, "
    "first_at = CASE WHEN f.model = EXCLUDED.model THEN f.first_at ELSE now() END, "
    "reason = EXCLUDED.reason, model = EXCLUDED.model, last_at = now()"
)

CLEAR_SQL = f"DELETE FROM {TABLE} WHERE file_hash = :file_hash AND task = :task"

LIST_SQL = (
    "SELECT f.task, f.reason, f.fail_count, f.model, f.first_at, f.last_at, "
    "       f.file_hash, r.file_name "
    f"FROM {TABLE} f "
    "LEFT JOIN research.research_report r ON r.file_hash = f.file_hash "
    "ORDER BY f.task, f.last_at DESC"
)


def _warn(msg: str) -> None:
    # 批次腳本的 logger 無聲（logging 只在 web/server.py 初始化），用 stderr。
    print(f"[llm_task_failure] {msg}", file=sys.stderr, flush=True)


async def table_ready(session) -> bool:
    return bool((await session.execute(text(TABLE_READY_SQL))).scalar())


async def fetch_failures(session, task: str, hashes: list[str]) -> dict[str, FailureRecord]:
    """file_hash → FailureRecord，只含有紀錄的那些。"""
    if not hashes:
        return {}
    rows = (await session.execute(text(FETCH_SQL), {"task": task, "hashes": list(hashes)})).all()
    return {h: FailureRecord(reason, model, int(n)) for h, reason, model, n in rows}


class FailureRecorder:
    """單一任務、單一 model 的寫入端。每次寫入各開一個短交易，全部 fail-open。"""

    def __init__(self, task: str, model: str, session_factory) -> None:
        if task not in TASKS:
            raise ValueError(f"未知的 task：{task!r}")
        self.task = task
        self.model = model
        self._sf = session_factory
        # 一個 recorder＝一支批次的一輪：同一篇只記一次。400 升級時觸發的研報可能已由單篇路徑
        # 記過（並行的另一篇才觸發升級），main 再記一次會讓「連續 3 輪」少算一輪。
        self._recorded: set[str] = set()

    async def record(self, file_hash: Optional[str], reason: str) -> None:
        if reason not in REASONS:
            raise ValueError(f"未知的 reason：{reason!r}")
        if not file_hash or file_hash in self._recorded:
            return
        self._recorded.add(file_hash)
        params = {"file_hash": file_hash, "task": self.task, "reason": reason, "model": self.model}
        await self._exec(RECORD_SQL, params, "記錄")

    async def clear(self, file_hash: Optional[str]) -> None:
        if not file_hash:
            return
        await self._exec(CLEAR_SQL, {"file_hash": file_hash, "task": self.task}, "清除")

    async def _exec(self, sql: str, params: dict, verb: str) -> None:
        try:
            async with self._sf() as session:
                await session.execute(text(sql), params)
                await session.commit()
        except Exception as exc:  # noqa: BLE001 — fail-open：費用防線不擋主流程
            _warn(f"{verb}失敗（{self.task} {params.get('file_hash')}）：{type(exc).__name__}: {exc}")


async def open_recorder(task: str, model: str, session_factory) -> Optional[FailureRecorder]:
    """表存在就回寫入端；表不存在或查不到就回 None（並警告），批次照舊跑。"""
    try:
        async with session_factory() as session:
            ready = await table_ready(session)
    except Exception as exc:  # noqa: BLE001
        _warn(f"無法確認 {TABLE} 是否存在，本輪不記錄也不跳過：{type(exc).__name__}: {exc}")
        return None
    if not ready:
        _warn(f"{TABLE} 不存在（先跑 make schema），本輪不記錄也不跳過")
        return None
    return FailureRecorder(task, model, session_factory)
