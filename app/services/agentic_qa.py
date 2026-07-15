"""M5 問答 agentic 迴圈：檢索批次的文字層合併（迴圈本體 run_agentic 見後續 task）。

import 約束（凍結契約 6）：本模組頂層只准 import 葉模組與標準庫，禁止 answer/
retrieval_pipeline——retrieval_pipeline 頂層 import answer，retrieve_context 一律
於函式內 import（比照 answer.py 既有模式）。合併時以 dataclasses.replace 重編
Source 實例，不需其類別定義。

文字層合併而非 scored 層：rerank 的 semaphore／deadline 防護封裝在
retrieval_pipeline 私有層（M5 不可改），正規的管線內多查詢合併屬 M6；本模組
靠濾空切塊＋嚴格塊檢核＋整批丟棄守住正確性（設計 §3）。
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date

# build_context 產物的塊首格式：每塊以「[n] 報告：{file_name}」起頭、塊間以空行
# 相接（answer.py build_context）。^ 配 MULTILINE 只防「行中」出現的同樣式——
# passage 經 clean_text 已折疊內部換行，但每個 passage 各自起一行，恰以此樣式
# 起行仍會誤切；該情況由塊數檢核捕捉（整批丟棄），正確性由檢核而非錨定守住。
_BLOCK_SPLIT_RE = re.compile(r"(?=^\[\d+\] 報告：)", re.MULTILINE)


def _split_blocks(context: str) -> list[str]:
    # 零寬 lookahead 對位置 0 命中會產生空首元素（build_context 產物首字元即為
    # 「[1] 報告：」），不濾空則塊數恆為 len(sources)+1、合法批次全被誤丟。
    return [p.strip() for p in _BLOCK_SPLIT_RE.split(context) if p.strip()]


def _batch_valid(sources: list, blocks: list[str]) -> bool:
    """完整性檢核：濾空後塊數＝篇數，且第 j 塊以 sources[j] 的編號前綴起頭。"""
    if len(blocks) != len(sources):
        return False
    return all(
        block.startswith(f"[{src.n}] 報告：") for src, block in zip(sources, blocks)
    )


def _as_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def merge_retrievals(
    batches: list[tuple[list, str]], *, max_reports: int, max_chars: int,
) -> tuple[list, str]:
    """純函式：多批 (sources, context) → 去重、round-robin 交錯、重編號、預算裁切。

    首批＝原問題的檢索結果：其檢核失敗即原樣回傳首批（等同一次性 RAG，零風險）；
    非首批檢核失敗只丟棄該批（fail-open，覆蓋略減）。
    """
    if not batches:
        return [], ""

    parsed: list[list[tuple]] = []  # 每批的 (source, block) 對
    for idx, (sources, context) in enumerate(batches):
        blocks = _split_blocks(context)
        if not _batch_valid(sources, blocks):
            if idx == 0:
                return sources, context
            continue
        parsed.append(list(zip(sources, blocks)))

    # round-robin 交錯（首批優先起手），report_id 去重先到先贏。
    interleaved: list[tuple] = []
    seen: set = set()
    for i in range(max((len(pairs) for pairs in parsed), default=0)):
        for pairs in parsed:
            if i >= len(pairs):
                continue
            src, block = pairs[i]
            if src.report_id in seen:
                continue
            seen.add(src.report_id)
            interleaved.append((src, block))

    # 預算裁切：篇數上限＋累計字數 skip-and-continue（比照 select_reports）。
    kept: list[tuple] = []
    total = 0
    for src, block in interleaved:
        if len(kept) >= max_reports:
            break
        if total + len(block) > max_chars:
            continue
        kept.append((src, block))
        total += len(block)

    # 重編號 1..N（錨定塊首、僅替換一次），再以最新 report_date 重算唯一 is_latest
    # （語意同 build_context）。
    merged_sources: list = []
    merged_blocks: list[str] = []
    for new_n, (src, block) in enumerate(kept, start=1):
        merged_blocks.append(block.replace(f"[{src.n}] 報告：", f"[{new_n}] 報告：", 1))
        merged_sources.append(replace(src, n=new_n, is_latest=False))
    latest_n, latest_d = None, None
    for src in merged_sources:
        d = _as_date(src.report_date)
        if d is not None and (latest_d is None or d > latest_d):
            latest_n, latest_d = src.n, d
    if latest_n is not None:
        for src in merged_sources:
            src.is_latest = src.n == latest_n

    return merged_sources, "\n\n".join(merged_blocks)
