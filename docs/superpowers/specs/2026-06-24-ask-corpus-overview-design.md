# 問答總覽路徑：枚舉／聚合題改走全語料分面統計

- 日期：2026-06-24
- 範圍：問答模式（`/api/ask` → `app/services/answer.py`）。新增一條與既有 RAG 並行的「總覽路徑」。**不動**檢索頁、瀏覽模式、既有 RAG 行為與離題閘門。

## 背景與動機

使用者反映：問「給我所有元大的報告種類」這類問題，系統**只根據檢索抓到的研報內容回答**，不應只局限於抓到的那幾篇。

根因是一個典型的 RAG 局限：問答管線一律走 `embed_query → hybrid_search → build_context（取前 8 篇）→ LLM 作答`。但「給我所有元大的報告種類」「台股最近一週有哪些新研報」「哪些券商出過台積電研報」這類**枚舉／聚合／總覽**問題，需要對**整個語料的結構化 metadata 做聚合**，而非語意檢索 top-k。top-k（上限 8 篇）天生答不出「全部」。

資料層調查（2026-06-24，14,433 篇）確認這類問題可由結構化欄位回答：

| 維度 | 欄位 | 可查性 |
|------|------|--------|
| 券商 | `source`（檔名解析，正規化代碼） | yuanta 734、kgi 1482… 但 **9,654 篇為空**（解析失敗） |
| 市場 | `market` | TW 9993 / WTX 1833 / GLOBAL 926 / US 723 / MACRO 618 / CN 311 / HK 15 / FX 13 / CRYPTO 1 |
| 商品類型 | `instrument_types`（text[]） | equity 12212 / index 6358 / futures 3700 / fx 1801 / commodity 1772 / bond 1750 / options 1693 / etf 1334 / crypto 112 |
| 個股 | `stock_targets`（text[] 代碼）、`company_name`、`stock_code` | `stock_targets` 填充率 **73%（10,493）**；`company_name` 16%（2303）；2330/台積電命中 2,014 篇 |
| 時間 | `report_date` | 齊全 |
| 報告種類 | `report_type` | **約 80% 為空**：全語料僅速報 1042／報告 990／策略 372… yuanta 734 篇有 732 篇為空 |

**關鍵限制**：`report_type` 欄位本身大多沒標到。因此「報告種類」這個問法，即使改查 DB，`report_type` 也答不出有意義的種類——必須改用**實際有標到的維度（市場／商品類型／個股）**來回答總覽，並對 `report_type` 的稀疏誠實說明。

## 設計決策（已與使用者拍板）

1. **期待的答案** = 語料總覽／分面統計（用實際有標到的維度回答，不是只看抓到的幾篇）。
2. **v1 過濾維度** = 最廣：券商 + 市場 + 時間 + 個股 + 商品類型。
3. **架構** = 規則式條件解析 + 純 Python 總覽判定（零 LLM、零延遲）+ 確定性 SQL 分面聚合 + LLM 潤飾。fail-open 一律回退既有 RAG。
4. **答案生成** = SQL 算好數字當「事實」傳給 LLM，LLM 只用這些數字潤飾作答（禁止憑空造數）。
5. **個股名稱→代碼字典**（台積電→2330）：**v1 不納入**。v1 個股維度只做「4 碼代碼直接比對 `stock_targets`/`stock_code`」＋「中文名比對 `company_name ILIKE`」。名稱→代碼對照列為後續增強（能把個股命中率從 16% company_name 拉到 73% stock_targets）。
6. **總覽路徑跳過離題閘門**：解析到 ≥1 個金融過濾條件即視為本質在領域內，省一次 LLM 呼叫。離題保護由「需 ≥1 可解析金融條件」這個門檻天然提供。

## 現況架構（變更前）

- `app/services/answer.py` `answer_question()`：傳輸無關事件產生器。首輪 = `classify_intent` 與 `embed+hybrid_search` 並行；續問 = 先 `condense_and_classify` 改寫＋判意圖，再檢索。離題 → 拒答；否則 `build_context(scored)`（取前 8 篇）→ `stream_completion` → 寫 `qa_log`。事件序列 `("status"|"sources"|"token"|"notice"|"ext_sources"|"done", payload)`。
- `app/services/intent.py`：`classify_intent`（IN/OUT bool）、`condense_and_classify`（改寫+意圖）。
- `app/services/retrieval.py` `hybrid_search`：雙路召回融合。
- `app/services/store.py`：`search_chunks_meta`/`search_chunks_lexical`；`_meta_columns` 定義報告欄位順序。
- `app/services/filename.py`：`BROKER_MAP`（「元大」/「MS」→ 代碼）、`SOURCE_DISPLAY`（代碼→中文）、`source_display()`。
- `db/schema.sql`：`research.research_report`（含上表所有欄位、`is_research`）。
- `web/server.py`：`/api/ask` SSE endpoint，把 `answer_question` 事件轉 SSE。

