"""每日簡報：決定性收集 → 一次 Claude 綜述 → research.report_brief（讀取零 LLM）。

與觀點雷達／閱讀頁摘錄同一種分工——**Python 決定「有什麼」，Claude 只負責「怎麼說」**：

- 窗期界定、來源研報清單、評等變動的判定與計數，全部是這裡的 SQL 與純函式；
- Claude 拿到的是一份已經整理好的素材，只回一段 markdown 綜述；
- 落 `research.report_brief` 一天一列，`/api/brief/*` 只做 SELECT。

## 三個刻意的決定

**一、窗期用入庫時間（`research_report.created_at`），不是 `report_date`。**
`report_date` 是研報自己標的日期，NAS 匯入的常比入庫日早——實測近 10 天入庫的 90 篇
有 79 篇（88%）`report_date` 超過一天前。拿它界定「今天有什麼新東西」會靜默漏掉近九成，
與排程「不可用 `--since-days`」是同一個坑。

**二、來源研報清單由本模組記錄，不從 markdown 反推。**
簡報要能點回原文。若改成事後從模型寫出來的文字裡認標題，模型漏列或多列一篇都不會有
任何錯誤訊息，而讀者看到的連結會少一篇或連到不存在的報告。

**三、評等變動是「與該券商前一次訊號比」，不是窗期內兩兩相比。**
一家券商在窗期內只會出一份報告，真正的變動一定要跨窗期比——所以 SQL 的 `lag()` 開在
全表上、只在最後才用窗期過濾出「這次變的」。窗期內比對的話，永遠一筆變動都算不出來。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 只有這兩個狀態代表「真的擷取到東西」。pending/rejected 是批次的過程紀錄，
# 不是讀者視角的事實——與 reading/queries.py 的 VALID_STATUSES 同一個理由。
VALID_SIGNAL_STATUSES = ["valid", "partial"]

# 一份簡報最多帶幾篇研報進 prompt。近 90 天平均每日 7 篇，這個上限只在補跑歷史或
# NAS 一次倒進大量檔案時才會碰到——超過就截斷並在 markdown 裡說明，不是靜默丟棄。
MAX_REPORTS_IN_PROMPT = 40

# 目標價變動要多大才算「變動」。同一家券商微調 0.5% 不值得寫進簡報，但也不能用固定
# 金額（台股三位數、美股兩位數、港股個位數混在同一張表裡）。
TARGET_CHANGE_MIN_PCT = 1.0


@dataclass(frozen=True)
class BriefReport:
    """窗期內新入庫的一篇研報（簡報的素材與「點回原文」的連結來源）。"""

    report_id: str
    file_hash: str
    file_name: str
    title: Optional[str]
    market: Optional[str]
    source: Optional[str]
    report_date: Optional[date]
    summary: Optional[str]

    @property
    def display_title(self) -> str:
        """對齊前端 displayTitle()：有標題用標題，缺值才回退檔名。

        title 是漸進補的（`generate_titles.py`），任何時點都有一部分研報沒有——
        缺值是常態不是錯誤。
        """
        return (self.title or "").strip() or self.file_name


@dataclass(frozen=True)
class BriefSignalChange:
    """窗期內某券商對某標的的評等／目標價變動（與該券商前一次訊號相比）。"""

    market: str
    instrument_code: str
    broker: str
    report_date: Optional[date]
    rating_from: Optional[str]
    rating_to: Optional[str]
    target_from: Optional[float]
    target_to: Optional[float]
    target_currency: Optional[str]
    instrument_name: Optional[str] = None

    @property
    def rating_changed(self) -> bool:
        return bool(
            self.rating_from
            and self.rating_to
            and self.rating_from != self.rating_to
            and "unknown" not in (self.rating_from, self.rating_to)
        )

    @property
    def target_change_pct(self) -> Optional[float]:
        if not self.target_from or not self.target_to or self.target_from <= 0:
            return None
        return (self.target_to - self.target_from) / self.target_from * 100.0


@dataclass(frozen=True)
class BriefRow:
    """已落庫的一份簡報（讀取路徑）。"""

    brief_date: date
    window_start: datetime
    window_end: datetime
    markdown: str
    report_ids: list[str]
    report_count: int
    signal_count: int
    model: Optional[str]
    created_at: datetime


# ── 收集（決定性） ────────────────────────────────────────────────

_WINDOW_REPORTS_SQL = text(
    """
    SELECT id::text, file_hash, file_name, title, market, source, report_date, summary
    FROM research.research_report
    WHERE created_at >= :start AND created_at < :end
      AND is_research IS NOT FALSE
    ORDER BY report_date DESC NULLS LAST, created_at DESC
    LIMIT :limit
    """
)


async def fetch_window_reports(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    limit: int = MAX_REPORTS_IN_PROMPT,
) -> list[BriefReport]:
    """窗期內新入庫的研究報告（新→舊）。

    `limit` 是 prompt 大小的上限，不是「這段時間只有這麼多篇」——真實篇數由
    呼叫端另外數（見 count_window_reports），兩者不同時要讓讀者知道被截斷了。
    """
    rows = (
        await session.execute(
            _WINDOW_REPORTS_SQL, {"start": start, "end": end, "limit": limit}
        )
    ).all()
    return [
        BriefReport(
            report_id=r[0],
            file_hash=r[1],
            file_name=r[2],
            title=r[3],
            market=r[4],
            source=r[5],
            report_date=r[6],
            summary=r[7],
        )
        for r in rows
    ]


_COUNT_WINDOW_REPORTS_SQL = text(
    """
    SELECT count(*) FROM research.research_report
    WHERE created_at >= :start AND created_at < :end AND is_research IS NOT FALSE
    """
)


async def count_window_reports(
    session: AsyncSession, start: datetime, end: datetime
) -> int:
    return int(
        (
            await session.execute(_COUNT_WINDOW_REPORTS_SQL, {"start": start, "end": end})
        ).scalar()
        or 0
    )


# 變動的「新鮮度」要兩個條件同時成立，少一個都會出錯：
#
# 1. `created_at`（擷取時間）落在窗期內——這是「我們什麼時候知道的」。
# 2. `report_date`（報告日期）不早於 SIGNAL_MAX_REPORT_AGE_DAYS——這是「這件事本身
#    是不是新的」。
#
# **只用 (1) 會把積壓批次翻出來的舊變動當成今天的新聞**：訊號擷取排的是跨全語料的
# 歷史積壓，2026-08-05 那一次大批量就在同一天內產生了 420 筆「變動」，其中絕大多數
# 來自幾個月前的報告。只用 (2) 則會讓同一筆變動天天重複出現（報告日期不會變）。
SIGNAL_MAX_REPORT_AGE_DAYS = 14

# lag() 刻意開在全表上（見模組 docstring 第三點）：一家券商在窗期內只會出一份報告，
# 「這次與上次比」的上次必然在窗期之外。最後才用窗期與新鮮度過濾。
_SIGNAL_CHANGES_SQL = text(
    """
    WITH ranked AS (
        SELECT
            market, instrument_code, broker, report_date, created_at,
            rating_normalized, target_price, target_currency,
            lag(rating_normalized) OVER w AS prev_rating,
            lag(target_price)      OVER w AS prev_target
        FROM research.report_signal
        WHERE extraction_status = ANY(CAST(:statuses AS text[]))
          AND broker IS NOT NULL
        WINDOW w AS (
            PARTITION BY market, instrument_code, broker
            ORDER BY report_date NULLS FIRST, created_at
        )
    )
    SELECT market, instrument_code, broker, report_date,
           prev_rating, rating_normalized, prev_target, target_price, target_currency
    FROM ranked
    WHERE created_at >= :start AND created_at < :end
      -- report_date IS NULL 一律排除：判不出新鮮度時，寧可漏報也不要把舊變動當新聞。
      AND report_date IS NOT NULL AND report_date >= :min_report_date
      AND (
            (prev_rating IS NOT NULL AND rating_normalized IS NOT NULL
             AND prev_rating <> rating_normalized
             AND prev_rating <> 'unknown' AND rating_normalized <> 'unknown')
         OR (prev_target IS NOT NULL AND target_price IS NOT NULL
             AND prev_target > 0
             AND abs(target_price - prev_target) / prev_target * 100 >= :min_pct)
      )
    ORDER BY report_date DESC NULLS LAST, market, instrument_code
    """
)


async def fetch_signal_changes(
    session: AsyncSession,
    start: datetime,
    end: datetime,
    min_pct: float = TARGET_CHANGE_MIN_PCT,
    max_report_age_days: int = SIGNAL_MAX_REPORT_AGE_DAYS,
) -> list[BriefSignalChange]:
    """窗期內**新擷取到**、且報告本身夠新的評等／目標價變動。

    空清單是常態：訊號擷取只跑高覆蓋子集，多數日子沒有任何一家券商改口。
    """
    rows = (
        await session.execute(
            _SIGNAL_CHANGES_SQL,
            {
                "start": start,
                "end": end,
                "statuses": VALID_SIGNAL_STATUSES,
                "min_pct": min_pct,
                "min_report_date": (end - timedelta(days=max_report_age_days)).date(),
            },
        )
    ).all()
    return [
        BriefSignalChange(
            market=r[0],
            instrument_code=r[1],
            broker=r[2],
            report_date=r[3],
            rating_from=r[4],
            rating_to=r[5],
            target_from=float(r[6]) if r[6] is not None else None,
            target_to=float(r[7]) if r[7] is not None else None,
            target_currency=r[8],
        )
        for r in rows
    ]


def with_instrument_names(
    changes: Sequence[BriefSignalChange], names: dict[tuple[str, str], str]
) -> list[BriefSignalChange]:
    """把 (market, code) → 公司名貼回變動清單。查無名稱者保持 None，呈現端回退代號。"""
    out = []
    for change in changes:
        name = names.get((change.market, change.instrument_code))
        out.append(change if name is None else _replace_name(change, name))
    return out


def _replace_name(change: BriefSignalChange, name: str) -> BriefSignalChange:
    return BriefSignalChange(
        market=change.market,
        instrument_code=change.instrument_code,
        broker=change.broker,
        report_date=change.report_date,
        rating_from=change.rating_from,
        rating_to=change.rating_to,
        target_from=change.target_from,
        target_to=change.target_to,
        target_currency=change.target_currency,
        instrument_name=name,
    )


# ── 素材與提示詞（純函式，可單測） ────────────────────────────────

_RATING_ZH = {
    "buy": "買進",
    "overweight": "加碼",
    "neutral": "中立",
    "underweight": "減碼",
    "sell": "賣出",
    "unknown": "未提供",
}

_MARKET_ZH = {
    "TW": "台股",
    "US": "美股",
    "HK": "港股",
    "CN": "陸股",
    "FX": "匯市",
    "WTX": "台指期",
    "MACRO": "總體與債市",
    "GLOBAL": "全球與商品",
    "CRYPTO": "加密資產",
}


def rating_zh(code: Optional[str]) -> str:
    return _RATING_ZH.get((code or "").lower(), code or "未提供")


def market_zh(code: Optional[str]) -> str:
    return _MARKET_ZH.get((code or "").upper(), code or "未分類")


def _fmt_target(value: Optional[float], currency: Optional[str]) -> str:
    if value is None:
        return "未提供"
    prefix = f"{currency} " if currency else ""
    return f"{prefix}{value:g}"


def build_material(
    reports: Sequence[BriefReport],
    changes: Sequence[BriefSignalChange],
    *,
    total_reports: int,
) -> str:
    """把素材攤成純文字餵給模型。

    **摘要（summary）是餵進去的主體，不是全文**：摘要早就批次產好且覆蓋率 100%，
    重跑全文等於為了同一件事再付一次錢。素材裡的每個數字都來自 DB，模型只負責
    組織它們——prompt 另外明令不得補充素材以外的數字。
    """
    lines: list[str] = []
    lines.append(f"# 新進研報（{total_reports} 篇）")
    if not reports:
        lines.append("（無）")
    for report in reports:
        head = f"- [{market_zh(report.market)}] {report.display_title}"
        if report.source:
            head += f"｜{report.source}"
        if report.report_date:
            head += f"｜{report.report_date.isoformat()}"
        lines.append(head)
        if report.summary:
            lines.append(f"  摘要：{report.summary.strip()}")
    if total_reports > len(reports):
        lines.append(
            f"（另有 {total_reports - len(reports)} 篇未列出，本次只取最新 {len(reports)} 篇）"
        )

    lines.append("")
    lines.append(f"# 評等／目標價變動（{len(changes)} 筆）")
    if not changes:
        lines.append("（無）")
    for change in changes:
        name = change.instrument_name or change.instrument_code
        parts = [f"- {name}（{change.market} {change.instrument_code}）｜{change.broker}"]
        if change.rating_changed:
            parts.append(
                f"評等 {rating_zh(change.rating_from)}→{rating_zh(change.rating_to)}"
            )
        pct = change.target_change_pct
        if pct is not None and abs(pct) >= TARGET_CHANGE_MIN_PCT:
            parts.append(
                "目標價 "
                f"{_fmt_target(change.target_from, change.target_currency)}"
                f"→{_fmt_target(change.target_to, change.target_currency)}"
                f"（{pct:+.1f}%）"
            )
        lines.append("｜".join(parts))
    return "\n".join(lines)


_PROMPT_TEMPLATE = """你是券商研究報告平台的每日簡報編輯。以下是 {brief_date} 的素材，\
請據此寫一份給投資研究人員看的每日簡報。

