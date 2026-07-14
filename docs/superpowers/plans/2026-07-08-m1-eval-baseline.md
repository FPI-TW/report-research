# M1 Eval 基準線 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立離線、reference-free 的 RAG 品質評測地基（`eval/` 套件），對固定題集跑「檢索→生成→評分」，產出可重現基準線供 M2 起量增益、防回歸。

**Architecture:** 四個 `eval/` 模組——`judge.py`（claude CLI 評審原語）、`ragas_metrics.py`（Faithfulness/Context Precision/Answer Relevancy 三純函式，judge/embed 注入）、`dataset.py`（挖 `research.qa_log` → 版本化題集）、`run_ragas.py`（編排 retrieve→generate→score→aggregate）——加 `eval/baselines/`（版本化基準線）。生成端刻意用 M0 的 `retrieve_context` + `build_user_prompt` + `stream_completion`，隔離檢索與生成、避開 `answer_question` 的 qa_log 寫入/overview/off-topic 分支。零 web 影響、無 schema 變更。

**Tech Stack:** Python 3.11、asyncio、numpy 2.4（已裝）、claude CLI（既有）、BGE-M3（既有）、SQLAlchemy async。測試：`unittest`（`TestCase` / `IsolatedAsyncioTestCase`）跑於 pytest。無新增依賴、不引入 `ragas`/`langchain`。

## Global Constraints

- **零 web 影響、無 schema 變更、無需 restart。** `eval/` 不進 `web/server.py`，不改 `app/services/*` 生成邏輯，不改 `scripts/eval_retrieval.py`/`analyze_qa_log.py`。
- **eval 旋鈕不進 M0 的 `app/config.Settings`**（離線專屬，走 CLI 參數/模組常數/`os.getenv`）。
- **不自造可疑的 reference-free recall 指標**；召回維度沿用既有 `scripts/eval_retrieval.py`。
- **不引入新依賴**：`numpy` 已可用（2.4.6）；claude CLI、BGE-M3、`SessionFactory` 皆既有。
- **判準預設值**：`DEFAULT_JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "claude-haiku-4-5")`；生成模型 `DEFAULT_MODEL = "claude-sonnet-4-6"`（自 `app.services.llm`）。
- **門檻（文件化回歸 gate，非 CI 硬失敗）**：Faithfulness>0.9、Context Precision>0.8、Answer Relevancy>0.85。
- **共用工作樹**：只 `git add <明確路徑>`，禁用 `git add -A`/`.`；提交前 `git diff --staged --stat` 驗範圍。Conventional Commits + 繁中 scope。commit message 結尾加 `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`。
- **分支**：`feat/m1-eval-baseline`（已建、已提交 spec `f4ea215`，疊於 M0 tip `33b7551`）。
- **匯入慣例**：CLI 入口（`dataset.py`/`run_ragas.py`）於檔首插 repo root 到 `sys.path`（沿用 `scripts/eval_retrieval.py:27` 慣例），再絕對匯入 `from eval.x import` 與 `from app.x import`。測試檔於檔首 `sys.path.insert(0, str(Path(__file__).resolve().parents[1]))`（沿用 `tests/test_config.py`）。
- **filters 白名單**：qa_log 的 `filters` 含 `null` 值與 `path:"overview"` 雜鍵；傳給 `hybrid_search(**filters)` 的 dict 只能含其接受的鍵 `market/instrument_type/relates_stock/relates_futures/report_type`，且 `path=="overview"` 的列（純 SQL 總覽路徑、非 RAG）須從題集濾除。

## 既有 seam（M0 已就緒，M1 直接消費，勿改）

- `app.services.retrieval_pipeline.retrieve_context(question, *, k, dense_scan, max_reports, max_passages, max_chars, filters=None, now=None, timer=None) -> tuple[list[Source], str]`
- `app.services.answer.build_user_prompt(question, context, history_block="") -> str`
- `app.services.answer.SYSTEM_PROMPT`（str）、`OFF_TOPIC_MESSAGE`（str）、`NO_CONTEXT_MESSAGE`（str）
- `app.services.answer.RETRIEVAL_K / ASK_DENSE_SCAN / MAX_REPORTS / MAX_PASSAGES_PER_REPORT / MAX_CONTEXT_CHARS`（int）
- `app.services.llm.stream_completion(prompt, *, model, system, timeout, allow_web, retries) -> AsyncIterator[str]`、`DEFAULT_MODEL="claude-sonnet-4-6"`、`SEARCH_EVENT`（控制標記，需在生成端跳過）
- `app.services.embed.embed_query_cached(text) -> list[float]`（1024 維，同步、LRU 快取）
- `app.services.textnorm.norm_for_match(s) -> str`
- `app.services.db.SessionFactory`（async_sessionmaker）
- `build_context` 產出的脈絡格式：每篇一塊，塊間以 `\n\n` 分隔；塊首行 `[i] 報告：<檔名>（市場 X，日期 Y）`，其後每段一行（段內無換行，經 `clean_text` 收斂）。

## File Structure

| 檔案 | 責任 |
|---|---|
| `eval/__init__.py` | 空檔，使 `eval/` 成正規套件，讓 `from eval.x import` 於 repo-root-on-path 時解析 |
| `eval/judge.py` | 評審 LLM 原語：`judge_json` drain `stream_completion` + robust JSON 解析；`JudgeError`；`DEFAULT_JUDGE_MODEL` |
| `eval/ragas_metrics.py` | 三指標純函式（judge/embed 注入）+ prompt 常數 + `_cosine`/`_average_precision` 小工具 |
| `eval/dataset.py` | 題集生成器：`select_questions`（純）+ `build_dataset`（DB）+ CLI；寫版本化 `eval/ragas_questions.json` |
| `eval/run_ragas.py` | 編排：`split_contexts` + `eval_question` + `aggregate`（純）+ `run` + CLI；寫 `eval/baselines/<label>.json` |
| `eval/baselines/baseline-m0.json` | 首跑產物（Task 5 操作性產生、commit）＝M2 前基準線 |
| `tests/test_eval_judge.py` | `judge_json` robust 解析單測（fake `stream_completion`） |
| `tests/test_ragas_metrics.py` | 三指標決定性單測（fake judge/embed，零真 LLM） |
| `tests/test_eval_dataset.py` | `select_questions` 去重/濾/白名單/抽樣單測（fake 列，無 DB） |
| `tests/test_run_ragas.py` | `split_contexts`/`aggregate` 純函式 + `eval_question` 全 mock 冒煙 + fail-open |

---

### Task 1: `eval/judge.py` — 評審 LLM 原語

