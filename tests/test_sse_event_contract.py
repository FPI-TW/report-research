"""SSE 事件契約守門：後端送得出來的事件種類集合 ⊆ 前端宣告集合。

## 為什麼要有這支

同一種漂移在本專案有**兩次有紀錄的實例**：

1. `section_draft` 從 M7 起就在送，但 `parseReportEvent` 沒有對應 case。parser 對未知
   event 一律回 `null` 被靜默丟棄——沒有錯誤、沒有紅燈，症狀只是「進度條停在 50%
   不動」，撐了好幾個里程碑。
2. `/api/progress` 的 takeaway／signal 覆蓋率從 P4 就在回，但 `progressSchema.ts` 沒
   宣告；zod 物件預設是 `strip`，未宣告的鍵不報錯、直接安靜丟掉。

在此之前**沒有任何測試在釘「後端事件種類集合 ⊆ 前端宣告集合」**。這支與
`frontend/src/lib/sseEventContract.test.ts` 吃同一份 `tests/fixtures/sse_events.json`：
後端這側證明「fixture 沒有漏掉任何後端會送的種類」，前端那側證明「fixture 裡的每一種
都解得出來」。兩側都綠才等於契約成立。

## 兩種驗法，刻意都要

- **動態**：用既有的 fake 替身跑一次 `report.generate_report`（**絕不 spawn claude
  CLI**），收集實際 yield 出來的 kind。證明的是「這些 kind 真的會經過分派層流出來」，
  連 payload 形狀都是真的。缺點是替身餵什麼就只跑得出什麼。
- **靜態（AST）**：掃服務模組裡「yield (字面字串, …)」的第一元素。補上動態測不到的
  死角——例如新加在 `report_writer` 深處、當前替身事件序沒涵蓋的那一種。

任一種單獨都有盲點，所以兩種都做。
"""

import ast
import json
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "sse_events.json"

# 服務層的事件發射點。ask 只有 answer.py（agentic 的 ("stage", …) 由 answer.py 轉成
# ("status", …) 才出去，overview／時效／離題各分支也都住在 answer.py）；report 的
# kind 分散在分派層與 writer 兩處，report.py 有一行 `yield (kind, payload)` 純轉發，
# 所以 writer 必須一起掃。
_SERVICE_MODULES = {
    "ask": ["app/services/answer.py"],
    "report": ["app/services/report.py", "app/services/report_writer.py"],
}

# `__`-前綴的 kind 是模組間的控制訊號（__final__／__fallback__／__failed__／__text__），
# 由 report.py 攔下來自己處理，一律不上線路。
def _is_wire_kind(kind: str) -> bool:
    return not kind.startswith("__")


