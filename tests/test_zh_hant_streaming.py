"""即時串流產出的簡體收尾：`_answer_correction` 的契約。

這條缺口與四支批次不同——批次是「產出完再寫」，串流是「邊產出邊送」。轉換是
整串決定的（見 `app/services/zh_hant.py` 的門檻），串流當下拿不到整串，於是問答
路徑的處置是：畫面就是 token 累積出來的，所以 token 照原樣送、`done` 再帶一份
校正後的整份答案讓畫面收斂（`answer.py` 的 `_answer_correction`）。

姊妹測試：
- 問答端到端 → `tests/test_answer.py` 的 AnswerGateTests
- 門檻與轉換規則本身 → `tests/test_zh_hant.py`
"""

import unittest

from app.services.answer import _answer_correction


class AnswerCorrectionTests(unittest.TestCase):
    def test_omits_field_when_unchanged(self):
        self.assertEqual(_answer_correction("台積電營收成長", "台積電營收成長"), {})

    def test_carries_final_text_when_changed(self):
        self.assertEqual(
            _answer_correction("台积电营收成长", "台積電營收成長"),
            {"answer": "台積電營收成長"},
        )


if __name__ == "__main__":
    unittest.main()
