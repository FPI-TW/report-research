"""比較兩份離線評測結果 JSON，把「有沒有回歸」從人眼比表變成退出碼。

**存在的理由**：`eval/baselines/` 有五份基準線、`scripts/eval_retrieval.py` 的檔頭直接
教人 `--json > eval/after.json`，但全 repo 沒有任何東西**讀**這些檔案——判定回歸靠人眼
逐欄比，於是實務上沒人比。第一份真工作已經在手上：PR #140（`048eef7`）修掉問答檢索的
相關度門檻量綱錯配（rerank 後的 sigmoid 分拿去比以 fused 尺度校準的 `ASK_RELEVANCE_FLOOR`），
選中的脈絡篇數會從 6-9 回到約 15，而該 commit 自己寫下的誠實預測是「context_precision
可能因此下降；若明顯下降，正確的後續是重新校準門檻而不是退回量綱錯配」。也就是說現在有
一個**已合併、但沒有任何工具量得到**的檢索行為變更。這支腳本就是那個工具。

刻意的設計取捨（改動前先讀）：

- **只讀 `summary`，一個指標都不重算。** 重算等於在這裡複製一份評分規則，兩邊定義漂掉時
  比較器會理直氣壯地說謊。逐題資料仍在 `cases`，要下鑽請讀原始檔。
- **方向表是白名單，不是預設值。** 不認得的鍵一律印進「未分類」並讓退出碼變 3，不當成
  「越大越好」——`n_errors`、`median_cited_age`、`latency_ms_*` 都是越小越好，猜錯方向
  就是製造假綠。新增指標時請同時在 `METRIC_SPECS` 補一筆。
- **樣本數不同就拒絕給結論（退出碼 2），而不是照算 delta。** RAGAS 題集只有 8 題、
  ±0.05 全在噪音內，且 `run_ragas.aggregate` 會略過 error 題——`baseline-m2` 名目 n=8、
  實際只有 7 題進均值，光看 `n` 看不出來，所以這裡比的是 `n - n_errors - n_no_context`。
- **不動任何門檻。** `FAITHFULNESS_MIN` / `CONTEXT_PRECISION_MIN` / `ANSWER_RELEVANCY_MIN`
  是政策決定（量測紀錄在 `eval/run_ragas.py` 的常數旁），本工具只回答「相對於 baseline
  有沒有變差」，不回答「夠不夠好」。
- **量尺（META）只有一邊有記錄也算不可比（退出碼 2）。** 非 META 鍵只有一邊有時照舊只列出、
  不判定（例如舊基準線沒有 latency_ms_*）；但 META 鍵缺一邊代表「不知道那一份是用哪把尺量的」，
  不能當成同一把。後果是明知的：`eval/run_ragas.py` 開始記 `judge_model` 等三個鍵之後，拿新結果
  比任何舊的 RAGAS 基準線（含 `eval/baselines/baseline-2026-09-02.json`）一律回 2，直到用新版
  重跑出新的基準線為止。兩份都沒有記錄（舊檔比舊檔）維持可比。
- **latency 用相對容忍值。** 對 45,387 ms 的均值套絕對 0.03 等於「差 0.03 毫秒就是回歸」，
  那種紅燈只會讓人把工具關掉。[0,1] 尺度的指標與計數用絕對值，非 [0,1] 的用相對值。

吃得下三種結果形狀（都以 `summary` 為根）：
  1. `eval/run_ragas.py`         平坦數值 + `thresholds_pass`
  2. `scripts/eval_retrieval.py` 平坦數值（hit_rate / 新近度）
  3. `scripts/eval_extraction.py` 平坦數值（order_* / rating_* / tp_*）

用法（**兩份必須是同一套評測的產物**，跨套會被形狀指紋擋下）：
  # 檢索：scripts/eval_retrieval.py --json 寫出的前後兩份
  uv run python scripts/eval_compare.py --baseline eval/before.json --candidate eval/after.json
  # 問答：eval/run_ragas.py --out 寫出的兩份（跑的時候記得 --concurrency 1）
  make eval-compare BASE=eval/baselines/baseline-2026-07-29.json CAND=eval/candidate-ragas.json

退出碼：
  0  無劣化
  1  至少一項判定指標劣化超過容忍值
  2  不可比（樣本數／評分規則版本／queryset 參數／judge 量尺不同，或量尺只有一邊有記錄）
  3  有未分類指標，因此不敢宣稱沒有回歸（其餘皆無劣化）
"""