輸出規則（違反任何一條都會被退回）：
1. 一律**繁體中文**，直接輸出 markdown 本文，不要開場白、不要程式碼圍欄。
2. **只能使用素材裡出現的事實與數字**。素材沒寫的價格、日期、預估值一律不得補充，
   寧可少講也不要推測。
3. 不做投資建議、不預測價格、不寫「建議買進／賣出」這類語句。
4. 章節固定為下列三段，順序不可改；某段沒有素材就寫「本期無」，不要略過整段。

## 今日重點
3-5 條，每條一句話，挑最值得研究人員知道的事，句末以（券商名）標示來源。

## 市場焦點
依市場分段（素材裡的市場標籤已中文化），每個市場 1-3 句。沒有素材的市場不要出現。

## 評等與目標價
先逐條列出素材「評等／目標價變動」區塊裡的每一筆，格式：
標的名（代號）｜券商｜評等 原→新｜目標價 原→新（幅度）；該欄位為「未提供」時照實寫。
接著，如果**研報摘要本身**提到了評等或目標價調整而上面那個區塊沒有，另列於其後並在
行末加註「（摘要提及）」。兩者都沒有才寫「本期無」。

註記不可省略：上面那個區塊是逐報告結構化擷取的結果，摘要提及的則是敘述文字，
兩者的可靠度不同，混在一起讀者分不出哪些是系統核對過的。

