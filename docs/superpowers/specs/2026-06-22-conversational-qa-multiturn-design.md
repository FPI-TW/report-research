# 問答多輪對話（Conversational RAG）設計

- 日期：2026-06-22
- 狀態：已實作（後端單元測試全綠＋真實 DB SQL 驗證＋整支審查通過；live LLM 端到端待服務重啟後驗證）
- 影響檔案：`app/services/answer.py`、`app/services/intent.py`、`app/services/llm.py`(微調)、`web/server.py`、`web/static/app/ask.js`、`web/static/index.html`、相關 CSS、`db/schema.sql`

## 1. 目標與動機

目前問答為**單輪、無狀態**：每次 `/api/ask` 獨立檢索並回答，畫面上 `#askQuestion` / `#askAnswer` 各只有一個元素，新問題覆蓋舊問題。使用者無法在同一個對話脈絡裡追問（例如「那台積電呢？」「再詳細說明」）。

本案把問答升級為**多輪對話式 RAG**：

1. 在同一對話內可連續追問，追問能正確被**理解**（模型看得到先前問答）與**檢索**（含代名詞的追問會先改寫成獨立查詢）。
2. 對話以 `conversation_id` 存入 DB，可重開續問；側欄歷史由「單題清單」改為「對話串清單」。

## 2. 已確認決策

- **脈絡深度＝智慧追問**：把近幾輪問答帶給模型理解；含代名詞／省略主語的追問，先用 Haiku 改寫成「獨立查詢」再做檢索與意圖判定。
- **保存程度＝DB 保存＋側欄對話串**：新增 conversation 概念；側欄改顯對話串、點開可繼續問。
- **多輪脈絡傳遞方式＝內嵌 prompt**（非 CLI `--resume` session）。維持 `stream_completion` 現有隔離（`--setting-sources ''`、`cwd=/tmp`、無狀態 spawn）與韌性重試；多輪脈絡以文字段落內嵌進單一 prompt。
- **DB 結構＝在 `qa_log` 加 `conversation_id` 欄**（不另開 `conversation` 表）。一個對話＝共用同 id 的多列。

## 3. 非目標（YAGNI）

- 對話改名／自訂標題（標題固定取該對話最早一題）。
- 跨對話全文搜尋。
- 無限長對話回溯（僅帶近 N 輪）。
- 多使用者帳號隔離（本系統為共用帳號，沿用現狀）。

## 4. 資料模型

`db/schema.sql` 以冪等 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` 補欄（對齊 feedback/sources/ext_sources 既有模式，避免上次 schema 漂移事故）：

```sql
ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS conversation_id uuid;
CREATE INDEX IF NOT EXISTS idx_qa_log_conversation
    ON research.qa_log (conversation_id, created_at);