from __future__ import annotations

import argparse
import json
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── 方向語義 ────────────────────────────────────────────────────────────────
HIGHER = "higher"  # 越大越好，參與判定
LOWER = "lower"  # 越小越好，參與判定
FLAG = "flag"  # 布林旗標，True 為好；True→False 視為劣化
INFO = "info"  # 描述性統計，印出但**不判定方向**（方向本身有歧義）
SAMPLE = "sample"  # 樣本數，決定可比性
META = "meta"  # 評分規則／題集參數，不同即不可比

ABS = "abs"  # 容忍值為絕對差（[0,1] 尺度的比率、以及計數）
REL = "rel"  # 容忍值為相對比例（毫秒、天數這類非 [0,1] 尺度）


@dataclass(frozen=True)
class Spec:
    direction: str
    mode: str = ABS
    why: str = ""


# 方向表。**新增指標必須在這裡補一筆**，否則會被歸為「未分類」而讓退出碼變 3。
# 分組對應三支產生器，鍵名一律逐字照抄產生器的輸出。
METRIC_SPECS: dict[str, Spec] = {
    # ── eval/run_ragas.py（問答 RAGAS）────────────────────────────────────
    "faithfulness": Spec(HIGHER),
    "context_precision": Spec(HIGHER),
    "answer_relevancy": Spec(HIGHER),
    # 牆鐘量測，跨機器與跨負載本來就會抖；絕對容忍值在毫秒尺度沒有意義。
    "latency_ms_mean": Spec(LOWER, REL),
    "latency_ms_p50": Spec(LOWER, REL),
    "latency_ms_p95": Spec(LOWER, REL),
    "n": Spec(SAMPLE),
    # 計數用絕對容忍值：預設 0.03 之下，多一題 error 就是劣化，這是要的行為。
    "n_errors": Spec(LOWER, ABS, "runner 例外／逾時，整題不入均值"),
    "n_no_context": Spec(LOWER, ABS, "檢索不到脈絡"),
    # judge 出錯只讓該指標為 None（不整題 error、不動 n_effective），所以要另外計數：
    # 多一次就是劣化，逼人去看是不是量尺壞了，而不是讓均值少一題還照樣比。
    "n_judge_errors": Spec(LOWER, ABS, "judge 出錯（重試後）的指標數，該指標不入均值"),
    "citation_rate": Spec(HIGHER, ABS, "答案至少引用一個存在來源 [n] 的題數比例"),
    "simplified_residual_rate": Spec(LOWER, ABS, "答案整份被判為簡體（zh_hant.looks_simplified）的題數比例"),
    "n_truncated": Spec(LOWER, ABS, "生成撞到逾時上限、疑似被截斷的題數"),
    "thresholds_pass": Spec(FLAG, ABS, "run_ragas 的三個絕對門檻是否全過"),
    "thresholds_failed": Spec(INFO, ABS, "未達標項目清單（字串）"),
    # 量尺。judge 換了、提示改了、解讀規則改了，分數就不是同一把尺量的。
    "judge_model": Spec(META, ABS, "judge 模型不同＝換了尺"),
    "judge_prompt_sha": Spec(META, ABS, "judge 提示模板（含 payload 版型）改了＝換了尺"),
    "judge_schema_version": Spec(META, ABS, "judge 回應的解讀規則改了＝換了尺"),
    # ── scripts/eval_retrieval.py（檢索）──────────────────────────────────
    "n_cases": Spec(SAMPLE),
    "hit_rate": Spec(HIGHER),
    "mean_p_at_k": Spec(HIGHER),
    "recency_pass_rate": Spec(HIGHER),
    "median_cited_age": Spec(LOWER, REL, "引用中位年齡（天）"),
    "pct_over_max_age": Spec(LOWER, ABS, "超過 max_age_days 的引用比例"),
    "n_cited": Spec(INFO, ABS, "引用總數，隨脈絡篇數浮動"),
    "max_age_days": Spec(META, ABS, "queryset 的新近度判定參數；不同則兩份的新近度指標定義不同"),
    # ── scripts/eval_extraction.py（抽取層 golden set，docs/EXTRACTION.md §8）──
    "order_hit_rate": Spec(HIGHER),
    "order_pair_acc": Spec(HIGHER),
    "order_pair_acc_within": Spec(HIGHER),
    "order_kendall_tau": Spec(HIGHER),
    "order_pair_acc_case_mean": Spec(INFO, ABS, "逐檔平均，僅供對照；判定用配對加總的 order_pair_acc"),
    "rating_coverage": Spec(HIGHER),
    "rating_accuracy": Spec(HIGHER),
    "tp_coverage": Spec(HIGHER),
    "tp_accuracy": Spec(HIGHER),
    "hallucination_rate": Spec(LOWER, ABS, "evidence 錨不回比例，prefix 計為錨不回"),
    # 去空白字元數是健檢：換抽取器本來就會動，方向不能一概而論（多抽到頁首頁尾未必是好事）。
    "chars_nows_total": Spec(INFO, REL, "文字健檢，非主指標；大幅下降＝新抽取器在吃字"),
    "n_cases_with_order": Spec(SAMPLE),
    "n_order_sentences": Spec(SAMPLE),
    "n_order_pairs": Spec(SAMPLE),
    "n_order_pairs_within": Spec(SAMPLE),
    "n_golden_tp": Spec(SAMPLE),
    "n_evidence": Spec(INFO, ABS, "帶 evidence 的訊號數，隨擷取結果浮動"),
    "n_evidence_prefix": Spec(INFO, ABS, "以 prefix 層錨定的 evidence 數"),
    "n_prefilled": Spec(SAMPLE),
    "n_draft": Spec(SAMPLE),
    "n_reviewed": Spec(SAMPLE),
}

