"""M8 忠實度查核（claim grounding，RAGAS 式 reference-free）。

把生成內容拆成原子主張、標記數值型主張、逐條對「解析後的證據文字」做 grounding
（supported / unsupported / no_source），分開記錄 faithfulness_score 與
numeric_support_rate。分數是「來源支持度／待複核」，非真實性保證。

分層（皆 fail-open：judge 異常／JSON 壞 → 不阻擋交付，僅標 degraded、不加分數）：
- 純 primitive（judge 注入、零 DB）：decompose_statements / ground_statements /
  faithfulness — 可用假 judge 做決定性單測，eval/ragas_metrics.py 亦復用之。
- 生產進入點 check_faithfulness（judge 預設走 claude CLI，仍可注入假 judge）。
- resolve_evidence_texts：把 EvidenceLedger 的 corpus 證據回查成文字（碰 DB，另測）。
  帳本只存來源身分不存文字（見 evidence.py），故 grounding 前必須回查；corpus 的
  chunk_id 實務多為 NULL，回查落在 report_id 粒度——v1 已知限制。

契約 judge：async judge(system: str, user: str) -> dict | list | None。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy import text as sql_text

from app.services.evidence import EvidenceLedger
from app.services.llm import DEFAULT_MODEL, stream_completion
from app.services.query_planner import parse_plan_json

logger = logging.getLogger(__name__)


# ── Prompt 常數（就地共置；一律要求 JSON-only。eval/ragas_metrics.py 復用之）──

DECOMPOSE_SYS = (
    "你是 RAG 評測助手。把下列『回答』拆解成一組獨立、原子的事實主張（statement）。"
    "每個主張須可獨立判斷真偽，不含連接詞堆疊。若回答只是『找不到資料』之類、"
    "未提出任何事實主張，回空陣列。\n"
    '只輸出 JSON，格式：{"statements": ["主張1", "主張2", ...]}，不要任何其他文字。'
)

GROUND_SYS = (
    "你是 RAG 忠實度評審。給定『參考片段』與一組『主張』，逐一判斷每個主張是否"
    "能由參考片段直接佐證支持（supported）。只依片段內容判斷，不用外部知識；片段沒說到、"
    "或與片段矛盾，一律 supported=false。\n"
    '只輸出 JSON，格式：{"verdicts": [{"idx": 0, "supported": true}, ...]}，'
    "idx 對應主張的 0-based 序號，不要任何其他文字。"
)


# ── 數值主張偵測（確定性，零 LLM）──
#
# 這個判斷是兩道閘門的**唯一依據**，漏判的代價不對稱：
#   - `report_writer._section_needs_fix`：`numeric_support_rate is None` 直接 return False。
#     一份研報若一條數值主張都沒偵測到，`REPORT_FAITHFULNESS_MIN` 形同虛設、
#     逐節修正輪永不觸發，**未獲語料支持的數字就這樣進了可下載的 PDF**。
#   - `answer._faithfulness_spot_check` 的入口閘門：整份回答判非數值 → 完全不查核。
# 反之偽陽性只是多跑一次 grounding。所以**寧可多抓，不可漏抓**。
#
# 原本只有繁體中文金融名詞白名單，在 M10 雙語上線後破功（2026-07-29 生產實測）：
# 一份英文提問產出的研報，17 條主張**全部**被判非數值 → 修正輪從未觸發。
# 實測 90 條真實主張中漏判 8 條，型態有四類，各自對應下面一條規則：
#   指數點位/大數字（45,000點、44,454點、1,049,256）→ 千分位回退
#   裸小數（動量指數為 48.7）                      → 小數回退
#   倍數寫成 x、英文 P/E（P/E 约为 10x）            → [xX] 單位與 P/E
#   簡體與英文金融詞（营收/净利/目标价/revenue…）    → 名詞表補齊
#
# **刻意不把「點」列為單位**：指數點位一律帶千分位逗號，已被回退規則涵蓋，
# 而加了它會讓「第 3 點提到…」變成數值主張——正是本判斷從一開始就要避免的誤判。
# 年份同理排除：`2026 年新建晶圓廠` 不是可查核的數值主張。
# 關鍵字到數字的距離維持 12 字：實測放寬到 30 字一條都沒有多抓到。
_NUMERIC_RE = re.compile(
    r"\d+(?:\.\d+)?\s*%"                       # 30% / 12.5 %
    r"|[\$￥€]\s*\d"                            # $123
    # 金額／倍數／可數量詞（繁簡並列）
    r"|\d+(?:\.\d+)?\s*(?:元|億|亿|萬|万|兆|百萬|百万|美元|台幣|台币|新台幣|新台币"
    r"|人民幣|人民币|港元|倍|個百分點|个百分点|座|家|bps|BP|[xX]\b)"
    # 金融名詞近旁的數字（繁／簡／英）。裸 PE 不列入：會咬中 PERFORMANCE 這類英文字。
    r"|(?:EPS|每股|營收|营收|收入|毛利率?|營益率|营益率|營業利益率?|营业利润率?"
    r"|淨利|净利|净利润|目標價|目标价|本益比|市盈率|P/E|殖利率|股息率"
    r"|年增|季增|年減|年减|季減|季减|市佔率?|市占率?|出貨量|出货量|產能|产能|資本支出|资本支出"
    r"|revenue|target price|gross margin|operating margin|net income|earnings"
    r"|yield|growth|margin)"
    r"[^。\n]{0,12}?\d"
    # 通用回退：帶千分位或小數點的數字＝量測值，與語言無關。
    r"|(?<!\d)\d{1,3}(?:,\d{3})+(?!\d)"        # 1,049,256 / 45,000
    r"|(?<!\d)(?!19\d\d|20\d\d)\d+\.\d+(?!\d)"  # 48.7（但 2026.07 之類的年份不算）
)


def is_numeric_claim(statement: str) -> bool:
    """主張是否含金融數值（供 numeric_support_rate 分母）。確定性、可單測。"""
    return bool(_NUMERIC_RE.search(statement or ""))


@dataclass(frozen=True)
class ClaimVerdict:
    text: str
    is_numeric: bool
    verdict: str  # "supported" | "unsupported" | "no_source"


@dataclass
class FaithfulnessResult:
    """一次查核的結果。degraded=True 代表 fail-open（judge 異常），分數欄位皆 None。"""

    faithfulness_score: float | None
    numeric_support_rate: float | None
    claims: list[ClaimVerdict] = field(default_factory=list)
    degraded: bool = False

    def to_evaluation(self, *, citation_coverage: float | None = None) -> dict:
        """落庫用 evaluation jsonb（report_doc / qa_log 共用形狀）。"""
        return {
            "citation_coverage": citation_coverage,
            "numeric_support_rate": self.numeric_support_rate,
            "faithfulness_score": self.faithfulness_score,
            "claims": [
                {"text": c.text, "is_numeric": c.is_numeric, "verdict": c.verdict}
                for c in self.claims
            ],
            "note": "來源支持度／待複核，非真實性保證",
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "degraded": self.degraded,
        }


# ── judge primitives（注入式，可假 judge 決定性單測）──


async def decompose_statements(text: str, *, judge) -> list[str] | None:
    """回答 → 原子主張清單。judge 異常/畸形 → None（fail-open，呼叫端標 degraded）。"""
    dec = await judge(DECOMPOSE_SYS, text)
    if dec is None:
        return None
    statements = dec.get("statements") if isinstance(dec, dict) else None
    return [s for s in (statements or []) if isinstance(s, str) and s.strip()]


async def ground_statements(
    statements: list[str], contexts: list[str], *, judge
) -> dict[int, bool] | None:
    """逐條主張對 contexts 判 supported → {idx: bool}。judge 異常 → None。

    payload 格式與 eval 版本逐字一致，避免 eval 基準線漂移。
    """
    if not statements:
        return {}
    joined_ctx = "\n\n".join(contexts)
    enumerated = "\n".join(f"{i}. {s}" for i, s in enumerate(statements))
    payload = f"參考片段：\n{joined_ctx}\n\n主張：\n{enumerated}"
    res = await judge(GROUND_SYS, payload)
    if res is None:
        return None
    verdicts = res.get("verdicts") if isinstance(res, dict) else None
    supmap: dict[int, bool] = {}
    for v in verdicts or []:
        if isinstance(v, dict) and isinstance(v.get("idx"), int):
            idx = v["idx"]
            if 0 <= idx < len(statements):
                supmap[idx] = v.get("supported") is True
    return supmap


async def faithfulness(answer: str, contexts: list[str], *, judge) -> float | None:
    """eval 相容：拆解 → 逐條佐證於 contexts。supported/total；total==0 → None。

    行為與原 eval/ragas_metrics.faithfulness 一致（eval 改 import 本函式）。
    """
    statements = await decompose_statements(answer, judge=judge)
    if not statements:
        return None
    supmap = await ground_statements(statements, contexts, judge=judge)
    if supmap is None:
        return None
    supported = sum(1 for ok in supmap.values() if ok)
    return supported / len(statements)


# ── 生產 judge（drain stream_completion + 容錯 JSON，fail-open）──


async def _default_judge(
    system: str, user: str, *, model: str, timeout: float
) -> dict | None:
    """claude CLI judge：drain 串流 → parse_plan_json。任何異常 → None（fail-open）。"""
    try:
        parts: list[str] = []
        async for chunk in stream_completion(
            user, model=model, system=system, timeout=timeout, allow_web=False
        ):
            parts.append(chunk)
        raw = "".join(parts).strip()
        if not raw:
            return None
        return parse_plan_json(raw)  # {"statements":...} / {"verdicts":...} 皆物件
    except Exception:
        logger.exception("faithfulness judge failed")
        return None


# ── M8 查核進入點 ──


async def check_faithfulness(
    text: str,
    context_texts: list[str],
    *,
    judge=None,
    model: str = DEFAULT_MODEL,
    timeout: float = 60.0,
) -> FaithfulnessResult:
    """對 text 做 grounding 查核。context_texts＝已解析的證據文字（見 resolve_evidence_texts）。

    - judge 未提供 → 走 _default_judge（claude CLI）。
    - decompose 失敗 → degraded（fail-open，不阻擋交付）。
    - context_texts 空（無可回查的證據）→ 全主張 no_source。
    - 有 context → ground；supported→supported，其餘→unsupported。
    numeric_support_rate 只計數值主張；無數值主張 → None。
    """
    if judge is None:
        async def judge(system: str, user: str):
            return await _default_judge(system, user, model=model, timeout=timeout)

    statements = await decompose_statements(text, judge=judge)
    if statements is None:
        return FaithfulnessResult(None, None, [], degraded=True)
    if not statements:
        # 無事實主張（如「找不到資料」）：非降級，但無分可算
        return FaithfulnessResult(None, None, [], degraded=False)

    has_context = any((c or "").strip() for c in context_texts)
    if not has_context:
        claims = [
            ClaimVerdict(s, is_numeric_claim(s), "no_source") for s in statements
        ]
        return summarize_claims(claims, degraded=False)

    supmap = await ground_statements(statements, context_texts, judge=judge)
    if supmap is None:
        return FaithfulnessResult(None, None, [], degraded=True)

    claims = [
        ClaimVerdict(
            s,
            is_numeric_claim(s),
            "supported" if supmap.get(i) else "unsupported",
        )
        for i, s in enumerate(statements)
    ]
    return summarize_claims(claims, degraded=False)


def summarize_claims(claims: list[ClaimVerdict], *, degraded: bool) -> FaithfulnessResult:
    """把逐條 verdict 彙總成 FaithfulnessResult（分數計算單一真相）。

    供研報「逐節查核→合併成 doc-level evaluation」復用（M8b）。
    """
    total = len(claims)
    supported = sum(1 for c in claims if c.verdict == "supported")
    numerics = [c for c in claims if c.is_numeric]
    num_supported = sum(1 for c in numerics if c.verdict == "supported")
    return FaithfulnessResult(
        faithfulness_score=(supported / total) if total else None,
        numeric_support_rate=(num_supported / len(numerics)) if numerics else None,
        claims=claims,
        degraded=degraded,
    )


# ── 證據回查（碰 DB；帳本只存身分，corpus 落 report_id 粒度）──

# 單筆 corpus 證據回查的字元上限：避免整份長研報灌爆 grounding payload。
_MAX_CHARS_PER_EVIDENCE = 4000


async def resolve_evidence_texts(
    ledger: EvidenceLedger,
    session,
    *,
    evidence_ids: list[str] | None = None,
    max_chars: int = _MAX_CHARS_PER_EVIDENCE,
) -> list[str]:
    """把帳本的 corpus 證據回查成文字（依 report_id 取該報告 chunk 串接，capped）。

    evidence_ids 給定時只回查那些證據（供研報逐節查核，只餵該節被分配的證據）；
    None 則回查整份帳本。external 證據的快照內容不在庫內（帳本只有 snapshot_ref/
    content_hash），故略過——無法在庫內驗證的外部數值主張，check_faithfulness 會因缺
    context 判 no_source。
    """
    if evidence_ids is None:
        candidates = list(ledger)
    else:
        candidates = [ev for eid in evidence_ids if (ev := ledger.get(eid)) is not None]
    seen_reports: set[str] = set()
    texts: list[str] = []
    for ev in candidates:
        if ev.kind != "corpus" or not ev.report_id or ev.report_id in seen_reports:
            continue
        seen_reports.add(ev.report_id)
        rows = (
            await session.execute(
                sql_text(
                    "SELECT content FROM research.report_chunk "
                    "WHERE report_id = :rid ORDER BY chunk_index"
                ),
                {"rid": ev.report_id},
            )
        ).all()
        joined = "\n".join(r[0] for r in rows if r[0])
        if joined.strip():
            texts.append(joined[:max_chars])
    return texts
