"""研報可見性：管理員隱藏／恢復研報與上傳研報的草稿／發布（`research.report_visibility`，revision 0004、0008）。

兩件事放在同一個模組，因為它們必須講同一種語言：

1. **讀取路徑的過濾片段**（`visible_report_sql`／`visible_report_id_sql`）。所有面向使用者的
   讀取（混合檢索兩路、檢索頁瀏覽、問答選篇、閱讀頁、相似研報、觀點雷達、總覽、每日簡報的
   來源選取與來源連結、原檔 presign）都經這裡產生的 `NOT EXISTS (...)` 排除**被隱藏或尚未發布**
   的研報。不另寫第二份條件：哪天可見性多了一種狀態，只改這裡。`tests/test_visibility_guard.py`
   以 AST 掃描那些模組，查 `research.research_report`／`research.report_chunk` 的函式或常數
   沒有呼叫這兩個函式（也不在豁免清單）就紅。
2. **管理端的查詢與寫入**（`list_reports`／`set_visibility`）。`/api/admin/reports*` 呼叫。

可見＝這份研報**沒有** visibility 列，或那一列 `hidden = false AND publication = 'published'`。

草稿語意（revision 0008，Admin v1.5 上傳管線）：

- 管理員上傳的研報入庫後是**草稿**（`publication='draft'`）：已抽字、標註、嵌入，chunk 也在索引裡，
  只是被這個片段濾掉——對所有使用者讀取路徑等於不存在（閱讀頁與原檔 404），發布當下立即可被檢索，
  不必重嵌。sync 從 NAS 進來的研報沒有這一列，等同已發布（「自動同步直接發布」零改動）。
- 草稿標記以 **file_hash** 為鍵：`store.upsert_report` 重新入庫（先刪後插、換新 report_id）之後
  草稿標記仍在，研報仍不可見。上傳 worker 在 upsert 之前、同一個 session 先寫標記，由 upsert 的
  那次 commit 一起落庫，所以不會有「已入庫但還沒標成草稿」的可見空窗。
- **NAS 送來與現存草稿同 hash 的檔**：sync 判 `skip_exists`（`store.report_exists` 不看可見性），
  維持草稿，不會因此被發布。退回清除時連 visibility 列一起刪，之後 NAS 再送來就照 NAS 規則直接發布。
- 隱藏與發布是兩件事：管理端的隱藏／恢復**拒絕**對草稿操作（`ReportIsDraftError`，API 回 409），
  恢復永遠不會順手發布；發布只走上傳審核（`published_at`／`published_by` 由它寫）。

刻意的範圍：

- **鍵是 file_hash**，不是 report_id：`store.upsert_report` 重新入庫會換 report_id，旗標若掛在
  report_id 上就會在下一次重新入庫時靜默消失。
- **批次照常處理隱藏的研報與草稿**（摘要、標題、摘錄、訊號擷取都不看這張表）：恢復或發布時立即
  完整回來，不必重跑任何批次。代價是隱藏期間批次仍會為它花 LLM 費用——隱藏是少數例外，划算。
- **管理面不過濾**：待複核佇列、監控計數看的是全庫現況（含草稿）。
- 片段寫成 `NOT EXISTS` 而不是 `LEFT JOIN ... IS NULL`：它是可以直接 AND 進任何 WHERE 的
  單一布林運算式，不改 FROM、不影響既有 join 與欄位位置；planner 對小表的 anti-join 走主鍵
  索引，對 HNSW 檢索的計畫沒有影響（實測見 PR 說明；0008 加上 publication 條件後在 devdb 重測，
  dense 仍是 HNSW index scan、字面仍是 trgm GIN bitmap scan）。

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

# report_visibility.publication 的詞彙（與 revision 0008 的 CHECK 逐字一致）。
PUBLICATION_DRAFT = "draft"
PUBLICATION_PUBLISHED = "published"
PUBLICATIONS: tuple[str, ...] = (PUBLICATION_DRAFT, PUBLICATION_PUBLISHED)

# 「這一列讓研報不可見」的條件：被隱藏，或尚未發布。兩個片段共用這一份。
_INVISIBLE_ROW = "(rvis.hidden OR rvis.publication <> 'published')"


def visible_report_sql(alias: str = "r") -> str:
    """`alias` 指向 research.research_report 的那個別名 → 「這份研報沒有被隱藏、且已發布」的布林運算式。"""
    if not _IDENT.match(alias):
        raise ValueError(f"visible_report_sql：不合法的別名 {alias!r}")
    return (
        "NOT EXISTS (SELECT 1 FROM research.report_visibility rvis "
        f"WHERE rvis.file_hash = {alias}.file_hash AND {_INVISIBLE_ROW})"
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
        f"WHERE rvis_r.id = {report_id_expr} AND {_INVISIBLE_ROW})"
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


class ReportIsDraftError(VisibilityError):
    """草稿（尚未發布的上傳研報）不能隱藏或恢復：發布與退回只走上傳審核，免得兩種狀態互相覆蓋。"""


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
    - 草稿（`publication='draft'`）拒絕隱藏與恢復，拋 ReportIsDraftError：恢復不可順手發布，
      隱藏草稿也沒有意義（它本來就不可見）。寫入的 UPSERT 另以 `WHERE publication = 'published'`
      再守一次，先查後寫之間就算有人把它改成草稿，也不會蓋掉。
    - 只動 hidden／reason／updated_*，**永遠不碰 publication／published_*。**
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
                "SELECT r.file_name, v.hidden, v.publication FROM research.research_report r "
                "LEFT JOIN research.report_visibility v ON v.file_hash = r.file_hash "
                "WHERE r.file_hash = :h"
            ),
            {"h": file_hash},
        )
    ).first()
    if report is None:
        raise ReportNotFoundError("研報不存在")
    file_name, previous = report[0], report[1]
    if report[2] == PUBLICATION_DRAFT:
        raise ReportIsDraftError("這份研報是尚未發布的草稿，請到上傳審核發布或退回")
    row = (
        await session.execute(
            text(
                "INSERT INTO research.report_visibility (file_hash, hidden, reason, updated_by) "
                "VALUES (:h, :hidden, :reason, CAST(:actor AS uuid)) "
                "ON CONFLICT (file_hash) DO UPDATE SET hidden = EXCLUDED.hidden, reason = EXCLUDED.reason, "
                "updated_by = EXCLUDED.updated_by, updated_at = now() "
                "WHERE research.report_visibility.publication = 'published' "
                "RETURNING updated_at, "
                "(SELECT u.username FROM research.app_user u WHERE u.id = CAST(:actor AS uuid))"
            ),
            {"h": file_hash, "hidden": hidden, "reason": note, "actor": actor_id},
        )
    ).first()
    if row is None:  # 先查後寫之間變成了草稿：UPSERT 的 WHERE 擋下、沒有回傳列
        raise ReportIsDraftError("這份研報是尚未發布的草稿，請到上傳審核發布或退回")
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