STATUS_BETTER = "改善"
STATUS_SAME = "持平"
STATUS_WORSE = "劣化"
STATUS_UNCOMPARABLE = "不可比"
STATUS_NONE = "—"

_ARROW = {HIGHER: "↑", LOWER: "↓", FLAG: "旗標", INFO: "—", SAMPLE: "—", META: "—"}


# 形狀指紋。三套產生器共用 `n` / `n_errors` 這類鍵，所以「有共同的判定指標」不足以
# 證明兩份可比——RAGAS 結果與檢索結果會在 n_errors 上比出一個看起來很正常的 delta。
_SHAPE_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ragas", ("faithfulness", "context_precision", "answer_relevancy")),
    # 抽取層排在 retrieval 之前：它也帶 n_cases，靠 order_* 鍵區分。
    ("extraction", ("order_pair_acc", "order_hit_rate")),
    ("retrieval", ("hit_rate", "n_cases")),
)


def detect_shape(summary: dict) -> str:
    for name, markers in _SHAPE_MARKERS:
        if any(m in summary for m in markers):
            return name
    return "unknown"


class CompareError(Exception):
    """輸入不是評測結果檔（缺 summary 之類）。呼叫端轉成退出碼 2。"""


@dataclass
class Row:
    key: str
    spec: Spec
    base: Any
    cand: Any
    delta: float | None = None
    status: str = STATUS_NONE
    note: str = ""
    regression: bool = False

    @property
    def gating(self) -> bool:
        return self.spec.direction in (HIGHER, LOWER, FLAG)


