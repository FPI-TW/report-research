"""對外 API（/external/v1/*）：以 `Authorization: Bearer <api_key>` 認證的研報搜尋與短效原檔連結。

認證、限流與每日額度在 `web/external_auth.py`；用戶端與 entitlement 的規則在
`app/services/api_clients.py`、`app/services/entitlement.py`，呼叫一律經 `deps.api_clients`。

兩條端點的資料邊界：

- 用戶端看得到的研報＝可見（未隱藏、已發布）**且**在它的 entitlement 內。搜尋把 entitlement
  下推到 `hybrid_search` 的兩路 SQL；請求帶的 `market`／`source`／`report_type`／`instrument_type`
  先與 entitlement 取交集（只能縮小、不能放寬），交集為空直接回空結果、不打檢索。
- 原檔連結端點**重新查研報**（同時帶 `visible_report_sql` 與 `ent.sql`），不信任先前的搜尋結果：
  查無、隱藏、草稿、不在 entitlement 一律 404 `report_not_found`，不洩漏存在與否。
- 搜尋結果附的 `file_url` 只做 canonical key 檢查、不逐筆 HEAD（`verify_object=False`），產生
  失敗的那筆給 null、不讓整個搜尋失敗（fail-open）；`file-url` 端點才 HEAD 驗 sha256。
- 不回目標價等雷達欄位。

輔助函式一律放在 `@router` 裝飾器之上（夾在裝飾器與 handler 之間會讓端點回 422）。
"""
from __future__ import annotations

import asyncio
import dataclasses
import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.config import get_settings
from app.services.entitlement import Entitlement
from app.services.filename import source_display
from app.services.original_file_url import (
    OriginalFileError,
    OriginalIntegrityError,
    OriginalNotFound,
    OriginalStorageUnavailable,
    mint_original_url,
)
from app.services.retrieval import DENSE_SCAN_SEARCH, LEX_CAP_SEARCH
from app.services.visibility import visible_report_sql
from web import deps
from web.errors import AppError
from web.external_auth import require_api_client
from web.routers.search import SEARCH_QUERY_MAX_CHARS

logger = logging.getLogger(__name__)

router = APIRouter()

FILE_SCOPE = "report.file"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def client_entitlement(client) -> Entitlement:
    """用戶端設定 → Entitlement。`resolve_key` 已擋掉缺 market 的用戶端；這裡仍 fail-closed。"""
    try:
        return Entitlement.from_mapping(client.entitlements)
    except ValueError:
        logger.exception("API 用戶端 %s（%s）的授權範圍無效", client.id, client.key_prefix)
        raise AppError(503, "api_client_misconfigured", "API 用戶端設定有誤，請聯絡管理員")


def narrow_entitlement(
    ent: Entitlement,
    *,
    market: str | None,
    source: str | None,
    report_type: str | None,
    instrument_type: str | None,
) -> Entitlement | None:
    """請求的過濾與 entitlement 取交集；任一維度交集為空回 None（＝不可能有結果）。

    每個請求參數是單一值：有給就把該維度收窄成那一個值，但前提是 entitlement 在該維度
    允許它（未設定＝不限）。`instrument_type` 也一樣要求「在授權清單內」，比「研報同時含
    授權類別與請求類別」嚴一點——寧可少給，不靠 SQL 的陣列重疊語意放寬。
    """
    fields = {
        "markets": (market, ent.markets),
        "sources": (source, ent.sources),
        "report_types": (report_type, ent.report_types),
        "instrument_types": (instrument_type, ent.instrument_types),
    }
    changes: dict[str, tuple[str, ...]] = {}
    for name, (wanted, allowed) in fields.items():
        if wanted is None:
            continue
        if allowed is not None and wanted not in allowed:
            return None
        changes[name] = (wanted,)
    return dataclasses.replace(ent, **changes)


