"""SSE 事件契約守門：後端送得出來的事件種類集合 ⊆ 前端宣告集合。

## 為什麼要有這支

同一種漂移在本專案有**兩次有紀錄的實例**：

1. 已移除的研報串流的 `section_draft` 從 M7 起就在送，但前端 parser 沒有對應 case。
   parser 對未知 event 一律回 `null` 被靜默丟棄——沒有錯誤、沒有紅燈，症狀只是
   「進度條停在 50% 不動」，撐了好幾個里程碑。
2. `/api/progress` 的 takeaway／signal 覆蓋率從 P4 就在回，但 `progressSchema.ts` 沒
   宣告；zod 物件預設是 `strip`，未宣告的鍵不報錯、直接安靜丟掉。

在此之前**沒有任何測試在釘「後端事件種類集合 ⊆ 前端宣告集合」**。這支與
`frontend/src/lib/sseEventContract.test.ts` 吃同一份 `tests/fixtures/sse_events.json`：
後端這側證明「fixture 沒有漏掉任何後端會送的種類」，前端那側證明「fixture 裡的每一種
都解得出來」。兩側都綠才等於契約成立。

## 驗法

**靜態（AST）**：掃服務模組裡「yield (字面字串, …)」的第一元素，與 fixture 的
service-origin 集合**雙向**比對。問答端到端的動態事件序另由 `tests/test_answer.py`
的 fake 替身覆蓋（絕不呼叫真的 LLM）。
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
# ("status", …) 才出去，overview／時效／離題各分支也都住在 answer.py）。
_SERVICE_MODULES = {
    "ask": ["app/services/answer.py"],
}

_STREAMS = tuple(_SERVICE_MODULES)


# `__`-前綴的 kind 是模組間的控制訊號，一律不上線路（目前 ask 沒有，規則保留）。
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
        self.assertEqual(set(fx) - {"_readme"}, set(_STREAMS), "fixture 有未掃描的串流")
        for stream in _STREAMS:
            self.assertIn(stream, fx)
            self.assertTrue(fx[stream], f"{stream} 不可為空")

    def test_every_entry_has_required_keys_and_legal_values(self):
        fx = _load_fixture()
        for stream in _STREAMS:
            for e in fx[stream]:
                with self.subTest(stream=stream, event=e.get("event")):
                    self.assertIn("event", e)
                    self.assertIn("data", e)
                    self.assertTrue(set(e["origin"]) <= {"service", "transport"})
                    self.assertIn(e["frontend"], ("parsed", "ignored"))

    def test_event_names_unique_per_stream(self):
        """同一種事件名只該有一筆（origin 是陣列，兩邊都送就列兩個值，不是兩筆）。"""
        fx = _load_fixture()
        for stream in _STREAMS:
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


if __name__ == "__main__":
    unittest.main()
