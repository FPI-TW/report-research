"""上線驗收：一份 `eval/run_ragas.py` 的結果，對該 profile 的凍結題集與政策門檻逐項判定（零 LLM、不連 DB）。

用法：`make eval-gate PROFILE=<名稱> RESULT=<run_ragas 結果檔>`
（或 `uv run python scripts/eval_gate.py --profile … --result …`）。

**退出碼是結論**：

- 0：三個門檻指標（F>0.9、CP>0.8、AR>0.55）全過，或沒過的都在 profile 的豁免清單裡。
- 1：至少一項**未豁免**的指標沒過。
- 2：不能下結論——結果不完整（有 error、judge 沒量到的題、題數不等於凍結題集）、或不可比（不是這份凍結題集
  量的、跑了 `--limit`／`--scope`、judge 解讀規則版本不是現行的、量尺系譜不是 profile 要求的那個），或 profile
  的評測設定本身不合格。

刻意的設計：

- **門檻是政策，不是設定**：數值與 `eval/run_ragas.py` 的 `FAITHFULNESS_MIN`／`CONTEXT_PRECISION_MIN`／
  `ANSWER_RELEVANCY_MIN` 相同（`tests/test_eval_gate.py` 對帳，那邊有量測紀錄）。profile 只能**豁免**某一項，
  不能改數值，而且每一項豁免都必須附理由與決定日期（`eval/profile_eval.py` 的 `load_eval_config`）；豁免的
  指標照樣印出數值與「沒過」，只是不讓退出碼變 1。
- 只讀結果檔的 `summary` 與 `config`，一個分數都不重算（同 `scripts/eval_compare.py` 的理由）。結果檔自己的
  `thresholds_pass` 不採信：它是產生當下的門檻算的（舊結果曾是 AR 0.85 的時代）。
- 這不是回歸比較：上線驗收回答「夠不夠好」，`make eval-compare` 回答「有沒有變差」，兩者都要跑。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.judge_schema import JUDGE_SCHEMA_VERSION  # noqa: E402
from eval import profile_eval as pe  # noqa: E402
from scripts.eval_compare import CompareError, detect_shape, load_result  # noqa: E402

# 政策門檻（數值與 eval/run_ragas.py 相同，量測紀錄在那邊的常數旁；對帳見模組 docstring）。
THRESHOLDS: dict[str, float] = {
    "faithfulness": 0.9,
    "context_precision": 0.8,
    "answer_relevancy": 0.55,
}
LABELS = {"faithfulness": "Faithfulness", "context_precision": "Context Precision",
          "answer_relevancy": "Answer Relevancy"}
JUDGE_META_KEYS = ("judge_model", "judge_prompt_sha", "judge_schema_version")

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_UNDECIDABLE = 2

STATUS_PASS = "過"
STATUS_FAIL = "未過"
STATUS_WAIVED = "未過（豁免）"


@dataclass
class MetricVerdict:
    metric: str
    value: float | None
    floor: float
    status: str
    waiver: str | None = None


@dataclass
class GateResult:
    profile: str
    result_path: str
    undecidable: list[str] = field(default_factory=list)
    metrics: list[MetricVerdict] = field(default_factory=list)
    lineage: str | None = None

    @property
    def exit_code(self) -> int:
        if self.undecidable:
            return EXIT_UNDECIDABLE
        if any(m.status == STATUS_FAIL for m in self.metrics):
            return EXIT_FAIL
        return EXIT_PASS


def result_lineage(doc: dict) -> str | None:
    """結果檔的量尺系譜：summary 的 META 鍵；較舊的結果只記在 config.judge.lineage。"""
    value = doc["summary"].get("judge_lineage")
    if isinstance(value, str) and value:
        return value
    judge = (doc.get("config") or {}).get("judge")
    value = judge.get("lineage") if isinstance(judge, dict) else None
    return value if isinstance(value, str) and value else None


def _is_count(value: Any) -> bool:
    return type(value) is int and value >= 0


def undecidable_reasons(doc: dict, dataset: pe.ProfileDataset, config: pe.EvalConfig) -> list[str]:
    """不能下結論的原因（空＝可以判定）。"""
    summary = doc["summary"]
    cfg = doc.get("config")
    if not isinstance(cfg, dict):
        cfg = {}
    out: list[str] = []
    if detect_shape(summary) != "ragas":
        return ["不是 eval/run_ragas.py 的結果檔（上線驗收只接 RAGAS 結果）"]
    sha = cfg.get("queryset_sha256")
    if sha != dataset.sha256:
        out.append(f"不是這份凍結題集量的：結果的 queryset_sha256={str(sha)[:12]}…，"
                   f"{dataset.rel_path} 是 {dataset.sha256[:12]}…")
    for key in ("limit", "scope"):
        if cfg.get(key) is not None:
            out.append(f"結果跑了 --{key}={cfg[key]}，上線驗收要整份題集")
    expected = len(dataset.questions)
    n = summary.get("n")
    if n != expected:
        out.append(f"題數不符：結果 n={n}，凍結題集 {expected} 題")
    for key in ("n_errors", "n_no_context", "n_judge_errors"):
        value = summary.get(key, 0)
        if not _is_count(value) or value:
            out.append(f"結果不完整：{key}={value}")
    for metric in THRESHOLDS:
        if summary.get(metric) is None:
            out.append(f"結果不完整：{metric} 沒有值")
        n_eff = summary.get(f"n_effective_{metric}")
        if n_eff != expected:
            out.append(f"結果不完整：{metric} 只有 {n_eff} 題入均值（應 {expected} 題）")
    missing = [k for k in JUDGE_META_KEYS if k not in summary]
    if missing:
        out.append(f"結果沒有記錄量尺（{'、'.join(missing)}）：請用現行版 run_ragas 重跑")
    elif summary["judge_schema_version"] != JUDGE_SCHEMA_VERSION:
        out.append(f"judge 解讀規則版本 v{summary['judge_schema_version']} 不是現行的 v{JUDGE_SCHEMA_VERSION}")
    lineage = result_lineage(doc)
    if config.judge_lineage is not None and lineage != config.judge_lineage:
        out.append(f"量尺系譜不符：結果是 {lineage or '未記錄'}，profile 要求 {config.judge_lineage}")
    return out


def evaluate(doc: dict, dataset: pe.ProfileDataset, config: pe.EvalConfig, *, profile: str = "",
             result_path: str = "") -> GateResult:
    """純函式：結果檔＋凍結題集＋評測設定 → 逐項判定與退出碼。"""
    res = GateResult(profile=profile, result_path=result_path, lineage=result_lineage(doc))
    res.undecidable = undecidable_reasons(doc, dataset, config)
    if res.undecidable and detect_shape(doc["summary"]) != "ragas":
        return res
    for metric, floor in THRESHOLDS.items():
        value = doc["summary"].get(metric)
        passed = isinstance(value, (int, float)) and not isinstance(value, bool) and value > floor
        waiver = config.waivers.get(metric)
        if passed:
            status = STATUS_PASS
        else:
            status = STATUS_WAIVED if waiver else STATUS_FAIL
        res.metrics.append(MetricVerdict(
            metric=metric, value=value if isinstance(value, (int, float)) else None, floor=floor, status=status,
            waiver=f"{waiver.reason}（{waiver.decided_on.isoformat()}）" if waiver else None,
        ))
    return res


def render(res: GateResult) -> str:
    out = ["=== 上線驗收 ===", f"profile : {res.profile}", f"結果    : {res.result_path}",
           f"量尺系譜: {res.lineage or '未記錄'}", ""]
    for m in res.metrics:
        value = f"{m.value:.3f}" if isinstance(m.value, float) else str(m.value)
        out.append(f"  {LABELS[m.metric]:<18} {value:>6}  (門檻 > {m.floor})  {m.status}")
        if m.waiver:
            out.append(f"    豁免理由：{m.waiver}")
    if res.undecidable:
        out += ["", "【不能下結論】"] + [f"  - {r}" for r in res.undecidable]
    code = res.exit_code
    verdict = {
        EXIT_PASS: "結論：通過上線驗收（退出碼 0）",
        EXIT_FAIL: "結論：有未豁免的指標沒過（退出碼 1）",
        EXIT_UNDECIDABLE: "結論：結果不完整或不可比，不給通過／失敗（退出碼 2）",
    }[code]
    return "\n".join(out + ["", verdict])


def to_json(res: GateResult) -> dict:
    return {
        "profile": res.profile, "result": res.result_path, "lineage": res.lineage,
        "undecidable": res.undecidable,
        "metrics": [{"metric": m.metric, "value": m.value, "floor": m.floor, "status": m.status, "waiver": m.waiver}
                    for m in res.metrics],
        "exit_code": res.exit_code,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="上線驗收：RAGAS 結果對 profile 的凍結題集與政策門檻逐項判定",
                                     epilog="退出碼：0 通過／1 有未豁免的指標沒過／2 結果不完整或不可比")
    parser.add_argument("--profile", required=True, help="profile 名稱（profiles/ 底下的目錄名）或目錄路徑")
    parser.add_argument("--result", required=True, help="eval/run_ragas.py 寫出的結果 JSON")
    parser.add_argument("--json", action="store_true", help="輸出機器可讀 JSON")
    args = parser.parse_args(argv)
    try:
        profile_dir = pe.resolve_profile_arg(args.profile)
        config = pe.load_eval_config(profile_dir)
        dataset = pe.load_profile_dataset(profile_dir)
        doc = load_result(args.result)
    except (pe.EvalProfileError, CompareError, OSError, ValueError) as exc:
        print(f"錯誤：{exc}")
        return EXIT_UNDECIDABLE
    res = evaluate(doc, dataset, config, profile=profile_dir.name, result_path=args.result)
    print(json.dumps(to_json(res), ensure_ascii=False, indent=2) if args.json else render(res))
    return res.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