## 設計

### 1. 流程：總覽分支插在 RAG 之前

`answer_question` 內，於既有檢索/離題流程**之前**插入分支（多輪則先沿用既有 `condense_and_classify` 取得 `standalone_query` 再判定）：

```
question →（多輪先 condense 得 standalone_query）
   ↓
filters = resolve_filters(query, today)
is_overview = detect_overview(query) and filters.any()
   ├─ is_overview 為真
   │     → 跳過 embed / hybrid_search / 離題閘門
   │     → overview = aggregate_facets(session, filters)
   │     → overview.total == 0 ? 回誠實「找不到…」
   │     → 否則 LLM 潤飾（事實＝overview）→ SSE token/sources/done
   └─ 否 → 既有流程（並行意圖判定 + 混合檢索，一字不改）
```

- **既有 RAG 路徑與離題閘門完全不變**：只有在 `detect_overview` 命中**且**至少解析到一個過濾條件時才改道。
- **比現況更快**：總覽路徑不做 embedding、不查向量、不跑離題 LLM，只有「聚合 SQL + 一次 LLM 潤飾」。
- **離題天然保護**：「列出所有天氣種類」解析不到任何 filter → `filters.any()` 為偽 → 回退 RAG → 既有離題閘門接手。

### 2. 新模組 `app/services/overview.py`

#### (a) `detect_overview(q: str) -> bool`
純函式。命中聚合提示詞即為真：`所有 / 全部 / 有哪些 / 哪些 / 列出 / 清單 / 列表 / 多少篇 / 幾篇 / 種類 / 類型 / 一覽 / 統計`（詞表可調）。大小寫/全半形先正規化（重用 `textnorm`）。

#### (b) `resolve_filters(q: str, today: date) -> OverviewFilters`
中文詞 → 結構化條件，全部確定性對應；解析不到的維度留空（best-effort）。`OverviewFilters` dataclass 欄位：`source/market/instrument_type/relates_*?/stock_code?/stock_name?/date_from?/date_to?`，附 `any()` 與 `applied_labels()`（給答案顯示「已套用：元大 / 台股 / 最近一週」）。

- **券商**：掃描 `BROKER_MAP` 的鍵（中文名與英文縮寫）與 `SOURCE_DISPLAY` 值；命中 → `source` 代碼。
- **市場**：關鍵詞 → enum。`台股/台灣→TW`、`美股/美國→US`、`陸股/中國/A股→CN`、`港股→HK`、`台指/期貨指數→WTX`、`總經/總體經濟→MACRO`、`全球/海外→GLOBAL`、`匯率/外匯→FX`、`加密/虛擬貨幣→CRYPTO`。（實作時先檢查 `tagging.py` 是否已有可重用的市場關鍵詞表。）
- **商品類型**：`期貨→futures`、`選擇權→options`、`ETF→etf`、`債→bond`、`原物料/商品→commodity`、`個股→equity`、`指數→index`、`外匯/匯→fx`、`加密→crypto`。
- **時間**：相對片語 → `date_from/date_to`，以注入的 `today` 計算（便於測試）：`最近一週/這週/近一週→[today-7, today]`、`最近一個月/近一個月→[today-30,…]`、`最近三個月/一季→[today-90,…]`、`今年→[今年-01-01, today]`、`去年→[去年全年]`、`YYYY年→該年全年`、`YYYY-MM→該月`。
- **個股**（v1 限定）：抽 4 碼數字代碼 → `stock_code = X OR X = ANY(stock_targets)`；抽中文公司名 → `company_name ILIKE '%名%'`。**不做名稱→代碼**（後續增強）。

#### (c) `aggregate_facets(session, filters) -> CorpusOverview`
以 `WHERE is_research = true AND <filters>` 對 `research.research_report` 跑一組聚合（可多個 `SELECT` 或單一 CTE）：
- `total`（總篇數）、`date_min`/`date_max`
- 按 `market` 計數、按 `instrument_types`（unnest）計數
- 按 `source` 計數（僅當未過濾券商時才有意義）
- 按 `report_type` 計數，**含「(未標註)」桶**（`COALESCE`/`NULL` 計數）
- top N `stock_targets`（unnest 計數）
- 最新 K 篇（`file_name`, `report_date`, `id`）作為**樣本來源**（供 `[n]` 引用與點閱）

