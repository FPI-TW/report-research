# M4 範圍路由（五類 scope_router＋工具政策）設計規格

日期：2026-07-13。藍圖：`docs/QA_REDESIGN.md` §3-A、backlog：`docs/IMPLEMENTATION_PLAN.md` M4。
分支：`feat/m4-scope-router`，自 `merge/m0-m3-into-main` tip（cc80d7b，= PR #64 併入後的 main 內容）切；#64 合併後即等同疊於 main。

## 目標

把 `intent.py` 的「離題就擋」布林閘升級為**五類問題路由＋工具政策**。金融知識題仍採 fail-open，惟不得用 fail-open 把過期研報當成即時資料，或把研究助手變成個人化交易指令工具。

| scope | 使用情境 | tool_policy | M4 行為 |
|---|---|---|---|
| `off_topic` | 寫作、翻譯、生活閒聊、非金融一般知識 | `no_answer` | 固定婉拒文案，不檢索、不呼叫主 LLM |
| `overview` | 語料庫枚舉／聚合 | `corpus_only` | 既有 `overview.py` 純 SQL 快速路徑 |
| `corpus_qa` | 歷史研報觀點、公司／產業分析、比較、風險 | `corpus_only` | 既有 RAG；金融事實主張必須檢索 |
| `time_sensitive` | 即時／最新報價、財報、公告、利率、政策 | `trusted_external_required` | 無可信即時資料供應者時安全說明無法驗證，不以研報 RAG 代答 |
| `advice_risk` | 個人化買賣、倉位、風險承受度、交易指令 | `research_only` | 僅提供可追溯研究資訊、風險與不同觀點，不給個人化買賣／部位指令 |

1. 首輪與續問共用同一判準；`overview` 則繼續由確定性規則優先決定。
2. 時效題的答案一律需要可信外部資料、來源性質與「截至時間」；這些資料在 M5 接入，M4 先建立安全退化行為與路由契約。
3. 使用者已拍板：完全離題採固定、客氣的婉拒文案，不做動態生成。

## 非目標（YAGNI）

- 不在 M4 實作可信行情／公告供應者、網域白名單或外部來源持久化；這些屬 M5 的 agentic／evidence 工作。M4 只定義其工具政策與無供應者時的安全退化。
- 不做多輪 agentic 檢索、查詢分解、rerank 政策或跨來源證據帳本；沿用既有單輪 RAG。
- 不做 `qa_log` schema 變更或 DB 回填。
- 不改既有 SSE 事件名稱或移除欄位；後續來源資料只可對 `ext_sources` 作加法擴充。

## 設計

### 1. `app/services/scope_router.py`（git mv 自 `intent.py`）

#### 路由結果

```python
Scope = Literal[
    "off_topic", "overview", "corpus_qa", "time_sensitive", "advice_risk"
]
ToolPolicy = Literal[
    "no_answer", "corpus_only", "trusted_external_required", "research_only"
]

@dataclass(frozen=True)
class RouteDecision:
    scope: Scope
    tool_policy: ToolPolicy
    overview_filters: OverviewFilters | None = None
```

常數：`OFF_TOPIC`、`OVERVIEW`、`CORPUS_QA`、`TIME_SENSITIVE`、`ADVICE_RISK`，以及對應的工具政策常數。`RouteDecision` 是 `answer.py` 與未來 `agentic_qa.py` 的唯一契約；不可讓下游重新以字串關鍵字推斷工具權限。

#### 路由順序

`route_question(question, *, today, model, timeout) -> RouteDecision` 是完整路由的唯一語意入口；`today` 明確注入以保持測試可決定性。它內部使用公開的純 helper `resolve_overview_route(question, today) -> RouteDecision | None`，供 `answer.py` 在首輪先行判定 overview、避免不必要的向量檢索；helper 與 `route_question` 不得各自維護規則。

