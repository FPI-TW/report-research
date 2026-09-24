"""judge 的量尺：回應解讀規則的版本，以及「這筆分數是哪個 judge 量的」。

生產忠實度抽查（`app/services/faithfulness.py`）與離線評測（`eval/`）共用。

**schema 版本**跟著「judge 回應怎麼被解讀」走，不跟著模型走：同一個模型在不同版本的解讀
規則下會算出不同的分數，所以它是量尺的一部分——離線評測把它記進 summary 的 META 鍵
（`scripts/eval_compare.py` 對不同版本拒絕給結論），生產端記進 `qa_log.evaluation`。

- 1：寬鬆解讀。缺 idx、型別不符的 verdict 靜默記為 unsupported／不相關；`statements`
  是字串時逐字迭代（每個字變成一條主張）；AR 取不到問題記 0.0。這些全都**不報錯、只壓低
  分數**——量尺壞掉的樣子和品質變差一模一樣。
- 2（DeepSeek 遷移 PR-08）：嚴格驗證，見下方 `parse_*`。違反一律拋 `JudgeSchemaError`，
  由 `call_validated` 重試 1 次；仍不合格時離線記該指標 None（run_ragas 的 judge_errors），
  生產記 `degraded_reason="schema"`。唯一的寬鬆例外是**生產端的 grounding 缺 idx**：仍計為
  unsupported、另記 WARNING（審查 L19——改成 degraded 等於那一題沒查，方向是漏抓）。
  CP 的候選片段同時改為 1 起編號、與脈絡本身的 `[n]` 及答案的引用一致（審查 M12）。

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
import logging

logger = logging.getLogger(__name__)

JUDGE_SCHEMA_VERSION = 2

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


# ── schema v2：嚴格驗證（生產與離線共用）─────────────────────────────────────


class JudgeSchemaError(Exception):
    """judge 回應的形狀不合 schema v2。刻意不繼承 ValueError：解析層（JSON 壞掉）的
    `except ValueError` 不該把「JSON 合法但內容不合格」一起吞成另一種錯誤。"""


def _obj(res, what: str) -> dict:
    if not isinstance(res, dict):
        raise JudgeSchemaError(f"{what}：回應不是 JSON 物件（{type(res).__name__}）")
    return res


def parse_statements(res) -> list[str]:
    """拆解結果 → 主張清單。`statements` 必須是 list[str]；空白字串濾掉（不算錯）。

    字串不算 list：v1 對 `{"statements": "一段話"}` 會逐字迭代，每個字變成一條主張。
    """
    st = _obj(res, "statements").get("statements")
    if not isinstance(st, list):
        raise JudgeSchemaError(f"statements 不是陣列（{type(st).__name__}）")
    bad = [type(x).__name__ for x in st if not isinstance(x, str)]
    if bad:
        raise JudgeSchemaError(f"statements 含非字串元素（{', '.join(sorted(set(bad)))}）")
    return [x for x in st if x.strip()]


def parse_verdicts(
    res, n: int, key: str, *, first: int = 0, allow_missing: bool = False
) -> tuple[dict[int, bool], list[int]]:
    """逐條判定 → ({位置: bool}, 缺漏的位置)。位置一律 0 起算（idx - first）。

    - `verdicts` 必須是物件陣列；`idx` 必須 `type(x) is int`（排除 bool：True 會被當成 1）。
    - `key`（supported／relevant）必須是 bool；`"true"`、1 都不算。
    - idx 集合必須**恰好等於** range(first, first + n)：越界、重複一律錯；缺漏預設也是錯，
      `allow_missing=True`（只給生產 grounding，L19）時改為回報缺漏的位置、記 WARNING。
    """
    vs = _obj(res, "verdicts").get("verdicts")
    if not isinstance(vs, list):
        raise JudgeSchemaError(f"verdicts 不是陣列（{type(vs).__name__}）")
    out: dict[int, bool] = {}
    for v in vs:
        if not isinstance(v, dict):
            raise JudgeSchemaError(f"verdict 不是物件：{v!r:.60}")
        idx = v.get("idx")
        if type(idx) is not int:
            raise JudgeSchemaError(f"idx 不是整數：{idx!r:.30}")
        val = v.get(key)
        if type(val) is not bool:
            raise JudgeSchemaError(f"{key} 不是布林：{val!r:.30}")
        pos = idx - first
        if not 0 <= pos < n:
            raise JudgeSchemaError(f"idx {idx} 越界（應在 {first}..{first + n - 1}）")
        if pos in out:
            raise JudgeSchemaError(f"idx {idx} 重複")
        out[pos] = val
    missing = [i for i in range(n) if i not in out]
    if missing:
        if not allow_missing:
            raise JudgeSchemaError(f"缺 idx {[i + first for i in missing][:10]}（共 {len(missing)} 個）")
        logger.warning("judge 漏判 %d/%d 條（idx %s），計為 unsupported", len(missing), n,
                       [i + first for i in missing][:10])
    return out, missing


def parse_questions(res) -> list[str]:
    """AR 反推問題 → 非空字串清單。取不到任何問題就是 schema 錯（v1 記 0.0，等於把
    「judge 沒回答」當成「答非所問」）。"""
    qs = _obj(res, "questions").get("questions")
    if not isinstance(qs, list):
        raise JudgeSchemaError(f"questions 不是陣列（{type(qs).__name__}）")
    if any(not isinstance(q, str) for q in qs):
        raise JudgeSchemaError("questions 含非字串元素")
    out = [q for q in qs if q.strip()]
    if not out:
        raise JudgeSchemaError("取不到任何反推問題")
    return out


SCHEMA_RETRIES = 1


async def call_validated(judge, system: str, user: str, parse, *, retries: int = SCHEMA_RETRIES):
    """呼叫 judge 並以 parse 驗證；JudgeSchemaError 時重試 retries 次，仍失敗就拋。

    judge 回 None（生產 judge 自身失敗的 fail-open 約定）原樣回 None、不重試——那不是
    schema 問題，原因由 judge 自己記。回傳 parse 的結果。
    """
    for attempt in range(retries + 1):
        res = await judge(system, user)
        if res is None:
            return None
        try:
            return parse(res)
        except JudgeSchemaError as e:
            if attempt >= retries:
                raise
            logger.warning("judge 回應不合 schema v%d，重試：%s", JUDGE_SCHEMA_VERSION, e)
    raise AssertionError("unreachable")  # pragma: no cover
