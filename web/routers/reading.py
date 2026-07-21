# web/routers/reading.py
"""研報閱讀頁 API（/api/reading/*）：骨架 metadata＋重點摘錄＋訊號、正典文字、相似研報。

從 web/server.py 拆出（第三步）。三條路由都是純讀取、零 LLM。

契約見 app/services/reading/schemas.py（已凍結，前端 zod 逐字鏡像）。
既有的 /api/report/{report_id}/full 與 /file 是舊 modal 的資料源，與此處無關、不動。

服務函式（SessionFactory、fetch_*）走 web.deps，測試 patch web.deps.X 即涵蓋。
READING_TEXT_MAX_CHARS 是本組設定常數，定義在本模組——test_reading_api 覆寫它時
須指向 web.routers.reading（非 web.deps，也非 web.server）。
"""
import hashlib
import logging
import os
import re
import time

from fastapi import APIRouter, HTTPException, Query

from app.services.filename import source_display
from app.services.reading.anchor import locate_chunk
from app.services.reading.schemas import (
    EpsEstimate,
    ReadingDoc,
    ReadingText,
    Signal,
    SimilarReport,
    SimilarResponse,
    Takeaway,
    ThesisDim,
)
from app.services.tagging import MARKET_DISPLAY
from app.services.textnorm import clean_extracted
from web import deps

logger = logging.getLogger(__name__)

router = APIRouter()


_FILE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

# /text 單次回傳上限。超過即截斷（truncated=True），但 text_sha256/text_chars 一律
# 是「完整正典文字」的值——見 _canonical_text 與 reading_text 的說明。
READING_TEXT_MAX_CHARS = 400_000


def _validate_file_hash(file_hash: str) -> None:
    """file_hash 是網址鍵，格式不符直接 422（不進 DB 查詢）。"""
    if not _FILE_HASH_RE.match(file_hash):
        raise HTTPException(status_code=422, detail="file_hash 非法")


def _canonical_text(full_text: str | None) -> tuple[str, str | None]:
    """回傳（正典文字, 其 sha256）。full_text 為 NULL/空 → ("", None)。

    **正典文字＝clean_extracted(full_text)，不是 full_text**：DB 存的是未清理的原始
    抽取文字（保留 PDF 抽字的 CJK 間空白與破碎換行），而 report_takeaway 的
    quote_start/quote_end 全部錨定於清理後的字串。詳見
    app/services/reading/anchor.py 模組 docstring 的「事實一」。

    **與 scripts/extract_takeaways.py 綁死**：那支批次以同樣的
    `sha256(clean_extracted(full_text))` 算出並寫入 report_takeaway.text_sha256，
    本函式算出的值要拿去和它比對驗章。兩邊任一側改了清理或編碼方式而另一側沒跟上，
    驗章會全篇失敗、跳轉靜默失效（不會拋錯）。要改就兩邊一起改。

    無全文不是錯誤：該篇只是沒有可讀文字（只能看 PDF），呼叫端據此回
    text_state="missing"。
    """
    canonical = clean_extracted(full_text) if full_text else ""
    if not canonical:
        return "", None
    return canonical, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _visible_chars(canonical: str) -> int:
    """/text 實際回給讀者的字元數（＝截斷後的長度）。

    截斷規則只有這一個定義處：骨架端點與 /text 都由此推導，兩邊才不會對「這條摘錄
    跳不跳得到」給出不同答案。
    """
    return min(len(canonical), READING_TEXT_MAX_CHARS)


def _reading_takeaways(rows, text_sha256: str | None, visible_chars: int) -> list[Takeaway]:
    """DB 摘錄列 → 契約 Takeaway，並在此驗章、套截斷。

    降級為不可跳（quote_start/quote_end/anchor_method 全 None，條目與引文照常顯示）
    有兩個獨立原因：

    1. **驗章不過**：每列的 text_sha256 是「擷取當時的正典文字」的 sha。與當前正典
       文字不符即代表全文已被重新 ingest 換過、offset 已漂移。
    2. **落在截斷範圍之外**：offset 對「完整正典文字」計算，但 /text 只回前
       READING_TEXT_MAX_CHARS 字。錨點超出這個範圍＝指向讀者手上根本沒有的文字。

    兩者都是「寧可不能跳，也不要跳到錯的地方」。第 2 點與 _chunk_anchor 的
    `anchor.end > visible_chars` 是同一條規則 —— 刻意在後端統一收回，而不是多發一個
    text_visible_chars 欄位讓前端各自判斷：可跳與否只該有一個真相來源，否則前端
    buildTextSegments（依 text.length 丟棄）與 isJumpable（只看 quote_start）會再次
    分岔，摘錄顯示為可點、點下去卻找不到錨點而靜默無事。
    """
    out: list[Takeaway] = []
    for r in rows:
        stale = text_sha256 is None or r.text_sha256 != text_sha256
        clipped = r.quote_end is None or r.quote_end > visible_chars
        drop = stale or clipped
        out.append(
            Takeaway(
                ordinal=r.ordinal,
                claim=r.claim,
                quote=r.quote,
                quote_start=None if drop else r.quote_start,
                quote_end=None if drop else r.quote_end,
                anchor_method=None if drop else r.anchor_method,
            )
        )
    return out