**Files:**
- Create: `eval/__init__.py`
- Create: `eval/judge.py`
- Test: `tests/test_eval_judge.py`

**Interfaces:**
- Consumes: `app.services.llm.stream_completion`（async generator）。
- Produces:
  - `class JudgeError(Exception)`
  - `DEFAULT_JUDGE_MODEL: str`（`os.getenv("EVAL_JUDGE_MODEL", "claude-haiku-4-5")`）
  - `async def judge_json(prompt: str, *, system: str, model: str = DEFAULT_JUDGE_MODEL, timeout: float = 60.0) -> dict | list` — drain 串流、robust 解析 JSON；空回應或解析失敗 raise `JudgeError`。

- [ ] **Step 1: 建 `eval/__init__.py`（空套件標記）**

```python
```

（空檔即可；`eval/` 因此成正規套件，`from eval.judge import ...` 於 repo root 在 `sys.path` 時可解析。）

- [ ] **Step 2: 寫 `tests/test_eval_judge.py` 的失敗測試**

```python
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval import judge as judge_mod  # noqa: E402
from eval.judge import JudgeError, judge_json  # noqa: E402


def _fake_stream(chunks):
    """回一個模擬 stream_completion 的 async generator 工廠（忽略引數）。"""

    async def _gen(prompt, *, model=None, system=None, timeout=None, allow_web=False, retries=2):
        for c in chunks:
            yield c

    return _gen


class JudgeJsonTests(unittest.IsolatedAsyncioTestCase):
    async def test_parses_bare_json_object(self):
        judge_mod.stream_completion = _fake_stream(['{"statements": ', '["a", "b"]}'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"statements": ["a", "b"]})

    async def test_strips_json_code_fence(self):
        judge_mod.stream_completion = _fake_stream(['```json\n{"verdicts": []}\n```'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"verdicts": []})

    async def test_extracts_json_amid_prose(self):
        judge_mod.stream_completion = _fake_stream(['好的，結果如下：{"questions": ["q1"]} 以上。'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, {"questions": ["q1"]})

    async def test_parses_json_array(self):
        judge_mod.stream_completion = _fake_stream(['[1, 2, 3]'])
        out = await judge_json("x", system="s")
        self.assertEqual(out, [1, 2, 3])

    async def test_empty_response_raises(self):
        judge_mod.stream_completion = _fake_stream(['   '])
        with self.assertRaises(JudgeError):
            await judge_json("x", system="s")

    async def test_malformed_json_raises(self):
        judge_mod.stream_completion = _fake_stream(['not json at all'])
        with self.assertRaises(JudgeError):
            await judge_json("x", system="s")

    async def test_default_judge_model_is_haiku(self):
        self.assertEqual(judge_mod.DEFAULT_JUDGE_MODEL, "claude-haiku-4-5")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: 跑測試確認失敗**

Run: `uv run pytest tests/test_eval_judge.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'eval.judge'` 或 import error）

- [ ] **Step 4: 實作 `eval/judge.py`**

```python
"""評審 LLM 原語：drain claude CLI 串流 + robust JSON 解析。

沿用 intent.py 的 drain 慣例（parts=[] async for ... "".join(parts)）。judge 不上網
（allow_web=False）。解析容忍 ```json 圍欄、前後散文，取第一個平衡的 {..}/[..]；
空回應或解析失敗一律 raise JudgeError，交由 run_ragas 逐題 fail-open。
"""

from __future__ import annotations

import json
import os
import re

from app.services.llm import stream_completion

DEFAULT_JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "claude-haiku-4-5")

_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


class JudgeError(Exception):
    """評審回應為空或無法解析為 JSON。"""


