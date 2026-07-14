# M1 Eval 基準線 — 設計 spec

> 里程碑 M1（`docs/IMPLEMENTATION_PLAN.md`）。藍圖：`docs/REPORT_GEN_REDESIGN.md` §3 Phase 5。依賴 M0（`retrieval_pipeline.retrieve_context` 已就緒）。分支 `feat/m1-eval-baseline`（疊於 M0）。

## Goal

建立**離線、reference-free** 的 RAG 品質評測地基：對一組固定題集跑「檢索→生成→評分」，產出可重現的**基準線**，讓 M2（rerank）起每個里程碑都能用同一組指標**量增益、防回歸**。無黃金答案，評審 LLM 走現有 `claude` CLI。

## 決策（已與使用者確認）

1. **指標三項**（真 reference-free、生成側，且是 M2 rerank 會移動的）：**Faithfulness**（幻覺紅線）、**Context Precision**（rerank 直接拉高）、**Answer Relevancy**（embedding）。「檢索召回」維度沿用既有 `scripts/eval_retrieval.py` 的關鍵字 hit-rate，**M1 不造可疑的 reference-free recall**。
2. **題集**：`eval/dataset.py` 從 `research.qa_log` 挖真實問題 → 去重、濾除 off-topic/no-context、市場多樣抽樣 → 寫**版本化** `eval/ragas_questions.json`（commit）。`run_ragas.py` 讀該檔。
3. **自實作 RAGAS 演算法**，不引入 `ragas`/`langchain`（現無此依賴，且與 CLI-LLM + 確定性 Python 分層衝突）。評審＝`llm.stream_completion` drain；embedding＝`embed.embed_query_cached`（BGE-M3 1024-dim）。

## 非目標（Out of Scope）

- 不改 `scripts/eval_retrieval.py`/`analyze_qa_log.py`（互補、不動）。
- 不接 reranker（M2）、不改任何 web 請求路徑或 `app/services/*` 生成邏輯。
- 不做 Context Recall（無黃金答案本質失真）；召回由既有關鍵字 harness 涵蓋。
- eval 旋鈕**不進** M0 的 web `Settings`（離線專屬，走 CLI 參數/模組常數）。

## 資料流（每題）

```
eval/ragas_questions.json（挖 qa_log、版本化）
  └ 每題 q:
     retrieve_context(q, k, dense_scan, max_reports, max_passages, max_chars, filters)  # M0 helper
        → (sources, context)   # context 為 [n] 編號片段字串
     split_contexts(context) → list[str]（以 [n] 表頭切每篇報告一塊）
     build_user_prompt(q, context) + drain stream_completion(system=SYSTEM_PROMPT) → answer
     faithfulness(answer, contexts, judge)          # 拆 statements + 逐條 grounding
     context_precision(q, answer, contexts, judge)  # 逐 context 相關性 + rank-weighted AP
     answer_relevancy(q, answer, judge, embed)      # 生成問題 embed vs 原問題 cosine
  → aggregate（各指標均值 + 逐題明細）→ eval/baselines/<label>.json
```

生成端**刻意用 `build_user_prompt`+`stream_completion` 而非 `answer_question`**：隔離檢索與生成，judge 輸入恰為 `(q, context, answer)`，避開 qa_log 寫入/overview/off-topic 分支。

## 模組單元

### `eval/dataset.py` — 題集生成器（DB → 版本化 JSON）

**介面**
```python
def select_questions(rows, *, per_market_cap, target) -> list[dict]:
    """純函式：去重(正規化問句)、濾 off-topic/no-context/過短、市場多樣抽樣。回 [{id, question, filters}]。"""
async def build_dataset(*, target, per_market_cap) -> dict:
    """讀 research.qa_log → select_questions → {version, generated_at, count, questions}。"""
```
- 讀 `research.qa_log`（`question, answer, filters, market(自 filters), created_at`）；`SessionFactory`（`app.services.db`）。
- 濾除：`answer == OFF_TOPIC_MESSAGE`、`answer == NO_CONTEXT_MESSAGE`、空/過短問句、重複（`textnorm` 正規化後比對）。
- 抽樣：依 `filters.market` 分組、每市場上限 `per_market_cap`、湊到 `target`（預設 ~30）。無 market 者歸一般組。
- CLI：`uv run python eval/dataset.py --out eval/ragas_questions.json --target 30`。
- **生成器語義**：偶爾 refresh；產出的 JSON 經人工過目後 commit＝穩定 baseline 輸入。`generated_at` 由 CLI 以現在時間戳入（不在純函式內取時間）。

**版本化 JSON schema**
```json
{"version": 1, "generated_at": "2026-07-08T...", "count": 30,
 "questions": [{"id": "q001", "question": "...", "filters": {"market": "TW"}}]}
```

### `eval/judge.py` — 評審 LLM 原語

