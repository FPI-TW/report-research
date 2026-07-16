"""M7：研報大綱→逐節生成的狀態機與逐節內容層。

本模組把研報生成從「單次一口氣寫完整份」拆成可續跑、可稽核的狀態機：
`report_run`（一次生成請求的完整生命週期）＋ `report_section`（逐節內容）。

分層（隨里程碑任務逐步接入）：
- T2（本檔基礎）：狀態機常數／轉換驗證、冪等 `request_key` 合成、checkpoint
  序列化、`report_run` upsert 與原子狀態推進、`report_section` 依 position upsert、
  續跑載入。**純資料層，不接 LLM。**
- T3 大綱、T4 逐節檢索、T5 帳本組裝、T6 逐節草稿串流由後續任務接上。

DB 寫入沿用 report-mark 慣用法（`text()`＋bindparam、自管 session）。`SessionFactory`
以模組屬性引入，方便測試以 fake session 替換（patch-where-used）。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text

from app.services.db import SessionFactory

# ── 狀態機常數（與 db/schema.sql 的 CHECK 逐字對齊）──────────────────────────
RUN_STATES: tuple[str, ...] = (
    "queued", "retrieving", "outlining", "drafting",
    "verifying", "rendering", "completed", "failed", "cancelled",
)
TERMINAL_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled"})

SECTION_STATES: tuple[str, ...] = (
    "pending", "retrieving", "drafting", "drafted", "verifying", "final", "failed",
)

# 正常前進路徑；any(非終端)→failed/cancelled 與 same→same 另行允許（見 is_valid_transition）
_FORWARD: dict[str, set[str]] = {
    "queued": {"retrieving"},
    "retrieving": {"outlining"},
    "outlining": {"drafting"},
    "drafting": {"verifying"},
    "verifying": {"rendering"},
    "rendering": {"completed"},
}


class InvalidTransition(ValueError):
    """不合法的狀態轉換（防止亂序推進 report_run.status）。"""


def is_valid_transition(current: str, nxt: str) -> bool:
    """狀態機守門：same→same 冪等；非終端→failed/cancelled 恆可；其餘依 _FORWARD。"""
    if current not in RUN_STATES or nxt not in RUN_STATES:
        return False
    if current == nxt:
        return True  # 冪等（續跑重入同狀態）
    if nxt in ("failed", "cancelled"):
        return current not in TERMINAL_STATES
    return nxt in _FORWARD.get(current, set())


# ── 冪等 request_key 合成 ───────────────────────────────────────────────────
def synthesize_request_key(
    question: str,
    *,
    filters: dict | None = None,
    model: str | None = None,
    conversation_id: str | None = None,
) -> str:
    """同題（同 filters/model/對話）重送得同鍵 → report_run 冪等回同一 run。

    以穩定序列化（filters sort_keys）避免 dict 順序造成假異鍵。
    """
    payload = "\x00".join(
        [
            (question or "").strip(),
            json.dumps(filters or {}, sort_keys=True, ensure_ascii=False),
            model or "",
            conversation_id or "",
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


# ── checkpoint（最後一致可續跑點）────────────────────────────────────────────
@dataclass
class Checkpoint:
    """fail-open 續跑鐵律：只能從最後一致 checkpoint 重試，不得改跑另一份完整內容。"""

    outline_ready: bool = False
    final_positions: list[int] = field(default_factory=list)  # 已 final 的節次
    current_revision_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "outline_ready": self.outline_ready,
            "final_positions": sorted(set(self.final_positions)),
            "current_revision_id": self.current_revision_id,
        }

    @classmethod
    def load(cls, obj: Any) -> "Checkpoint":
        """寬鬆讀取：None/{}/壞形狀 → 空 checkpoint（歷史/半途列安全退化）。"""
        if not isinstance(obj, dict):
            return cls()
        positions = obj.get("final_positions")
        if not isinstance(positions, list):
            positions = []
        # final_positions 是集合語意（已完成節次），去重排序保持 canonical
        positions = sorted({p for p in positions if isinstance(p, int)})
        rev = obj.get("current_revision_id")
        return cls(
            outline_ready=bool(obj.get("outline_ready")),
            final_positions=positions,
            current_revision_id=rev if isinstance(rev, str) else None,
        )


# ── report_run：upsert 與原子狀態推進 ───────────────────────────────────────
async def open_run(
    request_key: str,
    *,
    input_config: dict | None = None,
    qa_id: str | None = None,
    conversation_id: str | None = None,
) -> tuple[str, bool]:
    """以 request_key 建 run（ON CONFLICT DO NOTHING）。

    回 (run_id, is_new)：is_new=False 表撈回既有 in-flight/完成 run 供續跑或去重。
    冪等：同 request_key 重送恆回同一 run_id、不產生重複列。
    """
    new_id = str(uuid.uuid4())
    async with SessionFactory() as session:
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO research.report_run "
                    "(id, request_key, status, input_config, qa_id, conversation_id) "
                    "VALUES (:id, :rk, 'queued', CAST(:cfg AS jsonb), :qa, :conv) "
                    "ON CONFLICT (request_key) DO NOTHING RETURNING id"
                ),
                {
                    "id": new_id,
                    "rk": request_key,
                    "cfg": json.dumps(input_config or {}, ensure_ascii=False),
                    "qa": qa_id,
                    "conv": conversation_id,
                },
            )
        ).first()
        if inserted is not None:
            await session.commit()
            return str(inserted[0]), True
        # request_key 已存在：撈回既有 run 供續跑/去重
        row = (
            await session.execute(
                text("SELECT id FROM research.report_run WHERE request_key = :rk"),
                {"rk": request_key},
            )
        ).first()
        await session.commit()
    return str(row[0]) if row else new_id, False


async def advance_status(
    run_id: str,
    status: str,
    *,
    expected_current: str | None = None,
    outline: Any = None,
    checkpoint: Checkpoint | None = None,
    current_revision_id: str | None = None,
    revision: int | None = None,
    report_doc_id: str | None = None,
    evidence_manifest_hash: str | None = None,
    error_detail: str | None = None,
) -> None:
    """原子推進 report_run.status（＋可選欄），單一交易 UPDATE + updated_at。

    傳 status='' 表不改狀態、只更新附帶欄位。轉換不合法拋 InvalidTransition。
    """
    async with SessionFactory() as session:
        cur_row = (
            await session.execute(
                text("SELECT status FROM research.report_run WHERE id = :id"),
                {"id": run_id},
            )
        ).first()
        if cur_row is None:
            raise InvalidTransition(f"report_run 不存在：{run_id}")
        current = cur_row[0]
        if expected_current is not None and current != expected_current:
            raise InvalidTransition(
                f"預期 {expected_current} 但實為 {current}（run={run_id}）"
            )
        target = status or current
        if status and not is_valid_transition(current, status):
            raise InvalidTransition(f"{current} → {status}（run={run_id}）")

        sets = ["status = :st", "updated_at = now()"]
        params: dict[str, Any] = {"id": run_id, "st": target}
        if outline is not None:
            sets.append("outline = CAST(:outline AS jsonb)")
            params["outline"] = json.dumps(outline, ensure_ascii=False)
        if checkpoint is not None:
            sets.append("checkpoint = CAST(:ckpt AS jsonb)")
            params["ckpt"] = json.dumps(checkpoint.to_json(), ensure_ascii=False)
        if current_revision_id is not None:
            sets.append("current_revision_id = :crid")
            params["crid"] = current_revision_id
        if revision is not None:
            sets.append("revision = :rev")
            params["rev"] = revision
        if report_doc_id is not None:
            sets.append("report_doc_id = :rdid")
            params["rdid"] = report_doc_id
        if evidence_manifest_hash is not None:
            sets.append("evidence_manifest_hash = :emh")
            params["emh"] = evidence_manifest_hash
        if error_detail is not None:
            sets.append("error_detail = :err")
            params["err"] = error_detail

        await session.execute(
            text(
                f"UPDATE research.report_run SET {', '.join(sets)} WHERE id = :id"
            ),
            params,
        )
        await session.commit()


# ── report_section：依 position upsert 與載入 ───────────────────────────────
async def upsert_section(
    run_id: str,
    position: int,
    *,
    section_key: str | None = None,
    heading: str | None = None,
    draft_markdown: str | None = None,
    final_markdown: str | None = None,
    evidence_ids: list[str] | None = None,
    status: str | None = None,
) -> None:
    """依 uq(run_id, position) upsert 該節；只更新有傳入的欄位（COALESCE 保留舊值）。

    section_draft 覆寫語意：同 position 再寫 draft_markdown 即覆蓋前次草稿。
    """
    async with SessionFactory() as session:
        await session.execute(
            text(
                "INSERT INTO research.report_section "
                "(id, run_id, position, section_key, heading, draft_markdown, "
                " final_markdown, evidence_ids, status) "
                "VALUES (:id, :run, :pos, :skey, :head, :draft, :final, "
                "        CAST(:evids AS text[]), COALESCE(:st, 'pending')) "
                "ON CONFLICT (run_id, position) DO UPDATE SET "
                "  section_key = COALESCE(EXCLUDED.section_key, research.report_section.section_key), "
                "  heading = COALESCE(EXCLUDED.heading, research.report_section.heading), "
                "  draft_markdown = COALESCE(EXCLUDED.draft_markdown, research.report_section.draft_markdown), "
                "  final_markdown = COALESCE(EXCLUDED.final_markdown, research.report_section.final_markdown), "
                "  evidence_ids = COALESCE(EXCLUDED.evidence_ids, research.report_section.evidence_ids), "
                "  status = COALESCE(EXCLUDED.status, research.report_section.status), "
                "  updated_at = now()"
            ),
            {
                "id": str(uuid.uuid4()),
                "run": run_id,
                "pos": position,
                "skey": section_key,
                "head": heading,
                "draft": draft_markdown,
                "final": final_markdown,
                "evids": evidence_ids,
                "st": status,
            },
        )
        await session.commit()


async def load_run(run_id: str) -> dict | None:
    """撈 run 供續跑：status/outline/checkpoint/revision/current_revision_id。"""
    async with SessionFactory() as session:
        row = (
            await session.execute(
                text(
                    "SELECT id, request_key, status, outline, checkpoint, revision, "
                    "current_revision_id, report_doc_id "
                    "FROM research.report_run WHERE id = :id"
                ),
                {"id": run_id},
            )
        ).first()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "request_key": row[1],
        "status": row[2],
        "outline": row[3],
        "checkpoint": Checkpoint.load(row[4]),
        "revision": row[5],
        "current_revision_id": str(row[6]) if row[6] else None,
        "report_doc_id": str(row[7]) if row[7] else None,
    }


async def load_sections(run_id: str) -> list[dict]:
    """依 position 撈全節（續跑組裝用）。"""
    async with SessionFactory() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT position, section_key, heading, draft_markdown, "
                    "final_markdown, evidence_ids, status "
                    "FROM research.report_section WHERE run_id = :run "
                    "ORDER BY position ASC"
                ),
                {"run": run_id},
            )
        ).all()
    return [
        {
            "position": r[0],
            "section_key": r[1],
            "heading": r[2],
            "draft_markdown": r[3],
            "final_markdown": r[4],
            "evidence_ids": list(r[5]) if r[5] else [],
            "status": r[6],
        }
        for r in rows
    ]
