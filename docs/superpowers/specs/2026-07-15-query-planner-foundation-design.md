# query_planner 共用核心（M5/M6 地基 micro-PR）設計

日期：2026-07-15
狀態：定案
範圍：`app/services/query_planner.py`（新）、`app/config.py`（預留區）、對應測試
關聯：`docs/IMPLEMENTATION_PLAN.md` M5（輕量版）/ M6（深度版）明文「共用 query_planner」

## 動機

M5（問答 agentic 迴圈）與 M6（研報多查詢分解）計畫平行開發，但兩者都要「建立」
`app/services/query_planner.py` 並向 `app/config.py` 尾端加鍵——四個必然的 add/add
衝突。本 micro-PR 先落共用核心與設定預留區，把硬衝突轉成「各自擴充、不重寫」：

- 共用核心＝資料型別 + robust JSON 解析 + 正規化/去重/上限 + fail-open 包裝。
- 兩個 profile（`qa`／`report`）各佔檔內一個標記區段，prompt 由對應里程碑填入。
- config 以註解標記 M5/M6 專屬區段，之後各里程碑只在自己區段內加鍵。

## 介面契約（平行期間凍結）

```python
@dataclass(frozen=True)
class SubQuery:
    text: str
    fresh: bool = False   # M5：子查詢是否需要即時資料（走 M4a trusted adapter）
    facet: str = ""       # M6：面向標籤（子題覆蓋率量測用）

@dataclass(frozen=True)
class QueryPlan:
    subqueries: tuple[SubQuery, ...]  # 恆非空
    profile: str
    degraded: bool                    # True＝fail-open 產物（單一原始問題）

async def plan_queries(question, *, profile, max_subqueries=None,
                       model=None, timeout=None) -> QueryPlan
```

- `plan_queries` **永不 raise**：未知 profile、prompt 未實作、LLM 例外/逾時、
  解析失敗、正規化後為空 → 一律回 `QueryPlan((SubQuery(question),), degraded=True)`。
- LLM 輸出格式為物件 `{"subqueries": [{"q": ..., "fresh": ..., "facet": ...}, ...]}`；
  解析容忍圍欄/散文，**物件括號優先**（沿用 eval/judge `_loads_robust` 的 `[n]`
  小陣列汙染教訓）；項目容忍純字串。
- 正規化（純函式 `normalize_subqueries`，可獨立測試）：空白折疊、去空項、
  `SUBQUERY_MAX_LEN=200` 截斷、`norm_for_match` 去重、上限裁切（含原始問題），
  `include_original=True` 時保證原始問題在首位。

## Profile 區段所有權

| 區段 | 擁有者 | Step 0 狀態 |
|---|---|---|
| `_QA_PROFILE`（輕量版，1–N 子查詢+freshness） | M5 | `build_prompt=None` → 呼叫即 fail-open |
| `_REPORT_PROFILE`（深度版，≤8 面向） | M6 | 同上 |

M5/M6 只改自己的區段（填 prompt builder、調整 profile 參數），不動共用核心與
對方區段；共用核心若需變更，先合回 main 再雙邊 rebase。

## config 預留區與新鍵

`Settings`/`_load()`/`tests/test_config.py` 各加兩個標記區段：

- `# agentic_qa / query_planner（M5）`：`qa_planner_model`（QA_PLANNER_MODEL，
  預設 claude-haiku-4-5）、`qa_planner_timeout`（20）、`qa_planner_max_subqueries`（3）、
  `qa_max_rounds`（QA_MAX_ROUNDS，2——IMPLEMENTATION_PLAN M5 明文預設）。
- `# report 檢索增強 / query_planner（M6）`：`report_planner_model`（claude-haiku-4-5）、
  `report_planner_timeout`（30）、`report_planner_max_subqueries`（8——M6 明文上限）。

MMR/併發等 M6 內部旋鈕不在本 PR 預加：名稱屬 M6 spec 決策，屆時加進 M6 自己的
標記區段即可，不會與 M5 相撞。

## 循環 import 約束

`retrieval_pipeline.py` 頂層 import `answer`（Source/build_context），故 answer.py
反向只能函式內 import retrieve_context。`query_planner` 只 import 標準庫、
`app.config`、`app.services.llm`、`app.services.textnorm`（皆為葉模組），
**禁止 import answer / retrieval_pipeline / report**，維持可被任一側頂層 import。

## 測試

- `tests/test_query_planner.py`（新）：正規化純函式（去重/截斷/上限/首位保證/
  字串與物件項目混用）、robust 解析（圍欄/散文/物件優先於前導小陣列）、
  `plan_queries` fail-open 矩陣（未知 profile、builder 未實作、LLM raise、
  垃圾輸出、空清單）、happy path（stub `query_planner.stream_completion`，
  patch-where-used）。
- `tests/test_config.py`：新增兩個測試方法斷言上述預設真值（沿用逐鍵斷言慣例）。