素材如下：

{material}
"""


def build_prompt(brief_date: date, material: str) -> str:
    return _PROMPT_TEMPLATE.format(brief_date=brief_date.isoformat(), material=material)


# 模型偶爾會把整份 markdown 包進圍欄，或在前面加一句「好的，以下是…」。
_FENCE_PREFIX = "```"
_MIN_BRIEF_CHARS = 40


def parse_brief(raw: Optional[str]) -> Optional[str]:
    """把 LLM 原始輸出收成可落庫的 markdown；不合格回 None（由呼叫端記失敗）。

    **刻意沒有「模型講什麼就存什麼」的寬鬆路徑**：這份文字會直接呈現給讀者，
    一段開場白或半個圍欄都會出現在頁面上，而那種瑕疵不會有任何測試看得到。
    """
    if not raw:
        return None
    body = raw.strip()
    if body.startswith(_FENCE_PREFIX):
        # ```markdown\n...\n``` → 取中間；只剝最外層，內文自己的圍欄不動
        lines = body.splitlines()
        lines = lines[1:]
        while lines and not lines[-1].strip().startswith(_FENCE_PREFIX):
            lines.pop()
        if lines:
            lines.pop()
        body = "\n".join(lines).strip()
    # 模型有時會在真正的內容前加一句話；第一個 markdown 標題之前的東西一律丟掉。
    head = body.find("## ")
    if head > 0:
        body = body[head:]
    return body if len(body) >= _MIN_BRIEF_CHARS else None


# ── 讀取（零 LLM） ────────────────────────────────────────────────

_BRIEF_SELECT = """
    SELECT brief_date, window_start, window_end, markdown,
           report_ids::text[], report_count, signal_count, model, created_at
    FROM research.report_brief