def _chunk_anchor(
    canonical: str, chunk_content: str | None, visible_chars: int
) -> tuple[int | None, int | None]:
    """把檢索命中的 chunk 錨回正典文字 → (start, end)；錨不到一律 (None, None)。

    **錨不到不是錯誤**：前端據此不高亮，頁面照常（也因此此處不拋 4xx）。錨定邏輯全在
    app/services/reading/anchor.py（實測 400/400 命中），前端不重造比對。

    **offset 一律對「完整正典文字」計算**（locate_chunk 的契約），但回傳給讀者的 text
    可能被截斷（見 reading_text 的截斷語意）。錨點落在截斷範圍之外＝指向讀者手上根本
    沒有的文字 → 收回為 None。寧可不高亮，也不要指到不存在的位置。
    """
    if not chunk_content:
        return None, None
    anchor = locate_chunk(canonical, chunk_content)
    if anchor is None or anchor.end > visible_chars:
        return None, None
    return anchor.start, anchor.end


def _reading_signals(rows) -> list[Signal]:
    """radar 的 Signal dataclass → 閱讀頁契約 Signal（欄位形狀刻意不同）。

    注意 fiscal_year：radar 存 int、契約要 str，此處轉型（契約已凍結，不改欄位型別）。
    """
    return [
        Signal(
            instrument_code=s.instrument_code,
            market=s.market,
            broker=s.broker,
            broker_display=source_display(s.broker),
            rating_raw=s.rating_raw,
            rating_normalized=s.rating_normalized,
            target_price=s.target_price,
            target_currency=s.target_currency,
            target_horizon=s.target_horizon,
            eps_estimates=[
                EpsEstimate(
                    fiscal_year=str(e.fiscal_year) if e.fiscal_year is not None else None,
                    period=e.period,
                    currency=e.currency,
                    unit=e.unit,
                    value=e.value,
                )
                for e in s.eps
            ],
            # radar 的 _parse_thesis 依 THESIS_DIMENSIONS 順序建 dict，故此處順序穩定
            thesis=[
                ThesisDim(
                    key=key, stance=dim.stance, summary=dim.summary, evidence=dim.evidence
                )
                for key, dim in s.thesis.items()
            ],
        )
        for s in rows
    ]


@router.get("/api/reading/{file_hash}", response_model=ReadingDoc)
async def reading_doc(file_hash: str):
    """閱讀頁骨架：metadata + 重點摘錄 + 訊號。**不含全文**（PDF 是預設檢視）。

    全文另走 /api/reading/{file_hash}/text，前端只在需要文字檢視時才取。
    """
    _validate_file_hash(file_hash)
    async with deps.SessionFactory() as session:
        doc = await deps.fetch_doc(session, file_hash)
        if doc is None:
            raise HTTPException(status_code=404, detail="report not found")
        takeaway_rows = await deps.fetch_takeaways(session, doc.report_id)
        signal_rows = await deps.fetch_signals(session, doc.report_id)
    canonical, text_sha256 = _canonical_text(doc.full_text)
    signals = _reading_signals(signal_rows)
    return ReadingDoc(
        report_id=doc.report_id,
        file_hash=doc.file_hash,
        file_name=doc.file_name,
        market=doc.market,
        market_display=MARKET_DISPLAY.get(doc.market) if doc.market else None,
        source=doc.source,
        source_display=source_display(doc.source),
        report_date=doc.report_date.isoformat() if doc.report_date else None,
        report_type=doc.report_type,
        summary=doc.summary,
        instrument_types=doc.instrument_types,
        stock_targets=doc.stock_targets,
        futures_targets=doc.futures_targets,
        has_file=bool(doc.file_path) and os.path.isfile(doc.file_path),
        is_pdf=bool(doc.file_path) and doc.file_path.lower().endswith(".pdf"),
        text_state="ok" if canonical else "missing",
        text_chars=len(canonical),
        text_sha256=text_sha256,
        # visible_chars 與 /text 同源：落在截斷範圍外的錨點在此就收回，
        # 讀者不會看到一條「可點但點不到」的摘錄。
        takeaways=_reading_takeaways(takeaway_rows, text_sha256, _visible_chars(canonical)),
        # 全語料僅 0.68% 有訊號：空是常態不是錯誤，前端據此整區不進 DOM
        signals_state="available" if signals else "none",
        signals=signals,
    )