def _yielded_literal_kinds(path: Path) -> set[str]:
    """AST 掃「yield (字面字串, …)」的第一元素。

    刻意只認字面值：`yield (kind, payload)` 這種轉發解析不了，故轉發的上游模組要一起
    列進 _SERVICE_MODULES（見該常數註解）。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    kinds: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Yield) or not isinstance(node.value, ast.Tuple):
            continue
        elts = node.value.elts
        if elts and isinstance(elts[0], ast.Constant) and isinstance(elts[0].value, str):
            if _is_wire_kind(elts[0].value):
                kinds.add(elts[0].value)
    return kinds


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _declared(stream: str, *, origin: str | None = None) -> set[str]:
    entries = _load_fixture()[stream]
    return {
        e["event"]
        for e in entries
        if origin is None or origin in e["origin"]
    }


class FixtureShapeTests(unittest.TestCase):
    """fixture 自身的形狀——它是兩側測試的共同前提，壞了會讓兩邊一起變成空斷言。"""

    def test_streams_present_and_non_empty(self):
        fx = _load_fixture()
        for stream in ("ask", "report"):
            self.assertIn(stream, fx)
            self.assertTrue(fx[stream], f"{stream} 不可為空")

    def test_every_entry_has_required_keys_and_legal_values(self):
        fx = _load_fixture()
        for stream in ("ask", "report"):
            for e in fx[stream]:
                with self.subTest(stream=stream, event=e.get("event")):
                    self.assertIn("event", e)
                    self.assertIn("data", e)
                    self.assertTrue(set(e["origin"]) <= {"service", "transport"})
                    self.assertIn(e["frontend"], ("parsed", "ignored"))

    def test_event_names_unique_per_stream(self):
        """同一種事件名只該有一筆（origin 是陣列，兩邊都送就列兩個值，不是兩筆）。"""
        fx = _load_fixture()
        for stream in ("ask", "report"):
            names = [e["event"] for e in fx[stream]]
            self.assertEqual(len(names), len(set(names)), f"{stream} 有重複事件名")


class StaticEmitterScanTests(unittest.TestCase):
    """AST：服務層 yield 出來的 kind 必須與 fixture 的 service-origin 集合逐一對上。

    **雙向**比對（不只是子集）：多了代表前端沒宣告、少了代表 fixture 有殘留條目。
    """

    def _scan(self, stream: str) -> set[str]:
        kinds: set[str] = set()
        for rel in _SERVICE_MODULES[stream]:
            found = _yielded_literal_kinds(REPO_ROOT / rel)
            # 掃到 0 個就是掃描器失效（模組被重構成別的發射寫法），必須大聲失敗——
            # 靜默的 0 會讓下面的比對變成「空集合 == 空集合」而永遠綠。
            self.assertTrue(found, f"{rel} 掃不到任何 yield 事件，掃描器已失效")
            kinds |= found
        return kinds

    def test_ask_service_kinds_match_fixture(self):
        self.assertEqual(self._scan("ask"), _declared("ask", origin="service"))

    def test_report_service_kinds_match_fixture(self):
        self.assertEqual(self._scan("report"), _declared("report", origin="service"))

    def test_control_signals_are_excluded(self):
        """__final__ 等控制訊號不可出現在 fixture（它們不上線路，宣告了只會誤導）。"""
        raw = ast.parse(
            (REPO_ROOT / "app/services/report_writer.py").read_text(encoding="utf-8")
        )
        internal = {
            n.value.elts[0].value
            for n in ast.walk(raw)
            if isinstance(n, ast.Yield)
            and isinstance(n.value, ast.Tuple)
            and n.value.elts
            and isinstance(n.value.elts[0], ast.Constant)
            and isinstance(n.value.elts[0].value, str)
            and n.value.elts[0].value.startswith("__")
        }
        self.assertTrue(internal, "report_writer 應有 __-前綴控制訊號")
        self.assertEqual(internal & _declared("report"), set())


def _fake_source(n: int) -> dict:
    return {
        "n": n, "report_id": f"r-{n}", "file_name": "a.pdf",
        "market": "TW", "report_date": "2026-06-01",
    }


class DynamicReportRunTests(unittest.IsolatedAsyncioTestCase):
    """跑一次真的 generate_report（fake LLM 替身，零 claude CLI）並收集 kind。

    復用 tests/test_report_sectioned.py 的 stub 鏈：那組替身的簽章與真品一致（該檔
    _fake_draft 的註解說明了簽章漂移會怎麼讓測試靜默走錯路徑），不要在這裡另造一套。
    """

    def _harness(self):
        from tests.test_report_sectioned import _SectionedBase

        class _H(_SectionedBase):
            def runTest(self):  # noqa: N802 - 只為了能實例化，不會被跑
                pass

        return _H()

    async def _kinds(self, draft_events, *, empty_context=False):
        from app.services import report as rpt

        harness = self._harness()
        _capture, restore = harness._install(draft_events)
        orig_web = rpt.REPORT_ENABLE_WEB
        if empty_context:
            async def no_context(question, queries, **k):
                return [], ""

            rpt.retrieve_context_multi = no_context
            # 網搜開啟時「脈絡空」照樣往下生成（由模型上網補齊），拒生成的條件是
            # 「脈絡空**且**網搜關」——不關掉這個旗標就走不到 error 那條路。
            rpt.REPORT_ENABLE_WEB = False
        try:
            return [
                kind async for kind, _payload in rpt.generate_report("台積電趨勢")
            ]
        finally:
            rpt.REPORT_ENABLE_WEB = orig_web
            restore()

    async def test_happy_path_kinds_are_all_declared(self):
        # 事件序刻意覆蓋逐節路徑會經過的每一種 kind
        events = [
            ("outline", {"title": "台積電 深度研報",
                         "sections": [{"position": 0, "section_key": "exec_summary",
                                       "heading": "執行摘要"}]}),
            ("status", {"stage": "writing"}),
            ("token", "執行摘要內文"),
            ("section_draft", {"position": 0, "section_key": "exec_summary",
                               "heading": "執行摘要", "markdown": "執行摘要內文"}),
            ("section_skipped", {"position": 3, "heading": "動態分析"}),
            ("status", {"stage": "verifying"}),
            ("document_revision", {"revision_id": "rev-xyz", "revision": 1,
                                   "markdown_hash": "hhh"}),
            ("__final__", {
                "markdown": "# 台積電 深度研報\n\n## 執行摘要\n\n綜述[1]。\n",
                "manifest": {"schema_version": 1, "evidence": []},
                "sources": [_fake_source(1)],
                "outline": {"title": "台積電 深度研報", "sections": []},
                "claim_evidence": {"0": ["abc123"]},
                "revision_id": "rev-xyz", "markdown_hash": "hhh",
                "n_unknown": 0, "n_evidence": 3,
            }),
        ]
        emitted = set(await self._kinds(events))
        declared = _declared("report")
        self.assertLessEqual(
            emitted, declared, f"後端在送但 fixture 沒宣告：{sorted(emitted - declared)}"
        )
        # 這一輪必須真的跑出「進度條要用的那三種」，否則本測試等於什麼都沒驗
        self.assertLessEqual(
            {"outline", "section_draft", "section_skipped", "document_revision", "done"},
            emitted,
        )

    async def test_error_path_kind_is_declared(self):
        emitted = set(await self._kinds([], empty_context=True))
        self.assertIn("error", emitted)
        self.assertLessEqual(emitted, _declared("report"))


if __name__ == "__main__":
    unittest.main()
