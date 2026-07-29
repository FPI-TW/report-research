import sys
import unittest
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.answer import build_context  # noqa: E402
from app.services.rows import ChunkRow  # noqa: E402


def _row(chunk_id, rid, fn, market, rdate, content, distance):
    return ChunkRow._make((
        chunk_id, rid, f"h-{rid}", fn, None, market, "src", "sum", rdate, None,
        None, None, None, None, None, 0, content, distance,
    ))


# 固定「今天」讓新近度可重現
_NOW = datetime(2026, 7, 8, tzinfo=timezone.utc)


def _fixture():
    # 跨 tier、跨新近度、含長片段觸字數上限
    return [
        (2, 0.90, _row("c1", "rA", "A.pdf", "TW", "2026-07-01", "A 段一", 0.10)),
        (1, 0.70, _row("c2", "rB", "B.pdf", "US", "2026-01-01", "B 段一", 0.30)),
        (0, 0.65, _row("c3", "rC", "C.pdf", "TW", "2025-01-01", "C 段一（很舊）", 0.35)),
        (2, 0.88, _row("c1b", "rA", "A.pdf", "TW", "2026-07-01", "A 段二", 0.12)),
    ]


class BuildContextGoldenTests(unittest.TestCase):
    def test_golden_output_unchanged(self):
        sources, context = build_context(_fixture(), now=_NOW)
        got = ([asdict(s) for s in sources], context)
        # 執行者 Step 2：把實際 got 貼成 EXPECTED 後改為 assertEqual(got, EXPECTED)
        self.assertEqual(got, EXPECTED)


# 執行者於 Step 2 填入實際輸出（golden master）
EXPECTED = (
    [
        {
            "n": 1,
            "report_id": "rA",
            "file_name": "A.pdf",
            "market": "TW",
            "report_date": "2026-07-01",
            "title": None,
            "is_latest": True,
        },
        {
            "n": 2,
            "report_id": "rB",
            "file_name": "B.pdf",
            "market": "US",
            "report_date": "2026-01-01",
            "title": None,
            "is_latest": False,
        },
        {
            "n": 3,
            "report_id": "rC",
            "file_name": "C.pdf",
            "market": "TW",
            "report_date": "2025-01-01",
            "title": None,
            "is_latest": False,
        },
    ],
    "[1] 報告：A.pdf（市場 TW，日期 2026-07-01）\n"
    "A 段一\n"
    "A 段二\n\n"
    "[2] 報告：B.pdf（市場 US，日期 2026-01-01）\n"
    "B 段一\n\n"
    "[3] 報告：C.pdf（市場 TW，日期 2025-01-01）\n"
    "C 段一（很舊）",
)


class SelectReportsTests(unittest.TestCase):
    def test_returns_ordered_selected_reports(self):
        from app.services.answer import (
            select_reports, MAX_REPORTS, MAX_PASSAGES_PER_REPORT,
            MAX_CONTEXT_CHARS, RECENCY_HALF_LIFE_DAYS, ASK_MIN_REPORTS,
            ASK_RELEVANCE_FLOOR, ASK_STALE_AGE_DAYS, ASK_MAX_STALE_REPORTS,
        )
        sel = select_reports(
            _fixture(),
            max_reports=MAX_REPORTS, max_passages=MAX_PASSAGES_PER_REPORT,
            max_chars=MAX_CONTEXT_CHARS, now=_NOW.date(),
            half_life_days=RECENCY_HALF_LIFE_DAYS, min_reports=ASK_MIN_REPORTS,
            relevance_floor=ASK_RELEVANCE_FLOOR, stale_age_days=ASK_STALE_AGE_DAYS,
            max_stale=ASK_MAX_STALE_REPORTS,
        )
        rids = [s.report_id for s in sel]
        self.assertEqual(rids[0], "rA")                 # 最高 tier/分數在前
        self.assertTrue(all(s.passages for s in sel))   # 皆有 kept passage
        a = next(s for s in sel if s.report_id == "rA")
        self.assertEqual(a.passages, ["A 段一", "A 段二"])  # 同報告多段累積