```

- 「一個對話」＝ `qa_log` 中共用同 `conversation_id` 的列集合。
- 對話標題 = 該 `conversation_id` 內**最早**一列的 `question`。
- 對話排序 = 各對話**最新**一列的 `created_at` 由新到舊。
- 舊列 `conversation_id` 為 NULL：每列各自視為一個「單題對話」（id 退化為該列自身 id），歷史不破、可點開唯讀重現；對其續問則以它為脈絡開新對話串。

部署：透過 `make schema`（`psql ... < db/schema.sql`）套用，**拉新碼前先套 schema**。

## 5. 後端設計

### 5.1 請求／回應契約

`AskRequest`（`web/server.py`）新增可選欄位：

```
conversation_id: str | None = None   # 續問時帶；新對話可省略
```

- 新對話：前端不帶 `conversation_id`（或帶 null）→ 後端鑄一個新 uuid。
- 後端在 `done` 事件回傳 `conversation_id`（前端記住，後續續問沿用）：
  `done: { cited, qa_id, conversation_id }`

### 5.2 `answer_question()` 流程（`app/services/answer.py`）

簽章新增 `conversation_id: str | None`，並回傳實際使用的 id（透過 `done` 事件）。流程：

1. **載入對話歷史**：若帶 `conversation_id`，從 DB 取該對話最近 **3 輪**（`question`,`answer`，依 `created_at`），舊答案截斷至約 **600 字**控 prompt 大小。無 id 或查無 → 視為首輪。
2. **改寫＋意圖（僅有歷史時）**：呼叫 `condense_and_classify(history, question)`（見 5.3），一次 Haiku 呼叫得 `(standalone_query, in_domain)`。
   - **首輪（無歷史）**：維持現狀——`classify_intent(question)` 與 embedding/檢索**並行**（不增延遲）；`standalone_query = question`。
3. **檢索**：用 `standalone_query` → `embed_query_cached` → `hybrid_search` → `build_context`（本輪自己的 sources 與 `[n]` 編號）。
4. **離題**：`in_domain` 為偽 → 回 `notice` 提示卡（該輪），仍寫 `qa_log`（帶 `conversation_id`），不中斷對話、不擋後續續問。
5. **組 prompt**：見 5.4。
6. **串流**：`stream_completion`（不變）→ 解析 `[n]` → 寫 `qa_log`（帶 `conversation_id`）。
7. **事件序**：`sources → [status] → token* → ext_sources → done{cited,qa_id,conversation_id}`（沿用現有；`done` 多帶 `conversation_id`）。

### 5.3 改寫＋意圖合一（`app/services/intent.py`）

新增 `condense_and_classify(history, question) -> (standalone_query, in_domain)`：

- 單一 Haiku 呼叫，system prompt 要求：把追問結合先前對話改寫成**可獨立檢索的完整問題**，並判定該問題是否屬投資/市場研究領域。
- 輸出採易解析格式（例如兩行：`QUERY: ...` 與 `INTENT: IN|OUT`），解析失敗時 **fail-open**：`standalone_query` 退回原始 `question`、`in_domain=True`（沿用 intent.py 既有 fail-open 哲學）。
- 只在「有歷史」時呼叫（首輪不需要，省一次呼叫）。
- 此設計同時解決「`再多說一點` 這類追問單看會被判離題」的問題——意圖判定看的是**改寫後的完整問題**。

### 5.4 Prompt 組裝（`build_user_prompt` 擴充）

新增「先前對話」區塊（僅多輪時）：

```
先前對話（供理解脈絡，不是新問題）：
Q1: <第1輪問題>
A1: <第1輪回答（截斷）>
... 近 3 輪 ...

參考片段：
<本輪檢索到的編號脈絡>

問題：<本輪原始問題>
請依規則作答，並在論點句末標註對應的來源編號。
```

- `SYSTEM_PROMPT` 不變；先前對話以「資料而非指令」的既有防注入精神呈現。
- `[n]` 編號只對應**本輪**檢索的 sources（每輪獨立編號）。

### 5.5 新增／調整端點（`web/server.py`）

- `GET /api/conversations?limit=` — 對話串清單：每串回 `{conversation_id, title, last_at, turn_count}`。納入條件：該串**首題（最早一列）非離題**（沿用現有 `/api/history` 以 `answer IS DISTINCT FROM OFF_TOPIC_MESSAGE` 排除離題的判準，套在首題上）。
- `GET /api/conversations/{id}` — 單一對話全部輪次（每輪 `question, answer, sources, ext_sources, feedback, qa_id`），供重開重現與續問。
- `DELETE /api/conversations/{id}`（＋ POST alias `/api/conversations/{id}/delete`，沿用現有 DELETE 相容回退）— 刪整串。
- `/api/history`、`/api/history/{id}` 既有端點：**保留且維持可用**（不破壞既有相容），但前端全面改用 conversations 端點；舊端點不再是前端主路徑。

## 6. 前端設計（`web/static/app/ask.js` + `index.html` + CSS）

### 6.1 對話串版面

- `#askQuestion` / `#askAnswer` 單組元素 → 改為**可堆疊的對話串**容器：每輪 render 一組「使用者問泡 + 助理答泡（含動作列/來源）」，全部可往上捲（ChatGPT 式）。
- 續問時把新一輪 **append** 到串尾，不覆蓋舊輪；自動捲到底（沿用 `nearBottom`/`toBottom`）。