@dataclass
class Comparison:
    baseline_path: str
    candidate_path: str
    tolerance: float
    rel_tolerance: float
    sample_base: dict[str, int]
    sample_cand: dict[str, int]
    shape_base: str = "unknown"
    shape_cand: str = "unknown"
    incomparable: list[str] = field(default_factory=list)
    rows: list[Row] = field(default_factory=list)
    only_in_baseline: list[str] = field(default_factory=list)
    only_in_candidate: list[str] = field(default_factory=list)
    unclassified: list[str] = field(default_factory=list)
    config_diff: list[tuple[str, Any, Any]] = field(default_factory=list)
    notes_base: list[str] = field(default_factory=list)
    notes_cand: list[str] = field(default_factory=list)

    @property
    def regressions(self) -> list[Row]:
        return [r for r in self.rows if r.regression]

    @property
    def exit_code(self) -> int:
        if self.incomparable:
            return 2
        if self.regressions:
            return 1
        if self.unclassified:
            return 3
        return 0


def load_result(path: str | Path) -> dict:
    """讀結果檔並確認它有 summary 物件。三支產生器都寫 {"summary": {...}, ...}。"""
    p = Path(path)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise CompareError(f"讀不到檔案：{p}") from e
    except json.JSONDecodeError as e:
        raise CompareError(f"{p}：不是合法 JSON（{e}）") from e
    if not isinstance(data, dict) or not isinstance(data.get("summary"), dict):
        raise CompareError(f"{p}：不是評測結果檔（缺 summary 物件）")
    return data


def sample_facts(summary: dict) -> dict[str, int]:
    """抽出決定可比性的樣本數。

    `n` 是名目題數，會騙人：`aggregate` 略過 error 與無脈絡題，所以真正進均值的是
    `n - n_errors - n_no_context`（baseline-m2 名目 8、實際 7）。
    """
    facts: dict[str, int] = {}
    # 抽取層：draft 與 reviewed 的標註不是同一種東西，覆核筆數不同就不可比。
    for key in ("n", "n_cases", "n_reviewed", "n_order_sentences"):
        val = summary.get(key)
        if isinstance(val, int) and not isinstance(val, bool):
            facts[key] = val
    if "n" in facts:
        effective = facts["n"]
        for key in ("n_errors", "n_no_context"):
            val = summary.get(key)
            if isinstance(val, int) and not isinstance(val, bool):
                effective -= val
        facts["n_effective"] = effective
    return facts


def _threshold(spec: Spec, base_val: float, tolerance: float, rel_tolerance: float) -> float:
    if spec.mode == REL and base_val:
        return abs(base_val) * rel_tolerance
    return tolerance


def _classify(spec: Spec, base_val: Any, cand_val: Any, tolerance: float, rel_tolerance: float) -> Row:
    row = Row(key="", spec=spec, base=base_val, cand=cand_val)
    if spec.direction == FLAG:
        if isinstance(base_val, bool) and isinstance(cand_val, bool) and base_val != cand_val:
            row.status = STATUS_WORSE if base_val else STATUS_BETTER
            row.regression = base_val and not cand_val
        else:
            row.status = STATUS_SAME if base_val == cand_val else STATUS_UNCOMPARABLE
        return row

    numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (base_val, cand_val))
    if numeric:
        row.delta = float(cand_val) - float(base_val)

    if spec.direction not in (HIGHER, LOWER):
        row.status = STATUS_NONE
        return row
    if not numeric:
        row.status = STATUS_UNCOMPARABLE
        row.note = "缺值"
        return row

    worse_by = -row.delta if spec.direction == HIGHER else row.delta
    limit = _threshold(spec, float(base_val), tolerance, rel_tolerance)
    if worse_by > limit:
        row.status = STATUS_WORSE
        row.regression = True
    elif -worse_by > limit:
        row.status = STATUS_BETTER
    else:
        row.status = STATUS_SAME
    return row


_ORDER = {key: i for i, key in enumerate(METRIC_SPECS)}


def _order(key: str) -> tuple[int, str]:
    """輸出順序沿用方向表的宣告順序（＝產生器的輸出順序），未知鍵墊底。"""
    return (_ORDER[key], "") if key in _ORDER else (len(_ORDER), key)


