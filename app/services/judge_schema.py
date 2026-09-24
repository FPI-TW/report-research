"""judge 輸出的 schema 版本（生產忠實度抽查與離線評測共用）。

版本號跟著「judge 回應怎麼被解讀」走，不跟著模型走：同一個模型在不同版本的解讀規則下
會算出不同的分數，所以它是量尺的一部分——離線評測把它記進 summary 的 META 鍵
（`scripts/eval_compare.py` 對不同版本拒絕給結論），生產端記進 `qa_log.evaluation`。

- 1：寬鬆解讀。缺 idx、型別不符的 verdict 靜默記為 unsupported／不相關。

**刻意是葉模組**（不 import 任何 app.*）：`app/services/faithfulness.py` 與 `eval/` 兩邊都要
用它，放在任何一邊都會把另一邊的相依拖進來。
"""

from __future__ import annotations

JUDGE_SCHEMA_VERSION = 1
