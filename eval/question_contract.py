"""凍結 corpus QA 題集的純資料契約；驗證不需 DB、模型或金鑰。"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

FILTER_KEYS = {"market", "instrument_type", "relates_stock", "relates_futures", "report_type"}
MARKETS = {"TW", "US", "HK", "CN", "FX", "WTX", "MACRO", "GLOBAL", "CRYPTO"}
_PUNCTUATION = re.compile(r"[\s\W_]+", re.UNICODE)


def _question_key(question: str) -> str:
    return _PUNCTUATION.sub("", unicodedata.normalize("NFKC", question).casefold())


def validate_dataset(document: object) -> list[dict]:
    """回傳已驗證題目；契約錯誤直接拒絕整份題集。"""
    if not isinstance(document, dict) or document.get("version") != 2:
        raise ValueError("題集版本必須是 2")
    stamp = document.get("generated_at")
    try:
        parsed = datetime.fromisoformat(stamp)
    except (TypeError, ValueError) as exc:
        raise ValueError("generated_at 必須是 ISO 日期時間") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("generated_at 必須含時區")
    questions = document.get("questions")
    if not isinstance(questions, list) or not questions:
        raise ValueError("questions 必須是非空陣列")
    if type(document.get("count")) is not int or document["count"] != len(questions):
        raise ValueError("count 與 questions 筆數不符")
    seen: set[str] = set()
    for index, item in enumerate(questions, 1):
        if not isinstance(item, dict) or set(item) != {"id", "question", "filters", "scope"}:
            raise ValueError(f"第 {index} 題欄位不符")
        if item["id"] != f"q{index:03d}":
            raise ValueError(f"第 {index} 題 id 必須是 q{index:03d}")
        question = item["question"]
        if not isinstance(question, str) or question != question.strip() or len(question) < 6:
            raise ValueError(f"{item['id']} 問題須至少 6 字且無首尾空白")
        if question.endswith(("'", '"', "＇", "＂")):
            raise ValueError(f"{item['id']} 問題尾端有孤立引號")
        key = _question_key(question)
        if key in seen:
            raise ValueError(f"{item['id']} 問題重複")
        seen.add(key)
        if item["scope"] != "corpus_qa":
            raise ValueError(f"{item['id']} scope 必須是 corpus_qa")
        filters = item["filters"]
        if not isinstance(filters, dict) or set(filters) - FILTER_KEYS:
            raise ValueError(f"{item['id']} filters 有未知欄位")
        if "market" in filters and (not isinstance(filters["market"], str) or filters["market"] not in MARKETS):
            raise ValueError(f"{item['id']} market 不合法")
        for key, value in filters.items():
            if key == "market":
                continue
            if key in {"relates_stock", "relates_futures"}:
                valid = type(value) is bool
            else:
                valid = isinstance(value, str) and bool(value.strip())
            if not valid:
                raise ValueError(f"{item['id']} filters.{key} 型別不符")
    return questions


def load_dataset(path: str | Path) -> tuple[list[dict], str]:
    raw = Path(path).read_bytes()
    return validate_dataset(json.loads(raw)), hashlib.sha256(raw).hexdigest()


def _main() -> None:
    parser = argparse.ArgumentParser(description="離線驗證 corpus QA 題集契約")
    parser.add_argument("dataset", nargs="?", default="eval/ragas_questions.json")
    args = parser.parse_args()
    try:
        questions, digest = load_dataset(args.dataset)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(2, f"題集契約失敗：{exc}\n")
    print(f"題集契約通過：{len(questions)} 題，sha256={digest}")


if __name__ == "__main__":
    _main()
