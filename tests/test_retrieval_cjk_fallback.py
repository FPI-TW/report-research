# tests/test_retrieval_cjk_fallback.py
"""純中文問句的字面召回重探（`cjk_affix_candidates` + `pick_title_lead_term`）。

**缺口**：`retrieval._RE_RUN` 只在英數↔CJK 交界切開，一整串中文於是變成單一查詢詞，
字面路拿 `content_norm LIKE '%分析兆勁%'` 去找，語料裡當然沒有——**中文問句只要把
動詞或助詞黏在標的名旁邊，字面召回就整條失效，只剩 dense 一路**。2026-08-21 實測：
查「分析兆勁」0 個 chunk 命中、第一名跑出「鴻勁」；查「兆勁」30 個命中，正確那篇
tier 2、fused 0.86 排第一。加空格也繞不過去（`norm_for_match` 吃掉 CJK 之間的空白）。

**三件事各自都是靜默的，所以逐一釘住**：

1. 重探**只在字面路完全落空時**才跑。有命中卻照跑，等於在原本乾淨的候選池裡加雜訊。
2. 挑中的詞要**一併換掉 `phrase`／`terms`**。只補召回不補排序等於沒補：撈回來的
   chunk 會以原本那串整句算 tier，全部落在 TIER_SEMANTIC，跟雜訊同層。
3. `stats["lex_fallback_term"]` 是**三態**：缺鍵＝沒觸發、None＝觸發但語料標題挑不出
   詞、字串＝挑到了。前兩者的處置完全不同，混成一種就查不出「重探為什麼沒救到」。
"""
import asyncio
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import app.services.retrieval as R  # noqa: E402
from app.services.retrieval import (  # noqa: E402
    TIER_PHRASE,
    TIER_SEMANTIC,
    cjk_affix_candidates,
    extract_terms,
)
from app.services.rows import ChunkRow  # noqa: E402


def row(chunk_id, content, distance=0.3):
    return ChunkRow(
        chunk_id=chunk_id, report_id=f"r-{chunk_id}", file_hash=f"h-{chunk_id}",
        file_name=None, title=None, market=None, source=None, summary=None,
        report_date=None, report_type=None, instrument_types=None,
        relates_stock=None, relates_futures=None, stock_targets=None,
        futures_targets=None, chunk_index=0, content=content, distance=distance,
    )


class AffixCandidateTests(unittest.TestCase):
    def test_glued_question_yields_the_entity_as_a_candidate(self):
        """「分析兆勁」必須切得出「兆勁」——這正是實測失敗的那一題。"""
        _phrase, terms = extract_terms("分析兆勁")
        self.assertEqual(terms, ["分析兆勁"])  # 前提：現況就是不斷詞
        self.assertIn("兆勁", cjk_affix_candidates(terms))

    def test_subject_first_questions_yield_the_leading_name(self):
        """中文問句的主語幾乎都在句首，前綴必須涵蓋得到。"""
        for q, name in [
            ("勝一的獲利表現怎麼樣", "勝一"),
            ("台驊控股法說會重點是什麼", "台驊"),
            ("勤美最近的營運狀況如何", "勤美"),
        ]:
            with self.subTest(q=q):
                self.assertIn(name, cjk_affix_candidates(extract_terms(q)[1]))

    def test_short_terms_are_left_alone(self):
        """「兆勁」本身已經是可用查詢詞；它落空代表語料真的沒有，再切只是雜訊。"""
        self.assertEqual(cjk_affix_candidates(["兆勁"]), [])
        self.assertEqual(cjk_affix_candidates(["台積電"]), [])

    def test_non_cjk_terms_are_left_alone(self):
        """英數詞本來就被 _RE_RUN 切得好好的，不在這條路徑的守備範圍。"""
        self.assertEqual(cjk_affix_candidates(["cowos", "2444"]), [])

    def test_candidates_are_deduped_and_bounded(self):
        cands = cjk_affix_candidates(["台驊控股法說會重點是什麼"])
        self.assertEqual(len(cands), len(set(cands)))
        self.assertTrue(all(2 <= len(c) <= 6 for c in cands))