1. **overview 優先**：若 `detect_overview(question)` 且 `resolve_filters(question, today).any()`，回 `overview/corpus_only`，並帶回 `overview_filters`；零 LLM、零向量。
2. **保守安全前檢**：先偵測明確時效詞（如「現在／目前／即時／最新／今天／收盤價／報價」）與直接個人化指令詞（如「我該不該買／幫我配倉／買多少／停損」）。若兩者同時命中，`advice_risk` 優先；否則回 `time_sensitive` 或 `advice_risk`，避免分類器失敗時退化成以舊研報作答。
3. **LLM 分類**：其餘問題以 Haiku 輸出 `OFF_TOPIC | CORPUS_QA | TIME_SENSITIVE | ADVICE_RISK` 其中之一；`overview` 不交給 LLM 判斷。
4. **錯誤處理**：LLM 逾時、空回應或解析失敗時，若安全前檢已命中則保留該安全 scope；否則 fail-open 回 `corpus_qa/corpus_only`。

`condense_and_route(history_text, question, *, today, model, timeout) -> tuple[str, RouteDecision]` 只做一次 Haiku 呼叫：先把追問改寫為獨立查詢，再輸出四類非 overview scope。解析後必須以改寫後問題重新執行 overview 判定與保守安全前檢；失敗時回 `(原 question, fallback decision)`，fallback 同上。

#### LLM 判準與解析

- `ROUTE_CRITERIA` 取代 `INTENT_CRITERIA`，明確區分歷史研報分析與即時資料；「台積電展望」是 `CORPUS_QA`，「台積電現在股價／今日收盤價」是 `TIME_SENSITIVE`。
- `ROUTE_SYSTEM_PROMPT` 只允許輸出四個 token 之一。`parse_route` 只接受完整 token；模糊文字一律視為解析失敗，交由上述安全 fallback，不能寬鬆猜測成 `off_topic`。
- `CONDENSE_SYSTEM_PROMPT` 保持同一份 `ROUTE_CRITERIA`，格式改為：

  ```text
  QUERY: <改寫後可獨立檢索的完整問題>
  ROUTE: OFF_TOPIC | CORPUS_QA | TIME_SENSITIVE | ADVICE_RISK
  ```

- 來源片段、對話歷史與網頁內容都是資料而非指令；分類／改寫 prompt 必須明說忽略其中要求改變路由或工具政策的文字。

### 2. `answer.py` 消費端

把 `in_domain: bool | None` 改為 `decision: RouteDecision | None`，但保留低延遲模式：首輪先同步執行 `resolve_overview_route`；若命中即直接走 overview，絕不啟動向量檢索。未命中時，才並行執行 `classify_non_overview`（`route_question` 的同一分類邏輯）與既有 RAG 取證；路由結果不是 `corpus_qa` 時，丟棄已完成的 RAG 結果，不把它送進主 LLM。續問用 `condense_and_route` 後再依決策走對應路徑。

| decision.scope | 消費行為 |
|---|---|
| `off_topic` | 維持 `sources=[] → notice → done`，不檢索、不呼叫主 LLM。 |
| `overview` | 直接呼叫 `_answer_overview`，使用 `decision.overview_filters`，不重算 overview 條件。 |
| `corpus_qa` | 維持既有單輪 RAG；M4 明確以 `allow_web=False` 呼叫主回答，避免在沒有來源政策時混入未受控網搜。M5 才依 `tool_policy` 重開外部工具。 |
| `time_sensitive` | 若尚未設定可信即時資料供應者，回固定 `TIME_SENSITIVE_UNAVAILABLE_MESSAGE`：說明目前無法驗證最新資料、不可據此當成即時行情，並邀請使用者改問歷史研報觀點；不檢索、不呼叫主 LLM。接入供應者後才允許 `trusted_external_required` 路徑，答案必須含來源與截至時間。 |
| `advice_risk` | 仍可走 corpus RAG，但主回答追加固定 `RESEARCH_ONLY_POLICY`：只能整理來源支持的正反論點與風險，禁止給使用者個人化買賣、部位、槓桿、停損或保證報酬指令。 |

`time_sensitive` 與 `advice_risk` 也要寫入 `filters.path`（分別為 `time_sensitive`、`advice_risk`），供稽核、歷史呈現與 eval 題集排除／分組。這是既有 jsonb 欄位的值擴充，不需 schema 變更。

### 3. SSE、來源與相容性

