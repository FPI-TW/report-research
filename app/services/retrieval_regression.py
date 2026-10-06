"""檢索回歸檢查（零 LLM）：凍結題集的 top-k 對一次性基準快照，判斷檢索有沒有變差。

CLI 是 `scripts/retrieval_regression.py`（`capture` 擷取基準、`check` 比對，timer 每日跑 `check`）；
管理後台 `GET /api/admin/retrieval-regression` 只讀 `check` 寫的結果檔（`data_health` 的結果檔機制，
`data/health/retrieval_regression.json`），**web 不跑檢索**——比對要載 BGE-M3（2–3 GB）。

════════════════════════════════════════════════════════════════════════
量哪一層：`hybrid_search`，不是 `retrieval_pipeline.retrieve_context`
════════════════════════════════════════════════════════════════════════
- `retrieve_context` 在檢索之後還有 cross-encoder 重排（第二個模型、帶逾時的 fail-open：同一題逾時與否
  排序就不同）與 `build_context` 選篇（新近度衰減吃 `now`，**每天自己就會變**）。拿它當基準，每天都會
  有「沒有人改任何東西」的差異。
- 排除新研報（見下）必須在截斷之前做，需要完整的候選清單；`retrieve_context` 只回選好的來源。
- `hybrid_search` 涵蓋的正是語料、索引、嵌入與 SQL 這一層會靜默壞掉的東西：HNSW／trgm 索引、
  `content_norm`、NULL embedding、可見性過濾、dense／字面融合。參數與問答一致（`dense_scan` 用
  `ASK_DENSE_SCAN`），跟 `scripts/eval_retrieval.py` 同一個理由直呼它。
- 量不到的：重排與選篇的品質，那是 `eval/run_ragas.py` 的範圍（刻意不進 CI、人工前後各跑一次）。

════════════════════════════════════════════════════════════════════════
識別與「不是變差」的三種變化
════════════════════════════════════════════════════════════════════════
- **識別用 `file_hash`（研報）與 `(file_hash, chunk_index)`（片段）**：`report_id`／`chunk_id` 是 UUID，
  重新入庫（`store.upsert_report` 先刪後插）就換新，拿它比會把重新入庫誤判成整題消失。
- **新研報**：語料每 3 小時在長，新研報擠進 top-k 不代表變差。比對時把「基準時點之後才入庫」
  （`created_at` > 基準的 `corpus_cutoff`，即擷取當下的 `max(created_at)`）的研報從**完整候選清單**拿掉
  再取 top-k，等於在「基準那時的語料」上重排。基準清單裡本來就有的研報即使 `created_at` 變新
  （重新入庫會重設 `created_at`）也不算新。代價：基準之後才重新入庫、又不在該題基準裡的舊研報會被當成
  新的排除（偏保守，只會少算 gained，不會多報劣化）；排除數另外列出（`excluded_new_reports`）。
  另附不排除的 `raw_report_recall`，讓人看得出語料實際換了多少。
- **隱藏與下架**：基準裡的研報若已被管理員隱藏（`report_visibility`）或已不在語料裡，從分母拿掉、
  分開計數（`hidden_reports`／`removed_reports`），不算劣化。基準研報大半都不見時（可比較的題目不到一半）
  結論是「基準太舊、不可比」（告警，要人重新擷取），不是「劣化」。

════════════════════════════════════════════════════════════════════════
指標與門檻（門檻在 app/config.py）
════════════════════════════════════════════════════════════════════════
- `report_recall`（**判定用**）：基準 top-k 裡（仍可見的）研報，現在還在 top-k 的比例。用研報層級是因為
  抽取回填（E1d）會重切片段，`chunk_index` 對不上不代表找不到那篇。
- `chunk_recall`、`rbo`（rank-biased overlap，p=0.9，外插版；兩份清單完全相同＝1）：只顯示、不判定，
  比 report_recall 敏感，用來看「排序在動」的早期訊號。
- **檢索本身的不確定性**：字面路命中超過 cap（`retrieval.LEX_CAP`）時，進入精確距離排序的是任意 cap 列，
  同一題前後兩次跑的 top-k 可能不同（2026-10-06 在 devdb 實測：「散熱技術的進展如何？」相隔兩分鐘的兩次，
  研報召回 0.4）。這類題目在結果裡標 `lex_truncated`；單題門檻之外再允許少數題崩掉，就是為了吸收它。
- 劣化（rc=1）＝ 平均 report_recall < `RETRIEVAL_REGRESSION_MIN_MEAN_RECALL`（0.8）**或** report_recall <
  `RETRIEVAL_REGRESSION_MIN_QUESTION_RECALL`（0.5）的題數 > `RETRIEVAL_REGRESSION_MAX_DEGRADED_QUESTIONS`（2）。
  第二條是為了「一兩題完全崩掉」不會被其餘十幾題的平均稀釋。

結果檔只有計數、比例、題目（repo 內的凍結題集）、研報 file_hash 與標題（或檔名）；沒有片段內文。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.config import get_settings
from app.services import data_health
from app.services.embed import MODEL_NAME as EMBED_MODEL

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE = REPO_ROOT / "data" / "retrieval_regression" / "baseline.json"
# 重用凍結題集（不另建、不改它；`tests/test_eval_question_contract.py` 守內容）。基準記下它的 sha256，
# 題集一變就不可比——改題集等於換一把尺，要重新擷取。
DATASET_REL = "eval/ragas_questions.json"
DATASET = REPO_ROOT / DATASET_REL

RESULT_NAME = data_health.RESULT_RETRIEVAL_REGRESSION
BASELINE_FORMAT = "retrieval-regression-baseline"
BASELINE_VERSION = 1
RESULT_VERSION = 1
RBO_P = 0.9
MAX_BASELINE_BYTES = 2 * 1024 * 1024
# 每題列出的流失／新進研報上限（完整差異看 `check --json`）。
MAX_LISTED_REPORTS = 10
LABEL_MAX_CHARS = 120

OUTCOME_OK = "ok"
OUTCOME_DEGRADED = "degraded"
OUTCOME_SKIPPED = "skipped"
OUTCOME_ERROR = "error"
OUTCOMES = (OUTCOME_OK, OUTCOME_DEGRADED, OUTCOME_SKIPPED, OUTCOME_ERROR)

# 略過（rc=2，unit 以 SuccessExitStatus 放行）：會自己好的狀況。
REASON_DB_UNAVAILABLE = "db_unavailable"
REASON_LOW_MEMORY = "low_memory"
REASON_SYNC_RUNNING = "sync_running"
# 錯誤（rc=3，告警）：要人處理才會好。
REASON_NO_BASELINE = "no_baseline"
REASON_BASELINE_INVALID = "baseline_invalid"
REASON_INCOMPARABLE = "incomparable"
REASON_DATASET_INVALID = "dataset_invalid"
REASON_EMBED_FAILED = "embed_failed"
REASON_QUERY_FAILED = "query_failed"
REASON_UNEXPECTED = "unexpected"
REASONS = (
    REASON_DB_UNAVAILABLE, REASON_LOW_MEMORY, REASON_SYNC_RUNNING, REASON_NO_BASELINE, REASON_BASELINE_INVALID,
    REASON_INCOMPARABLE, REASON_DATASET_INVALID, REASON_EMBED_FAILED, REASON_QUERY_FAILED, REASON_UNEXPECTED,
)

VERDICT_OK = "ok"
VERDICT_DEGRADED = "degraded"
VERDICT_INCOMPARABLE = "incomparable"


class BaselineError(ValueError):
    """基準檔格式不對或與現況不可比。"""


class BaselineMissing(BaselineError):
    """還沒有基準檔。"""


@dataclass(frozen=True)
class ReportMeta:
    created_at: datetime | None
    hidden: bool
    label: str | None = None


@dataclass(frozen=True)
class Thresholds:
    min_mean_recall: float
    min_question_recall: float
    max_degraded_questions: int

    @classmethod
    def from_settings(cls, settings=None) -> Thresholds:
        s = settings or get_settings()
        return cls(s.retrieval_regression_min_mean_recall, s.retrieval_regression_min_question_recall,
                   s.retrieval_regression_max_degraded_questions)

    def as_dict(self) -> dict:
        return {"min_mean_recall": self.min_mean_recall, "min_question_recall": self.min_question_recall,
                "max_degraded_questions": self.max_degraded_questions}


def baseline_path(settings=None) -> Path:
    raw = (settings or get_settings()).retrieval_regression_baseline
    return Path(raw) if raw else DEFAULT_BASELINE


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


# ── 檢索結果 → 可比較的項目 ───────────────────────────────────────────────


def scored_items(scored: Iterable[tuple[int, float, Any]]) -> list[dict]:
    """hybrid_search 的 [(tier, fused, ChunkRow)] → [{file_hash, chunk_index, tier, fused}]（順序不變）。

    以欄位名取值（ChunkRow 是 NamedTuple；不寫數字索引，理由見 app/services/rows.py）。
    """
    out = []
    for tier, fused, row in scored:
        out.append({"file_hash": str(row.file_hash), "chunk_index": int(row.chunk_index),
                    "tier": int(tier), "fused": round(float(fused), 4)})
    return out


def _unique(seq: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ── DB（唯讀；批次／管理面用，刻意不帶可見性過濾——要知道誰被隱藏了）───────


_CORPUS_SQL = text("SELECT max(created_at) AS cutoff, count(*) AS n FROM research.research_report")

_META_SQL = text(
    """
    SELECT r.file_hash, r.created_at, COALESCE(NULLIF(r.title, ''), r.file_name) AS label,
           EXISTS (SELECT 1 FROM research.report_visibility v
                   WHERE v.file_hash = r.file_hash AND v.hidden) AS hidden
    FROM research.research_report r
    WHERE r.file_hash = ANY(CAST(:hashes AS text[]))
    """
)


async def fetch_corpus_state(session) -> tuple[datetime | None, int]:
    row = (await session.execute(_CORPUS_SQL)).one()
    return row.cutoff, int(row.n)


async def fetch_report_meta(session, hashes: Iterable[str]) -> dict[str, ReportMeta]:
    """file_hash → 入庫時間、是否隱藏、顯示名稱。不在結果裡的 file_hash＝已不在語料裡（下架）。"""
    wanted = sorted(set(hashes))
    if not wanted:
        return {}
    rows = (await session.execute(_META_SQL, {"hashes": wanted})).all()
    return {r.file_hash: ReportMeta(created_at=r.created_at, hidden=bool(r.hidden), label=r.label) for r in rows}


# ── 指標 ───────────────────────────────────────────────────────────────────


def rbo(a: Sequence, b: Sequence, p: float = RBO_P) -> float | None:
    """外插版 rank-biased overlap（Webber et al. 2010，式 32）；兩份相同＝1、完全不重疊＝0。

    深度取兩者較長者，較短的那份視為後面沒有東西。清單內元素須唯一。
    """
    depth = max(len(a), len(b))
    if depth == 0:
        return None
    seen_a: set = set()
    seen_b: set = set()
    overlap = 0
    acc = 0.0
    for d in range(1, depth + 1):
        x = a[d - 1] if d <= len(a) else None
        y = b[d - 1] if d <= len(b) else None
        if x is not None and y is not None and x == y:
            overlap += 1
        else:
            if x is not None and x in seen_b:
                overlap += 1
            if y is not None and y in seen_a:
                overlap += 1
        if x is not None:
            seen_a.add(x)
        if y is not None:
            seen_b.add(y)
        acc += (overlap / d) * p ** d
    value = (overlap / depth) * p ** depth + (1 - p) / p * acc
    return round(min(1.0, max(0.0, value)), 4)


def _ratio(hit: int, total: int) -> float | None:
    return round(hit / total, 4) if total else None


def compare_question(
    baseline_items: Sequence[Mapping[str, Any]],
    current_items: Sequence[Mapping[str, Any]],
    meta: Mapping[str, ReportMeta],
    *,
    cutoff: datetime | None,
    k: int,
) -> dict:
    """單題比對。`current_items` 是**完整**候選清單（未截斷），排除新研報後才取前 k。"""
    base_hashes = {str(i["file_hash"]) for i in baseline_items}

    def is_new(h: str) -> bool:
        if h in base_hashes or cutoff is None:
            return False
        m = meta.get(h)
        return m is not None and m.created_at is not None and m.created_at > cutoff

    raw_top = list(current_items[:k])
    effective = [c for c in current_items if not is_new(str(c["file_hash"]))][:k]

    eligible, hidden, removed = [], [], []
    for item in baseline_items:
        h = str(item["file_hash"])
        m = meta.get(h)
        if m is None:
            removed.append(h)
        elif m.hidden:
            hidden.append(h)
        else:
            eligible.append(item)

    b_chunks = [(str(i["file_hash"]), int(i["chunk_index"])) for i in eligible]
    c_chunks = [(str(c["file_hash"]), int(c["chunk_index"])) for c in effective]
    b_reports = _unique(h for h, _ in b_chunks)
    c_reports = _unique(h for h, _ in c_chunks)
    raw_reports = _unique(str(c["file_hash"]) for c in raw_top)
    excluded = _unique(str(c["file_hash"]) for c in raw_top if is_new(str(c["file_hash"])))
    c_report_set, raw_set, c_chunk_set = set(c_reports), set(raw_reports), set(c_chunks)

    def ref(h: str) -> dict:
        m = meta.get(h)
        label = m.label if m is not None else None
        return {"file_hash": h, "label": label[:LABEL_MAX_CHARS] if isinstance(label, str) else None}

    b_report_set = set(b_reports)
    lost = [h for h in b_reports if h not in c_report_set]
    gained = [h for h in c_reports if h not in b_report_set]
    return {
        "comparable": bool(b_reports),
        "report_recall": _ratio(sum(h in c_report_set for h in b_reports), len(b_reports)),
        "raw_report_recall": _ratio(sum(h in raw_set for h in b_reports), len(b_reports)),
        "chunk_recall": _ratio(sum(c in c_chunk_set for c in b_chunks), len(b_chunks)),
        "rbo": rbo(b_chunks, c_chunks) if b_chunks else None,
        "baseline_reports": len(_unique(str(i["file_hash"]) for i in baseline_items)),
        "eligible_reports": len(b_reports),
        "current_reports": len(c_reports),
        "hidden_reports": len(_unique(hidden)),
        "removed_reports": len(_unique(removed)),
        "excluded_new_reports": len(excluded),
        "lost": [ref(h) for h in lost[:MAX_LISTED_REPORTS]],
        "gained": [ref(h) for h in gained[:MAX_LISTED_REPORTS]],
        "lost_total": len(lost),
        "gained_total": len(gained),
    }


def _mean(values: Iterable[float | None]) -> float | None:
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def summarize(per_question: list[dict], thresholds: Thresholds) -> dict:
    """彙整並就地標記每題的 `degraded`。回傳含 `verdict`（ok／degraded／incomparable）的摘要。"""
    comparable = [q for q in per_question if q.get("comparable")]
    for q in per_question:
        q["degraded"] = bool(q.get("comparable")) and (q.get("report_recall") or 0.0) < thresholds.min_question_recall
    degraded_ids = [q["id"] for q in per_question if q["degraded"]]
    mean_recall = _mean(q.get("report_recall") for q in comparable)
    if len(comparable) < math.ceil(len(per_question) / 2) or mean_recall is None:
        verdict = VERDICT_INCOMPARABLE
    elif mean_recall < thresholds.min_mean_recall or len(degraded_ids) > thresholds.max_degraded_questions:
        verdict = VERDICT_DEGRADED
    else:
        verdict = VERDICT_OK
    return {
        "verdict": verdict,
        "questions": len(per_question),
        "comparable": len(comparable),
        "mean_report_recall": mean_recall,
        "mean_raw_report_recall": _mean(q.get("raw_report_recall") for q in comparable),
        "mean_chunk_recall": _mean(q.get("chunk_recall") for q in comparable),
        "mean_rbo": _mean(q.get("rbo") for q in comparable),
        "degraded_questions": len(degraded_ids),
        "degraded_ids": degraded_ids,
        "hidden_reports": sum(q.get("hidden_reports", 0) for q in per_question),
        "removed_reports": sum(q.get("removed_reports", 0) for q in per_question),
        "excluded_new_reports": sum(q.get("excluded_new_reports", 0) for q in per_question),
        "lex_truncated_questions": sum(bool(q.get("lex_truncated")) for q in per_question),
    }


# ── 基準檔 ─────────────────────────────────────────────────────────────────


def build_baseline(
    *,
    questions: Sequence[Mapping[str, Any]],
    items_by_id: Mapping[str, Sequence[Mapping[str, Any]]],
    dataset_sha256: str,
    k: int,
    dense_scan: int,
    captured_at: datetime,
    corpus_cutoff: datetime | None,
    corpus_reports: int,
    simulated_as_of: bool = False,
) -> dict:
    out_questions = []
    for q in questions:
        items = list(items_by_id.get(q["id"], []))[:k]
        out_questions.append({
            "id": q["id"], "question": q["question"], "filters": dict(q.get("filters") or {}),
            "items": [{"rank": n, "file_hash": i["file_hash"], "chunk_index": int(i["chunk_index"]),
                       "tier": int(i.get("tier", 0)), "fused": float(i.get("fused", 0.0))}
                      for n, i in enumerate(items, 1)],
        })
    return {
        "format": BASELINE_FORMAT,
        "version": BASELINE_VERSION,
        "captured_at": _iso(captured_at),
        "corpus_cutoff": _iso(corpus_cutoff),
        "corpus_reports": int(corpus_reports),
        "simulated_as_of": bool(simulated_as_of),
        "dataset": {"path": DATASET_REL, "sha256": dataset_sha256, "count": len(questions)},
        "params": {"k": int(k), "dense_scan": int(dense_scan), "embed_model": EMBED_MODEL, "rbo_p": RBO_P},
        "questions": out_questions,
    }


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _is_int(value: Any, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def validate_baseline(doc: Any) -> dict:
    """結構檢查；不對就 BaselineError（訊息直接給人看）。"""
    if not isinstance(doc, dict) or doc.get("format") != BASELINE_FORMAT:
        raise BaselineError("不是檢索回歸基準檔（format 不符）")
    if doc.get("version") != BASELINE_VERSION:
        raise BaselineError(f"基準檔版本 {doc.get('version')!r} 不支援（程式只認 {BASELINE_VERSION}）")
    if data_health.parse_time(doc.get("captured_at")) is None:
        raise BaselineError("captured_at 不是含時區的 ISO 時間")
    if doc.get("corpus_cutoff") is not None and data_health.parse_time(doc.get("corpus_cutoff")) is None:
        raise BaselineError("corpus_cutoff 不是 ISO 時間")
    dataset, params = doc.get("dataset"), doc.get("params")
    if not isinstance(dataset, dict) or not _is_hex64(dataset.get("sha256")):
        raise BaselineError("dataset.sha256 缺漏或格式不對")
    if not isinstance(params, dict) or not _is_int(params.get("k"), 1) or not _is_int(params.get("dense_scan"), 1):
        raise BaselineError("params.k／params.dense_scan 必須是正整數")
    questions = doc.get("questions")
    if not isinstance(questions, list) or not questions:
        raise BaselineError("questions 必須是非空陣列")
    seen: set[str] = set()
    for q in questions:
        if not isinstance(q, dict) or not isinstance(q.get("id"), str) or not isinstance(q.get("items"), list):
            raise BaselineError("questions[*] 必須有 id 與 items")
        if q["id"] in seen:
            raise BaselineError(f"題目 {q['id']} 重複")
        seen.add(q["id"])
        if len(q["items"]) > params["k"]:
            raise BaselineError(f"{q['id']} 的項目數超過 k")
        for item in q["items"]:
            if not (isinstance(item, dict) and _is_hex64(item.get("file_hash"))
                    and _is_int(item.get("chunk_index"))):
                raise BaselineError(f"{q['id']} 有項目缺 file_hash／chunk_index")
    return doc


def load_baseline(path: Path) -> dict:
    try:
        size = path.stat().st_size
    except FileNotFoundError as exc:
        raise BaselineMissing(f"沒有基準檔 {path}") from exc
    if size > MAX_BASELINE_BYTES:
        raise BaselineError(f"基準檔超過 {MAX_BASELINE_BYTES} bytes")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise BaselineError(f"基準檔讀不了：{type(exc).__name__}") from exc
    return validate_baseline(doc)


def compatibility_problems(baseline: Mapping[str, Any], *, dataset_sha256: str,
                           questions: Sequence[Mapping[str, Any]]) -> list[str]:
    """基準與現況「量的是不是同一件事」：題集、題目文字與過濾條件、嵌入模型。"""
    problems = []
    if baseline["dataset"]["sha256"] != dataset_sha256:
        problems.append("題集 sha256 與基準不同（題集變了，基準不可比，請重新擷取）")
    model = baseline["params"].get("embed_model")
    if model is not None and model != EMBED_MODEL:
        problems.append(f"嵌入模型不同（基準 {model}、程式 {EMBED_MODEL}），請重新擷取")
    by_id = {q["id"]: q for q in questions}
    for bq in baseline["questions"]:
        q = by_id.get(bq["id"])
        if q is None or q["question"] != bq.get("question") or dict(q.get("filters") or {}) != bq.get("filters", {}):
            problems.append(f"題目 {bq['id']} 與題集不一致")
    return problems


def write_json_atomic(path: Path, doc: Mapping[str, Any]) -> None:
    """同目錄 tempfile → fsync → os.replace。失敗就拋（擷取基準時寫不進去必須讓人知道）。"""
    data = json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=True, default=str)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def baseline_summary(baseline: Mapping[str, Any]) -> dict:
    """結果檔／管理頁要的基準資訊（不含逐題項目）。"""
    return {
        "captured_at": baseline.get("captured_at"),
        "corpus_cutoff": baseline.get("corpus_cutoff"),
        "corpus_reports": baseline.get("corpus_reports"),
        "simulated_as_of": bool(baseline.get("simulated_as_of")),
        "k": baseline["params"]["k"],
        "dense_scan": baseline["params"]["dense_scan"],
        "dataset_sha256": baseline["dataset"]["sha256"],
    }


# ── 管理後台（GET /api/admin/retrieval-regression）只讀結果檔 ──────────────

_STATUS_BY_OUTCOME = {
    OUTCOME_OK: data_health.STATUS_OK,
    OUTCOME_DEGRADED: data_health.STATUS_FAIL,
    OUTCOME_ERROR: data_health.STATUS_FAIL,
    OUTCOME_SKIPPED: data_health.STATUS_UNKNOWN,
}


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _str(value: Any, limit: int) -> str | None:
    return value[:limit] if isinstance(value, str) else None


def _time(value: Any) -> str | None:
    return value if data_health.parse_time(value) is not None else None


def _refs(value: Any) -> list[dict]:
    out = []
    for r in (value if isinstance(value, list) else [])[:MAX_LISTED_REPORTS]:
        if isinstance(r, Mapping) and _is_hex64(r.get("file_hash")):
            out.append({"file_hash": r["file_hash"], "label": _str(r.get("label"), LABEL_MAX_CHARS)})
    return out


def _question_view(q: Any) -> dict | None:
    if not isinstance(q, Mapping) or not isinstance(q.get("id"), str):
        return None
    view = {"id": q["id"][:16], "question": _str(q.get("question"), 300) or "",
            "comparable": q.get("comparable") is True, "degraded": q.get("degraded") is True,
            "lex_truncated": q.get("lex_truncated") is True}
    for key in ("report_recall", "raw_report_recall", "chunk_recall", "rbo"):
        view[key] = _num(q.get(key))
    for key in ("baseline_reports", "eligible_reports", "current_reports", "hidden_reports", "removed_reports",
                "excluded_new_reports", "lost_total", "gained_total"):
        view[key] = _int(q.get(key))
    view["lost"] = _refs(q.get("lost"))
    view["gained"] = _refs(q.get("gained"))
    return view


def _comparison_view(c: Any) -> dict | None:
    if not isinstance(c, Mapping) or _time(c.get("finished_at")) is None:
        return None
    b, t, s = c.get("baseline"), c.get("thresholds"), c.get("summary")
    baseline = None
    if isinstance(b, Mapping):
        baseline = {"captured_at": _time(b.get("captured_at")), "corpus_cutoff": _time(b.get("corpus_cutoff")),
                    "corpus_reports": _int(b.get("corpus_reports")),
                    "simulated_as_of": b.get("simulated_as_of") is True,
                    "k": _int(b.get("k")), "dense_scan": _int(b.get("dense_scan")),
                    "dataset_sha256": _str(b.get("dataset_sha256"), 64)}
    thresholds = None
    if isinstance(t, Mapping):
        thresholds = {"min_mean_recall": _num(t.get("min_mean_recall")) or 0.0,
                      "min_question_recall": _num(t.get("min_question_recall")) or 0.0,
                      "max_degraded_questions": _int(t.get("max_degraded_questions"))}
    summary = None
    if isinstance(s, Mapping):
        verdict = s.get("verdict") if s.get("verdict") in (VERDICT_OK, VERDICT_DEGRADED, VERDICT_INCOMPARABLE) \
            else VERDICT_INCOMPARABLE
        summary = {"verdict": verdict}
        for key in ("mean_report_recall", "mean_raw_report_recall", "mean_chunk_recall", "mean_rbo"):
            summary[key] = _num(s.get(key))
        for key in ("questions", "comparable", "degraded_questions", "hidden_reports", "removed_reports",
                    "excluded_new_reports", "lex_truncated_questions"):
            summary[key] = _int(s.get(key))
    questions = [v for q in (c.get("questions") if isinstance(c.get("questions"), list) else [])[:100]
                 if (v := _question_view(q)) is not None]
    return {
        "finished_at": c["finished_at"],
        "duration_s": _num(c.get("duration_s")),
        "baseline": baseline,
        "dense_scan": _int(c.get("dense_scan")) or None,
        "params_changed": c.get("params_changed") is True,
        "thresholds": thresholds,
        "summary": summary,
        "questions": questions,
    }


def section(now: datetime | None = None) -> dict:
    """最後一次 `check` 的結果（讀檔）＋狀態燈。逐欄檢查型別，壞掉的欄位當沒有。

    - `status`：比對結論 ok → ok、劣化或錯誤 → fail、略過 → unknown；`params_changed`（問答的 dense_scan
      與基準不同）至少 warn；最後一次「真的比對過」比 48 小時舊 → stale、至少 warn。
    - 略過或錯誤的那次不會蓋掉上一次的比對：`comparison` 沿用上一次的結果（看 `comparison.finished_at`）。
    """
    now = now or _now()
    empty = {"outcome": None, "reason": None, "message": None, "comparison": None}
    result, why = data_health.read_result(RESULT_NAME)
    if result is None:
        return {"status": data_health.STATUS_UNKNOWN, "available": False, "unavailable_reason": why,
                "finished_at": None, "age_hours": None, "stale": False, "exit_code": None, **empty}
    finished = _time(result.get("finished_at"))
    outcome = result.get("outcome") if result.get("outcome") in OUTCOMES else None
    reason = result.get("reason") if result.get("reason") in REASONS else None
    comparison = _comparison_view(result.get("comparison"))
    stale_basis = comparison["finished_at"] if comparison else finished
    stale = data_health.is_stale(RESULT_NAME, stale_basis, now)
    status = _STATUS_BY_OUTCOME.get(outcome, data_health.STATUS_UNKNOWN)
    if comparison and comparison["params_changed"]:
        status = data_health.worst([status, data_health.STATUS_WARN])
    if stale:
        status = data_health.worst([status, data_health.STATUS_WARN])
    hours = data_health.age_hours(finished, now)
    exit_code = result.get("exit_code")
    return {
        "status": status, "available": True, "unavailable_reason": None, "finished_at": finished,
        "age_hours": round(hours, 2) if hours is not None else None, "stale": stale,
        "exit_code": exit_code if isinstance(exit_code, int) and not isinstance(exit_code, bool) else None,
        "outcome": outcome, "reason": reason, "message": _str(result.get("message"), 500),
        "comparison": comparison,
    }