### 6.2 每輪來源獨立掛載（關鍵重構）

- 現況：module 級單一 `sources`/`extSources`。多輪後**必須**改為**每則助理訊息各自掛載**自己的 sources/extSources。
- `[n]` 點擊、「資料來源／外部參考」展開、讚/倒讚/複製動作列，全部綁定**該則訊息**（用該訊息的 sources 解析 `report_id`），否則點舊訊息的 `[n]` 會開錯報告。
- 串流中的 token 寫入**當前輪**的答泡；latest-wins 序號（`state.askReq`）沿用以防舊串流污染。

### 6.3 側欄：對話串清單

- 由「單題清單」改為「**對話串清單**」：每列顯示該串標題（首題、單行截斷），點擊 → `GET /api/conversations/{id}` 載入全部輪次到主畫面，並把輸入框接上該 `conversation_id` 繼續問。
- 刪除鈕改為刪**整串**（沿用 confirm dialog 與 DELETE→POST 回退）。
- 新增**「新對話」**按鈕：清空對話串畫面、清掉當前 `conversation_id`（下次提問由後端鑄新 id）、回到著陸（landing）狀態。

### 6.4 狀態

- 前端保存「當前 active `conversation_id`」：首輪提問不帶 → `done` 事件取回後存起；之後續問帶上。
- 提問送出 body：`{ question, conversation_id }`（問答一律全語料，沿用不帶側欄篩選）。

## 7. 邊界與相容性

- **舊 `qa_log`（NULL conversation_id）**：側欄各自成單題對話，可唯讀重現；續問以其為脈絡開新串。
- **離題追問**：該輪以提示卡呈現，不寫入「可成為對話標題」的計算（沿用現有排除離題邏輯），但對話可續。
- **prompt 大小／延遲**：機器 CPU-bound（無 GPU），近 3 輪＋截斷舊答是延遲與脈絡的折衷；追問多一次 Haiku 改寫（輕量）。
- **韌性**：`stream_completion` 的 529 重試／逾時快速失敗／fallback 全部沿用，不動。
- **防注入**：先前對話與參考片段皆以「資料非指令」呈現，沿用 SYSTEM_PROMPT 第 4 條。

## 8. 測試策略

- **單元（純函式，零工具鏈或 unittest）**：
  - `build_user_prompt` 多輪格式（含/不含歷史）。
  - `condense_and_classify` 輸出解析（正常、缺欄、亂格式 → fail-open）。
  - 對話歷史截斷邏輯（近 3 輪、答案截斷字數）。
  - conversations 清單聚合（分組、標題取首題、排序取最新、離題排除）。
- **流程**：`answer_question` 首輪（不呼叫改寫）vs 續問（呼叫改寫）路徑切換。
- **手動／Playwright**：登入 → 提問 → 追問（驗證脈絡）→ 切對話 → 續問 → 點舊輪 `[n]` 開正確報告 → 新對話清空 → 刪整串。

## 9. 風險

- **延遲**：追問新增一次 Haiku 改寫呼叫（在檢索關鍵路徑上）。緩解：只在有歷史時呼叫、用 Haiku、合併意圖判定省一次呼叫。
- **改寫品質**：改寫失準會影響檢索。緩解：fail-open 退回原問題；prompt 給足近 3 輪脈絡。
- **前端重構面**：每輪來源獨立掛載牽動 `[n]`/動作列/來源切換多處，需逐輪綁定避免串台。
- **schema 部署順序**：拉新碼前未套 `make schema` 會 500（前車之鑑）；文件與計畫明列部署步驟。