# ---------------------------------------------------------------------------
# M6：MMR 多樣性選取＋gate_scores（rerank 分數/relevance_floor 解耦）


def _row_src(chunk_id, rid, *, source="src", rdate="2026-07-01", content="內容一段",
             market="TW", distance=0.10):
    return ChunkRow._make((
        chunk_id, rid, f"h-{rid}", f"{rid}.pdf", None, market, source, "sum", rdate, None,
        None, None, None, None, None, 0, content, distance,
    ))


def _base_kwargs(**overrides):
    """select_reports 的顯式基準 kwargs：日期全取近期使過舊閘門不干擾。"""
    kw = dict(
        max_reports=10, max_passages=3, max_chars=10_000, now=_NOW.date(),
        half_life_days=90.0, min_reports=0, relevance_floor=0.62,
        stale_age_days=365, max_stale=2,
    )
    kw.update(overrides)
    return kw


def _select(scored, **kw):
    from app.services.answer import select_reports
    return select_reports(scored, **_base_kwargs(**kw))


# 三個可分辨的方向向量：vA 與 vA_DUP 近乎同向（冗餘）、vC 正交（異質）
_VA = [1.0, 0.0, 0.0]
_VA_DUP = [0.998, 0.02, 0.0]
_VC = [0.0, 1.0, 0.0]


def _mmr_fixture():
    """三報告：兩篇高分近同 embedding（rA/rB）＋一篇異質中分（rC）。"""
    scored = [
        (0, 0.90, _row_src("c1", "rA", source="甲", content="A 實段")),
        (0, 0.88, _row_src("c2", "rB", source="乙", content="B 實段")),
        (0, 0.70, _row_src("c3", "rC", source="丙", content="C 實段")),
    ]
    embs = {"c1": _VA, "c2": _VA_DUP, "c3": _VC}
    return scored, embs