@router.get("/api/reading/{file_hash}/text", response_model=ReadingText)
async def reading_text(file_hash: str, chunk: int | None = Query(None, ge=0)):
    """正典文字（＝clean_extracted(full_text)）。所有 offset 都以此字串為準。

    **截斷語意**：text 超過 READING_TEXT_MAX_CHARS 時只回前綴並標 truncated=True，
    但 text_sha256 與 text_chars 仍是「完整正典文字」的值 —— takeaway 的錨點是對完整
    文字算出來的，回截斷版的 sha 會讓前端的驗章一律失敗、跳轉整個失效。
    超出截斷範圍的錨點一律由後端收回為 None（此處的 chunk_start/chunk_end 走
    _chunk_anchor，骨架端點的 takeaway offset 走 _reading_takeaways），前端不需要、
    也不應該自行判斷截斷。

    **?chunk=N**：檢索命中的 chunk_index。帶了就一併回該段在正典文字上的字元區間
    （chunk_start/chunk_end），供前端標出「你從檢索點進來的那一段」。chunk 不存在或
    錨不到 → 兩者為 None，回應仍是 200：**沒有命中位置不是錯誤**，頁面照常。
    """
    _validate_file_hash(file_hash)
    async with deps.SessionFactory() as session:
        doc = await deps.fetch_doc(session, file_hash)
        # 同一個 session 內取完：出了 with 區塊 session 已關閉
        chunk_content = (
            await deps.fetch_chunk_content(session, doc.report_id, chunk)
            if doc is not None and chunk is not None
            else None
        )
    if doc is None:
        raise HTTPException(status_code=404, detail="report not found")
    canonical, text_sha256 = _canonical_text(doc.full_text)
    if not canonical or text_sha256 is None:
        # text_state="missing" 的那一篇：骨架回 200，這裡沒有文字可給
        raise HTTPException(status_code=404, detail="report text not available")
    visible = _visible_chars(canonical)
    truncated = len(canonical) > visible
    body = canonical[:visible]
    chunk_start, chunk_end = _chunk_anchor(canonical, chunk_content, visible)
    return ReadingText(
        file_hash=doc.file_hash,
        text=body,
        text_sha256=text_sha256,  # 完整正典文字的 sha，截斷後也不重算
        text_chars=len(canonical),  # 完整長度，非回傳字串長度
        truncated=truncated,
        chunk_start=chunk_start,
        chunk_end=chunk_end,
    )


@router.get("/api/reading/{file_hash}/similar", response_model=SimilarResponse)
async def reading_similar(file_hash: str, limit: int = Query(6, ge=1, le=20)):
    """相似研報（全篇均勻取樣 probe + 廣度加權；理由見 reading/queries.py）。"""
    _validate_file_hash(file_hash)
    t0 = time.monotonic()
    async with deps.SessionFactory() as session:
        doc = await deps.fetch_doc(session, file_hash)
        if doc is None:
            raise HTTPException(status_code=404, detail="report not found")
        rows = await deps.fetch_similar(session, doc.report_id, limit=limit)
    logger.info(
        "reading similar file_hash=%s items=%d elapsed_ms=%.1f",
        file_hash, len(rows), (time.monotonic() - t0) * 1000,
    )
    return SimilarResponse(
        file_hash=file_hash,
        items=[
            SimilarReport(
                file_hash=r.file_hash,
                file_name=r.file_name,
                market=r.market,
                source=r.source,
                source_display=source_display(r.source),
                report_date=r.report_date.isoformat() if r.report_date else None,
                summary=r.summary,
                matched_probes=r.matched_probes,
                total_probes=r.total_probes,
            )
            for r in rows
        ],
    )