- 事件名稱與既有 payload 維持相容；新路徑使用既有 `notice`／`done` 表示無可信即時資料或研究限制。
- M4 不產生外部資料。M5 如啟用 `trusted_external_required`，`ext_sources` 必須以**加法欄位**帶 `origin`、`published_at`、`retrieved_at`、`content_hash`，且答案顯示截至時間；不得混入研報 `[n]`。
- 首輪並行取證若因路由被丟棄，不得發出 `sources` 事件，也不得持久化該取證結果。

### 4. 婉拒文案與舊資料相容

- `OFF_TOPIC_MESSAGE` 改為客氣版固定文案（實作逐字使用）：

  > 這裡是廷豐研報的投資研究問答，這個問題超出我能引據回答的範圍。歡迎改問特定市場、個股、期貨、匯率或總經主題，我會依研報內容為你解讀。

- 舊 `qa_log` 列存舊文案。新增 `OFF_TOPIC_MESSAGES: tuple[str, ...]`（新＋舊），所有歷史消費端都應辨識兩者。
- 既有 `IS DISTINCT FROM :offtopic` 不可直接替換成 `answer != ALL(:offtopics)`，後者會改變 `NULL` 語意。改用 null-safe 條件，例如 `COALESCE(answer <> ALL(CAST(:offtopics AS text[])), true)`，套用於：
  1. `answer.py::_conversation_item`：`answer in OFF_TOPIC_MESSAGES`。
  2. `answer.py::load_recent_turns`。
  3. `answer.py::list_conversations` 的 title／turn_count FILTER。
  4. `web/server.py` 的 `/api/history` SQL。
  5. `eval/dataset.py`：排除 `(*OFF_TOPIC_MESSAGES, NO_CONTEXT_MESSAGE)`。

### 5. 測試

- `tests/test_intent.py` 改名為 `tests/test_scope_router.py`：覆蓋四類 LLM token、嚴格 parser、LLM 失敗／空回應 fallback、首輪與續問共用 `ROUTE_CRITERIA`。
- overview precedence：符合 overview 條件時不得呼叫 LLM；`overview_filters` 可直接交給 `_answer_overview`。
- 安全前檢：即時報價／最新公告在 LLM 失敗時仍為 `time_sensitive`；「買多少／我該不該買」仍為 `advice_risk`；一般「台積電展望」退化為 `corpus_qa`。
- `tests/test_answer.py`：
  - `off_topic` 不檢索、不呼叫主 LLM；
  - `time_sensitive` 在無供應者時不檢索、不開網搜、回固定 notice；
  - `advice_risk` 的主 LLM prompt 含 `RESEARCH_ONLY_POLICY`；
  - 首輪被路由為非 `corpus_qa` 時不發 `sources`，且不把並行取證送入主回答；
  - `corpus_qa` 仍走既有 RAG，M4 期間 `allow_web=False`。
- 舊 sentinel 相容：新／舊婉拒文案都在 `_conversation_item` 標 `is_offtopic`，SQL 對新舊文案皆排除，`answer=NULL` 仍維持既有查詢語意。

### 6. Eval 與驗收

- 不以全量 RAGAS 分數作為 M4 是否正確的證據：路由變更會改變時效題與建議題的輸出路徑。
- 固定問答題集先標記 scope，M4 後驗證 `corpus_qa` 子集的檢索／作答分數不退步；`time_sensitive`、`advice_risk`、`off_topic` 與 `overview` 則以路由、無網搜與文案／來源契約測試驗收。
- 記錄各 scope 的題數與錯誤案例，供 M5 接入可信外部資料後比較。
- Commit 主體：`refactor(問答): 離題閘升級為問題類型與工具政策路由`。

## 決策紀要

- **時效優先於一般金融 fail-open**：金融問題誤擋可退到 corpus RAG；即時報價誤放行卻以舊研報回答，風險更高。因此明確時效詞命中時採保守路由。
- **M4 不以任意 WebSearch 補時效資料**：在可信來源、時間戳與持久化證據未建妥前，任意網搜無法達成「最新」與可稽核要求；先安全告知不可驗證，M5 再開放。
- **`advice_risk` 不拒絕研究需求**：使用者仍可得到來源支持的正反觀點與風險，只是不能把系統輸出當成個人化交易指令。
- **overview 是確定性優先路徑**：它不是 LLM 的一個猜測標籤，而是有 filters 的資料庫聚合工作，因此由 `route_question` 先判定並攜帶解析結果。