class MMRSelectTests(unittest.TestCase):
    def test_lambda_one_equals_current_loop(self):
        # mmr_lambda=1.0＋embeddings（不傳 gate_scores）→ 選集與現行迴圈等價
        base = _select(_fixture())
        embs = {"c1": _VA, "c2": _VA_DUP, "c3": _VC}
        mmr = _select(_fixture(), mmr_lambda=1.0, chunk_embeddings=embs)
        self.assertEqual(
            [s.report_id for s in mmr], [s.report_id for s in base]
        )
        self.assertEqual([s.passages for s in mmr], [s.passages for s in base])

    def test_redundant_report_displaced_by_diverse_one(self):
        scored, embs = _mmr_fixture()
        sel = _select(scored, max_reports=2, mmr_lambda=0.5, chunk_embeddings=embs)
        self.assertEqual([s.report_id for s in sel], ["rA", "rC"])
        # 對照：λ=1.0（純相關度）時重複的 rB 仍入選
        sel = _select(scored, max_reports=2, mmr_lambda=1.0, chunk_embeddings=embs)
        self.assertEqual([s.report_id for s in sel], ["rA", "rB"])

    def test_missing_embedding_candidate_not_penalized(self):
        scored, embs = _mmr_fixture()
        del embs["c2"]  # rB 缺 embedding → max_sim 視為 0，不受冗餘懲罰
        sel = _select(scored, max_reports=2, mmr_lambda=0.5, chunk_embeddings=embs)
        self.assertEqual([s.report_id for s in sel], ["rA", "rB"])

    def test_empty_embeddings_fall_back_to_current_loop(self):
        # chunk_embeddings={} → MMR 停用 → 走現行迴圈（配額不生效即為路徑證明）
        scored = [
            (0, 0.90, _row_src("c1", "r1", source="甲")),
            (0, 0.88, _row_src("c2", "r2", source="甲")),
            (0, 0.86, _row_src("c3", "r3", source="甲")),
        ]
        sel = _select(
            scored, mmr_lambda=0.7, chunk_embeddings={}, mmr_max_per_source=1,
        )
        self.assertEqual([s.report_id for s in sel], ["r1", "r2", "r3"])

    def test_source_cap_gives_slot_to_next_diverse_candidate(self):
        scored = [
            (0, 0.90, _row_src("c1", "r1", source="甲")),
            (0, 0.88, _row_src("c2", "r2", source="甲")),
            (0, 0.86, _row_src("c3", "r3", source="甲")),
            (0, 0.84, _row_src("c4", "r4", source="乙")),
        ]
        embs = {
            "c1": [1.0, 0.0, 0.0, 0.0], "c2": [0.0, 1.0, 0.0, 0.0],
            "c3": [0.0, 0.0, 1.0, 0.0], "c4": [0.0, 0.0, 0.0, 1.0],
        }
        sel = _select(
            scored, max_reports=3, mmr_lambda=1.0, chunk_embeddings=embs,
            mmr_max_per_source=2,
        )
        # 同券商第 3 篇（r3）被跳過，槽位給次一多樣候選 r4
        self.assertEqual([s.report_id for s in sel], ["r1", "r2", "r4"])

    def test_min_reports_not_limited_by_source_cap(self):
        scored = [
            (0, 0.90, _row_src("c1", "r1", source="甲")),
            (0, 0.88, _row_src("c2", "r2", source="甲")),
            (0, 0.86, _row_src("c3", "r3", source="甲")),
        ]
        embs = {"c1": [1.0, 0.0], "c2": [0.0, 1.0], "c3": [1.0, 1.0]}
        sel = _select(
            scored, max_reports=3, min_reports=3, mmr_lambda=1.0,
            chunk_embeddings=embs, mmr_max_per_source=1,
        )
        self.assertEqual([s.report_id for s in sel], ["r1", "r2", "r3"])

    def test_relaxation_fills_when_all_same_source(self):
        # 候選全同券商：配額放寬段補入、篇數不淨減
        scored = [
            (0, 0.90, _row_src("c1", "r1", source="甲")),
            (0, 0.88, _row_src("c2", "r2", source="甲")),
            (0, 0.86, _row_src("c3", "r3", source="甲")),
        ]
        embs = {"c1": [1.0, 0.0], "c2": [0.0, 1.0], "c3": [1.0, 1.0]}
        sel = _select(
            scored, max_reports=3, min_reports=1, mmr_lambda=1.0,
            chunk_embeddings=embs, mmr_max_per_source=1,
        )
        self.assertEqual([s.report_id for s in sel], ["r1", "r2", "r3"])

    def test_relaxation_enforces_stale_quota_not_relaxed(self):
        # 放寬段只放寬多樣性配額；過舊配額（max_stale）仍照常把關。三篇同券商使
        # 第 2/3 篇被 source 配額擠進放寬段；其中兩篇過舊，放寬段取滿 max_stale=1
        # 後，第三篇過舊者仍被過舊配額擋下（若放寬段一併放寬 stale，會多收一篇）。
        scored = [
            (0, 0.90, _row_src("c1", "r1", source="甲", rdate="2026-07-01")),  # 新近
            (0, 0.88, _row_src("c2", "r2", source="甲", rdate="2025-01-01")),  # 過舊
            (0, 0.86, _row_src("c3", "r3", source="甲", rdate="2025-01-01")),  # 過舊
        ]
        embs = {"c1": [1.0, 0.0, 0.0], "c2": [0.0, 1.0, 0.0], "c3": [0.0, 0.0, 1.0]}
        sel = _select(
            scored, max_reports=3, min_reports=1, max_stale=1,
            stale_age_days=180, mmr_lambda=1.0, chunk_embeddings=embs,
            mmr_max_per_source=1,
        )
        # r1 保底入選、r2 經放寬段補入（stale_used 0→1）、r3 因過舊配額用罄被擋
        self.assertEqual([s.report_id for s in sel], ["r1", "r2"])

    def test_month_cap_skips_same_month(self):
        scored = [
            (0, 0.90, _row_src("c1", "r1", rdate="2026-07-05")),
            (0, 0.88, _row_src("c2", "r2", rdate="2026-07-01")),
            (0, 0.86, _row_src("c3", "r3", rdate="2026-06-20")),
        ]
        embs = {"c1": [1.0, 0.0], "c2": [0.0, 1.0], "c3": [1.0, 1.0]}
        sel = _select(
            scored, max_reports=2, mmr_lambda=1.0, chunk_embeddings=embs,
            mmr_max_per_month=1,
        )
        self.assertEqual([s.report_id for s in sel], ["r1", "r3"])

    def test_best_chunk_id_skips_empty_content_chunk(self):
        # rD 首個 chunk 內容 clean_text 後為空 → 代表 chunk 應為次一非空者 cD2
        scored = [
            (0, 0.90, _row_src("cA1", "rA", content="A 實段")),
            (0, 0.88, _row_src("cD0", "rD", content="   ")),
            (0, 0.87, _row_src("cD2", "rD", content="D 實段")),
            (0, 0.70, _row_src("cC1", "rC", content="C 實段")),
        ]
        # embedding 鍵在 cD2：rD 應受冗餘懲罰而被異質 rC 擠掉
        embs = {"cA1": _VA, "cD2": _VA_DUP, "cC1": _VC}
        sel = _select(scored, max_reports=2, mmr_lambda=0.5, chunk_embeddings=embs)
        self.assertEqual([s.report_id for s in sel], ["rA", "rC"])
        # 對照：鍵誤放在空內容 chunk cD0 → 查無代表 embedding → rD 逃過懲罰
        embs = {"cA1": _VA, "cD0": _VA_DUP, "cC1": _VC}
        sel = _select(scored, max_reports=2, mmr_lambda=0.5, chunk_embeddings=embs)
        self.assertEqual([s.report_id for s in sel], ["rA", "rD"])

    def test_bad_embedding_shape_falls_back_without_raising(self):
        scored, _ = _mmr_fixture()
        sel = _select(
            scored, max_reports=2, mmr_lambda=0.5,
            chunk_embeddings={"c1": "壞形狀"},
        )
        # fallback 現行迴圈：純相關度序
        self.assertEqual([s.report_id for s in sel], ["rA", "rB"])