**介面**
```python
async def judge_json(prompt, *, system, model=DEFAULT_JUDGE_MODEL, timeout=60.0) -> dict | list:
    """drain stream_completion(prompt, system=system, model=model, allow_web=False) →
       robust 解析 JSON（去 ```json 圍欄、取第一個 {..}/[..]、json.loads）。解析失敗 raise JudgeError。"""
```
- 沿用 drain 慣例（`intent.py:71-80`）：`parts=[]; async for c in stream_completion(...): parts.append(c); text="".join(parts)`。
- `DEFAULT_JUDGE_MODEL = os.getenv("EVAL_JUDGE_MODEL", "claude-haiku-4-5")`。
- `allow_web=False`（judge 不上網）。`class JudgeError(Exception)`。

### `eval/ragas_metrics.py` — 三指標純函式（judge/embed 注入）

**介面**（judge/embed 為可注入 callable → 單測用 fake、零真 LLM）
```python
async def faithfulness(answer, contexts, *, judge) -> float | None:
    # 1) judge(DECOMPOSE_SYS, answer) -> {"statements":[...]}
    # 2) judge(GROUND_SYS, contexts+statements) -> {"verdicts":[{"idx":i,"supported":bool}]}（一次批次）
    # 分數 = supported / total；total==0（答案無事實主張，如「找不到資料」）→ None（自均值排除，不以空洞值灌水）
async def context_precision(question, answer, contexts, *, judge) -> float:
    # judge(CTX_RELEVANCE_SYS, question+answer+enumerated contexts) -> {"verdicts":[{"idx":i,"relevant":bool}]}
    # RAGAS rank-weighted AP：sum_k(precision@k * rel_k)/total_relevant；total_relevant==0 → 0.0
async def answer_relevancy(question, answer, *, judge, embed) -> float:
    # 1) judge(GENQ_SYS, answer) -> {"questions":[q1,q2,q3]}
    # 2) cosine(embed(orig_q), embed(gen_qi)) 平均；embed 回 1024-vec；numpy cosine
```
- **Prompt 常數就地共置**（`DECOMPOSE_SYS`/`GROUND_SYS`/`CTX_RELEVANCE_SYS`/`GENQ_SYS`），feature 內 prompt+parser 同檔（呼應藍圖風險表「prompt/parser 漂移」防護）。
- 每指標的 judge 呼叫盡量**批次**（faithfulness 2 call、precision 1 call、relevancy 1 call ≈ 每題 4 judge call）。
- `_cosine(a, b)` 小工具（numpy）。

### `eval/run_ragas.py` — 編排

**介面**
```python
async def eval_question(q, *, judge, embed, retrieval_params) -> dict:
    """retrieve→generate→三指標→{id, question, faithfulness, context_precision, answer_relevancy, n_contexts, error?}。"""
async def run(dataset_path, *, out_path, judge_model, limit, concurrency) -> dict:
    """讀題集→有界併發 eval_question→aggregate→寫報表。"""
def aggregate(per_q) -> dict:  # 純函式：各指標均值(略過 error 與 None)、計數(含 n_errors/n_no_context)、通過門檻旗標
```
- 檢索參數取自 `answer.py` 既有模組常數（`RETRIEVAL_K/ASK_DENSE_SCAN/MAX_REPORTS/MAX_PASSAGES_PER_REPORT/MAX_CONTEXT_CHARS`）＝與問答線上一致。
- 生成：`build_user_prompt(q, context)` + drain `stream_completion(system=SYSTEM_PROMPT, model=DEFAULT_MODEL)`。
- **有界併發**（`--concurrency` 預設 2–3，`asyncio.Semaphore`）：claude CLI spawn 吃 IO（見 `summary-gen-disk-io` 教訓）。
- **逐題 fail-open**：任一題 retrieve/generate/judge 異常 → 記 `error`、不計入均值、不中斷批次。
- CLI：`uv run python eval/run_ragas.py --dataset eval/ragas_questions.json --out eval/baselines/baseline-m0.json --judge-model claude-haiku-4-5 [--limit N] [--concurrency 3] [--json]`。
- 輸出 `{summary:{faithfulness, context_precision, answer_relevancy, n, n_errors, thresholds_pass}, cases:[...]}`；純文字表 + `--json`。

### `eval/baselines/` — 版本化基準線

首跑產 `baseline-m0.json`（commit）＝M2 前基準線。門檻（文件化、回歸 gate）：**Faithfulness>0.9、Context Precision>0.8、Answer Relevancy>0.85**。

## 測試策略

- **`ragas_metrics`（核心）**：fake judge/embed 決定性單測——faithfulness 全支持=1.0/半支持=0.5/零 statement=1.0；context_precision 已知 verdict 序列驗 rank-weighted AP 值、全不相關=0.0；answer_relevancy 用 fake embed 向量驗平均 cosine。**零真 LLM**。
- **`dataset.select_questions`**：fake qa_log 列驗去重/濾 off-topic/no-context/市場上限/target。純函式、無 DB。
- **`judge.judge_json`**：robust 解析單測（裸 JSON、```json 圍欄、前後雜訊、畸形→JudgeError）——以 fake stream_completion 注入。
- **`run_ragas`**：全 mock（retrieve_context/stream_completion/judge/embed）冒煙測，驗 `eval_question`/`aggregate` 報表結構與 fail-open（注入一題 error 驗略過不崩）。
- **真 LLM 端到端**＝手動/選用：首跑 `run_ragas` 產基準線時人工抽驗數條 judge 判定校準可信度（非 CI）。

## 部署與相容

- **零 web 影響、無 schema 變更、無需 restart**。`eval/` 不進 `web/server.py`。
- 需求：`claude` CLI on PATH（既有）；`dataset.py` 需 DB（挖 qa_log）；`run_ragas.py` 需 DB（檢索）+ 首次載 BGE-M3。
- 依賴：無新增（numpy 已可用；claude CLI、BGE-M3、SessionFactory 皆既有）。
- 市場代碼/findb 對齊：不受影響。

## 風險與緩解

| 風險 | 緩解 |
|---|---|
| judge 呼叫多、慢（~4×N call） | Haiku 預設、有界併發、`--limit` 抽樣快測；離線批次不影響線上 |
| judge JSON 不穩 | `judge_json` robust 解析 + JudgeError；逐題 fail-open |
| 指標可信度未知 | 首跑人工抽驗校準；門檻為相對 gate（比基準線）非絕對真理 |
| qa_log 稀疏/偏斜 | 市場多樣抽樣；不足時 target 下修；題檔人工過目後才 commit |
| CLI spawn IO 風暴 | 併發預設低（2–3）；沿用 `--setting-sources ''`（stream_completion 既有） |