def build_comparison(
    base_doc: dict,
    cand_doc: dict,
    *,
    baseline_path: str = "baseline",
    candidate_path: str = "candidate",
    tolerance: float = 0.03,
    rel_tolerance: float = 0.25,
) -> Comparison:
    """純函式：兩份結果文件 → 逐指標對照與可比性結論。不做 I/O、不印東西。"""
    base, cand = base_doc["summary"], cand_doc["summary"]
    cmp_ = Comparison(
        baseline_path=baseline_path,
        candidate_path=candidate_path,
        tolerance=tolerance,
        rel_tolerance=rel_tolerance,
        sample_base=sample_facts(base),
        sample_cand=sample_facts(cand),
        shape_base=detect_shape(base),
        shape_cand=detect_shape(cand),
    )

    for key in sorted(set(base) | set(cand), key=_order):
        in_base, in_cand = key in base, key in cand
        if key not in METRIC_SPECS:
            cmp_.unclassified.append(key)
            continue
        if not in_cand:
            cmp_.only_in_baseline.append(key)
            continue
        if not in_base:
            cmp_.only_in_candidate.append(key)
            continue
        row = _classify(METRIC_SPECS[key], base[key], cand[key], tolerance, rel_tolerance)
        row.key = key
        cmp_.rows.append(row)

    cmp_.incomparable = _comparability(base, cand, cmp_)
    cmp_.config_diff = _config_diff(base_doc.get("config"), cand_doc.get("config"))
    cmp_.notes_base = _as_notes(base_doc.get("notes"))
    cmp_.notes_cand = _as_notes(cand_doc.get("notes"))
    return cmp_


def _as_notes(raw: Any) -> list[str]:
    """`notes` 兩種形狀都真實存在：舊的研報基準線是字串陣列，baseline-2026-07-29 是單一
    字串。對字串 `for x in raw` 會逐字迭代——不會拋例外，只會印出幾百行單字。"""
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x) for x in raw]
    return []


def _comparability(base: dict, cand: dict, cmp_: Comparison) -> list[str]:
    reasons: list[str] = []
    if cmp_.shape_base != cmp_.shape_cand and "unknown" not in (cmp_.shape_base, cmp_.shape_cand):
        reasons.append(f"結果形狀不同：{cmp_.shape_base} → {cmp_.shape_cand}（三套評測不能互比）")
    for key in sorted(set(cmp_.sample_base) | set(cmp_.sample_cand)):
        b, c = cmp_.sample_base.get(key), cmp_.sample_cand.get(key)
        if b != c:
            reasons.append(f"樣本數不同：{key} {b} → {c}")
    for key, spec in METRIC_SPECS.items():
        if spec.direction != META:
            continue
        if key in base and key in cand and base[key] != cand[key]:
            reasons.append(f"評分規則／題集參數不同：{key} {base[key]} → {cand[key]}（{spec.why}）")
        elif (key in base) != (key in cand):
            side = "baseline" if key in base else "candidate"
            reasons.append(
                f"量尺只有 {side} 有記錄：{key}（舊格式結果檔沒有記錄量尺，無法確認兩份用同一把尺；"
                "請用同一版 run_ragas 重跑兩邊）"
            )
    if not any(r.gating for r in cmp_.rows):
        reasons.append("兩份檔案沒有任何共同的判定指標——形狀不同？（RAGAS／研報／檢索三套不能互比）")
    return reasons


def _config_diff(base_cfg: Any, cand_cfg: Any) -> list[tuple[str, Any, Any]]:
    """研報結果檔帶 config 快照：旋鈕變了才解釋得了指標為什麼動。純資訊，不判定。"""
    if not isinstance(base_cfg, dict) or not isinstance(cand_cfg, dict):
        return []
    return [
        (k, base_cfg.get(k), cand_cfg.get(k))
        for k in sorted(set(base_cfg) | set(cand_cfg))
        if base_cfg.get(k) != cand_cfg.get(k)
    ]