class _Patch:
    """把 hybrid_search 的三個 IO 依賴換掉；記錄每一次呼叫。"""

    def __init__(self, lex_results, picked):
        self.lex_results = list(lex_results)  # 依序回傳給每一次 lexical 呼叫
        self.picked = picked
        self.lex_calls: list[list[str]] = []
        self.pick_calls: list[list[str]] = []

    def __enter__(self):
        async def fake_meta(*a, **k):
            # dense 路固定給一列「提到兆勁」的候選，讓 tier 判定看得出換詞的效果
            return [row("d1", "兆勁法說重點摘要：網通業務轉為最大客戶獨供")]

        async def fake_lex(_s, _v, patterns, **k):
            self.lex_calls.append(list(patterns))
            return self.lex_results.pop(0) if self.lex_results else ([], 0)

        async def fake_pick(_s, candidates):
            self.pick_calls.append(list(candidates))
            return self.picked

        self._orig = (R.search_chunks_meta, R.search_chunks_lexical, R.pick_title_lead_term)
        R.search_chunks_meta = fake_meta
        R.search_chunks_lexical = fake_lex
        R.pick_title_lead_term = fake_pick
        return self

    def __exit__(self, *exc):
        (R.search_chunks_meta, R.search_chunks_lexical, R.pick_title_lead_term) = self._orig
        return False


def run(q, patch, **kw):
    stats: dict = {}
    scored = asyncio.run(R.hybrid_search(None, q, [0.0], stats=stats, **kw))
    return scored, stats


class FallbackWiringTests(unittest.TestCase):
    def test_fires_only_when_lexical_came_back_empty(self):
        with _Patch(lex_results=[([], 0), ([row("l1", "兆勁法說重點摘要")], 1)],
                    picked="兆勁") as p:
            _scored, stats = run("分析兆勁", p)
        self.assertEqual(len(p.lex_calls), 2)
        self.assertEqual(p.lex_calls[0], ["%分析兆勁%"])
        self.assertEqual(p.lex_calls[1], ["%兆勁%"])  # 重探只帶挑中的那一個詞
        self.assertEqual(stats["lex_fallback_term"], "兆勁")

    def test_does_not_fire_when_lexical_already_hit(self):
        """有命中就代表原查詢詞本來就有用，重探只會在乾淨的候選池裡加雜訊。"""
        with _Patch(lex_results=[([row("l1", "兆勁法說")], 1)], picked="兆勁") as p:
            _scored, stats = run("分析兆勁", p)
        self.assertEqual(len(p.lex_calls), 1)
        self.assertEqual(p.pick_calls, [])
        self.assertNotIn("lex_fallback_term", stats)

    def test_picked_term_also_drives_the_tier(self):
        """只補召回不補排序等於沒補：換詞後提到該標的的 chunk 要升到 TIER_PHRASE。"""
        with _Patch(lex_results=[([], 0), ([row("l1", "兆勁法說重點摘要")], 1)],
                    picked="兆勁") as p:
            scored, _stats = run("分析兆勁", p)
        self.assertTrue(scored)
        self.assertTrue(all(t == TIER_PHRASE for t, _f, _r in scored))

    def test_without_the_swap_everything_would_stay_semantic(self):
        """反面對照：挑不到詞時 phrase 維持原樣，同一列就只能是 TIER_SEMANTIC。"""
        with _Patch(lex_results=[([], 0)], picked=None) as p:
            scored, stats = run("分析兆勁", p)
        self.assertEqual(len(p.lex_calls), 1)  # 挑不到就不重探
        self.assertIsNone(stats["lex_fallback_term"])  # 但要留下「試過了」的痕跡
        self.assertTrue(all(t == TIER_SEMANTIC for t, _f, _r in scored))

    def test_no_candidates_means_no_probe_and_no_key(self):
        """查詢詞短到不需要重探時，連 pick 都不該呼叫，stats 也不該多出鍵。"""
        with _Patch(lex_results=[([], 0)], picked="兆勁") as p:
            _scored, stats = run("兆勁", p)
        self.assertEqual(p.pick_calls, [])
        self.assertNotIn("lex_fallback_term", stats)


if __name__ == "__main__":
    unittest.main()
