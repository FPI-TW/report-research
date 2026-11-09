"""待審題集（草擬工具的產物）的格式、研究員審核規則與凍結（freeze）——純資料，不需 DB、模型或金鑰。

流程（`scripts/draft_eval_questions.py`；`docs/WORKFLOW.md`「導入驗收」）：

1. `draft`：從語料分層抽樣，LLM 產生候選題目、參考答案要點與支撐研報，寫成待審檔（本模組的格式）。
2. 研究員逐題在 `review.status` 標 `approved`／`rejected`／`modified`（`note` 自由填寫）。要改題目就直接改
   `question`、`filters`、`category`（或 `supporting_reports`）並標 `modified`；`original` 是 LLM 原稿，**不要改**。
3. `freeze`：全部審完、核准題數與類型涵蓋達到政策（`eval/profile_eval.py` 的 `MIN_QUESTIONS`／
   `MIN_PER_CATEGORY`）才產生凍結題集與清單；否則逐條說明缺什麼，不寫任何檔。

審核規則刻意嚴格（`review_problems`）：
- `approved` 的題目必須與原稿相同；改過卻標 `approved` 是錯（清單的 `edited` 才記得準）。
- `modified` 必須真的改過；沒改就標 `approved`。
- 還有 `pending` 就不凍結：審了一半的題集量出來的基準線，之後補審就不可比。
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from eval import profile_eval as pe
from eval.question_contract import FILTER_KEYS, _question_key, validate_dataset

DRAFT_FORMAT = "eval-question-draft"
DRAFT_VERSION = 1

STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_MODIFIED = "modified"
STATUSES = (STATUS_PENDING, STATUS_APPROVED, STATUS_REJECTED, STATUS_MODIFIED)
ACCEPTED = (STATUS_APPROVED, STATUS_MODIFIED)

_DRAFT_ID = re.compile(r"d\d{3,}")
_EDITABLE = ("category", "question", "filters")
_ITEM_KEYS = {"draft_id", "category", "question", "filters", "reference_points", "supporting_reports",
              "original", "review"}
_SUPPORT_KEYS = {"file_hash", "label", "market", "report_type", "report_date", "source"}


class FreezeRefused(ValueError):
    """不能凍結；`problems` 是逐條、給研究員看的原因。"""

    def __init__(self, problems: list[str]):
        super().__init__("；".join(problems))
        self.problems = problems


def new_item(draft_id: str, *, category: str, question: str, filters: dict, reference_points: list[str],
             supporting_reports: list[dict]) -> dict:
    """一題候選（LLM 原稿同時存進 `original`，審核以它判斷研究員改了什麼）。"""
    return {
        "draft_id": draft_id,
        "category": category,
        "question": question,
        "filters": dict(filters),
        "reference_points": list(reference_points),
        "supporting_reports": [dict(s) for s in supporting_reports],
        "original": {"category": category, "question": question, "filters": dict(filters)},
        "review": {"status": STATUS_PENDING, "note": ""},
    }


def new_draft(*, profile: str, generated_at: str, model: str, sample: dict, items: list[dict]) -> dict:
    return {
        "format": DRAFT_FORMAT,
        "version": DRAFT_VERSION,
        "profile": profile,
        "generated_at": generated_at,
        "model": model,
        "sample": sample,
        "instructions": (
            "逐題把 review.status 改成 approved／rejected／modified（可填 review.note）。要改題目就直接改 "
            "question、filters、category 或 supporting_reports 並標 modified；original 是 LLM 原稿，不要改。"
            "全部審完後執行 scripts/draft_eval_questions.py freeze。"
        ),
        "items": items,
    }


def _edited(item: dict) -> bool:
    original = item.get("original") or {}
    return any(item.get(k) != original.get(k) for k in _EDITABLE)


def _item_shape_problems(item: Any, index: int) -> list[str]:
    if not isinstance(item, dict):
        return [f"第 {index} 題不是物件"]
    did = item.get("draft_id")
    where = did if isinstance(did, str) else f"第 {index} 題"
    out = []
    if not isinstance(did, str) or not _DRAFT_ID.fullmatch(did):
        out.append(f"{where}：draft_id 格式不對（要 d001 這種）")
    unknown = sorted(set(item) - _ITEM_KEYS)
    missing = sorted(_ITEM_KEYS - set(item))
    if unknown:
        out.append(f"{where}：有未知的欄位 {'、'.join(unknown)}")
    if missing:
        out.append(f"{where}：缺欄位 {'、'.join(missing)}")
        return out
    original = item["original"]
    if not isinstance(original, dict) or set(original) != set(_EDITABLE):
        out.append(f"{where}：original（LLM 原稿）被改壞了")
    review = item["review"]
    if not isinstance(review, dict) or review.get("status") not in STATUSES:
        out.append(f"{where}：review.status 必須是 {'／'.join(STATUSES)}")
    elif not isinstance(review.get("note", ""), str):
        out.append(f"{where}：review.note 必須是字串")
    if item["category"] not in pe.CATEGORIES:
        out.append(f"{where}：category 必須是 {'／'.join(pe.CATEGORIES)}")
    if not isinstance(item["filters"], dict) or set(item["filters"]) - FILTER_KEYS:
        out.append(f"{where}：filters 只能用 {'、'.join(sorted(FILTER_KEYS))}")
    points = item["reference_points"]
    if not isinstance(points, list) or not all(isinstance(p, str) and p.strip() for p in points):
        out.append(f"{where}：reference_points 必須是字串陣列")
    support = item["supporting_reports"]
    if not isinstance(support, list) or not all(
        isinstance(s, dict) and pe._is_hex64(s.get("file_hash")) and set(s) <= _SUPPORT_KEYS for s in support
    ):
        allowed = "、".join(sorted(_SUPPORT_KEYS))
        out.append(f"{where}：supporting_reports 每筆要有 file_hash（sha256），只能用 {allowed}")
    return out


def validate_draft(doc: Any) -> list[dict]:
    """待審檔的形狀（不看審核結果）。不合格拋 FreezeRefused。"""
    if not isinstance(doc, dict) or doc.get("format") != DRAFT_FORMAT or doc.get("version") != DRAFT_VERSION:
        raise FreezeRefused([f"不是待審題集（要 format={DRAFT_FORMAT}、version={DRAFT_VERSION}）"])
    items = doc.get("items")
    if not isinstance(items, list) or not items:
        raise FreezeRefused(["items 必須是非空陣列"])
    problems = [p for i, item in enumerate(items, 1) for p in _item_shape_problems(item, i)]
    ids = [item.get("draft_id") for item in items if isinstance(item, dict)]
    dup = sorted({i for i in ids if ids.count(i) > 1 and isinstance(i, str)})
    if dup:
        problems.append(f"draft_id 重複：{'、'.join(dup)}")
    if problems:
        raise FreezeRefused(problems)
    return items


def review_problems(items: list[dict]) -> list[str]:
    """審核狀態與內容不一致的地方（approved 卻改過、modified 卻沒改、未審完）。"""
    out = []
    pending = [i["draft_id"] for i in items if i["review"]["status"] == STATUS_PENDING]
    if pending:
        listed = "、".join(pending[:10]) + ("…" if len(pending) > 10 else "")
        out.append(f"還有 {len(pending)} 題未審（pending）：{listed}")
    for item in items:
        status, edited = item["review"]["status"], _edited(item)
        if status == STATUS_APPROVED and edited:
            out.append(f"{item['draft_id']}：內容與 LLM 原稿不同，請標 modified")
        elif status == STATUS_MODIFIED and not edited:
            out.append(f"{item['draft_id']}：標了 modified 但 question／filters／category 與原稿相同，請標 approved")
    return out


def summarize(items: list[dict]) -> dict:
    """待審檔的進度（status 子命令用）。"""
    accepted = [i for i in items if i["review"]["status"] in ACCEPTED]
    return {
        "total": len(items),
        "by_status": {s: sum(1 for i in items if i["review"]["status"] == s) for s in STATUSES},
        "accepted_coverage": pe.coverage_of([i["category"] for i in accepted]),
        "problems": review_problems(items) + pe.coverage_problems([i["category"] for i in accepted]),
    }


def dumps(doc: dict) -> bytes:
    """凍結檔的序列化（指紋算的就是這份位元組）。"""
    return (json.dumps(doc, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def freeze(draft: Any, *, draft_sha256: str, profile: str, frozen_at: datetime) -> tuple[bytes, dict]:
    """待審檔 → (凍結題集的位元組, 清單)。任何一項不合格拋 FreezeRefused，列出全部原因。"""
    if frozen_at.tzinfo is None or frozen_at.utcoffset() is None:
        raise ValueError("frozen_at 必須含時區")
    items = validate_draft(draft)
    problems = review_problems(items)
    accepted = [i for i in items if i["review"]["status"] in ACCEPTED]
    problems += pe.coverage_problems([i["category"] for i in accepted])
    for item in accepted:
        need = pe.MIN_SUPPORT.get(item["category"], pe.DEFAULT_MIN_SUPPORT)
        hashes = {s["file_hash"] for s in item["supporting_reports"]}
        if len(hashes) < need:
            label = pe.CATEGORY_LABELS[item["category"]]
            problems.append(f"{item['draft_id']}：{label}題要至少 {need} 份不同的支撐研報")
    # 題目契約（與 eval/question_contract.py 同一套）：逐題檢查，訊息帶 draft_id 才找得到是哪一題。
    seen: dict[str, str] = {}
    for item in accepted:
        single = {"version": 2, "generated_at": frozen_at.isoformat(), "count": 1,
                  "questions": [{"id": "q001", "question": item["question"], "filters": item["filters"],
                                 "scope": "corpus_qa"}]}
        try:
            validate_dataset(single)
        except ValueError as exc:
            problems.append(f"{item['draft_id']}：題目不合契約（{str(exc).replace('q001 ', '')}）")
            continue
        key = _question_key(item["question"])
        if key in seen:
            problems.append(f"{item['draft_id']}：與 {seen[key]} 的題目重複")
        seen.setdefault(key, item["draft_id"])
    if problems:
        raise FreezeRefused(problems)

    questions = [
        {"id": f"q{n:03d}", "question": item["question"], "filters": item["filters"], "scope": "corpus_qa"}
        for n, item in enumerate(accepted, 1)
    ]
    doc = {
        "version": 2,
        "generated_at": frozen_at.isoformat(),
        "count": len(questions),
        "_note": ("由 scripts/draft_eval_questions.py freeze 產生（LLM 草擬、研究員審核）。凍結指紋在 "
                  f"{pe.MANIFEST_FILE}：不可手改，要換題請重新草擬、審核並 freeze（舊基準線隨之不可比）。"),
        "questions": questions,
    }
    validate_dataset(doc)
    raw = dumps(doc)
    categories = [item["category"] for item in accepted]
    manifest = {
        "format": pe.MANIFEST_FORMAT,
        "version": pe.MANIFEST_VERSION,
        "profile": profile,
        "questions_file": pe.QUESTIONS_FILE,
        "questions_sha256": pe.sha256_bytes(raw),
        "count": len(questions),
        "coverage": pe.coverage_of(categories),
        "policy": {"min_questions": pe.MIN_QUESTIONS, "min_per_category": pe.MIN_PER_CATEGORY},
        "frozen_at": frozen_at.isoformat(),
        "draft": {"sha256": draft_sha256, "generated_at": draft.get("generated_at"), "model": draft.get("model")},
        "items": [
            {
                "id": q["id"],
                "draft_id": item["draft_id"],
                "category": item["category"],
                "supporting_reports": sorted({s["file_hash"] for s in item["supporting_reports"]}),
                "edited": _edited(item),
            }
            for q, item in zip(questions, accepted)
        ],
    }
    pe.validate_manifest(manifest, questions, manifest["questions_sha256"])
    return raw, manifest
