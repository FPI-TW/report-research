"""profile 範圍的評測設定：凍結題集在哪、凍結指紋對不對、上線驗收的豁免與量尺系譜。

每家券商導入時建自己的凍結題集（`scripts/draft_eval_questions.py`：LLM 草擬 → 研究員審 → `freeze`），放在
`profiles/<name>/eval/`：

- `questions.json`：題集本體，與 `eval/question_contract.py` **同一套契約**（version 2、四個欄位、id 連號）。
  `eval/run_ragas.py` 等入口照舊只讀這四個欄位。
- `questions.manifest.json`：凍結指紋與每題的審核紀錄（類型、支撐研報的 `file_hash`、研究員有沒有改過）。
  讀題集時一律核對：題集檔的 sha256 必須等於清單記的值、題號逐一對上、題數與類型涵蓋達到下方政策。
  任一不符就拒讀（`EvalProfileError`）——凍結的題集被手改過，量出來的分數就不是同一把尺。
- `eval.toml`（選填）：上線驗收（`scripts/eval_gate.py`）的設定，鍵見 `load_eval_config`。驗證 fail-closed：
  未知鍵、型別錯、豁免缺理由一律拋錯，不做「認不得就忽略」。

**廷豐不搬家**：廷豐的凍結題集仍是 `eval/ragas_questions.json`（`tests/test_eval_question_contract.py` 守內容、
基準線記它的 sha256）。`profiles/tingfong/eval/eval.toml` 以 `[questions].legacy_repo_path` 指回它，所以
`--profile tingfong` 與不帶 `--profile` 讀到的是同一份檔、同一個 sha256。不指定 `--profile` 時各入口的行為與
加入本模組之前逐字相同。

**刻意只 import 標準函式庫與 `eval.question_contract`**：容器的 scheduler（`deploy/container/scheduler.py`）
要用 `own_questions_path` 判斷「這個 profile 有沒有自己的題集」，上線驗收也是離線工具；兩者都不該為此載入
DB、模型或 LLM 設定。
"""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from eval.question_contract import load_dataset, validate_dataset

REPO_ROOT = Path(__file__).resolve().parents[1]
PROFILES_DIR = REPO_ROOT / "profiles"
# 與 app/profile.py 的 PROFILE_DIR_ENV／DEFAULT_PROFILE_DIR 同值（tests/test_profile_eval.py 釘住）；
# 這裡不 import app.profile，理由見模組 docstring。
PROFILE_DIR_ENV = "PROFILE_DIR"
DEFAULT_PROFILE_DIR = "profiles/tingfong"
# 不帶 --profile 時各入口的預設題集（＝廷豐的凍結題集）。
DEFAULT_DATASET_REL = "eval/ragas_questions.json"

EVAL_DIR = "eval"
QUESTIONS_FILE = "questions.json"
MANIFEST_FILE = "questions.manifest.json"
CONFIG_FILE = "eval.toml"
MANIFEST_FORMAT = "eval-question-manifest"
MANIFEST_VERSION = 1

# ── 題集政策（已定案：至少 30 題，涵蓋個股、產業、總經、跨篇比較）────────────────────────
# 類型詞彙是封閉的；待審檔、清單與草擬 prompt 都用這組鍵。顯示名稱只給人看。
CATEGORY_STOCK = "stock"
CATEGORY_INDUSTRY = "industry"
CATEGORY_MACRO = "macro"
CATEGORY_CROSS = "cross_report"
CATEGORIES: tuple[str, ...] = (CATEGORY_STOCK, CATEGORY_INDUSTRY, CATEGORY_MACRO, CATEGORY_CROSS)
CATEGORY_LABELS: Mapping[str, str] = MappingProxyType({
    CATEGORY_STOCK: "個股", CATEGORY_INDUSTRY: "產業", CATEGORY_MACRO: "總經", CATEGORY_CROSS: "跨篇比較",
})
MIN_QUESTIONS = 30
# 每一類至少幾題才算「涵蓋」：30 題平均分到四類是 7–8 題；5 題讓某一類偏少仍可凍結，
# 但不會只有一兩題（那一類的分數就只是一兩題的抖動）。
MIN_PER_CATEGORY = 5
# 跨篇比較題至少要有兩份支撐研報，否則它不是跨篇題。
MIN_SUPPORT = {CATEGORY_CROSS: 2}
DEFAULT_MIN_SUPPORT = 1

