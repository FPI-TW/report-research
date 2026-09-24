"""judge 的量尺：回應解讀規則的版本，以及「這筆分數是哪個 judge 量的」。

生產忠實度抽查（`app/services/faithfulness.py`）與離線評測（`eval/`）共用。

**schema 版本**跟著「judge 回應怎麼被解讀」走，不跟著模型走：同一個模型在不同版本的解讀
規則下會算出不同的分數，所以它是量尺的一部分——離線評測把它記進 summary 的 META 鍵
（`scripts/eval_compare.py` 對不同版本拒絕給結論），生產端記進 `qa_log.evaluation`。

- 1：寬鬆解讀。缺 idx、型別不符的 verdict 靜默記為 unsupported／不相關。

**judge 身分**：`qa_log.evaluation` 自 DeepSeek 遷移 PR-07 起帶 `judge_model`。讀分數的三處
（監控卡 `web/routers/monitor.py`、待複核佇列 `web/routers/review.py`、離線彙總
`scripts/eval_faithfulness.py`）**只計現行 judge 的分數**，一律經本模組的 SQL 片段或
`is_current_judge`，不各寫一份——換 judge 之後舊尺的 0.85 與新尺的 0.85 不是同一件事，
混著平均、混著排「待複核」就是在比兩把尺。

缺 `judge_model` 的舊列一律視為 `LEGACY_JUDGE_MODEL`：`FAITHFULNESS_MODEL` 在那之前
沿用 `ASK_INTENT_MODEL`，其預設自 2026-07-23 起是 `claude-haiku-4-5`，生產環境檔兩處都
沒有覆寫（2026-09-23 核對鍵名）。**這個常數永遠不跟著生產預設改**：生產 judge 換掉之後，
舊列仍是 haiku 量的。

**刻意是葉模組**（不 import 任何 app.*）：`app/services/faithfulness.py`、`eval/`、
`web/routers/` 與 `scripts/` 都要用它，放在任何一邊都會把另一邊的相依拖進來。
"""

from __future__ import annotations

import json

JUDGE_SCHEMA_VERSION = 1

# 缺 judge_model 的舊 evaluation 所屬的 judge（理由見模組 docstring）。
LEGACY_JUDGE_MODEL = "claude-haiku-4-5"

# SQL：一列 qa_log 的 evaluation 是哪個 judge 量的。evaluation 為 NULL 時也回
# LEGACY_JUDGE_MODEL，所以計數要搭配 count(evaluation) 或 evaluation IS NOT NULL。
# 空字串比照缺鍵（NULLIF），與 judge_model_of 逐條等價。
JUDGE_MODEL_SQL = f"COALESCE(NULLIF(evaluation->>'judge_model', ''), '{LEGACY_JUDGE_MODEL}')"

# SQL 條件：只計現行 judge。呼叫端綁定參數 :judge_model（＝ settings.faithfulness_model）。
CURRENT_JUDGE_SQL = f"({JUDGE_MODEL_SQL}) = :judge_model"


def judge_model_of(evaluation) -> str | None:
    """evaluation dict 的 judge；缺鍵視為 LEGACY_JUDGE_MODEL。不是 dict（未查核）回 None。"""
    if not isinstance(evaluation, dict):
        return None
    value = evaluation.get("judge_model")
    if value is None or value == "":
        return LEGACY_JUDGE_MODEL
    # 非字串時比照 PostgreSQL 的 `->>`：純量取其 JSON 文字（1 → "1"、true → "true"）。
    return value if isinstance(value, str) else json.dumps(value)


def is_current_judge(evaluation, current: str) -> bool:
    """與 CURRENT_JUDGE_SQL 同一條規則的 Python 版（離線彙總用）。"""
    return judge_model_of(evaluation) == current