"""


def _to_row(r) -> BriefRow:
    return BriefRow(
        brief_date=r[0],
        window_start=r[1],
        window_end=r[2],
        markdown=r[3],
        report_ids=list(r[4] or []),
        report_count=int(r[5] or 0),
        signal_count=int(r[6] or 0),
        model=r[7],
        created_at=r[8],
    )


_LATEST_SQL = text(_BRIEF_SELECT + " ORDER BY brief_date DESC LIMIT 1")
_BY_DATE_SQL = text(_BRIEF_SELECT + " WHERE brief_date = :d")
_DATES_SQL = text(
    "SELECT brief_date FROM research.report_brief ORDER BY brief_date DESC LIMIT :limit"
)


async def fetch_latest(session: AsyncSession) -> Optional[BriefRow]:
    row = (await session.execute(_LATEST_SQL)).first()
    return _to_row(row) if row else None


async def fetch_by_date(session: AsyncSession, d: date) -> Optional[BriefRow]:
    row = (await session.execute(_BY_DATE_SQL, {"d": d})).first()
    return _to_row(row) if row else None


async def fetch_dates(session: AsyncSession, limit: int = 30) -> list[date]:
    return [r[0] for r in (await session.execute(_DATES_SQL, {"limit": limit})).all()]


_REPORTS_BY_IDS_SQL = text(
    """
    SELECT id::text, file_hash, file_name, title, market, source, report_date, summary
    FROM research.research_report
    WHERE id = ANY(CAST(:ids AS uuid[]))
    ORDER BY report_date DESC NULLS LAST, file_name
    """
)


async def fetch_reports_by_ids(
    session: AsyncSession, ids: Sequence[str]
) -> list[BriefReport]:
    """依 id 取回簡報的來源研報。

    **查無的 id 直接消失，不是錯誤**：`report_ids` 刻意沒有 FK，語料重建後
    （`ingest_all.py` 先刪後插）舊 id 會失效。簡報本文仍然有效，只是少幾個連結。
    """
    if not ids:
        return []
    rows = (await session.execute(_REPORTS_BY_IDS_SQL, {"ids": list(ids)})).all()
    return [
        BriefReport(
            report_id=r[0],
            file_hash=r[1],
            file_name=r[2],
            title=r[3],
            market=r[4],
            source=r[5],
            report_date=r[6],
            summary=r[7],
        )
        for r in rows
    ]


# ── 寫入 ──────────────────────────────────────────────────────────

_UPSERT_SQL = text(
    """
    INSERT INTO research.report_brief
        (id, brief_date, window_start, window_end, markdown,
         report_ids, report_count, signal_count, model)
    VALUES
        (CAST(:id AS uuid), :brief_date, :window_start, :window_end, :markdown,
         CAST(:report_ids AS uuid[]), :report_count, :signal_count, :model)
    ON CONFLICT (brief_date) DO UPDATE SET
        window_start = EXCLUDED.window_start,
        window_end   = EXCLUDED.window_end,
        markdown     = EXCLUDED.markdown,
        report_ids   = EXCLUDED.report_ids,
        report_count = EXCLUDED.report_count,
        signal_count = EXCLUDED.signal_count,
        model        = EXCLUDED.model,
        created_at   = now()
    """
)


async def upsert_brief(
    session: AsyncSession,
    *,
    brief_id: str,
    brief_date: date,
    window_start: datetime,
    window_end: datetime,
    markdown: str,
    report_ids: Sequence[str],
    report_count: int,
    signal_count: int,
    model: Optional[str],
) -> None:
    await session.execute(
        _UPSERT_SQL,
        {
            "id": brief_id,
            "brief_date": brief_date,
            "window_start": window_start,
            "window_end": window_end,
            "markdown": markdown,
            "report_ids": list(report_ids),
            "report_count": report_count,
            "signal_count": signal_count,
            "model": model,
        },
    )