# 上線驗收可以豁免的指標（＝run_ragas 的三個門檻指標）。
GATE_METRICS: tuple[str, ...] = ("faithfulness", "context_precision", "answer_relevancy")
WAIVER_REASON_MIN_CHARS = 10


class EvalProfileError(ValueError):
    """profile 的評測設定或凍結題集不合格。訊息帶檔案與原因。"""


@dataclass(frozen=True)
class Waiver:
    metric: str
    reason: str
    decided_on: date


@dataclass(frozen=True)
class EvalConfig:
    """`profiles/<name>/eval/eval.toml` 的內容（檔案不存在時是全預設）。"""

    legacy_repo_path: str | None = None
    judge_lineage: str | None = None
    waivers: Mapping[str, Waiver] = field(default_factory=lambda: MappingProxyType({}))


@dataclass(frozen=True)
class ProfileDataset:
    """一個 profile 的凍結題集：題目、題集檔的 sha256，以及（profile 自有題集才有的）清單。"""

    profile_dir: Path
    path: Path
    questions: list[dict]
    sha256: str
    manifest: dict | None  # None＝沿用 repo 內既有題集（廷豐），由該題集自己的測試守內容

    @property
    def rel_path(self) -> str:
        return display_path(self.path)


def display_path(path: Path) -> str:
    """repo 內的檔案記相對路徑（結果檔與基準檔可攜），repo 外的記絕對路徑。"""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path.resolve())


# ── profile 目錄 ─────────────────────────────────────────────────────────


def resolve_profile_arg(spec: str) -> Path:
    """`--profile` 的值 → profile 目錄。只有名稱（不含 `/`）時是 `profiles/<名稱>`；含路徑時相對 repo 根。"""
    raw = (spec or "").strip()
    if not raw:
        raise EvalProfileError("--profile 不可為空")
    if "/" not in raw and os.sep not in raw:
        path = PROFILES_DIR / raw
    else:
        path = Path(raw)
        path = path if path.is_absolute() else REPO_ROOT / path
    if not path.is_dir():
        raise EvalProfileError(f"找不到 profile 目錄：{path}")
    return path.resolve()


def active_profile_dir(env: Mapping[str, str] | None = None) -> Path:
    """目前環境的 profile 目錄（`PROFILE_DIR`，規則與 `app.profile.resolve_profile_dir` 相同）。"""
    env = os.environ if env is None else env
    value = (env.get(PROFILE_DIR_ENV) or "").strip() or DEFAULT_PROFILE_DIR
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def own_questions_path(profile_dir: Path) -> Path | None:
    """profile 自己帶的凍結題集（`eval/questions.json`）；沒有就回 None。廷豐的沿用題集不算。"""
    path = Path(profile_dir) / EVAL_DIR / QUESTIONS_FILE
    return path if path.is_file() else None


# ── eval.toml ────────────────────────────────────────────────────────────


