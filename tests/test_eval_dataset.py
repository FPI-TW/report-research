import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.answer import (  # noqa: E402
    NO_CONTEXT_MESSAGE,
    OFF_TOPIC_MESSAGES,
    TIME_SENSITIVE_MESSAGES,
    TIME_SENSITIVE_UNAVAILABLE_MESSAGE,
)
from eval.dataset import select_questions  # noqa: E402


def _row(question, answer="正常回答內容", filters=None):
    return {"question": question, "answer": answer, "filters": filters or {}}


class SelectQuestionsTests(unittest.TestCase):
    def test_assigns_sequential_ids(self):
        rows = [_row("台積電先進製程展望如何"), _row("聯發科手機晶片市占")]
        out = select_questions(rows, per_market_cap=10, target=10)
        self.assertEqual([q["id"] for q in out], ["q001", "q002"])

    def test_drops_off_topic_and_no_context(self):
        rows = [
            _row("台積電先進製程展望如何"),
            _row("幫我寫一首詩", answer=OFF_TOPIC_MESSAGES[0]),
            _row("有沒有火星股票", answer=NO_CONTEXT_MESSAGE),
            _row("舊版離題問題", answer=OFF_TOPIC_MESSAGES[-1]),
            _row("時間敏感問題", answer=TIME_SENSITIVE_UNAVAILABLE_MESSAGE),
            # 每一種時效婉拒文案都要排除（英文版與帶網搜提示的新版先前會漏進題集）。
            *[_row(f"時效婉拒文案第 {i} 種", answer=m) for i, m in enumerate(TIME_SENSITIVE_MESSAGES)],
        ]
        out = select_questions(rows, per_market_cap=10, target=10)
        self.assertEqual([q["question"] for q in out], ["台積電先進製程展望如何"])

    def test_drops_short_questions(self):
        rows = [_row("台積電先進製程展望如何"), _row("嗨")]
        out = select_questions(rows, per_market_cap=10, target=10)
        self.assertEqual(len(out), 1)

    def test_dedups_normalized_questions(self):
        rows = [_row("台積電 展望 如何"), _row("台積電展望如何")]  # 正規化後相同
        out = select_questions(rows, per_market_cap=10, target=10)
        self.assertEqual(len(out), 1)

    def test_respects_target(self):
        rows = [_row(f"這是第{i}個夠長的問題內容") for i in range(10)]
        out = select_questions(rows, per_market_cap=10, target=3)
        self.assertEqual(len(out), 3)

    def test_market_diversity_round_robin(self):
        rows = (
            [_row(f"美股問題內容編號{i}", filters={"market": "US"}) for i in range(5)]
            + [_row("台股問題內容一", filters={"market": "TW"})]
        )
        # 每市場上限 2、target 3 → 應含到 TW（多樣），非全部 US
        out = select_questions(rows, per_market_cap=2, target=3)
        markets = {q["filters"].get("market") for q in out}
        self.assertIn("TW", markets)
        self.assertEqual(len(out), 3)

    def test_whitelists_filter_keys_and_drops_nulls(self):
        rows = [_row("台積電展望如何嗎", filters={
            "market": "TW", "report_type": None, "relates_stock": None,
        })]
        out = select_questions(rows, per_market_cap=10, target=10)
        self.assertEqual(out[0]["filters"], {"market": "TW"})

    def test_drops_overview_path_rows(self):
        rows = [
            _row("給我所有元大的報告種類", filters={"path": "overview", "market": None}),
            _row("台積電先進製程展望如何"),
        ]
        out = select_questions(rows, per_market_cap=10, target=10)
        self.assertEqual([q["question"] for q in out], ["台積電先進製程展望如何"])

    def test_drops_time_sensitive_path_rows(self):
        """M4a：成功的時效答案（模板文字、path=time_sensitive）非固定婉拒文案，
        不會被答案比對攔下；必須以 path 排除，防其問題被挖進 corpus_qa 題集。"""
        rows = [
            _row(
                "台積電今天收盤價多少",
                answer="根據受信任資料來源（exchange｜fake）：台積電 2330 為 1085.00 TWD。",
                filters={"path": "time_sensitive"},
            ),
            _row("台積電先進製程展望如何"),
        ]
        out = select_questions(rows, per_market_cap=10, target=10)
        self.assertEqual([q["question"] for q in out], ["台積電先進製程展望如何"])


if __name__ == "__main__":
    unittest.main()
