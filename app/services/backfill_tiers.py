"""首次導入的分層回填：分層規則、待補判斷的 SQL、token 估計與每日上限的帳。零 LLM。

讀者是 `scripts/backfill_tiers.py`（編排入口，呼叫既有批次）與 `scripts/estimate_backfill.py`（估價）。
本模組只 import 標準函式庫與 `app.services` 的純模組，不 import 任何 LLM 呼叫層。

## 分層

- **第一層**（全部歷史）：抽取、切塊、嵌入、標註（側檔已給 `market`＋`stock_targets` 就不呼叫標註 LLM）
  → 可搜尋、問答、閱讀。由既有的匯入負責（`ingest_connectors.py`，或全語料三支），不經這裡。
- **第二層**（`report_date` 在最近 `[backfill].recent_months` 個月內）：摘要、標題、摘錄、訊號。開跑前先估價，
  `backfill_tiers.py --tier 2` 一次跑完。
- **第三層**（其餘：`report_date` 早於窗期，**或 `report_date` 為 NULL**）：同第二層的派生，由背景補齊
  `backfill_tiers.py --tier 3` 在每日 token 上限與每輪份數上限內慢慢補。

- **「最近 N 個月」**：`report_date >= recent_cutoff(今天, N)`，邊界當天算第二層；`recent_cutoff` 是同一日往前推
  N 個曆月（月底夾到該月最後一天，例如 2026-03-31 往前 1 個月是 2026-02-28）。日期未來的研報也算第二層。
- **`report_date` 為 NULL 歸第三層**：沒有日期的多半是檔名、內文、mtime 都推不出日期的舊檔，歸第二層會把
  它們當新檔付費；第三層由背景在上限內補，不會漏。訊號擷取本來就排除無日期的研報（`extract_signals`）。
- 今天以台北時間（`llm_usage.TZ`）計，與用量檔切日一致。

## 待補判斷（重用各批次的「已完成」與跳過名單）

一份研報某項派生「待補」＝類型開了該功能（`doc_types.doc_type_enabled_sql`）且：

- 摘要：`summary IS NULL`；標題：`title IS NULL`（側檔給了 `title` 就不是 NULL）。
- 摘錄：沒有任何 `valid`／`partial` 的 `report_takeaway` 列；訊號：有 `report_date`、有 `stock_targets`，且沒有任何
  `valid`／`partial` 的 `report_signal` 列。**不比對擷取版本**：版本升級的重跑走各批次的 `--reextract`，不經分層回填。
- 跳過名單（`research.llm_task_failure`）判定要跳過的不算待補（`llm_failures.skip_clause_sql`，每項各一段），
  所以內容型失敗的研報不會每輪重排、白佔每輪份數。

這只是**挑候選**：實際送不送 LLM 仍由各批次自己的 checkpoint 決定（摘錄還比 `text_sha256`、訊號只擷取
高覆蓋標的子集），所以重跑不會重複付費。挑候選只看摘要、標題、摘錄（`SELECTION_TASKS`）：訊號只在同一份研報
有其他派生要補時順帶跑——子集外的研報永遠不會有訊號列，用它挑候選會讓同一批研報每輪重排。

## token 估計（`estimate_text_tokens`）

不載 tokenizer（離線、零相依）：CJK 字 × `CJK_TOKENS_PER_CHAR` ＋ 其他非空白字元 ÷ `OTHER_CHARS_PER_TOKEN`，
空白不計（內文的 CJK 字間空白經 `clean_extracted` 後本來就會消失）。每個任務只算它實際送出的前綴
（`TaskSpec.excerpt`，與各批次 `--excerpt` 預設相同），再加上 prompt 本身的開銷（`TaskSpec.prompt_tokens`，
`tests/test_backfill_tiers.py` 以各批次的 prompt 常數對帳）。

**誤差**：係數是 o200k 類 tokenizer 對中文約 0.6～1 token／字、英數約 3～4 字元／token 的經驗值，**沒有在本 repo
對真實 tokenizer 量過**，未校正的估計請當成 ±30% 的量級看；表格、數字多的研報偏低估。對照組：預設 profile 語料的實測
平均（每份輸入）是標註約 6.1k、摘要 5.9k、摘錄 6.7k、標題 2.6k、訊號 9.9k token。報價前的建議做法是**先小跑一批**
（例如 `backfill_tiers.py --tier 2 --max-reports 50`），再帶 `data/llm_usage.jsonl` 重估：`calibration_from_usage`
以同一批研報的實測 ÷ 估計得出每項任務的輸入倍率，輸出 token 與每次秒數改用實測平均，誤差就由同一份語料決定。
輸出 token 沒有輸入可推，未校正時用 `TaskSpec.output_tokens`（保守估計）。

## 每日上限的帳（背景補齊）

`BudgetState`（`data/backfill/state.json`；目錄 `BACKFILL_DIR` 只給測試用）記「台北日 → 已用 token、
已處理份數」。每批跑完以用量檔裡這批研報的實測 token 入帳，讀不到用量檔時以估計值入帳（寧可多算）。
換日自動歸零。
"""