# ── 輸出 ────────────────────────────────────────────────────────────────────
def fmt_value(v: Any) -> str:
    if v is None:
        return "n/a"
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float):
        return f"{v:.4f}" if abs(v) < 1000 else f"{v:,.0f}"
    if isinstance(v, list):
        return "、".join(str(x) for x in v) if v else "（無）"
    return str(v)


def fmt_delta(row: Row) -> str:
    if row.delta is None:
        return "—"
    # 計數指標（n_errors 之類）兩邊都是整數時印 +1 而非 +1.0000：小數位會讓人以為
    # 那是均值，進而誤以為容忍值有在做插值。
    ints = all(isinstance(v, int) and not isinstance(v, bool) for v in (row.base, row.cand))
    if ints:
        body = f"{row.delta:+,.0f}"
    else:
        body = f"{row.delta:+.4f}" if abs(row.delta) < 1000 else f"{row.delta:+,.0f}"
    if row.spec.mode == REL and isinstance(row.base, (int, float)) and row.base:
        body += f" ({row.delta / row.base:+.1%})"
    return body


def _width(text: str) -> int:
    """顯示寬度：CJK 全形算 2。等寬終端下對齊靠這個，len() 會讓中文欄位短一半。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int, *, right: bool = False) -> str:
    fill = " " * max(0, width - _width(text))
    return fill + text if right else text + fill


def _table(rows: list[Row]) -> list[str]:
    header = ("指標", "方向", "baseline", "candidate", "delta", "判定")
    cells = [header]
    for r in rows:
        status = r.status + (f"（{r.note}）" if r.note else "")
        cells.append(
            (
                r.key,
                _ARROW[r.spec.direction],
                fmt_value(r.base),
                fmt_value(r.cand),
                fmt_delta(r),
                status,
            )
        )
    widths = [max(_width(row[i]) for row in cells) for i in range(len(header))]
    return [
        "  "
        + _pad(c[0], widths[0])
        + "  "
        + _pad(c[1], widths[1])
        + "  "
        + _pad(c[2], widths[2], right=True)
        + "  "
        + _pad(c[3], widths[3], right=True)
        + "  "
        + _pad(c[4], widths[4], right=True)
        + "  "
        + c[5]
        for c in cells
    ]


def render(cmp_: Comparison) -> str:
    def sample_str(facts: dict[str, int]) -> str:
        return "、".join(f"{k}={v}" for k, v in facts.items()) or "未知"

    out = [
        "=== eval 結果比較 ===",
        f"baseline : {cmp_.baseline_path}",
        f"candidate: {cmp_.candidate_path}",
        f"形狀     : {cmp_.shape_base}  →  {cmp_.shape_cand}",
        f"樣本     : {sample_str(cmp_.sample_base)}  →  {sample_str(cmp_.sample_cand)}",
        f"容忍值   : 絕對 {cmp_.tolerance}；相對 {cmp_.rel_tolerance:.0%}（僅非 [0,1] 尺度指標）",
    ]

    if cmp_.incomparable:
        out += ["", "【不可比】以下 delta 僅供參考，不構成通過／失敗結論："]
        out += [f"  - {r}" for r in cmp_.incomparable]

    gating = [r for r in cmp_.rows if r.gating]
    if gating:
        out += ["", "判定指標（↑ 越大越好／↓ 越小越好）"] + _table(gating)

    descriptive = [r for r in cmp_.rows if not r.gating]
    if descriptive:
        out += ["", "描述性指標（刻意不判定方向）"] + _table(descriptive)

    if cmp_.unclassified:
        out += [
            "",
            "【未分類指標】方向表沒有這些鍵，因此無法判斷變好或變壞：",
        ]
        out += [f"  - {k}" for k in cmp_.unclassified]
        out += ["  請在 scripts/eval_compare.py 的 METRIC_SPECS 補上方向後重跑。"]

    if cmp_.only_in_baseline or cmp_.only_in_candidate:
        out += ["", "只出現在單邊的指標（無法比較）："]
        out += [f"  - 只在 baseline：{k}" for k in cmp_.only_in_baseline]
        out += [f"  - 只在 candidate：{k}" for k in cmp_.only_in_candidate]

    if cmp_.config_diff:
        out += ["", "config 差異（解釋指標為什麼動，不參與判定）："]
        out += [f"  - {k}: {fmt_value(b)} → {fmt_value(c)}" for k, b, c in cmp_.config_diff]

    # 兩份 notes 一模一樣時只印一次：同一支 harness 產出的前後兩跑常常照抄同一段
    # 但書，印兩遍會把真正的差異推出畫面。
    if cmp_.notes_base and cmp_.notes_base == cmp_.notes_cand:
        sections = [("兩份共同", cmp_.notes_base)]
    else:
        sections = [("baseline", cmp_.notes_base), ("candidate", cmp_.notes_cand)]
    for label, notes in sections:
        if notes:
            out += ["", f"{label} 的 notes（產生者自己的但書，請讀）："]
            out += [f"  - {n}" for n in notes]

    out += ["", _verdict(cmp_)]
    return "\n".join(out)


def _verdict(cmp_: Comparison) -> str:
    code = cmp_.exit_code
    if code == 2:
        return "結論：不可比，拒絕給通過／失敗（退出碼 2）"
    if code == 1:
        names = "、".join(r.key for r in cmp_.regressions)
        return f"結論：劣化 {len(cmp_.regressions)} 項 —— {names}（退出碼 1）"
    if code == 3:
        return f"結論：判定指標無劣化，但有 {len(cmp_.unclassified)} 個未分類指標，不敢宣稱沒有回歸（退出碼 3）"
    return "結論：無劣化（退出碼 0）"


def to_json(cmp_: Comparison) -> dict:
    return {
        "baseline": cmp_.baseline_path,
        "candidate": cmp_.candidate_path,
        "tolerance": cmp_.tolerance,
        "rel_tolerance": cmp_.rel_tolerance,
        "shape": {"baseline": cmp_.shape_base, "candidate": cmp_.shape_cand},
        "sample": {"baseline": cmp_.sample_base, "candidate": cmp_.sample_cand},
        "incomparable": cmp_.incomparable,
        "metrics": [
            {
                "key": r.key,
                "direction": r.spec.direction,
                "baseline": r.base,
                "candidate": r.cand,
                "delta": r.delta,
                "status": r.status,
                "note": r.note,
                "regression": r.regression,
                "gating": r.gating,
            }
            for r in cmp_.rows
        ],
        "unclassified": cmp_.unclassified,
        "only_in_baseline": cmp_.only_in_baseline,
        "only_in_candidate": cmp_.only_in_candidate,
        "config_diff": [{"key": k, "baseline": b, "candidate": c} for k, b, c in cmp_.config_diff],
        "exit_code": cmp_.exit_code,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="比較兩份 eval 結果 JSON，劣化超過容忍值即非零退出",
        epilog="退出碼：0 無劣化／1 有劣化／2 不可比／3 有未分類指標",
    )
    parser.add_argument("--baseline", required=True, help="基準線結果 JSON")
    parser.add_argument("--candidate", required=True, help="待比較結果 JSON")
    parser.add_argument("--tolerance", type=float, default=0.03, help="絕對容忍值（[0,1] 指標與計數），預設 0.03")
    parser.add_argument(
        "--rel-tolerance",
        type=float,
        default=0.25,
        help="相對容忍值（latency 等非 [0,1] 尺度指標），預設 0.25",
    )
    parser.add_argument("--json", action="store_true", help="輸出機器可讀 JSON 而非對照表")
    args = parser.parse_args(argv)

    try:
        base_doc = load_result(args.baseline)
        cand_doc = load_result(args.candidate)
    except CompareError as e:
        print(f"錯誤：{e}")
        return 2

    cmp_ = build_comparison(
        base_doc,
        cand_doc,
        baseline_path=args.baseline,
        candidate_path=args.candidate,
        tolerance=args.tolerance,
        rel_tolerance=args.rel_tolerance,
    )
    print(json.dumps(to_json(cmp_), ensure_ascii=False, indent=2) if args.json else render(cmp_))
    return cmp_.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
