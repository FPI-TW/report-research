# tests/test_report_budget.py
"""逐節研報預算前瞻與骨架保底（2026-07-28 生產逾時修復）。

修的問題:draft_report 迴圈頂端只有「超過 deadline 就 return」的硬停止,完全繞過
「動態子節可砍、骨架節不可缺」這條既有規則。而 build_outline 把 risk_outlook 排在
**最後**,於是預算被前面的分析子節吃光時骨架節根本輪不到 → produced=True 已成立
→ 不能退單次 → 硬失敗,整份丟棄。生產實測:11 分 13 秒、4 節草稿約 15.5K 字全損。
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services import report_writer as rw  # noqa: E402
from app.services.report_writer import Checkpoint, plan_section_action  # noqa: E402


class PlanSectionActionTests(unittest.TestCase):
    """排程決策的窮舉表。純函式、不讀時鐘,所以可以完全窮舉。"""

    def _p(self, **kw):
        base = dict(kind="analysis", has_analysis_draft=True, now=100.0,
                    section_stop=700.0, hard_stop=900.0, est=150.0)
        base.update(kw)
        return plan_section_action(**base)

    def test_no_deadline_always_runs(self):
        # eval 路徑不帶 deadline → 不設任何界
        self.assertEqual(self._p(section_stop=None, hard_stop=None), "run")

    def test_past_hard_stop_stops(self):
        self.assertEqual(self._p(now=901.0), "stop")

    def test_hard_stop_applies_to_skeleton_too(self):
        self.assertEqual(self._p(kind="framing", now=901.0), "stop")

    def test_analysis_skipped_when_projected_over_section_stop(self):
        # 600 + 150 = 750 > 700 → 砍
        self.assertEqual(self._p(now=600.0), "skip")

    def test_skeleton_never_skipped(self):
        """骨架節即使前瞻超支也照跑——它們缺一即不可出貨（section_coverage 分母恆 5）。"""
        self.assertEqual(self._p(kind="framing", now=600.0), "run")

    def test_first_analysis_protected(self):
        """第一個 analysis 子節受保護:全部 analysis 皆缺＝「重點分析」整章消失,
        與骨架節缺章同罪（draft_report 對此本來就會拒絕出貨）。"""
        self.assertEqual(self._p(has_analysis_draft=False, now=600.0), "run")

    def test_runs_when_budget_ample(self):
        self.assertEqual(self._p(now=100.0), "run")

    def test_kill_switch_keeps_hard_stop(self):
        """kill switch（section_stop=None）只關掉前瞻跳過,**不可**連硬停止一起關掉
        ——那會讓逐節迴圈完全無界,比修復前更糟。"""
        self.assertEqual(self._p(section_stop=None, now=600.0), "run")   # 不再前瞻跳過
        self.assertEqual(self._p(section_stop=None, now=901.0), "stop")  # 硬停止仍在

    def test_boundary_exactly_at_section_stop(self):
        # now + est == section_stop → 不砍（邊界含入,避免恰好貼齊時誤殺）
        self.assertEqual(self._p(now=550.0), "run")


class CheckpointTelemetryTests(unittest.TestCase):
    def test_roundtrip_with_telemetry(self):
        c = Checkpoint(outline_ready=True, section_seconds=[118.44, 97.2],
                       skipped_positions=[5, 3, 5])
        obj = c.to_json()
        self.assertEqual(obj["section_seconds"], [118.4, 97.2])   # 一位小數
        self.assertEqual(obj["skipped_positions"], [3, 5])        # 去重排序
        back = Checkpoint.load(obj)
        self.assertEqual(back.section_seconds, [118.4, 97.2])
        self.assertEqual(back.skipped_positions, [3, 5])

    def test_lenient_load_of_legacy_and_bad_shapes(self):
        """歷史 checkpoint 沒有這兩個欄位;壞形狀也不得炸（load 一律寬鬆）。"""
        for bad in (None, {}, {"section_seconds": "x", "skipped_positions": 7},
                    {"section_seconds": [1, "a", 2.5], "skipped_positions": [1, "b"]}):
            c = Checkpoint.load(bad)
            self.assertIsInstance(c.section_seconds, list)
            self.assertIsInstance(c.skipped_positions, list)


def _outline_5():
    """2 骨架 + 3 分析 + 1 骨架(排最後,與 build_outline 的實際順序一致)。"""
    return {
        "title": "研報T",
        "sections": [
            {"position": 0, "key": "exec_summary", "heading": "執行摘要",
             "topic": "q", "kind": "framing"},
            {"position": 1, "key": "analysis", "heading": "面向A",
             "topic": "ta", "kind": "analysis"},
            {"position": 2, "key": "analysis", "heading": "面向B",
             "topic": "tb", "kind": "analysis"},
            {"position": 3, "key": "analysis", "heading": "面向C",
             "topic": "tc", "kind": "analysis"},
            {"position": 4, "key": "risk_outlook", "heading": "風險與展望",
             "topic": "r", "kind": "framing"},
        ],
    }


class _Clock:
    """可控假時鐘:每次「撰寫一節」推進 step 秒。"""

    def __init__(self, step: float):
        self.t = 0.0
        self.step = step

    def now(self) -> float:
        return self.t

    def tick(self) -> None:
        self.t += self.step


class BudgetLookaheadDraftTests(unittest.IsolatedAsyncioTestCase):
    """draft_report 端到端:預算在分析節中段耗盡時仍須交付。"""

    async def _run(self, *, deadline, step, lookahead=True):
        from types import SimpleNamespace

        clock = _Clock(step)
        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf",
                              market="TW", report_date="2026-01-01")

        async def fake_outline(*a, **k):
            return _outline_5()

        async def fake_retrieve(topic, **k):
            return ([src], "[1] 報告：a.pdf\n片段")

        async def fake_stream(*a, **k):
            clock.tick()  # 撰寫這一節花掉 step 秒
            yield "內文"

        s = rw.get_settings()
        cfg = SimpleNamespace(**{
            **{f: getattr(s, f) for f in s.__dataclass_fields__},
            "report_budget_lookahead_enabled": lookahead,
            "report_faithfulness_enabled": False,  # M8 另有專屬測試
        })

        with patch.object(rw, "plan_outline", fake_outline), \
             patch.object(rw, "retrieve_for_section", fake_retrieve), \
             patch.object(rw, "stream_completion", fake_stream), \
             patch.object(rw, "_now", clock.now), \
             patch.object(rw, "get_settings", lambda: cfg):
            return [e async for e in rw.draft_report("q", "ctx", deadline=deadline)]

    async def test_deadline_mid_analysis_still_delivers_skeleton(self):
        """**核心回歸**:預算在分析節中途耗盡 → 砍動態子節、骨架跑完 → 交付 __final__。

        修復前:硬停止 return → risk_outlook（排最後）根本輪不到 → produced=True
        → __failed__ → 整份丟棄。生產 2026-07-28 就是這個形態。
        """
        # step=300、deadline=1000 → section_stop=820
        #   pos0 骨架 @0 跑 → t=300
        #   pos1 分析A（第一個,受保護）@300 跑 → t=600
        #   pos2 分析B @600:600+300=900 > 820 → 砍
        #   pos3 分析C @600 → 同樣砍
        #   pos4 骨架 @600 跑 → t=900（< hard_stop 1000）
        events = await self._run(deadline=1000.0, step=300.0)
        kinds = [k for k, _ in events]

        self.assertIn("__final__", kinds, "預算耗盡仍必須交付,不可整份丟棄")
        self.assertNotIn("__failed__", kinds)

        final = next(p for k, p in events if k == "__final__")
        headings = [s["heading"] for s in final["outline"]["sections"]]
        self.assertIn("風險與展望", headings)  # 骨架節有被規劃

        drafted = [p["heading"] for k, p in events if k == "section_draft"]
        self.assertEqual(drafted, ["執行摘要", "面向A", "風險與展望"])
        self.assertNotIn("面向B", drafted)  # 動態子節被砍
        self.assertNotIn("面向C", drafted)

    async def test_ample_budget_drafts_every_section(self):
        events = await self._run(deadline=100000.0, step=10.0)
        drafted = [p["heading"] for k, p in events if k == "section_draft"]
        self.assertEqual(
            drafted, ["執行摘要", "面向A", "面向B", "面向C", "風險與展望"]
        )

    async def test_hard_stop_still_enforced_with_lookahead_off(self):
        """kill switch 關掉前瞻,但硬停止必須還在（否則比修復前更糟:完全無界）。"""
        events = await self._run(deadline=650.0, step=300.0, lookahead=False)
        kinds = [k for k, _ in events]
        # 沒有前瞻 → 分析節照跑到撞破 deadline → 已吐內容 → __failed__（修復前行為）
        self.assertIn("__failed__", kinds)
        self.assertNotIn("__final__", kinds)


class TelemetrySurvivesFinalCheckpointTests(unittest.IsolatedAsyncioTestCase):
    """收尾寫 rendering 時不得覆蓋逐節累積的遙測。

    修復前:該處新建了一個 Checkpoint(...)，把 section_seconds / skipped_positions
    一併清空 → 生產實測成功 run 的 section_seconds 為 []，而成功 run 的耗時分佈
    正是校準 REPORT_DRAFT_BUDGET 最該用的資料（失敗 run 反而留得住,更諷刺）。
    """

    async def test_final_checkpoint_keeps_section_seconds(self):
        from types import SimpleNamespace

        clock = _Clock(50.0)
        src = SimpleNamespace(n=1, report_id="r1", file_name="a.pdf",
                              market="TW", report_date="2026-01-01")
        ckpts = []

        async def fake_outline(*a, **k):
            return _outline_5()

        async def fake_retrieve(topic, **k):
            return ([src], "[1] 報告：a.pdf\n片段")

        async def fake_stream(*a, **k):
            clock.tick()
            yield "內文"

        async def rec_advance(run_id, status, **k):
            if "checkpoint" in k and k["checkpoint"] is not None:
                ckpts.append((status, k["checkpoint"].to_json()))

        async def rec_upsert(*a, **k):
            return None

        s = rw.get_settings()
        cfg = SimpleNamespace(**{
            **{f: getattr(s, f) for f in s.__dataclass_fields__},
            "report_faithfulness_enabled": False,
        })

        with patch.object(rw, "plan_outline", fake_outline), \
             patch.object(rw, "retrieve_for_section", fake_retrieve), \
             patch.object(rw, "stream_completion", fake_stream), \
             patch.object(rw, "advance_status", rec_advance), \
             patch.object(rw, "upsert_section", rec_upsert), \
             patch.object(rw, "_now", clock.now), \
             patch.object(rw, "get_settings", lambda: cfg):
            events = [e async for e in rw.draft_report(
                "q", "ctx", run_id="run-tele", deadline=100000.0,
            )]

        self.assertEqual(events[-1][0], "__final__")
        rendering = [c for st, c in ckpts if st == "rendering"]
        self.assertTrue(rendering, "收尾必須寫一次 rendering checkpoint")
        final_ck = rendering[-1]
        self.assertEqual(len(final_ck["section_seconds"]), 5,
                         "收尾 checkpoint 必須保留五節的耗時遙測")
        self.assertEqual(final_ck["final_positions"], [0, 1, 2, 3, 4])


class StreamSectionBudgetTests(unittest.IsolatedAsyncioTestCase):
    """單節牆鐘:修掉「_stream_section 從未把 retries 傳給 stream_completion」。"""

    async def test_retries_passed_explicitly_when_budget_given(self):
        """不設界時每個 attempt 內部還會依 stream_completion 的預設 retries=2 再跑
        3 次 → 單節最壞 2×3×150 + 150(檢索) = 1050s,遠超任何 run-level 預算。"""
        seen = {}

        async def fake_stream(prompt, **k):
            seen.update(k)
            yield "內文"

        clock = _Clock(0.0)
        with patch.object(rw, "stream_completion", fake_stream), \
             patch.object(rw, "_now", clock.now):
            out = [e async for e in rw._stream_section(
                "sys", "p", timeout=150, retry=1, model="m", budget=240,
            )]

        self.assertEqual(out[-1][0], "__text__")
        self.assertIn("retries", seen)          # 必須顯式傳,不可放任預設
        self.assertLessEqual(seen["timeout"], 150)

    async def test_no_budget_keeps_legacy_defaults(self):
        seen = {}

        async def fake_stream(prompt, **k):
            seen.update(k)
            yield "內文"

        with patch.object(rw, "stream_completion", fake_stream):
            _ = [e async for e in rw._stream_section(
                "sys", "p", timeout=150, retry=1, model="m",
            )]
        self.assertEqual(seen["timeout"], 150)
        self.assertEqual(seen["retries"], 2)    # 既有預設,行為不變

    async def test_budget_exhausted_before_attempt_returns_empty(self):
        """額度不足 _MIN_ATTEMPT 時不再開 LLM——開了也只會在吐字前被砍。"""
        called = {"n": 0}

        async def fake_stream(prompt, **k):
            called["n"] += 1
            yield "內文"

        clock = _Clock(0.0)
        with patch.object(rw, "stream_completion", fake_stream), \
             patch.object(rw, "_now", clock.now):
            out = [e async for e in rw._stream_section(
                "sys", "p", timeout=150, retry=1, model="m", budget=1.0,
            )]

        self.assertEqual(called["n"], 0)
        self.assertEqual(out[-1], ("__text__", ""))


if __name__ == "__main__":
    unittest.main()