from __future__ import annotations

import calendar
import json
import os
import re
import tempfile
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path

from app.services import llm_failures
from app.services.doc_types import doc_type_enabled_sql
from app.services.llm_usage import TZ

REPO_ROOT = Path(__file__).resolve().parents[2]

TASK_TAG = "tag"
TASK_SUMMARY = "summary"
TASK_TITLE = "title"
TASK_TAKEAWAY = "takeaway"
TASK_SIGNAL = "signal"
# 第二、三層的派生，順序＝編排入口執行的順序（同 sync 鏈第 5、6 段）。
DERIVED_TASKS: tuple[str, ...] = (TASK_SUMMARY, TASK_TITLE, TASK_TAKEAWAY, TASK_SIGNAL)
ALL_TASKS: tuple[str, ...] = (TASK_TAG, *DERIVED_TASKS)
SELECTION_TASKS: tuple[str, ...] = (TASK_SUMMARY, TASK_TITLE, TASK_TAKEAWAY)
# 任務 → doc_types 的功能名（標註不受類型控制：類型就是標註產生的）。
TASK_FEATURE = {TASK_SUMMARY: "summary", TASK_TITLE: "title", TASK_TAKEAWAY: "takeaway", TASK_SIGNAL: "signal"}

TIER_RECENT = 2
TIER_OLDER = 3


# ── 分層 ────────────────────────────────────────────────────────────────


def today_taipei(now: datetime | None = None) -> date:
    return (now or datetime.now(TZ)).astimezone(TZ).date()


