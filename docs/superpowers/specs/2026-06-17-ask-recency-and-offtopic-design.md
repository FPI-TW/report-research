# 設計：問答優化 — 引用偏好較新資料 + 限制離題問題

- 日期：2026-06-17
- 範圍：RAG 問答（`/api/ask`）。只動問答路徑；**不碰** `hybrid_search`（與檢索頁共用）與前端。
- 目標：
  1. 回答時盡量引用「越新越好」的研報。
  2. 限制離題（與研報語料無關）問題：直接拒答，不跑 LLM。

> **修訂（2026-06-18）：A 段「相關度門檻」已退役，改用 LLM 意圖閘門。**
> 上線後實測發現門檻**結構性不可行**：離題的「我想喝飲料推薦給我」(max_dense≈0.60)
> 分數**高於**正常的「可口可樂的投資評級如何」(≈0.57)、「怪獸飲料財報表現」(≈0.57)
> ——因為語料含飲料股研報，兩類檢索到同一批報告，分數重疊且倒置，**不存在**能擋前者
> 又不誤殺後者的門檻。改用 `app/services/intent.py::classify_intent`（Haiku 只看問題意圖，
> 與檢索並行跑、fail-open）。實測 9/9 正確（含上述三題）。詳見文末「C. 修訂」。
> 下方 A 段保留為決策歷程，**實作以 C 段為準**。

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

## 校準結果（2026-06-18，對真實語料實測）

8 題實測（4 離題 + 4 領域內）：

| 類型 | 範例 | max_dense |
|---|---|---|
| 離題 | 推薦飲料 / 天氣 / 寫詩 / 晚餐 | 0.448–0.486 |
| 領域內 | 台積電 / 升息 / 台指期 / AI 伺服器 | 0.681–0.721 |

兩群分離明顯（空隙 ~0.20）。0.45 太低（BGE-M3 中文有 ~0.48 語意地板，連天氣都過）。
取兩群中點，`ASK_MIN_RELEVANCE` 預設由 0.45 → **0.58**（兩側各留 ~0.10 餘裕）；
仍可純靠環境變數調整、免改碼。

> 註：0.58 隨後也被推翻——見 C 段。它仍會誤擋「可口可樂的投資評級」(0.567)，
> 且擋不住「我想喝飲料推薦給我」(0.597)。門檻這條路是死的。

## C. 修訂（2026-06-18）：門檻退役，改用 LLM 意圖閘門

### 為何門檻不可行（實測）

| 問題 | 性質 | max_dense | 0.58 判定 |
|---|---|---|---|
| 我想喝飲料推薦給我 | 離題 | 0.597 | 放行（錯）|
| 可口可樂的投資評級如何 | 正常 | 0.567 | 誤擋（錯）|
| 怪獸飲料財報表現 | 正常 | 0.573 | 誤擋（錯）|

離題題分數**高於**正常題：語料含飲料股研報，兩類檢索同批報告，分數重疊且倒置。
**任何門檻值都無法同時**擋住「想喝飲料」又放行「飲料股評級」。門檻只能分主題遠近、
分不出意圖。

### 方案：`app/services/intent.py::classify_intent`

- Haiku（`ASK_INTENT_MODEL`，預設 `claude-haiku-4-5`）**只看問題本身**，輸出 `IN`/`OUT`。
- system prompt 明確區分：詢問任一公司（含飲料/食品）的股價/評級/財報＝IN；要求推薦一杯
  飲料來喝、閒聊、寫作＝OUT。含範例。
- `parse_intent`：開頭 `OUT`→離題；其餘 **fail-open** 回在領域（判斷器抖動寧可多答、不誤擋）。
- `classify_intent`：任何錯誤/逾時（`ASK_INTENT_TIMEOUT`，預設 20s）/空回應 → fail-open。

### `answer_question` 串接

- `asyncio.create_task(classify_intent(question))` **與 embedding/檢索並行**，把 Haiku 延遲
  藏在檢索時間裡；`await intent_task` 後再決定。失敗則 cancel 子任務。
- `OUT` → 空來源 + `OFF_TOPIC_MESSAGE` + 不跑主 LLM（離題 ~5s 回應，舊版要 ~100s）。
- 移除 `is_off_topic` / `MIN_RELEVANCE` / `ASK_MIN_RELEVANCE`（連同其單元測試）。

### 驗證

- 27 單元測試綠（含 `tests/test_intent.py` 的 `parse_intent` 7 題、`AnswerGateTests` 改 monkeypatch `classify_intent`）。
- 真實 Haiku 分類器實測 **9/9 正確**（飲料三題＋天氣/寫詩/台積電/升息/台指期）。

### 取捨

- 每題 +1 次 Haiku 呼叫（~5s）。靠並行隱藏；對正常題（主回答 ~100s）可忽略，離題題反而更快。
- fail-open：判斷器故障時退化為「一律回答」（舊行為），不會把系統卡死或誤擋真問題。