def _clean_filter(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


async def fetch_file_pointer(session, report_id: str, ent: Entitlement):
    """file-url 端點用的那一條 SQL：(file_name, file_hash, source_object_key) 或 None。

    可見性與 entitlement 都在 SQL 內——查無、隱藏、草稿、授權外對呼叫端完全相同。
    """
    ent_clause, ent_params = ent.sql("r")
    return (await session.execute(
        text(
            "SELECT r.file_name, r.file_hash, r.source_object_key FROM research.research_report r "
            f"WHERE r.id = CAST(:id AS uuid) AND {visible_report_sql('r')} AND {ent_clause}"
        ),
        {"id": report_id, **ent_params},
    )).first()


async def fetch_file_pointers(session, report_ids: list[str], ent: Entitlement) -> dict[str, tuple]:
    """搜尋結果當頁的原檔指標：{report_id: (file_name, file_hash, source_object_key)}。

    搜尋列（`ChunkRow`）沒有 object key，所以當頁另查一次；同樣帶可見性與 entitlement。
    """
    if not report_ids:
        return {}
    ent_clause, ent_params = ent.sql("r")
    rows = (await session.execute(
        text(
            "SELECT CAST(r.id AS text), r.file_name, r.file_hash, r.source_object_key "
            "FROM research.research_report r "
            f"WHERE r.id = ANY(CAST(:ids AS uuid[])) AND {visible_report_sql('r')} AND {ent_clause}"
        ),
        {"ids": report_ids, **ent_params},
    )).all()
    return {row[0]: tuple(row[1:]) for row in rows}


async def _search_file_urls(report_ids: list[str], ent: Entitlement) -> dict[str, tuple[str | None, str | None]]:
    """{report_id: (file_url, expires_at)}；任何一筆失敗都只讓那筆是 (None, None)。"""
    out: dict[str, tuple[str | None, str | None]] = {rid: (None, None) for rid in report_ids}
    try:
        async with deps.SessionFactory() as session:
            pointers = await fetch_file_pointers(session, report_ids, ent)
    except Exception:
        logger.exception("對外搜尋：查原檔指標失敗，當頁 file_url 全部為 null")
        return out
    ttl = get_settings().external_file_url_ttl_seconds

    async def one(rid: str) -> None:
        pointer = pointers.get(rid)
        if pointer is None:
            return
        file_name, file_hash, object_key = pointer
        issued = _utcnow()
        try:
            url = await mint_original_url(
                file_name=file_name, object_key=object_key, file_hash=file_hash,
                ttl_seconds=ttl, verify_object=False,
            )
        except OriginalFileError as exc:
            logger.info("對外搜尋：研報 %s 不附 file_url（%s）", rid, type(exc).__name__)
            return
        except Exception:
            logger.exception("對外搜尋：研報 %s 產生 file_url 失敗", rid)
            return
        out[rid] = (url, _iso_utc(issued + timedelta(seconds=ttl)))

    await asyncio.gather(*(one(rid) for rid in report_ids))
    return out


def _report_payload(group) -> dict:
    # 刻意不回命中段落（passages）：對外回應只給研報層級的欄位，要內文請取原檔。
    mr = group.meta_row
    return {
        "report_id": mr.report_id,
        "title": mr.title,
        "file_name": mr.file_name,
        "market": mr.market,
        "source": mr.source,
        "source_name": source_display(mr.source),
        "report_date": mr.report_date.isoformat() if mr.report_date else None,
        "report_type": mr.report_type,
        "instrument_types": list(mr.instrument_types) if mr.instrument_types else None,
        "summary": mr.summary,
        "score": group.best_score,
    }


@router.get("/external/v1/search")
async def external_search(
    q: str = Query(..., min_length=1, max_length=SEARCH_QUERY_MAX_CHARS),
    limit: int = Query(10, ge=1, le=20),
    page: int = Query(1, ge=1),
    sort: Literal["relevance", "date_desc", "date_asc"] = Query("relevance"),
    market: str | None = Query(None),
    source: str | None = Query(None),
    report_type: str | None = Query(None),
    instrument_type: str | None = Query(None),
    client=Depends(require_api_client("search")),
):
    """研報混合檢索（同 /api/search 的檢索與排序），結果限於用戶端的 entitlement。"""
    ent = client_entitlement(client)
    narrowed = narrow_entitlement(
        ent,
        market=_clean_filter(market),
        source=_clean_filter(source),
        report_type=_clean_filter(report_type),
        instrument_type=_clean_filter(instrument_type),
    )
    # page 從 1 起算，每頁 limit 篇；超過最後一頁回空的 results（total 照常）。
    body = {"query": q, "total": 0, "page": page, "limit": limit, "results": []}
    if narrowed is None:
        return body
    qvec = await asyncio.to_thread(deps.embed_query_cached, q)
    async with deps.SessionFactory() as session:
        scored = await deps.hybrid_search(
            session,
            q,
            qvec,
            dense_scan=DENSE_SCAN_SEARCH,
            lex_cap=LEX_CAP_SEARCH,
            lex_per_report=True,
            lex_unlimited=True,
            entitlement=narrowed,
        )
    ranked = deps.rank_reports(scored, sort=sort)
    start = (page - 1) * limit
    results = [_report_payload(g) for g in ranked[start: start + limit]]
    if FILE_SCOPE in client.scopes and results:
        urls = await _search_file_urls([r["report_id"] for r in results], ent)
        for r in results:
            r["file_url"], r["file_url_expires_at"] = urls.get(r["report_id"], (None, None))
    logger.info(
        "external search client_id=%s key_prefix=%s total=%d page=%d limit=%d sort=%s",
        client.id, client.key_prefix, len(ranked), page, limit, sort,
    )
    body.update(total=len(ranked), results=results)
    return body


@router.get("/external/v1/reports/{report_id}/file-url")
async def external_report_file_url(report_id: str, client=Depends(require_api_client(FILE_SCOPE))):
    """單篇研報原檔的短效 presigned URL（HEAD 驗 sha256 後才簽）。"""
    if not deps._valid_uuid(report_id):
        raise AppError(404, "report_not_found", "查無此研報")
    ent = client_entitlement(client)
    try:
        async with deps.SessionFactory() as session:
            row = await fetch_file_pointer(session, report_id, ent)
    except Exception:
        logger.exception("對外原檔連結：查研報失敗 client_id=%s", client.id)
        raise AppError(503, "unavailable", "服務暫時無法使用")
    if row is None:
        raise AppError(404, "report_not_found", "查無此研報")
    file_name, file_hash, object_key = row
    ttl = get_settings().external_file_url_ttl_seconds
    issued = _utcnow()
    try:
        url = await mint_original_url(
            file_name=file_name, object_key=object_key, file_hash=file_hash,
            ttl_seconds=ttl, verify_object=True,
        )
    except OriginalNotFound:
        raise AppError(404, "original_not_found", "這篇研報沒有可提供的原檔")
    except (OriginalIntegrityError, OriginalStorageUnavailable) as exc:
        logger.warning("對外原檔連結：研報 %s 無法簽出（%s）", report_id, type(exc).__name__)
        raise AppError(503, "original_unavailable", "原檔暫時無法提供")
    logger.info(
        "external file-url client_id=%s key_prefix=%s report_id=%s ttl=%s",
        client.id, client.key_prefix, report_id, ttl,
    )
    return JSONResponse(
        {"report_id": report_id, "file_url": url, "expires_at": _iso_utc(issued + timedelta(seconds=ttl))},
        headers={"Cache-Control": "no-store"},
    )
