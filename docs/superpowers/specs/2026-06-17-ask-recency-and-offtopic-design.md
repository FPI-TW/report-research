# 設計：問答優化 — 引用偏好較新資料 + 限制離題問題

- 日期：2026-06-17
- 範圍：RAG 問答（`/api/ask`）。只動問答路徑；**不碰** `hybrid_search`（與檢索頁共用）與前端。
- 目標：
  1. 回答時盡量引用「越新越好」的研報。
  2. 限制離題（與研報語料無關）問題：直接拒答，不跑 LLM。

## 背景（現況）

- `app/services/retrieval.py::hybrid_search` 依 `(tier, fused_score)` 排序，無時間權重；被檢索頁與問答共用。
- `app/services/answer.py::build_context` 照 `scored` 出現順序貪婪取報告，無新舊偏好。
- `answer_question` 僅在 `context` 完全為空時回 `NO_CONTEXT_MESSAGE`；但 dense 檢索幾乎一定回最近鄰，故**離題問題現會跑完整 LLM** 並給「未提及」式回答，而非真正拒答。
- 本專案無 `Settings` 類別，設定走 `os.getenv`（見 `app/services/db.py`）。測試為零工具鏈 `unittest`（見 `tests/test_answer.py`）。

## A. 離題相關度門檻拒答

### 判定（新增純函式，可單測）

```text
is_off_topic(scored, *, min_relevance) -> bool
  - scored 為空                         → True（離題）
  - best_tier  = scored[0][0]           # 已依 tier 排序，首列即最高 tier
  - best_dense = max(1.0 - row[-1])     # 全候選最相似塊的 cosine
  - return best_tier == 0 and best_dense < min_relevance
```

規則：**字面命中（tier≥1：查詢詞全中或片語命中）一律視為在領域內**，不受門檻影響；
僅「純語意命中、且最相似塊都低於門檻」才判離題。避免用研報專有名詞發問卻被誤拒。

### 流程（`answer_question`，於 `hybrid_search` 後）

離題時：`yield ("sources", [])` → `yield ("token", OFF_TOPIC_MESSAGE)` → 寫 `qa_log` → `yield ("done", {"cited": []})`，**不呼叫 LLM**（順帶省 ~100s 延遲）。沿用既有 token/done 事件序，**前端零改動**。

```text
OFF_TOPIC_MESSAGE =
  "這個問題與廷豐研報的語料無關，請改問與研報內容相關的問題（例如特定市場、個股、期貨或總經主題）。"
```

### 設定

- `ASK_MIN_RELEVANCE`（float，預設 `0.45`）— BGE-M3 cosine 起手值，**需實測校準**；若本機 DB 可起，於實作時跑數題在領域/離題問題定預設。

## B. 脈絡新舊重排 + 提示偏好

### 重排（重構 `build_context` 報告排序）

1. 候選分塊**依 report 分組**，每篇記 `best_tier`、`best_fused`、`report_date`、passages。
2. 新近度加分：
   ```text
   age_days       = max(0, (now.date() - report_date).days)   # report_date 為 None → factor 0
   recency_factor = 0.5 ** (age_days / half_life_days)         # 今天=1.0、半衰期前=0.5
   effective      = best_fused + recency_weight * recency_factor
   ```
3. **排序鍵 =（best_tier, effective）由高到低**。`best_tier` 為硬保證（沿用 retrieval.py 哲學）→ 新近度只在**同 tier 內**重排，不讓較新弱相關篇跳過字面強命中的舊篇。
4. 再套 `max_reports` / `max_passages` / `max_chars` 上限與連續編號 `[1..N]`（依新順序給號）。

`report_date` 可能為 `date`/字串/`None`：有 `isoformat` 直接用、字串以 `YYYY-MM-DD` 解析、否則 factor=0。

### 可測性

`build_context(..., now=None)`：注入「今天」，測試固定時間避免 flaky；預設 `now=utc_now()`。

### 設定

- `ASK_RECENCY_WEIGHT`（float，預設 `0.06`）— 最大加分 0.06，刻意小於 tier 間距，僅微調近似篇。
- `ASK_RECENCY_HALF_LIFE_DAYS`（float，預設 `180`）。

### Prompt

`SYSTEM_PROMPT` 新增第 5 條：
> 5. 當多篇參考片段資訊重疊或衝突時，以『日期較新』的報告為準，並在作答與引用時優先採用較新的來源。

## 測試（unittest，沿用 `tests/test_answer.py` 樣式）

- `is_off_topic`：tier≥1 → 非離題；tier0 高 dense → 非離題；tier0 低 dense → 離題；空 scored → 離題。
- `build_context` 新近度：兩篇相關度近似、固定 `now`，較新者排前並拿 `[1]`。
- 既有四個 `BuildContextTests` 須續綠（tier/relevance 差距夠大、無日期 factor=0、tie 穩定排序保序）。

## 不做（YAGNI）

- 不動 `hybrid_search`、不加額外 LLM 分類器、不改前端、不引入 `Settings` 類別。

## 已知待校準

- `ASK_MIN_RELEVANCE=0.45` 為猜測值，上線前以實際語料校準；可純靠環境變數調整、免改碼。