`CorpusOverview` dataclass 持有以上欄位 + `filters`（含 `applied_labels()`）。

### 3. 答案生成（SQL 算數字 + LLM 潤飾）

- 把 `CorpusOverview` 序列化成緊湊「事實區塊」（純文字/markdown），連同使用者原問題餵給 LLM。
- system prompt 要求：**只能引用提供的數字，禁止造數**；針對使用者問法（如「種類」）自然作答；當問的是 `report_type` 而其多為「(未標註)」時，誠實說明並改用市場／商品類型／個股維度回答；繁體中文；句末附「在檢索頁查看全部 N 篇（已套用：元大／台股／…）」提示。
- `sources` = 最新 K 篇樣本（沿用 `Source` dataclass，可點 `[n]`）。
- 透過**既有 SSE 事件**輸出（`sources` → `status retrieved` → `token`…→ `done`），前端最小變動。
- **deep-link**：v1 在答案文字內以純文字提示「在檢索頁查看全部」即可（重用檢索頁既有 URL 狀態），不強制改前端；若要可點連結再評估。

### 4. 與 `answer_question` 的接線

- **首輪**：先 `resolve_filters + detect_overview`（純 Python、即時）。命中 → 走總覽路徑（短連線開 session 跑聚合 SQL），跳過 embed/檢索/離題。否則維持既有「並行意圖判定 + 檢索」。
- **續問**：先沿用既有 `condense_and_classify` 得 `standalone_query`（多輪改寫不可省），再對 `standalone_query` 跑 `resolve_filters + detect_overview`。
- **qa_log**：照舊寫入；於 `filters` jsonb 內加一個標記（如 `{"path": "overview", "overview_filters": {...}}`）供觀測，不改 schema。

### 5. 錯誤處理

- 非總覽題／解析不到 filter → 回退既有 RAG（既有行為，零風險）。
- `total == 0` → 誠實訊息「在語料中找不到符合條件（元大／台股…）的研報」，不跑潤飾 LLM。
- LLM 潤飾失敗／逾時 → 退回**純模板**輸出分面數字（保證有答案、不阻斷）。
- 聚合 SQL 異常 → 回退 RAG（fail-open）。

## 測試

- **純函式**（無 LLM、無 DB）：
  - `detect_overview`：「給我所有元大的報告種類」→True；「台積電的投資評級」→False。
  - `resolve_filters`：「元大」→source=yuanta；「台股」→market=TW；「最近一週」→`[today-7, today]`（注入 today）；「2330」/「台積電」→個股條件；多條件並存。
- **分面聚合**（conftest 既有 async DB fixture，餵測試列）：counts 正確、`report_type` NULL 進「(未標註)」桶、`total==0` 路徑。
- **整合**：範例題「給我所有元大的報告種類」→ 回分面答案、篇數反映 yuanta 全量（非卡在 8）、`sources` 為樣本。
- **迴歸**：既有 `test_answer`/`test_intent`/`test_retrieval_rank` 全綠；非總覽題（如「台積電展望」）行為與輸出事件序列不變。
- 程式碼品質：`uv run black/ruff/mypy`、`uv run pytest`。

## 不在本次範圍（YAGNI / 後續）

- 個股「名稱→代碼」對照字典（台積電→2330）——最能提升個股維度命中率的後續增強。
- `report_type` 回補標註（讓「種類」欄位本身有意義）——獨立的標註管線工程。
- 檢索頁可點 deep-link 的前端整合（v1 用純文字提示替代）。
- 跨多券商比較類問法（「哪家券商最常出台積電」雖可由 `source` 分面導出，但 v1 先聚焦單一過濾集合的總覽）。
- **離題洩漏（已知限制）**：含聚合提示詞且剝除停用詞後仍留 ≥3 字 CJK 殘餘的離題問題（如「幫我統計一下心情」），`_extract_stock_name` 會把殘餘誤判為 `stock_name` → 走總覽路徑 → 回「找不到符合條件的研報」而非離題拒答。因 fail-open，最壞情形僅為無害的「找不到」訊息（無崩潰、無幻覺、不汙染 RAG），且本質源於殘餘式公司名抽取——待「名稱→代碼字典」導入後可一併收斂。v1 接受此邊界。