def _loads_robust(raw: str) -> dict | list:
    """從評審輸出解析 JSON：先去 ```json 圍欄、直接 loads；失敗則取第一個平衡括號子串。"""
    s = raw.strip()
    m = _FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        pass
    starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
    if not starts:
        raise JudgeError(f"no JSON found in judge output: {raw[:120]!r}")
    start = min(starts)
    open_ch = s[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    for i in range(start, len(s)):
        if s[i] == open_ch:
            depth += 1
        elif s[i] == close_ch:
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(s[start : i + 1])
                except (ValueError, TypeError) as e:
                    raise JudgeError(f"malformed JSON: {e}") from e
    raise JudgeError(f"unbalanced JSON in judge output: {raw[:120]!r}")


async def judge_json(
    prompt: str,
    *,
    system: str,
    model: str = DEFAULT_JUDGE_MODEL,
    timeout: float = 60.0,
) -> dict | list:
    """drain stream_completion 取全文 → robust 解析為 JSON。空/畸形 → JudgeError。"""
    parts: list[str] = []
    async for chunk in stream_completion(
        prompt, model=model, system=system, timeout=timeout, allow_web=False
    ):
        parts.append(chunk)
    text = "".join(parts)
    if not text.strip():
        raise JudgeError("empty judge response")
    return _loads_robust(text)
```

- [ ] **Step 5: 跑測試確認通過**

Run: `uv run pytest tests/test_eval_judge.py -v`
Expected: PASS（7 passed）

- [ ] **Step 6: Commit**

```bash
git add eval/__init__.py eval/judge.py tests/test_eval_judge.py
git commit -m "$(cat <<'EOF'
feat(eval): 加 judge_json 評審 LLM 原語（M1）

drain claude CLI 串流 + robust JSON 解析（去 ```json 圍欄、取第一個平衡括號）；
空/畸形回應 raise JudgeError。judge 不上網、預設 Haiku。M1 eval 基準線地基。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: `eval/ragas_metrics.py` — 三指標純函式

**Files:**
- Create: `eval/ragas_metrics.py`
- Test: `tests/test_ragas_metrics.py`

**Interfaces:**
- Consumes: 注入的 `judge` 與 `embed` callable。**judge 契約**：`async def judge(system: str, user: str) -> dict | list`（run_ragas 以 adapter 包 `judge_json`）。**embed 契約**：`embed(text: str) -> list[float]`（同步、回 1024 維向量）。
- Produces:
  - `async def faithfulness(answer: str, contexts: list[str], *, judge) -> float | None`
  - `async def context_precision(question: str, answer: str, contexts: list[str], *, judge) -> float`
  - `async def answer_relevancy(question: str, answer: str, *, judge, embed) -> float`
  - prompt 常數：`DECOMPOSE_SYS`、`GROUND_SYS`、`CTX_RELEVANCE_SYS`、`GENQ_SYS`
  - `_cosine(a, b) -> float`、`_average_precision(rel: list[int]) -> float`

- [ ] **Step 1: 寫 `tests/test_ragas_metrics.py` 純工具與 faithfulness 的失敗測試**

```python
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval.ragas_metrics import (  # noqa: E402
    _average_precision,
    _cosine,
    answer_relevancy,
    context_precision,
    faithfulness,
)


def make_judge(by_marker):
    """回一個 async judge：依 system prompt 內含的標記字選回應。by_marker: {substr: payload}。"""

    async def _judge(system, user):
        for marker, payload in by_marker.items():
            if marker in system:
                return payload
        raise AssertionError(f"unexpected judge system: {system[:40]!r}")

    return _judge


def make_embed(vectors):
    """回一個 embed：依文字查表回向量；查無則回零向量。"""

    def _embed(text):
        return vectors.get(text, [0.0, 0.0, 0.0])

    return _embed


class PureHelperTests(unittest.TestCase):
    def test_cosine_identical_is_one(self):
        self.assertAlmostEqual(_cosine([1.0, 0.0], [1.0, 0.0]), 1.0)

    def test_cosine_orthogonal_is_zero(self):
        self.assertAlmostEqual(_cosine([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_cosine_zero_vector_is_zero(self):
        self.assertEqual(_cosine([0.0, 0.0], [1.0, 1.0]), 0.0)

    def test_average_precision_all_relevant(self):
        # rel=[1,1,1] → (1/1 + 2/2 + 3/3)/3 = 1.0
        self.assertAlmostEqual(_average_precision([1, 1, 1]), 1.0)

    def test_average_precision_none_relevant(self):
        self.assertEqual(_average_precision([0, 0, 0]), 0.0)

    def test_average_precision_rank_weighted(self):
        # rel=[0,1,1] → hits at k=2,3: (1/2 + 2/3)/2 = 0.58333...
        self.assertAlmostEqual(_average_precision([0, 1, 1]), (0.5 + 2 / 3) / 2)


class FaithfulnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_supported_is_one(self):
        judge = make_judge({
            "拆解": {"statements": ["s1", "s2"]},
            "佐證": {"verdicts": [{"idx": 0, "supported": True}, {"idx": 1, "supported": True}]},
        })
        score = await faithfulness("ans", ["ctx"], judge=judge)
        self.assertAlmostEqual(score, 1.0)

    async def test_half_supported_is_half(self):
        judge = make_judge({
            "拆解": {"statements": ["s1", "s2"]},
            "佐證": {"verdicts": [{"idx": 0, "supported": True}, {"idx": 1, "supported": False}]},
        })
        score = await faithfulness("ans", ["ctx"], judge=judge)
        self.assertAlmostEqual(score, 0.5)

    async def test_zero_statements_is_none(self):
        judge = make_judge({"拆解": {"statements": []}})
        score = await faithfulness("找不到相關資料", ["ctx"], judge=judge)
        self.assertIsNone(score)

    async def test_missing_verdict_counts_unsupported(self):
        judge = make_judge({
            "拆解": {"statements": ["s1", "s2"]},
            "佐證": {"verdicts": [{"idx": 0, "supported": True}]},
        })
        score = await faithfulness("ans", ["ctx"], judge=judge)
        self.assertAlmostEqual(score, 0.5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_ragas_metrics.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'eval.ragas_metrics'`）

- [ ] **Step 3: 實作 `eval/ragas_metrics.py`（工具 + prompts + faithfulness + context_precision + answer_relevancy）**

```python
"""Reference-free RAGAS 三指標（自實作，不引入 ragas/langchain）。

judge/embed 皆為注入 callable，故可用 fake 決定性單測、零真 LLM。prompt 與 parser
同檔共置（防漂移）。judge 契約：async judge(system, user) -> dict|list；embed 契約：
embed(text) -> list[float]（1024 維）。
"""

from __future__ import annotations

import numpy as np

# --- Prompt 常數（就地共置；一律要求 JSON-only 輸出）---

DECOMPOSE_SYS = (
    "你是 RAG 評測助手。把下列『回答』拆解成一組獨立、原子的事實主張（statement）。"
    "每個主張須可獨立判斷真偽，不含連接詞堆疊。若回答只是『找不到資料』之類、"
    "未提出任何事實主張，回空陣列。\n"
    '只輸出 JSON，格式：{"statements": ["主張1", "主張2", ...]}，不要任何其他文字。'
)

GROUND_SYS = (
    "你是 RAG 忠實度評審。給定『參考片段』與一組『主張』，逐一判斷每個主張是否"
    "能由參考片段直接支持（supported）。只依片段內容判斷，不用外部知識；片段沒說到、"
    "或與片段矛盾，一律 supported=false。\n"
    '只輸出 JSON，格式：{"verdicts": [{"idx": 0, "supported": true}, ...]}，'
    "idx 對應主張的 0-based 序號，不要任何其他文字。"
)

CTX_RELEVANCE_SYS = (
    "你是 RAG 檢索精準度評審。給定『問題』『回答』與一組候選片段，逐一判斷每個片段"
    "是否與回答此問題相關（relevant）——即該片段是否提供了回答問題所需的資訊。\n"
    '只輸出 JSON，格式：{"verdicts": [{"idx": 0, "relevant": true}, ...]}，'
    "idx 對應候選片段的 0-based 序號，不要任何其他文字。"
)

GENQ_SYS = (
    "你是 RAG 答案相關性評審。閱讀下列『回答』，反推它最可能在回答的 3 個問題"
    "（假設你沒看過原問題）。問題須具體、可獨立理解。\n"
    '只輸出 JSON，格式：{"questions": ["問題1", "問題2", "問題3"]}，不要任何其他文字。'
)


def _cosine(a, b) -> float:
    """兩向量餘弦相似度；任一為零向量 → 0.0。"""
    va = np.asarray(a, dtype=float)
    vb = np.asarray(b, dtype=float)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.dot(va, vb) / (na * nb))


def _average_precision(rel: list[int]) -> float:
    """RAGAS rank-weighted AP：sum_k(precision@k * rel_k) / total_relevant。無相關 → 0.0。"""
    total_relevant = sum(rel)
    if total_relevant == 0:
        return 0.0
    score = 0.0
    hits = 0
    for k, r in enumerate(rel, start=1):
        if r:
            hits += 1
            score += hits / k
    return score / total_relevant


async def faithfulness(answer: str, contexts: list[str], *, judge) -> float | None:
    """拆解回答為主張 → 逐條佐證於 contexts。分數 = supported/total；total==0 → None。"""
    dec = await judge(DECOMPOSE_SYS, answer)
    statements = dec.get("statements") if isinstance(dec, dict) else None
    statements = [s for s in (statements or []) if isinstance(s, str) and s.strip()]
    if not statements:
        return None  # 無事實主張（如「找不到資料」）：自均值排除，不以空洞值灌水
    joined_ctx = "\n\n".join(contexts)
    enumerated = "\n".join(f"{i}. {s}" for i, s in enumerate(statements))
    payload = f"參考片段：\n{joined_ctx}\n\n主張：\n{enumerated}"
    res = await judge(GROUND_SYS, payload)
    verdicts = res.get("verdicts") if isinstance(res, dict) else None
    supported = sum(
        1
        for v in (verdicts or [])
        if isinstance(v, dict) and v.get("supported") is True
    )
    return supported / len(statements)


async def context_precision(
    question: str, answer: str, contexts: list[str], *, judge
) -> float:
    """逐 context 判相關性 → rank-weighted AP。無 context → 0.0。"""
    if not contexts:
        return 0.0
    enumerated = "\n\n".join(f"[{i}]\n{c}" for i, c in enumerate(contexts))
    payload = f"問題：{question}\n\n回答：{answer}\n\n候選片段：\n{enumerated}"
    res = await judge(CTX_RELEVANCE_SYS, payload)
    verdicts = res.get("verdicts") if isinstance(res, dict) else None
    relmap: dict[int, int] = {}
    for v in verdicts or []:
        if isinstance(v, dict) and isinstance(v.get("idx"), int):
            relmap[v["idx"]] = 1 if v.get("relevant") is True else 0
    rel = [relmap.get(i, 0) for i in range(len(contexts))]
    return _average_precision(rel)


async def answer_relevancy(question: str, answer: str, *, judge, embed) -> float:
    """由回答反推 3 個問題 → 各與原問題的 embedding cosine 取平均。無反推問題 → 0.0。"""
    out = await judge(GENQ_SYS, answer)
    gen = out.get("questions") if isinstance(out, dict) else None
    gen = [q for q in (gen or []) if isinstance(q, str) and q.strip()]
    if not gen:
        return 0.0
    qv = embed(question)
    sims = [_cosine(qv, embed(g)) for g in gen]
    return sum(sims) / len(sims)
```

- [ ] **Step 4: 跑 faithfulness 與工具測試確認通過**

Run: `uv run pytest tests/test_ragas_metrics.py -v`
Expected: PASS（PureHelperTests 6 + FaithfulnessTests 4 = 10 passed）

- [ ] **Step 5: 補 context_precision 與 answer_relevancy 測試**

在 `tests/test_ragas_metrics.py` 的 `FaithfulnessTests` 類別後（`if __name__` 之前）加入：

```python
class ContextPrecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_relevant_is_one(self):
        judge = make_judge({
            "精準度": {"verdicts": [{"idx": 0, "relevant": True}, {"idx": 1, "relevant": True}]},
        })
        score = await context_precision("q", "a", ["c0", "c1"], judge=judge)
        self.assertAlmostEqual(score, 1.0)

    async def test_none_relevant_is_zero(self):
        judge = make_judge({
            "精準度": {"verdicts": [{"idx": 0, "relevant": False}, {"idx": 1, "relevant": False}]},
        })
        score = await context_precision("q", "a", ["c0", "c1"], judge=judge)
        self.assertEqual(score, 0.0)

    async def test_rank_weighted_first_irrelevant(self):
        # rel=[0,1,1] → (1/2 + 2/3)/2
        judge = make_judge({
            "精準度": {"verdicts": [
                {"idx": 0, "relevant": False},
                {"idx": 1, "relevant": True},
                {"idx": 2, "relevant": True},
            ]},
        })
        score = await context_precision("q", "a", ["c0", "c1", "c2"], judge=judge)
        self.assertAlmostEqual(score, (0.5 + 2 / 3) / 2)

    async def test_no_contexts_is_zero(self):
        judge = make_judge({"精準度": {"verdicts": []}})
        score = await context_precision("q", "a", [], judge=judge)
        self.assertEqual(score, 0.0)


class AnswerRelevancyTests(unittest.IsolatedAsyncioTestCase):
    async def test_mean_cosine_of_generated(self):
        judge = make_judge({"反推": {"questions": ["g1", "g2"]}})
        embed = make_embed({
            "orig": [1.0, 0.0, 0.0],
            "g1": [1.0, 0.0, 0.0],   # cosine 1.0
            "g2": [0.0, 1.0, 0.0],   # cosine 0.0
        })
        score = await answer_relevancy("orig", "a", judge=judge, embed=embed)
        self.assertAlmostEqual(score, 0.5)

    async def test_no_generated_questions_is_zero(self):
        judge = make_judge({"反推": {"questions": []}})
        embed = make_embed({})
        score = await answer_relevancy("orig", "a", judge=judge, embed=embed)
        self.assertEqual(score, 0.0)
```

- [ ] **Step 6: 跑全套指標測試確認通過**

Run: `uv run pytest tests/test_ragas_metrics.py -v`
Expected: PASS（10 + ContextPrecisionTests 4 + AnswerRelevancyTests 2 = 16 passed）

- [ ] **Step 7: Commit**

```bash
git add eval/ragas_metrics.py tests/test_ragas_metrics.py
git commit -m "$(cat <<'EOF'
feat(eval): 加 RAGAS 三指標純函式（M1）

Faithfulness（拆主張+逐條佐證，零主張→None）、Context Precision（rank-weighted AP）、
Answer Relevancy（反推問題 embedding cosine）；judge/embed 注入故可 fake 決定性單測。
prompt 與 parser 同檔共置防漂移。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: `eval/dataset.py` — 題集生成器

**Files:**
- Create: `eval/dataset.py`
- Test: `tests/test_eval_dataset.py`

**Interfaces:**
- Consumes: `app.services.db.SessionFactory`、`sqlalchemy.text`、`app.services.textnorm.norm_for_match`、`app.services.answer.OFF_TOPIC_MESSAGE`/`NO_CONTEXT_MESSAGE`。
- Produces:
  - `MIN_QUESTION_LEN: int = 6`
  - `_RETRIEVAL_FILTER_KEYS: tuple[str, ...]`
  - `def select_questions(rows: list[dict], *, per_market_cap: int, target: int) -> list[dict]` — 回 `[{"id","question","filters"}]`。
  - `async def build_dataset(*, target: int, per_market_cap: int) -> dict` — 回 `{"version","generated_at":None,"count","questions"}`（時間戳由 CLI 覆寫）。

- [ ] **Step 1: 寫 `tests/test_eval_dataset.py` 的失敗測試**

```python
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.services.answer import NO_CONTEXT_MESSAGE, OFF_TOPIC_MESSAGE  # noqa: E402
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
            _row("幫我寫一首詩", answer=OFF_TOPIC_MESSAGE),
            _row("有沒有火星股票", answer=NO_CONTEXT_MESSAGE),
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
            "market": "TW", "report_type": None, "path": "overview_should_not_leak",
        })]
        # path=="overview" 會被整列濾除，故改測非 overview 的雜鍵/None 清洗：
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_eval_dataset.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'eval.dataset'`）

- [ ] **Step 3: 實作 `eval/dataset.py`**

```python
"""題集生成器：挖 research.qa_log → 去重/濾/白名單/市場多樣抽樣 → 版本化 JSON。

生成器語義：偶爾 refresh、人工過目後 commit＝穩定 baseline 輸入。純函式 select_questions
不取時間；generated_at 由 CLI 以現在時間戳入。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import OrderedDict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.services.answer import NO_CONTEXT_MESSAGE, OFF_TOPIC_MESSAGE  # noqa: E402
from app.services.db import SessionFactory  # noqa: E402
from app.services.textnorm import norm_for_match  # noqa: E402

MIN_QUESTION_LEN = 6

# 只保留 hybrid_search 接受的檢索相關 filter 鍵；qa_log 的 null 值與 path 等雜鍵剝除
_RETRIEVAL_FILTER_KEYS = (
    "market",
    "instrument_type",
    "relates_stock",
    "relates_futures",
    "report_type",
)


def _clean_filters(filters: dict) -> dict:
    """只留白名單鍵、丟棄 None 值 → 可安全展開給 hybrid_search 的 dict。"""
    out: dict = {}
    for key in _RETRIEVAL_FILTER_KEYS:
        v = filters.get(key)
        if v is not None:
            out[key] = v
    return out


def select_questions(rows: list[dict], *, per_market_cap: int, target: int) -> list[dict]:
    """純函式：濾 off-topic/no-context/overview/過短、去重（正規化）、市場多樣抽樣。

    rows: [{"question","answer","filters"}]（filters 為 dict）。回 [{"id","question","filters"}]。
    """
    seen: set[str] = set()
    per_market: "OrderedDict[str, list[dict]]" = OrderedDict()
    for r in rows:
        q = (r.get("question") or "").strip()
        a = (r.get("answer") or "").strip()
        filters = r.get("filters") or {}
        if not q or len(q) < MIN_QUESTION_LEN:
            continue
        if a in (OFF_TOPIC_MESSAGE, NO_CONTEXT_MESSAGE):
            continue
        if filters.get("path") == "overview":  # 總覽走純 SQL、非 RAG，不進題集
            continue
        key = norm_for_match(q)
        if key in seen:
            continue
        seen.add(key)
        cleaned = _clean_filters(filters)
        market = cleaned.get("market") or "_general"
        bucket = per_market.setdefault(market, [])
        if len(bucket) < per_market_cap:
            bucket.append({"question": q, "filters": cleaned})

    # round-robin 攤平各市場、湊到 target（多樣優先）
    result: list[dict] = []
    lists = list(per_market.values())
    while len(result) < target and any(lists):
        progressed = False
        for lst in lists:
            if lst:
                result.append(lst.pop(0))
                progressed = True
                if len(result) >= target:
                    break
        if not progressed:
            break

    return [
        {"id": f"q{n:03d}", "question": it["question"], "filters": it["filters"]}
        for n, it in enumerate(result, start=1)
    ]


async def build_dataset(*, target: int, per_market_cap: int) -> dict:
    """讀 research.qa_log（最近 2000 列）→ select_questions → 版本化結構（generated_at 待 CLI 覆寫）。"""
    async with SessionFactory() as session:
        res = await session.execute(
            text(
                "SELECT question, answer, filters FROM research.qa_log "
                "WHERE answer IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 2000"
            )
        )
        rows = [
            {"question": row[0], "answer": row[1], "filters": row[2] or {}}
            for row in res
        ]
    questions = select_questions(rows, per_market_cap=per_market_cap, target=target)
    return {
        "version": 1,
        "generated_at": None,
        "count": len(questions),
        "questions": questions,
    }


def _main() -> None:
    parser = argparse.ArgumentParser(description="挖 qa_log 產出版本化 eval 題集")
    parser.add_argument("--out", default="eval/ragas_questions.json")
    parser.add_argument("--target", type=int, default=30)
    parser.add_argument("--per-market-cap", type=int, default=8)
    args = parser.parse_args()

    dataset = asyncio.run(
        build_dataset(target=args.target, per_market_cap=args.per_market_cap)
    )
    dataset["generated_at"] = datetime.now(timezone.utc).isoformat()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {dataset['count']} questions -> {out_path}")


if __name__ == "__main__":
    _main()
```

- [ ] **Step 4: 跑測試確認通過**

Run: `uv run pytest tests/test_eval_dataset.py -v`
Expected: PASS（8 passed）

- [ ] **Step 5: Commit**

```bash
git add eval/dataset.py tests/test_eval_dataset.py
git commit -m "$(cat <<'EOF'
feat(eval): 加 dataset 題集生成器（M1）

挖 research.qa_log → 濾 off-topic/no-context/overview/過短、去重（正規化）、
filter 白名單清洗（丟 null 與 path 雜鍵）、市場多樣 round-robin 抽樣 → 版本化
ragas_questions.json。select_questions 為純函式、無 DB、可決定性單測。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: `eval/run_ragas.py` — 編排

**Files:**
- Create: `eval/run_ragas.py`
- Test: `tests/test_run_ragas.py`

**Interfaces:**
- Consumes: `retrieve_context`（`app.services.retrieval_pipeline`）、`build_user_prompt`/`SYSTEM_PROMPT`/`RETRIEVAL_K`/`ASK_DENSE_SCAN`/`MAX_REPORTS`/`MAX_PASSAGES_PER_REPORT`/`MAX_CONTEXT_CHARS`（`app.services.answer`）、`stream_completion`/`DEFAULT_MODEL`/`SEARCH_EVENT`（`app.services.llm`）、`embed_query_cached`（`app.services.embed`）、`judge_json`/`DEFAULT_JUDGE_MODEL`/`JudgeError`（`eval.judge`）、三指標（`eval.ragas_metrics`）。
- Produces:
  - `def split_contexts(context: str) -> list[str]`
  - `async def eval_question(q: dict, *, judge, embed, retrieval_params: dict) -> dict`
  - `def aggregate(per_q: list[dict]) -> dict`
  - `async def run(dataset_path, *, out_path, judge_model, limit, concurrency) -> dict`

- [ ] **Step 1: 寫 `tests/test_run_ragas.py` 的失敗測試（split_contexts + aggregate 純函式）**

```python
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from eval.run_ragas import aggregate, split_contexts  # noqa: E402


class SplitContextsTests(unittest.TestCase):
    def test_splits_on_numbered_headers(self):
        context = (
            "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n段落一\n段落二\n\n"
            "[2] 報告：b.pdf（市場 US，日期 2026-02-02）\n段落三"
        )
        parts = split_contexts(context)
        self.assertEqual(len(parts), 2)
        self.assertTrue(parts[0].startswith("[1] 報告：a.pdf"))
        self.assertTrue(parts[1].startswith("[2] 報告：b.pdf"))

    def test_empty_context_is_empty_list(self):
        self.assertEqual(split_contexts(""), [])
        self.assertEqual(split_contexts("   "), [])


class AggregateTests(unittest.TestCase):
    def test_means_skip_none_and_error(self):
        per_q = [
            {"id": "q001", "faithfulness": 1.0, "context_precision": 0.8, "answer_relevancy": 0.9},
            {"id": "q002", "faithfulness": None, "context_precision": 0.6, "answer_relevancy": 0.7},
            {"id": "q003", "error": "boom"},
        ]
        agg = aggregate(per_q)
        self.assertAlmostEqual(agg["faithfulness"], 1.0)          # 只算 q001（q002 None、q003 error）
        self.assertAlmostEqual(agg["context_precision"], 0.7)     # (0.8+0.6)/2
        self.assertAlmostEqual(agg["answer_relevancy"], 0.8)      # (0.9+0.7)/2
        self.assertEqual(agg["n"], 3)
        self.assertEqual(agg["n_errors"], 1)
        self.assertEqual(agg["n_no_context"], 1)

    def test_thresholds_pass_flag(self):
        per_q = [{"id": "q1", "faithfulness": 0.95, "context_precision": 0.85, "answer_relevancy": 0.9}]
        self.assertTrue(aggregate(per_q)["thresholds_pass"])
        per_q = [{"id": "q1", "faithfulness": 0.80, "context_precision": 0.85, "answer_relevancy": 0.9}]
        self.assertFalse(aggregate(per_q)["thresholds_pass"])

    def test_all_errors_means_none(self):
        agg = aggregate([{"id": "q1", "error": "x"}])
        self.assertIsNone(agg["faithfulness"])
        self.assertFalse(agg["thresholds_pass"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 跑測試確認失敗**

Run: `uv run pytest tests/test_run_ragas.py -v`
Expected: FAIL（`ModuleNotFoundError: No module named 'eval.run_ragas'`）

- [ ] **Step 3: 實作 `eval/run_ragas.py`**

```python
"""M1 eval 編排：讀題集 → 逐題 retrieve→generate→三指標 → aggregate → 寫基準線。

生成端刻意用 build_user_prompt + stream_completion（非 answer_question）以隔離檢索與
生成、避開 qa_log 寫入/overview/off-topic。有界併發（claude CLI spawn 吃 IO）、逐題
fail-open（任一階段異常記 error、不計均值、不中斷批次）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.answer import (  # noqa: E402
    ASK_DENSE_SCAN,
    MAX_CONTEXT_CHARS,
    MAX_PASSAGES_PER_REPORT,
    MAX_REPORTS,
    RETRIEVAL_K,
    SYSTEM_PROMPT,
    build_user_prompt,
)
from app.services.embed import embed_query_cached  # noqa: E402
from app.services.llm import DEFAULT_MODEL, SEARCH_EVENT, stream_completion  # noqa: E402
from app.services.retrieval_pipeline import retrieve_context  # noqa: E402
from eval.judge import DEFAULT_JUDGE_MODEL, judge_json  # noqa: E402
from eval.ragas_metrics import answer_relevancy, context_precision, faithfulness  # noqa: E402

RETRIEVAL_PARAMS = {
    "k": RETRIEVAL_K,
    "dense_scan": ASK_DENSE_SCAN,
    "max_reports": MAX_REPORTS,
    "max_passages": MAX_PASSAGES_PER_REPORT,
    "max_chars": MAX_CONTEXT_CHARS,
}

_CTX_SPLIT_RE = re.compile(r"(?=^\[\d+\] )", re.MULTILINE)

FAITHFULNESS_MIN = 0.9
CONTEXT_PRECISION_MIN = 0.8
ANSWER_RELEVANCY_MIN = 0.85


def split_contexts(context: str) -> list[str]:
    """把 build_context 的編號脈絡字串以 [n] 表頭切成每篇一塊。空 → []。"""
    if not context.strip():
        return []
    return [p.strip() for p in _CTX_SPLIT_RE.split(context) if p.strip()]


async def _generate_answer(question: str, context: str) -> str:
    """以 build_user_prompt + stream_completion 生成答案（跳過 SEARCH_EVENT 控制標記）。"""
    prompt = build_user_prompt(question, context)
    parts: list[str] = []
    async for chunk in stream_completion(prompt, system=SYSTEM_PROMPT, model=DEFAULT_MODEL):
        if chunk == SEARCH_EVENT:
            continue
        parts.append(chunk)
    return "".join(parts)


async def eval_question(q: dict, *, judge, embed, retrieval_params: dict) -> dict:
    """單題：retrieve→generate→三指標。任一階段異常 → {..., "error": str}（fail-open）。"""
    base = {"id": q.get("id"), "question": q.get("question")}
    try:
        sources, context = await retrieve_context(
            q["question"], filters=q.get("filters") or {}, **retrieval_params
        )
        contexts = split_contexts(context)
        answer = await _generate_answer(q["question"], context)
        f = await faithfulness(answer, contexts, judge=judge)
        cp = await context_precision(q["question"], answer, contexts, judge=judge)
        ar = await answer_relevancy(q["question"], answer, judge=judge, embed=embed)
        return {
            **base,
            "faithfulness": f,
            "context_precision": cp,
            "answer_relevancy": ar,
            "n_contexts": len(contexts),
        }
    except Exception as e:  # noqa: BLE001 — 離線批次逐題 fail-open，不讓單題炸掉整批
        return {**base, "error": f"{type(e).__name__}: {e}"}


def _mean_of(per_q: list[dict], key: str) -> float | None:
    vals = [c[key] for c in per_q if "error" not in c and c.get(key) is not None]
    return sum(vals) / len(vals) if vals else None


def aggregate(per_q: list[dict]) -> dict:
    """各指標均值（略過 error 與 None）、計數（n/n_errors/n_no_context）、門檻旗標。純函式。"""
    f = _mean_of(per_q, "faithfulness")
    cp = _mean_of(per_q, "context_precision")
    ar = _mean_of(per_q, "answer_relevancy")
    n_errors = sum(1 for c in per_q if "error" in c)
    n_no_context = sum(
        1 for c in per_q if "error" not in c and c.get("faithfulness") is None
    )
    thresholds_pass = (
        f is not None
        and cp is not None
        and ar is not None
        and f > FAITHFULNESS_MIN
        and cp > CONTEXT_PRECISION_MIN
        and ar > ANSWER_RELEVANCY_MIN
    )
    return {
        "faithfulness": f,
        "context_precision": cp,
        "answer_relevancy": ar,
        "n": len(per_q),
        "n_errors": n_errors,
        "n_no_context": n_no_context,
        "thresholds_pass": thresholds_pass,
    }


async def run(
    dataset_path,
    *,
    out_path,
    judge_model: str = DEFAULT_JUDGE_MODEL,
    limit: int | None = None,
    concurrency: int = 3,
) -> dict:
    """讀題集 → 有界併發 eval_question → aggregate → 寫報表（{summary, cases}）。"""
    dataset = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    questions = dataset.get("questions", [])
    if limit is not None:
        questions = questions[:limit]

    async def _judge(system: str, user: str):
        return await judge_json(user, system=system, model=judge_model)

    sem = asyncio.Semaphore(concurrency)

    async def _one(q: dict) -> dict:
        async with sem:  # 限制同時 spawn 的 claude CLI 數，避開 IO 風暴
            return await eval_question(
                q, judge=_judge, embed=embed_query_cached, retrieval_params=RETRIEVAL_PARAMS
            )

    cases = await asyncio.gather(*[_one(q) for q in questions])
    summary = aggregate(list(cases))
    report = {"summary": summary, "cases": list(cases)}
    if out_path is not None:
        out = Path(out_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return report


def _print_summary(report: dict) -> None:
    s = report["summary"]

    def fmt(v):
        return f"{v:.3f}" if isinstance(v, float) else "n/a"

    print("=== M1 RAGAS 基準線 ===")
    print(f"Faithfulness      : {fmt(s['faithfulness'])}  (門檻 > {FAITHFULNESS_MIN})")
    print(f"Context Precision : {fmt(s['context_precision'])}  (門檻 > {CONTEXT_PRECISION_MIN})")
    print(f"Answer Relevancy  : {fmt(s['answer_relevancy'])}  (門檻 > {ANSWER_RELEVANCY_MIN})")
    print(f"n={s['n']}  errors={s['n_errors']}  no_context={s['n_no_context']}")
    print(f"thresholds_pass   : {s['thresholds_pass']}")


def _main() -> None:
    parser = argparse.ArgumentParser(description="跑 M1 RAGAS reference-free 評測")
    parser.add_argument("--dataset", default="eval/ragas_questions.json")
    parser.add_argument("--out", default="eval/baselines/baseline-m0.json")
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="改輸出完整 JSON 到 stdout")
    args = parser.parse_args()

    report = asyncio.run(
        run(
            args.dataset,
            out_path=args.out,
            judge_model=args.judge_model,
            limit=args.limit,
            concurrency=args.concurrency,
        )
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_summary(report)
        print(f"\nwrote report -> {args.out}")


if __name__ == "__main__":
    _main()
```

- [ ] **Step 4: 跑純函式測試確認通過**

Run: `uv run pytest tests/test_run_ragas.py -v`
Expected: PASS（SplitContextsTests 2 + AggregateTests 3 = 5 passed）

- [ ] **Step 5: 補 `eval_question` 全 mock 冒煙測與 fail-open 測**

在 `tests/test_run_ragas.py` 的 `AggregateTests` 類別後（`if __name__` 之前）加入：

```python
import eval.run_ragas as rr  # noqa: E402


class _FakeSource:
    pass


def _install_fakes(monkeypatch_targets):
    """把 retrieve_context / stream_completion 換成假物；回原值供還原。"""
    saved = {name: getattr(rr, name) for name in monkeypatch_targets}
    return saved


def _restore(saved):
    for name, val in saved.items():
        setattr(rr, name, val)


class EvalQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_mocked_smoke(self):
        async def fake_retrieve(question, *, filters=None, **params):
            ctx = "[1] 報告：a.pdf（市場 TW，日期 2026-01-01）\n某段落內容"
            return [_FakeSource()], ctx

        async def fake_stream(prompt, *, system=None, model=None, timeout=120.0,
                              allow_web=False, retries=2):
            for c in ["台積電", "展望", "正向 [1]"]:
                yield c

        async def fake_judge(system, user):
            if "拆解" in system:
                return {"statements": ["台積電展望正向"]}
            if "佐證" in system:
                return {"verdicts": [{"idx": 0, "supported": True}]}
            if "精準度" in system:
                return {"verdicts": [{"idx": 0, "relevant": True}]}
            if "反推" in system:
                return {"questions": ["台積電展望如何"]}
            raise AssertionError(system[:30])

        def fake_embed(text):
            return [1.0, 0.0, 0.0]

        saved = _install_fakes(["retrieve_context", "stream_completion"])
        try:
            rr.retrieve_context = fake_retrieve
            rr.stream_completion = fake_stream
            out = await rr.eval_question(
                {"id": "q001", "question": "台積電展望"},
                judge=fake_judge, embed=fake_embed, retrieval_params={},
            )
        finally:
            _restore(saved)

        self.assertNotIn("error", out)
        self.assertEqual(out["id"], "q001")
        self.assertAlmostEqual(out["faithfulness"], 1.0)
        self.assertAlmostEqual(out["context_precision"], 1.0)
        self.assertAlmostEqual(out["answer_relevancy"], 1.0)
        self.assertEqual(out["n_contexts"], 1)

    async def test_retrieve_failure_is_fail_open(self):
        async def boom_retrieve(question, *, filters=None, **params):
            raise RuntimeError("db down")

        saved = _install_fakes(["retrieve_context"])
        try:
            rr.retrieve_context = boom_retrieve
            out = await rr.eval_question(
                {"id": "q001", "question": "x"},
                judge=None, embed=None, retrieval_params={},
            )
        finally:
            _restore(saved)

        self.assertIn("error", out)
        self.assertIn("db down", out["error"])
        self.assertEqual(out["id"], "q001")
```

- [ ] **Step 6: 跑全套 run_ragas 測試確認通過**

Run: `uv run pytest tests/test_run_ragas.py -v`
Expected: PASS（5 + EvalQuestionTests 2 = 7 passed）

- [ ] **Step 7: 全套回歸（確認零 web 影響、既有測試不動）**

Run: `uv run pytest -q`
Expected: PASS（既有 392 + M1 新增 38 ≈ 430 passed；無既有測試變動或失敗）

- [ ] **Step 8: Commit**

```bash
git add eval/run_ragas.py tests/test_run_ragas.py
git commit -m "$(cat <<'EOF'
feat(eval): 加 run_ragas 編排（M1）

讀題集 → 有界併發（Semaphore，避 CLI IO 風暴）逐題 retrieve→generate→三指標 →
aggregate → 寫 baselines/*.json。生成用 build_user_prompt+stream_completion（隔離
生成、避 qa_log 寫入）；split_contexts/aggregate 為純函式可單測；逐題 fail-open。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 產生並提交基準線 `eval/baselines/baseline-m0.json`（操作性，需 live infra）

> **注意：本 task 非 TDD 單元任務**——它要真跑 DB + claude CLI + BGE-M3 產出基準線，屬操作性步驟。若 subagent 環境無法起 DB / 載模型，改由編排者（主 session）或使用者手動執行本節命令，再 commit 產物。單元正確性已由 Task 1–4 保證。

**Files:**
- Create: `eval/ragas_questions.json`（題集，人工過目後 commit）
- Create: `eval/baselines/baseline-m0.json`（基準線報表）

**前置檢查：**
- [ ] **Step 1: 確認 DB 可連、claude CLI 在 PATH**

Run: `uv run python -c "import asyncio; from app.services.db import SessionFactory; from sqlalchemy import text; asyncio.run((lambda: None)())"` 並 `which claude`
Expected: 無錯誤；`claude` 路徑印出。（DB 未起時先 `make up-db`。）

- [ ] **Step 2: 產生題集**

Run: `uv run python eval/dataset.py --out eval/ragas_questions.json --target 30 --per-market-cap 8`
Expected: 印出 `wrote N questions -> eval/ragas_questions.json`（N 視 qa_log 存量，可能 < 30）。

- [ ] **Step 3: 人工過目題集**

Run: `uv run python -c "import json; d=json.load(open('eval/ragas_questions.json')); print(d['count']); [print(q['id'], q['question'][:40], q['filters']) for q in d['questions']]"`
Expected: 題目為合理投資/市場提問、無明顯離題殘留、市場有多樣性。若有壞題手動刪除該 question 物件並修正 `count`。

- [ ] **Step 4: 小樣本快測（校準 judge 可信度）**

Run: `uv run python eval/run_ragas.py --dataset eval/ragas_questions.json --out /tmp/ragas-smoke.json --limit 3 --concurrency 2`
Expected: 印出三指標摘要；`errors=0` 或可解釋。人工抽看 `/tmp/ragas-smoke.json` 的 `cases[*]` 判定是否合理（faithfulness 對「找不到資料」類應為 null）。

- [ ] **Step 5: 跑全量基準線**

Run: `uv run python eval/run_ragas.py --dataset eval/ragas_questions.json --out eval/baselines/baseline-m0.json --judge-model claude-haiku-4-5 --concurrency 3`
Expected: 印出摘要（三指標 + n/errors/no_context + thresholds_pass），寫出 `eval/baselines/baseline-m0.json`。（~N×4 judge call，數分鐘；離線批次不影響線上。）

- [ ] **Step 6: Commit 題集與基準線**

```bash
git add eval/ragas_questions.json eval/baselines/baseline-m0.json
git commit -m "$(cat <<'EOF'
chore(eval): 提交 M1 題集與 M0 基準線

ragas_questions.json（挖 qa_log、人工過目）＋ baseline-m0.json（Faithfulness/
Context Precision/Answer Relevancy 首跑值）＝M2 rerank 前的回歸基準線。

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage：**
- 指標三項（Faithfulness/Context Precision/Answer Relevancy）→ Task 2。✅
- 題集挖 qa_log、去重/濾/市場抽樣、版本化 JSON → Task 3。✅
- 自實作 RAGAS、不引入 ragas/langchain → Task 2（純 numpy）。✅
- judge_json drain + robust 解析 + JudgeError + Haiku 預設 → Task 1。✅
- 三指標 judge/embed 注入、prompt 就地共置、批次（faithfulness 2 call / precision 1 / relevancy 1）→ Task 2。✅
- run_ragas eval_question/run/aggregate、檢索參數取自 answer.py 常數、build_user_prompt+stream_completion 生成、有界併發、逐題 fail-open、CLI → Task 4。✅
- eval/baselines/ 首跑 baseline-m0.json、門檻 0.9/0.8/0.85 → Task 4（常數）+ Task 5（產物）。✅
- 測試策略（fake judge/embed 指標決定性、dataset 濾/去重、judge robust、run_ragas 全 mock 冒煙 + fail-open）→ Task 1–4 各測檔。✅
- 零 web 影響、無 schema、無新增依賴 → Global Constraints + Task 4 Step 7 回歸。✅
- 生成端隔離（非 answer_question，避 qa_log 寫入/overview/off-topic）→ Task 4 `_generate_answer`。✅
- 資料流 split_contexts → Task 4。✅

**2. Placeholder scan：** 無 TBD/TODO；每個 code step 皆含完整程式碼；每個 test step 皆含實際測試碼與預期輸出。Task 5 明標「非 TDD、操作性、需 live infra」並附具體命令，非佔位。✅

**3. Type consistency：**
- judge 契約 `async judge(system, user) -> dict|list` 於 Task 2 Interfaces 定義，Task 4 的 `_judge` adapter 與測試 fake 一致。✅
- embed 契約 `embed(text) -> list[float]` 一致（Task 2 定義、Task 4 傳 `embed_query_cached`）。✅
- `retrieve_context(question, *, filters=None, **params)` 呼叫與 M0 實際簽名一致（k/dense_scan/max_reports/max_passages/max_chars）。✅
- `faithfulness -> float | None`、`aggregate` 對 None 與 error 分別計數，前後一致。✅
- `select_questions` 回 `[{"id","question","filters"}]`、`eval_question` 讀 `q["question"]`/`q.get("filters")` 一致。✅

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-07-08-m1-eval-baseline.md`. Two execution options:

**1. Subagent-Driven (recommended)** — 每 task 派新 subagent、task 間審查、快速迭代（Task 1–4 為 TDD 單元；Task 5 為操作性、由編排者或使用者跑 live infra 後 commit）。

**2. Inline Execution** — 於本 session 以 executing-plans 批次執行、含 checkpoint 審查。