def _reject_unknown(file: Path, table: Mapping[str, Any], allowed: set[str], prefix: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        raise EvalProfileError(f"{file}：{prefix}有未知的鍵 {'、'.join(unknown)}（可用：{'、'.join(sorted(allowed))}）")


def _table(file: Path, data: Mapping[str, Any], key: str) -> dict:
    value = data.get(key, {})
    if not isinstance(value, dict):
        raise EvalProfileError(f"{file}：{key} 必須是 table")
    return value


def load_eval_config(profile_dir: Path) -> EvalConfig:
    """讀 `eval.toml`；檔案不存在＝全預設。

    ```toml
    [questions]
    legacy_repo_path = "eval/ragas_questions.json"   # 只給沿用 repo 內既有題集的 profile（廷豐）

    [gate]
    judge_lineage = "gpt-6-luna-2026-11"   # 選填：上線驗收要求的量尺系譜（結果不是這個系譜＝不可比）

    [gate.waivers.context_precision]       # 每一項豁免都必須附理由與決定日期
    reason = "……"
    decided_on = 2026-11-10
    ```
    """
    file = Path(profile_dir) / EVAL_DIR / CONFIG_FILE
    if not file.is_file():
        return EvalConfig()
    try:
        with file.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise EvalProfileError(f"{file}：TOML 語法錯誤：{exc}") from exc
    _reject_unknown(file, data, {"questions", "gate"}, "")
    questions = _table(file, data, "questions")
    _reject_unknown(file, questions, {"legacy_repo_path"}, "[questions] ")
    legacy = questions.get("legacy_repo_path")
    if legacy is not None:
        if not isinstance(legacy, str) or not legacy.strip() or Path(legacy).is_absolute():
            raise EvalProfileError(f"{file}：[questions].legacy_repo_path 必須是相對 repo 根的路徑字串")
        resolved = (REPO_ROOT / legacy).resolve()
        if not resolved.is_relative_to(REPO_ROOT) or not resolved.is_file():
            raise EvalProfileError(f"{file}：[questions].legacy_repo_path 指向的檔案不在 repo 內或不存在：{legacy}")
        if own_questions_path(profile_dir) is not None:
            raise EvalProfileError(
                f"{file}：同時有 {EVAL_DIR}/{QUESTIONS_FILE} 與 legacy_repo_path，不知道要用哪一份")
    gate = _table(file, data, "gate")
    _reject_unknown(file, gate, {"judge_lineage", "waivers"}, "[gate] ")
    lineage = gate.get("judge_lineage")
    if lineage is not None and (not isinstance(lineage, str) or not lineage.strip()):
        raise EvalProfileError(f"{file}：[gate].judge_lineage 必須是非空字串")
    waivers: dict[str, Waiver] = {}
    for metric, spec in _table(file, gate, "waivers").items():
        where = f"[gate.waivers.{metric}]"
        if metric not in GATE_METRICS:
            raise EvalProfileError(f"{file}：{where} 不是可豁免的指標（可用：{'、'.join(GATE_METRICS)}）")
        if not isinstance(spec, dict):
            raise EvalProfileError(f"{file}：{where} 必須是 table（reason、decided_on）")
        _reject_unknown(file, spec, {"reason", "decided_on"}, f"{where} ")
        reason = spec.get("reason")
        if not isinstance(reason, str) or len(reason.strip()) < WAIVER_REASON_MIN_CHARS:
            raise EvalProfileError(f"{file}：{where} 必須附理由（reason，至少 {WAIVER_REASON_MIN_CHARS} 字）")
        decided = spec.get("decided_on")
        if not isinstance(decided, date) or isinstance(decided, datetime):
            raise EvalProfileError(f"{file}：{where} 必須附決定日期（decided_on = YYYY-MM-DD，不加引號）")
        waivers[metric] = Waiver(metric=metric, reason=reason.strip(), decided_on=decided)
    return EvalConfig(
        legacy_repo_path=legacy,
        judge_lineage=lineage.strip() if isinstance(lineage, str) else None,
        waivers=MappingProxyType(waivers),
    )


# ── 涵蓋與清單 ───────────────────────────────────────────────────────────


def coverage_of(categories: list[str]) -> dict[str, int]:
    counts = {c: 0 for c in CATEGORIES}
    for c in categories:
        counts[c] = counts.get(c, 0) + 1
    return counts


def coverage_problems(categories: list[str]) -> list[str]:
    """題數與類型涵蓋沒達到政策的地方（給人看的句子；空＝達標）。"""
    problems = []
    unknown = sorted({c for c in categories if c not in CATEGORIES})
    if unknown:
        problems.append(f"未知的題目類型：{'、'.join(unknown)}（可用：{'、'.join(CATEGORIES)}）")
    if len(categories) < MIN_QUESTIONS:
        short = MIN_QUESTIONS - len(categories)
        problems.append(f"核准題數 {len(categories)}，至少要 {MIN_QUESTIONS} 題（還差 {short} 題）")
    counts = coverage_of(categories)
    for c in CATEGORIES:
        if counts[c] < MIN_PER_CATEGORY:
            problems.append(
                f"{CATEGORY_LABELS[c]}（{c}）只有 {counts[c]} 題，每一類至少要 {MIN_PER_CATEGORY} 題"
                f"（還差 {MIN_PER_CATEGORY - counts[c]} 題）")
    return problems


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def validate_manifest(manifest: Any, questions: list[dict], questions_sha256: str, *, where: str = "清單") -> dict:
    """凍結清單對題集的核對：格式、指紋、題號、每題的類型與支撐研報、涵蓋政策。不合格拋 EvalProfileError。"""
    if not isinstance(manifest, dict):
        raise EvalProfileError(f"{where}不是 JSON 物件")
    if manifest.get("format") != MANIFEST_FORMAT or manifest.get("version") != MANIFEST_VERSION:
        raise EvalProfileError(f"{where}格式不符（要 format={MANIFEST_FORMAT}、version={MANIFEST_VERSION}）")
    if manifest.get("questions_file") != QUESTIONS_FILE:
        raise EvalProfileError(f"{where}的 questions_file 必須是 {QUESTIONS_FILE}")
    if not _is_hex64(manifest.get("questions_sha256")):
        raise EvalProfileError(f"{where}缺 questions_sha256")
    if manifest["questions_sha256"] != questions_sha256:
        raise EvalProfileError(
            f"題集與凍結指紋不符（{where}記 {manifest['questions_sha256'][:12]}…，題集檔是 {questions_sha256[:12]}…）："
            "凍結後的題集不可手改；要換題請重新草擬、審核並 freeze（舊基準線隨之不可比）")
    items = manifest.get("items")
    if not isinstance(items, list) or manifest.get("count") != len(questions) or len(items) != len(questions):
        raise EvalProfileError(f"{where}的 count／items 與題集筆數 {len(questions)} 不符")
    categories = []
    for q, item in zip(questions, items):
        if not isinstance(item, dict) or item.get("id") != q["id"]:
            raise EvalProfileError(f"{where}的題號與題集對不上（{q['id']}）")
        cat = item.get("category")
        categories.append(cat)
        support = item.get("supporting_reports")
        need = MIN_SUPPORT.get(cat, DEFAULT_MIN_SUPPORT) if isinstance(cat, str) else DEFAULT_MIN_SUPPORT
        if not isinstance(support, list) or len(support) < need or not all(_is_hex64(h) for h in support):
            raise EvalProfileError(f"{where}的 {q['id']} 支撐研報要至少 {need} 份 file_hash（sha256）")
        if type(item.get("edited")) is not bool:
            raise EvalProfileError(f"{where}的 {q['id']} 缺 edited（研究員有沒有改過 LLM 原稿）")
    problems = coverage_problems(categories)
    if problems:
        raise EvalProfileError(f"{where}的題數或類型涵蓋未達政策：" + "；".join(problems))
    if manifest.get("coverage") != coverage_of(categories):
        raise EvalProfileError(f"{where}的 coverage 與逐題類型不符")
    return manifest


def load_profile_dataset(profile_dir: Path) -> ProfileDataset:
    """profile 的凍結題集（含指紋核對）。沒有題集、清單缺漏或不符都拋 EvalProfileError。"""
    profile_dir = Path(profile_dir)
    config = load_eval_config(profile_dir)
    if config.legacy_repo_path is not None:
        path = (REPO_ROOT / config.legacy_repo_path).resolve()
        questions, digest = load_dataset(path)
        return ProfileDataset(profile_dir=profile_dir, path=path, questions=questions, sha256=digest, manifest=None)
    path = own_questions_path(profile_dir)
    if path is None:
        raise EvalProfileError(
            f"{profile_dir} 沒有凍結題集（{EVAL_DIR}/{QUESTIONS_FILE}）：先以 scripts/draft_eval_questions.py "
            "草擬、審核並 freeze")
    raw = path.read_bytes()
    try:
        questions = validate_dataset(json.loads(raw))
    except (ValueError, json.JSONDecodeError) as exc:
        raise EvalProfileError(f"{path}：題集契約失敗：{exc}") from exc
    digest = sha256_bytes(raw)
    manifest_path = path.parent / MANIFEST_FILE
    if not manifest_path.is_file():
        raise EvalProfileError(f"{path} 沒有凍結清單（{MANIFEST_FILE}）：題集只能經 freeze 產生")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalProfileError(f"{manifest_path}：不是合法 JSON：{exc}") from exc
    validate_manifest(manifest, questions, digest, where=f"{manifest_path.name} ")
    return ProfileDataset(profile_dir=profile_dir, path=path, questions=questions, sha256=digest, manifest=manifest)


def dataset_arg(profile: str | None, dataset: str | None, default: str = DEFAULT_DATASET_REL) -> str:
    """入口的 `--profile`／`--dataset` → 傳給題集讀取的路徑字串。兩者都給就拒絕。

    沒給 `--profile` 時原樣回 `--dataset`（沒給就是 `default`）：與加入 `--profile` 之前逐字相同（結果檔的
    `config.queryset` 記的就是這個字串）。給了 `--profile` 時先核對凍結指紋，回相對於工作目錄的路徑。
    """
    if profile is not None and dataset is not None:
        raise EvalProfileError("--profile 與 --dataset 只能擇一：profile 的題集由 profile 決定")
    if profile is None:
        return default if dataset is None else dataset
    return os.path.relpath(load_profile_dataset(resolve_profile_arg(profile)).path)
