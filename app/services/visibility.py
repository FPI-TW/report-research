"""研報可見性：管理員隱藏／恢復研報（`research.report_visibility`，revision 0004）。

兩件事放在同一個模組，因為它們必須講同一種語言：

1. **讀取路徑的過濾片段**（`visible_report_sql`／`visible_report_id_sql`）。所有面向使用者的
   讀取（混合檢索兩路、檢索頁瀏覽、問答選篇、閱讀頁、相似研報、觀點雷達、總覽、每日簡報的
   來源選取與來源連結、原檔 presign）都經這裡產生的 `NOT EXISTS (...)` 排除被隱藏的研報。
   不另寫第二份條件：哪天可見性多了一種狀態，只改這裡。`tests/test_visibility_guard.py`
   以 AST 掃描那些模組，查 `research.research_report`／`research.report_chunk` 的函式或常數
   沒有呼叫這兩個函式（也不在豁免清單）就紅。
2. **管理端的查詢與寫入**（`list_reports`／`set_visibility`）。`/api/admin/reports*` 呼叫。

刻意的範圍：

- **鍵是 file_hash**，不是 report_id：`store.upsert_report` 重新入庫會換 report_id，旗標若掛在
  report_id 上就會在下一次重新入庫時靜默消失。
- **批次照常處理隱藏的研報**（摘要、標題、摘錄、訊號擷取都不看這張表）：恢復時立即完整
  回來，不必重跑任何批次。代價是隱藏期間批次仍會為它花 LLM 費用——隱藏是少數例外，划算。
- **管理面不過濾**：待複核佇列、監控計數看的是全庫現況。
- 片段寫成 `NOT EXISTS` 而不是 `LEFT JOIN ... IS NULL`：它是可以直接 AND 進任何 WHERE 的
  單一布林運算式，不改 FROM、不影響既有 join 與欄位位置；planner 對小表的 anti-join 走主鍵
  索引，對 HNSW 檢索的計畫沒有影響（實測見 PR 說明）。

bind 參數一律 `CAST(:x AS ...)`，不寫 `:x::type`（見 reading/queries.py 檔頭）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 片段裡的別名與運算式只來自程式內的字面（不是使用者輸入），仍嚴格限制字元：拼進 SQL 的東西
# 不該靠「呼叫端應該不會亂傳」。
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_COLUMN_EXPR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")
_FILE_HASH = re.compile(r"^[0-9a-f]{64}$")

REASON_MAX_CHARS = 500


def visible_report_sql(alias: str = "r") -> str:
    """`alias` 指向 research.research_report 的那個別名 → 「這份研報沒有被隱藏」的布林運算式。"""
    if not _IDENT.match(alias):
        raise ValueError(f"visible_report_sql：不合法的別名 {alias!r}")
    return (
        "NOT EXISTS (SELECT 1 FROM research.report_visibility rvis "
        f"WHERE rvis.file_hash = {alias}.file_hash AND rvis.hidden)"
    )


def visible_report_id_sql(report_id_expr: str) -> str:
    """只有 report_id 可用的查詢（例如只查 report_signal 的簡報變動）→ 同語意的布林運算式。

    多一次 research_report 主鍵查找；只用在沒有 join research_report 的查詢。
    """
    if not _COLUMN_EXPR.match(report_id_expr):
        raise ValueError(f"visible_report_id_sql：不合法的欄位運算式 {report_id_expr!r}")
    return (
        "NOT EXISTS (SELECT 1 FROM research.research_report rvis_r "
        "JOIN research.report_visibility rvis ON rvis.file_hash = rvis_r.file_hash "
        f"WHERE rvis_r.id = {report_id_expr} AND rvis.hidden)"
    )


def valid_file_hash(value: str) -> bool:
    return bool(_FILE_HASH.match(value or ""))


# ── 管理端 ──────────────────────────────────────────────────────────────


class VisibilityError(Exception):
    """管理操作的可預期錯誤；訊息是給管理員看的中文。"""


class ReportNotFoundError(VisibilityError):
    pass


class InvalidReasonError(VisibilityError):
    pass


@dataclass(frozen=True)
class AdminReportRow:
    report_id: str
    file_hash: str
    file_name: str
    title: Optional[str]
    source: Optional[str]
    market: Optional[str]
    report_date: Optional[date]
    created_at: Optional[datetime]
    hidden: bool
    reason: Optional[str]
    updated_by: Optional[str]  # 帳號名；舊列或帳號查不到時 None
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class VisibilityState:
    file_hash: str
    hidden: bool
    reason: Optional[str]
    updated_by: Optional[str]
    updated_at: Optional[datetime]


_LIKE_ESC = str.maketrans({"%": r"\%", "_": r"\_", "\\": r"\\"})

_ADMIN_COLS = (
    "r.id::text, r.file_hash, r.file_name, r.title, r.source, r.market, r.report_date, r.created_at, "
    "COALESCE(v.hidden, false), v.reason, "
    "(SELECT u.username FROM research.app_user u WHERE u.id = v.updated_by), v.updated_at"
)
_ADMIN_FROM = (
    "FROM research.research_report r "
    "LEFT JOIN research.report_visibility v ON v.file_hash = r.file_hash"
)


async def list_reports(
    session: AsyncSession,
    *,
    q: Optional[str] = None,
    hidden: Optional[bool] = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[AdminReportRow]]:
    """管理頁的研報清單：依標題／檔名／券商關鍵字（ILIKE，`%`／`_` 視為字面）與是否隱藏篩選。

    **不套 is_research 條件**：非研究檔本來就不進使用者路徑，但管理員可能正是要找它。
    排序：入庫新→舊，id 當決勝鍵讓翻頁穩定。
    """
    conds: list[str] = []
    params: dict = {}
    if q and q.strip():
        conds.append("(r.title ILIKE :q OR r.file_name ILIKE :q OR r.source ILIKE :q)")
        params["q"] = "%" + q.strip().translate(_LIKE_ESC) + "%"
    if hidden is True:
        conds.append("COALESCE(v.hidden, false)")
    elif hidden is False:
        conds.append("NOT COALESCE(v.hidden, false)")
    where = ("WHERE " + " AND ".join(conds)) if conds else ""
    total = (await session.execute(text(f"SELECT count(*) {_ADMIN_FROM} {where}"), params)).scalar_one()
    rows = (
        await session.execute(
            text(
                f"SELECT {_ADMIN_COLS} {_ADMIN_FROM} {where} "
                "ORDER BY r.created_at DESC, r.id LIMIT :limit OFFSET :offset"
            ),
            {**params, "limit": limit, "offset": offset},
        )
    ).all()
    return int(total), [
        AdminReportRow(
            report_id=r[0], file_hash=r[1], file_name=r[2], title=r[3], source=r[4], market=r[5],
            report_date=r[6], created_at=r[7], hidden=bool(r[8]), reason=r[9], updated_by=r[10],
            updated_at=r[11],
        )
        for r in rows
    ]


async def set_visibility(
    session: AsyncSession,
    file_hash: str,
    *,
    hidden: bool,
    reason: Optional[str],
    actor_id: Optional[str],
) -> VisibilityState:
    """設定一份研報的可見性，並在**同一筆交易**寫稽核。呼叫端負責 commit。

    - 研報必須存在於 research_report（以 file_hash 查）；不存在拋 ReportNotFoundError。
    - 隱藏必須給原因（去頭尾空白後非空）；原因上限 REASON_MAX_CHARS 字。
    - 稽核 detail 只記狀態變化、檔名與「有沒有寫原因」，**不記原因全文與研報內文**
      （與待複核的註記同一條規則）。
    """
    # 函式內 import：本模組的過濾片段被 store／檢索等熱路徑 import，不該為此連帶載入帳號模組。
    from app.services.accounts import record_audit

    if not valid_file_hash(file_hash):
        raise ReportNotFoundError("研報不存在")
    note = (reason or "").strip() or None
    if hidden and not note:
        raise InvalidReasonError("隱藏研報必須填寫原因")
    if note and len(note) > REASON_MAX_CHARS:
        raise InvalidReasonError(f"原因最多 {REASON_MAX_CHARS} 字")
    report = (
        await session.execute(
            text(
                "SELECT r.file_name, v.hidden FROM research.research_report r "
                "LEFT JOIN research.report_visibility v ON v.file_hash = r.file_hash "
                "WHERE r.file_hash = :h"
            ),
            {"h": file_hash},
        )
    ).first()
    if report is None:
        raise ReportNotFoundError("研報不存在")
    file_name, previous = report[0], report[1]
    row = (
        await session.execute(
            text(
                "INSERT INTO research.report_visibility (file_hash, hidden, reason, updated_by) "
                "VALUES (:h, :hidden, :reason, CAST(:actor AS uuid)) "
                "ON CONFLICT (file_hash) DO UPDATE SET hidden = EXCLUDED.hidden, reason = EXCLUDED.reason, "
                "updated_by = EXCLUDED.updated_by, updated_at = now() "
                "RETURNING updated_at, "
                "(SELECT u.username FROM research.app_user u WHERE u.id = CAST(:actor AS uuid))"
            ),
            {"h": file_hash, "hidden": hidden, "reason": note, "actor": actor_id},
        )
    ).first()
    await record_audit(
        session, actor_id=actor_id, action="report.hide" if hidden else "report.restore",
        target_type="report", target_id=file_hash,
        detail={
            "hidden": hidden,
            "previous_hidden": bool(previous) if previous is not None else False,
            "has_reason": note is not None,
            "file_name": file_name,
        },
    )
    return VisibilityState(file_hash=file_hash, hidden=hidden, reason=note, updated_by=row[1], updated_at=row[0])