def recent_cutoff(today: date, months: int) -> date:
    """第二層的起始日（含）：`today` 往前推 `months` 個曆月，日夾到該月最後一天。"""
    if months < 1:
        raise ValueError("months 至少為 1")
    index = today.year * 12 + (today.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    day = min(today.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def tier_of(report_date: date | None, cutoff: date) -> int:
    """派生的層別：有日期且不早於 cutoff → 第二層；早於 cutoff 或 NULL → 第三層。"""
    if report_date is None:
        return TIER_OLDER
    return TIER_RECENT if report_date >= cutoff else TIER_OLDER


def tier_condition_sql(tier: int, alias: str = "r") -> str:
    """層別的 WHERE 片段（參數 `:cutoff`）。與 `tier_of` 等價（`tests/test_backfill_tiers.py`）。"""
    if tier == TIER_RECENT:
        return f"{alias}.report_date >= CAST(:cutoff AS date)"
    if tier == TIER_OLDER:
        return f"({alias}.report_date IS NULL OR {alias}.report_date < CAST(:cutoff AS date))"
    raise ValueError(f"只有第二、三層有派生要補：{tier!r}")


# ── 待補判斷 ────────────────────────────────────────────────────────────


def _skip(task: str, alias: str, skip_blocked: bool) -> str:
    return f" AND {llm_failures.skip_clause_sql(alias, param_suffix=f'_{task}')}" if skip_blocked else ""


def pending_expr(task: str, alias: str = "r", *, skip_blocked: bool = True) -> str:
    """某項派生「待補」的布林運算式（說明見模組 docstring）。"""
    if task not in TASK_FEATURE:
        raise ValueError(f"未知的派生任務：{task!r}")
    feature = doc_type_enabled_sql(alias, TASK_FEATURE[task])
    skip = _skip(task, alias, skip_blocked)
    if task == TASK_SUMMARY:
        return f"({feature} AND {alias}.summary IS NULL{skip})"
    if task == TASK_TITLE:
        return f"({feature} AND {alias}.title IS NULL{skip})"
    if task == TASK_TAKEAWAY:
        return (
            f"({feature} AND NOT EXISTS (SELECT 1 FROM research.report_takeaway t "
            f"WHERE t.report_id = {alias}.id AND t.extraction_status IN ('valid', 'partial')){skip})"
        )
    if task == TASK_SIGNAL:
        return (
            f"({feature} AND {alias}.report_date IS NOT NULL "
            f"AND COALESCE(cardinality({alias}.stock_targets), 0) > 0 "
            f"AND NOT EXISTS (SELECT 1 FROM research.report_signal s "
            f"WHERE s.report_id = {alias}.id AND s.extraction_status IN ('valid', 'partial')){skip})"
        )
    raise ValueError(f"未知的派生任務：{task!r}")


def skip_params(models: Mapping[str, str]) -> dict:
    """`pending_expr(skip_blocked=True)` 的參數：每項任務各一組（鍵帶任務名後綴）。"""
    out: dict = {}
    for task in DERIVED_TASKS:
        out.update(llm_failures.skip_params(task, models[task], param_suffix=f"_{task}"))
    return out


def _base_where(alias: str = "r") -> str:
    return f"{alias}.full_text IS NOT NULL AND {alias}.full_text <> ''"


def build_candidates_sql(tier: int, *, skip_blocked: bool, limit: bool, exclude: bool = False) -> str:
    """某一層有派生待補的研報：新的先（NULL 日期最後），同日以 file_hash 排定（輸出穩定、可續跑）。

    回傳欄：file_hash、report_date、四項 need_*、送出的前綴（`left(full_text, MAX_EXCERPT)`，供估計）。
    `exclude`：排除 `:exclude` 這批 file_hash（本次執行已處理過的；沒補成的不在同一次執行裡重排）。
    """
    needs = ", ".join(f"{pending_expr(t, skip_blocked=skip_blocked)} AS need_{t}" for t in DERIVED_TASKS)
    any_selected = " OR ".join(pending_expr(t, skip_blocked=skip_blocked) for t in SELECTION_TASKS)
    sql = (
        f"SELECT r.file_hash, r.report_date, {needs}, left(r.full_text, {MAX_EXCERPT}) AS head "
        "FROM research.research_report r "
        f"WHERE {_base_where()} AND {tier_condition_sql(tier)} AND ({any_selected}) "
        + ("AND NOT (r.file_hash = ANY(CAST(:exclude AS text[]))) " if exclude else "")
        + "ORDER BY r.report_date DESC NULLS LAST, r.file_hash"
    )
    if limit:
        sql += " LIMIT :limit"
    return sql


def build_recent_hashes_sql() -> str:
    """一批 file_hash 裡屬於第二層的（`scripts/connector_sync.py` 只把它們交給本輪的摘要、標題、摘錄）。"""
    return (
        "SELECT r.file_hash FROM research.research_report r "
        f"WHERE r.file_hash = ANY(CAST(:hashes AS text[])) AND {tier_condition_sql(TIER_RECENT)}"
    )


def build_status_sql(*, skip_blocked: bool) -> str:
    """查進度：依層別彙總總份數與各項待補數（零 LLM、唯讀）。"""
    sums = ", ".join(
        f"count(*) FILTER (WHERE {pending_expr(t, skip_blocked=skip_blocked)}) AS pending_{t}" for t in DERIVED_TASKS
    )
    return (
        f"SELECT CASE WHEN {tier_condition_sql(TIER_RECENT)} THEN {TIER_RECENT} ELSE {TIER_OLDER} END AS tier, "
        f"count(*) AS reports, {sums} "
        "FROM research.research_report r "
        f"WHERE {_base_where()} "
        "GROUP BY 1 ORDER BY 1"
    )


def build_estimate_sql(*, skip_blocked: bool) -> str:
    """估價用：DB 裡每份研報的層別判斷所需欄位、各項是否待補、送出的前綴。以 file_hash 做 keyset 分頁。

    `skip_blocked=False` 給跳過名單的表不存在的舊庫（同各批次：表不在就不套跳過條件）。
    """
    needs = ", ".join(f"{pending_expr(t, skip_blocked=skip_blocked)} AS need_{t}" for t in DERIVED_TASKS)
    return (
        f"SELECT r.file_hash, r.report_date, {needs}, left(r.full_text, {MAX_EXCERPT}) AS head "
        "FROM research.research_report r "
        f"WHERE {_base_where()} AND r.file_hash > :after "
        "ORDER BY r.file_hash LIMIT :batch"
    )


# ── token 估計 ──────────────────────────────────────────────────────────

# CJK 統一表意文字（含擴充 A、相容）、注音、日文假名、全形標點與全形英數。
_CJK = re.compile(r"[⺀-⿟　-ヿ㄀-ㄯ㐀-䶿一-鿿豈-﫿＀-￯]")
_SPACE = re.compile(r"\s")
CJK_TOKENS_PER_CHAR = 0.8
OTHER_CHARS_PER_TOKEN = 3.5


def _char_counts(text: str) -> tuple[int, int]:
    """(CJK 字數, 其他非空白字元數)。"""
    if not text:
        return 0, 0
    cjk = len(_CJK.findall(text))
    return cjk, max(len(text) - cjk - len(_SPACE.findall(text)), 0)


def _tokens(cjk: int, other: int) -> int:
    return int(round(cjk * CJK_TOKENS_PER_CHAR + other / OTHER_CHARS_PER_TOKEN))


def estimate_text_tokens(text: str) -> int:
    """文字的 token 估計（方法與誤差見模組 docstring）。"""
    return _tokens(*_char_counts(text))


@dataclass(frozen=True)
class TaskSpec:
    """一項任務每次呼叫的估計參數。

    - `excerpt`：送出的內文前綴字數（＝該批次 `--excerpt` 的預設；標註同 `_ingest_core` 的行內標註）。
    - `prompt_tokens`：prompt 指令與檔名等固定部分的 token（以各批次的 prompt 常數估得，測試對帳）。
    - `output_tokens`：每次呼叫的輸出 token（保守估計；用量檔有實測就改用實測平均）。
    - `seconds`：每次呼叫的牆鐘秒數（未校正的預設；用量檔有實測就改用實測平均）。
    """

    excerpt: int
    prompt_tokens: int
    output_tokens: int
    seconds: float


TASK_SPECS: dict[str, TaskSpec] = {
    TASK_TAG: TaskSpec(excerpt=10000, prompt_tokens=900, output_tokens=250, seconds=20.0),
    TASK_SUMMARY: TaskSpec(excerpt=12000, prompt_tokens=160, output_tokens=300, seconds=20.0),
    TASK_TITLE: TaskSpec(excerpt=3000, prompt_tokens=460, output_tokens=120, seconds=10.0),
    TASK_TAKEAWAY: TaskSpec(excerpt=24000, prompt_tokens=360, output_tokens=900, seconds=45.0),
    TASK_SIGNAL: TaskSpec(excerpt=16000, prompt_tokens=800, output_tokens=1500, seconds=60.0),
}
MAX_EXCERPT = max(s.excerpt for s in TASK_SPECS.values())


def input_tokens_by_task(head: str, tasks: Iterable[str] = ALL_TASKS) -> dict[str, int]:
    """一份研報對每項任務的輸入 token 估計（內文前綴＋prompt 開銷）。前綴分段只掃一次。"""
    wanted = list(tasks)
    cuts = sorted({TASK_SPECS[t].excerpt for t in wanted})
    cumulative: dict[int, int] = {}
    cjk = other = prev = 0
    for cut in cuts:
        seg_cjk, seg_other = _char_counts(head[prev:cut])
        cjk, other = cjk + seg_cjk, other + seg_other
        cumulative[cut] = _tokens(cjk, other)
        prev = cut
    return {t: TASK_SPECS[t].prompt_tokens + cumulative[TASK_SPECS[t].excerpt] for t in wanted}


@dataclass(frozen=True)
class Calibration:
    """用量檔校正：輸入 token 的倍率（實測 ÷ 估計，以同一批研報比）、實測的平均輸出 token 與每次秒數。"""

    input_factor: Mapping[str, float] = field(default_factory=dict)
    output_tokens: Mapping[str, float] = field(default_factory=dict)
    seconds: Mapping[str, float] = field(default_factory=dict)
    samples: Mapping[str, int] = field(default_factory=dict)

    def factor(self, task: str) -> float:
        return float(self.input_factor.get(task, 1.0))

    def out_tokens(self, task: str) -> float:
        return float(self.output_tokens.get(task, TASK_SPECS[task].output_tokens))

    def call_seconds(self, task: str) -> float:
        return float(self.seconds.get(task, TASK_SPECS[task].seconds))


MIN_CALIBRATION_SAMPLES = 20


def usage_tokens(row: Mapping) -> tuple[int, int] | None:
    """用量檔一行的 (輸入, 輸出) token；沒有 usage（失敗在第一個 chunk 之前、CLI 時代的舊列）回 None。"""
    tokens = row.get("tokens")
    if not isinstance(tokens, dict):
        return None
    hit, miss, completion = tokens.get("hit"), tokens.get("miss"), tokens.get("completion")
    if hit is None and miss is None and completion is None:
        return None
    return int(hit or 0) + int(miss or 0), int(completion or 0)


def iter_usage_rows(lines: Iterable[str]) -> Iterable[dict]:
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            yield row


def calibration_from_usage(rows: Iterable[Mapping], estimates: Mapping[tuple[str, str], int], *,
                           min_samples: int = MIN_CALIBRATION_SAMPLES) -> Calibration:
    """以用量檔校正估計。

    `estimates`：(任務, file_hash) → 本工具對那份研報的輸入估計。輸入倍率只用「用量檔與語料都有」的研報算
    （Σ實測 ÷ Σ估計，同一批研報比，避免語料組成不同造成偏差）；輸出與秒數用該任務全部成功呼叫的平均。
    樣本數不到 `min_samples` 的任務不校正（沿用預設）。同一份研報同一任務有多筆時只取最後一筆（重試的累計）。
    """
    last: dict[tuple[str, str], Mapping] = {}
    outputs: dict[str, list[int]] = {}
    secs: dict[str, list[float]] = {}
    for row in rows:
        task = row.get("task")
        if task not in TASK_SPECS or row.get("kind"):
            continue  # kind 非空＝失敗
        used = usage_tokens(row)
        if used is None:
            continue
        outputs.setdefault(task, []).append(used[1])
        if isinstance(row.get("total_ms"), (int, float)):
            secs.setdefault(task, []).append(float(row["total_ms"]) / 1000.0)
        fh = row.get("file_hash")
        if isinstance(fh, str) and (task, fh) in estimates:
            last[(task, fh)] = row
    measured: dict[str, int] = {}
    estimated: dict[str, int] = {}
    matched: dict[str, int] = {}
    for (task, fh), row in last.items():
        used = usage_tokens(row)
        assert used is not None
        measured[task] = measured.get(task, 0) + used[0]
        estimated[task] = estimated.get(task, 0) + estimates[(task, fh)]
        matched[task] = matched.get(task, 0) + 1
    factor = {t: measured[t] / estimated[t] for t in matched if matched[t] >= min_samples and estimated[t] > 0}
    out_avg = {t: sum(v) / len(v) for t, v in outputs.items() if len(v) >= min_samples}
    sec_avg = {t: sum(v) / len(v) for t, v in secs.items() if len(v) >= min_samples}
    return Calibration(input_factor=factor, output_tokens=out_avg, seconds=sec_avg,
                       samples={t: matched.get(t, 0) for t in TASK_SPECS})


# ── 每日上限的帳 ────────────────────────────────────────────────────────

DIR_ENV = "BACKFILL_DIR"


def work_dir() -> Path:
    """分層回填的工作目錄（帳、編排鎖、每批的 hashes 檔）。`BACKFILL_DIR` 只給測試用（conftest 指到不存在的目錄）；
    生產一律 `data/backfill/`。"""
    return Path(os.environ.get(DIR_ENV) or REPO_ROOT / "data" / "backfill")


def state_path() -> Path:
    return work_dir() / "state.json"


@dataclass
class BudgetState:
    day: str = ""  # 台北日 YYYY-MM-DD
    tokens: int = 0  # 當日背景補齊已用（輸入＋輸出）
    reports: int = 0  # 當日背景補齊已處理份數
    measured: bool = True  # 當日的帳全部來自用量檔實測（有任何一批以估計入帳就是 False）

    def for_day(self, day: date) -> "BudgetState":
        """換日歸零。"""
        return self if self.day == day.isoformat() else BudgetState(day=day.isoformat())

    def remaining(self, limit: int) -> int:
        return max(limit - self.tokens, 0)


def load_state(path: Path) -> BudgetState:
    """讀帳。檔案不存在或壞掉＝空帳（壞掉時印一行警告由呼叫端負責；這裡不拋，以免一個壞檔讓背景永遠不跑）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return BudgetState()
    if not isinstance(data, dict):
        return BudgetState()
    try:
        return BudgetState(day=str(data.get("day") or ""), tokens=int(data.get("tokens") or 0),
                           reports=int(data.get("reports") or 0), measured=bool(data.get("measured", True)))
    except (TypeError, ValueError):
        return BudgetState()


def save_state(path: Path, state: BudgetState) -> None:
    """原子寫入（同目錄暫存檔＋os.replace）；目錄不存在就不寫（測試把路徑指到不存在的目錄）。"""
    if not path.parent.is_dir():
        return
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(asdict(state), fh, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


# ── 一輪的規劃（純函式） ────────────────────────────────────────────────


@dataclass(frozen=True)
class Candidate:
    file_hash: str
    report_date: date | None
    tasks: tuple[str, ...]  # 待補的派生（DERIVED_TASKS 的子集，順序同之）
    tokens: int  # 待補各項的估計 token（輸入＋輸出）合計


def candidate_from_row(file_hash: str, report_date: date | None, needs: Mapping[str, bool], head: str,
                       calibration: Calibration | None = None) -> Candidate:
    cal = calibration or Calibration()
    tasks = tuple(t for t in DERIVED_TASKS if needs.get(t))
    ins = input_tokens_by_task(head or "", tasks)
    tokens = sum(int(round(ins[t] * cal.factor(t) + cal.out_tokens(t))) for t in tasks)
    return Candidate(file_hash=file_hash, report_date=report_date, tasks=tasks, tokens=tokens)


@dataclass(frozen=True)
class RoundPlan:
    selected: tuple[Candidate, ...]
    tokens: int
    stopped_by: str | None  # None＝候選全部排入；"reports"＝份數上限；"tokens"＝token 上限
    oversized: tuple[str, ...] = ()  # 單篇估計就超過整份上限的研報（調高上限才補得到）


def plan_round(candidates: Sequence[Candidate], *, token_budget: int | None, max_reports: int | None,
               full_budget: int | None = None) -> RoundPlan:
    """依序排入候選，直到份數或 token 上限。順序就是候選的順序（新的先），不跳著挑小的：
    跳著挑會讓大篇永遠排不到。單篇估計大於 `full_budget`（整日上限）的研報略過並回報，否則它會永遠卡在隊首。
    """
    selected: list[Candidate] = []
    oversized: list[str] = []
    used = 0
    stopped: str | None = None
    for cand in candidates:
        if max_reports is not None and len(selected) >= max_reports:
            stopped = "reports"
            break
        if full_budget is not None and cand.tokens > full_budget:
            oversized.append(cand.file_hash)
            continue
        if token_budget is not None and used + cand.tokens > token_budget:
            stopped = "tokens"
            break
        selected.append(cand)
        used += cand.tokens
    return RoundPlan(selected=tuple(selected), tokens=used, stopped_by=stopped, oversized=tuple(oversized))


def chunked(items: Sequence, size: int) -> list[Sequence]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def measured_tokens(rows: Iterable[Mapping], hashes: set[str]) -> tuple[int, int]:
    """用量檔新增的行裡，屬於這批研報、派生任務的 (token 合計, 有 usage 的呼叫數)。重試的每一次都算（都計費）。"""
    total = calls = 0
    for row in rows:
        if row.get("task") not in DERIVED_TASKS or row.get("file_hash") not in hashes:
            continue
        used = usage_tokens(row)
        if used is None:
            continue
        total += used[0] + used[1]
        calls += 1
    return total, calls