class GateScoresTests(unittest.TestCase):
    def test_gate_keeps_tier0_low_rerank_high_fused(self):
        # finding 3：tier 0、rerank 分 0.2＜floor 但 gate（fused）0.8 → 保留
        scored = [(0, 0.2, _row_src("c1", "rX"))]
        sel = _select(scored, gate_scores={"c1": 0.8})
        self.assertEqual([s.report_id for s in sel], ["rX"])

    def test_without_gate_scores_floor_uses_fused(self):
        scored = [(0, 0.2, _row_src("c1", "rX"))]
        self.assertEqual(_select(scored), [])

    def test_gate_missing_key_falls_back_to_own_fused(self):
        scored = [(0, 0.2, _row_src("c1", "rX"))]
        self.assertEqual(_select(scored, gate_scores={"其他": 0.9}), [])


class BuildContextForwardingTests(unittest.TestCase):
    def test_forwards_gate_scores(self):
        scored = [(0, 0.2, _row_src("c1", "rX"))]
        sources, context = build_context(scored, now=_NOW, min_reports=0)
        self.assertEqual(sources, [])
        self.assertEqual(context, "")
        sources, context = build_context(
            scored, now=_NOW, min_reports=0, gate_scores={"c1": 0.8},
        )
        self.assertEqual([s.report_id for s in sources], ["rX"])
        self.assertIn("[1]", context)

    def test_forwards_mmr_kwargs(self):
        scored, embs = _mmr_fixture()
        sources, _ = build_context(
            scored, now=_NOW, min_reports=0, max_reports=2,
            mmr_lambda=0.5, chunk_embeddings=embs,
        )
        self.assertEqual([s.report_id for s in sources], ["rA", "rC"])
        self.assertEqual([s.n for s in sources], [1, 2])


if __name__ == "__main__":
    unittest.main()
