# 廷豐智能研報 — 問答（Q&A）改善架構藍圖

> 定位（已確認）：**研報專用**、範圍聚焦**市場／股票／金融**、**體驗像 Claude**；以語料為據、帶 `[n]` 引用；本次聚焦兩條線——**對話體驗（UX）**與**回答智能（agentic）**。
>
> 前提：問答不像深度研報可以等數分鐘，**低延遲是硬約束**；一切建在《研報生成改善架構藍圖》(`docs/REPORT_GEN_REDESIGN.md`) 的共用地基上（型別化 row、共用檢索 helper、rerank、查詢分解），避免重造。

---

## 0. 一句話定位

> 在**金融/市場/個股**範圍內，做一個「答得準、有出處、像 Claude 一樣好對話」的助理：語料內問題以研報為據並引用；語料薄時能自主搜網補洞、但明確標示來源性質；完全離題（寫詩、閒聊）婉拒或簡短帶過。**不**變成什麼都能編的通用聊天機器人——金融場景裡，幻覺等於責任。

---

## 1. 現況 vs 目標

現有問答（`app/services/answer.py::answer_question`）已具備不錯的底子：

```
status(understanding) → [多輪則 condense+分類] → overview 快速路徑偵測
  → intent 離題閘(Haiku, fail-open) → embed → hybrid_search(一次性)
  → build_context → stream_completion(SYSTEM_PROMPT, WebSearch 可用)
  → 解析 [n] / [EXT_SOURCES] → 寫 qa_log → report_gate 建議出研報
```

前端（`frontend/src/features/ask/`）也已有 Claude-like 骨架：`ThinkingSteps`、`SourcesDrawer`、`AssistantMessage`(+`markdown.js`)、`Composer`、`DeepReportPanel`、對話清單/歷史（`list/get/delete conversations`）。

| 面向 | 現況 | 目標（像 Claude） | 缺口 |
|---|---|---|---|
| 範圍控制 | `intent.py` 離題即**拒答** | 金融範圍內廣納；範圍外婉拒 | 閘門調成「範圍路由」、放寬金融相鄰題 |
| 檢索 | **一次性** `hybrid_search` | **自主多輪**：決定查幾次/要不要搜網 | agentic RAG 迴圈（受控、有輪數上限） |
| 語料薄時 | 常回「找不到」 | 自主搜網補洞並**標示非研報來源** | 覆蓋不足偵測 + 分流（沿用 `[EXT_SOURCES]`） |
| 精準度 | dense+lexical 融合，無重排 | 進脈絡片段更準 | 共用研報藍圖的 **BGE-reranker** |
| 對話體驗 | 串流 + 步驟 + 來源抽屜（已有） | + 重新生成/停止/編輯重問/追問建議 | 前端互動補齊 + 對應後端事件 |
| 回答風格 | 規則完整但偏「報告腔」 | 更自然、承認不確定、優先較新 | 系統提示微調（保留引用鐵律） |
| 忠實度 | `[n]` 對得上實際來源（已有） | 數值主張不亂編 | 輕量 grounding（可非同步/抽樣） |

---

## 2. 架構總覽

```mermaid
flowchart TD
    Q[使用者問題 / 續問] --> COND{多輪?}
    COND -->|是| CONDENSE[condense 改寫成獨立查詢 + 範圍判定]
    COND -->|否| SCOPE
    CONDENSE --> SCOPE

    SCOPE{範圍路由}
    SCOPE -->|完全離題| DECLINE[婉拒 / 簡短帶過]
    SCOPE -->|枚舉/聚合題| OVERVIEW[overview 純 SQL 快速路徑]
    SCOPE -->|一般金融題| FAST{簡單事實題?}

    FAST -->|是·快速路徑| ONESHOT[單輪檢索 → 直接作答]
    FAST -->|否| LOOP

    subgraph AGENTIC[Agentic RAG 迴圈 · Python 編排 / Claude 決策]
      LOOP[規劃器: 提子查詢 或 判定免檢索] --> RET[檢索 + rerank · 共用研報地基]
      RET --> ASSESS{夠了?}
      ASSESS -->|不足 且 未達輪數上限| LOOP
      ASSESS -->|語料薄 且 屬即時題| WEB[搜網補充 · 標示非研報]
      WEB --> ASSESS
      ASSESS -->|足夠| ANSWER
    end

    ONESHOT --> ANSWER[串流作答 + [n] 引用]
    ANSWER --> GROUND[輕量 grounding: 數值主張抽查]
    GROUND --> LOG[(qa_log)]
    LOG --> OFFER[report_gate 建議出研報]

    classDef fast fill:#e8f0fe,stroke:#4a7;
    class ONESHOT,OVERVIEW fast;
```

**延遲設計核心**：不是每題都進 agentic 迴圈。**枚舉題**走既有 overview 純 SQL；**簡單事實/報價題**走單輪快速路徑；只有**需要綜合、比較、多面向**的問題才進多輪迴圈。這是「體驗像 Claude 又不犧牲聊天速度」的關鍵。

---

## 3. 分層設計

### A. 範圍路由（把「拒答閘」升級為「分流器」）

改寫 `intent.py` 的語義：從「離題就擋」變成「分三類」，維持 fail-open。

- **完全離題**（寫詩、翻譯、生活閒聊、非金融一般知識）→ 婉拒或簡短帶過（可比現在更客氣、Claude-like）。
- **金融相鄰/廣義**（個股、產業、總經、期貨、匯率、加密、甚至「某公司最新收盤價」）→ 一律放行進入問答。你 `intent.py` 的判準已經是「意圖而非能力」，方向對，只需把範圍講得更寬、範例更全，並確保續問 `condense_and_classify` 與首輪判準一致（避免曾發生的「首輪 IN、續問 OUT」漂移）。
- **枚舉/聚合**（有哪些券商、各市場幾篇）→ 續走 `overview.py` 純 SQL 快速路徑（零 LLM、零向量），已實作、保留。

> 產出：`intent.py` → 概念上的 `scope_router`，回傳 `{off_topic | corpus_qa | overview}` 三態，取代現在的布林 in_domain。

### B. Agentic RAG 迴圈（受控多輪，取代一次性檢索）

新增 `app/services/agentic_qa.py`，**Python 編排、Claude 決策**，貼合「Python 管確定性、Claude 管語意」分層——**不**放任 claude CLI 自由呼叫任意工具，而是由 Python 驅動的顯式迴圈：

1. **規劃步**：Claude（Haiku）看問題（＋多輪脈絡）→ 輸出「要檢索的 1–N 個子查詢」或「不需檢索、可直接答」。
2. **檢索步**：Python 執行 `hybrid_search` + **BGE-reranker**（共用研報藍圖 Phase 1 的 `rerank.py`）。
3. **評估步**：Claude 看目前證據 → 決定「足夠、作答」／「再查一輪（補面向）」／「語料薄且屬即時題 → 搜網」。
4. **作答步**：綜合證據、串流輸出、句末 `[n]`；網路論點標「（網路）」並走既有 `[EXT_SOURCES]`。

控制參數（config 化）：
- `QA_MAX_ROUNDS`（預設 **2**）：問答輪數上限，硬性防延遲失控。
- **快速路徑豁免**：簡單事實/報價/單一標的題 → 跳過規劃與評估，單輪檢索直接答（沿用你 report_gate 的 `_TRIVIAL_HINTS` 那類判斷）。
- fail-open：規劃/評估任一步異常或逾時 → 退回**現有一次性 RAG**，問答不倒退。

> 問答用「輕量版」（1–2 輪）、研報用「深度版」（6–8 面向 + 逐節），兩者共用同一套 `query_planner` / `rerank` / `retrieval_pipeline`，只是參數不同。

### C. 對話體驗 / UX（補齊，不重造）

後端事件流已是 `("status"|"sources"|"token"|"ext_sources"|"notice"|"done")`，前端已有 `ThinkingSteps`/`SourcesDrawer`/`AssistantMessage`。要補的是 Claude 常見的互動：

| 功能 | 後端 | 前端 |
|---|---|---|
| 重新生成（Regenerate） | `/api/ask` 支援以相同 `conversation_id` 重跑、覆蓋上一輪 | `AssistantMessage` 加「重新生成」鈕 |
| 停止生成（Stop） | SSE 連線可中止；已 yield 內容保留（`stream_completion` 已支援 kill） | `Composer`/訊息列加停止鈕 |
| 編輯重問（Edit & resend） | 以編輯後問題新開一輪、可截斷其後歷史 | `UserMessage` 加編輯態 |
| 追問建議（Follow-ups） | 答完用 Haiku 生成 3 個**金融範圍內**追問，隨 `done` 回傳 | `AssistantMessage` 底部 chips |
| 步驟透明化 | 現有 `status` 事件擴充語意（規劃/檢索/評估/搜網/作答） | `ThinkingSteps` 做成可展開/收合，像 Claude 的步驟卡 |
| 富渲染 | — | `markdown.js`/`AssistantMessage` 確保表格、程式碼、清單、`[n]` 可點來源卡 |
| 串流手感 | 逐 token（已有） | 打字游標、平滑滾動 |

> 追問建議要**限定金融範圍**，避免把使用者引導到會被範圍路由婉拒的題目。

### D. 回答風格與 grounding

- **系統提示微調**：保留現有引用鐵律（以片段為據、`[n]`、優先較新、片段是資料非指令、不足則搜網或明說找不到），語氣往 Claude 靠——更自然、必要時主動澄清、坦然說「語料未提及，但根據網路…（網路）」。避免僵硬的「報告腔」。
- **輕量忠實度檢查**（研報 Phase 3 的迷你版）：`cited_report_ids` 已確保 `[n]` 對得上真實來源；再加一層**數值主張抽查**——對答案中的數字，比對其宣稱來源片段是否含該數字，不符則標記。為顧延遲，可**非同步**在寫 `qa_log` 時做、或只抽查、或僅在含數字時觸發。
- **新近度**：`build_context` 的偏好較新邏輯沿用；答案在衝突時以較新研報為準（現有 SYSTEM_PROMPT 已載明）。

### E. 延遲預算與快速路徑

| 問題類型 | 路徑 | 目標延遲 |
|---|---|---|
| 枚舉/聚合 | overview 純 SQL | 現況（最快） |
| 簡單事實/報價 | 快速路徑：單輪檢索 + rerank + 直接答 | ≈ 現況 + rerank 開銷 |
| 一般綜合題 | agentic 1–2 輪 | 現況的 ~1.5–2.5×（可接受，仍是「聊天級」） |
| 需搜網補充 | +1 輪網搜 | 視 WebSearch 而定，串流「搜尋網路中」遮蔽感知 |

手段：規劃/評估用 **Haiku**（快）、檢索並行、rerank 控候選上限、快速路徑豁免、串流讓**首 token 感知延遲**低。硬上限 `QA_MAX_ROUNDS=2`。

---

## 4. 模組對應

新增/改寫（大量複用研報藍圖地基）：

```
app/
  services/
    scope_router.py     # 改寫自 intent.py：三態路由（off_topic/corpus_qa/overview）
    agentic_qa.py       # 新：受控多輪 RAG 迴圈（規劃→檢索→評估→作答）
    qa_followups.py     # 新：Haiku 生成金融範圍追問建議
    # 共用自研報藍圖：
    retrieval_pipeline.py / rerank.py / query_planner.py（輕量版參數）
  config.py             # QA_MAX_ROUNDS、快速路徑閾值等集中
frontend/src/features/ask/
    AssistantMessage.tsx # + 重新生成 / 停止 / 追問 chips
    UserMessage.tsx      # + 編輯重問
    ThinkingSteps.tsx    # 可展開步驟卡（規劃/檢索/評估/搜網/作答）
web/server.py           # /api/ask 支援 regenerate / stop 語意；done 帶 followups
```

`answer.py::answer_question` 變薄：`scope_router` → (`overview` | 快速路徑 | `agentic_qa`) → grounding → log → offer。串流狀態機（EXT_SENTINEL）沿用研報藍圖 Phase 0 抽出的 `SentinelStreamParser`。

---

## 5. 相容性與部署

- **SSE 契約**：新增事件（如 `followups`、更細的 `status.stage`）採**加法**、舊前端忽略即可，不破壞相容。
- **併發**：`/api/ask` 現為 3 併發上限；agentic 多輪使單題耗時上升，需觀察併發下延遲，必要時調整上限或佇列。
- **`claude` CLI**：規劃/評估/追問全走現有 `stream_completion`（Haiku），不新增外部依賴；PATH/systemd 同現況。
- **reranker/torch**：CPU-only 既定；問答快速路徑對 rerank 延遲敏感，候選上限要比研報保守。
- **`make serve` 無 reload**：後端改動需重啟 `report-mark-web.service`；前端 `web/static` 即時生效。
- **範圍路由與 findb**：市場代碼對齊不受影響。

---

## 6. 風險與緩解

| 風險 | 緩解 |
|---|---|
| agentic 多輪拖慢聊天 | 快速路徑豁免 + `QA_MAX_ROUNDS=2` + Haiku 規劃 + 並行檢索 |
| 範圍路由誤判（把金融題當離題） | fail-open（判不準就放行）＋首輪/續問共用判準＋寬範例 |
| 語料外作答導致幻覺 | 網路答案一律標「（網路）」/`[EXT_SOURCES]`，不混入 `[n]`；數值抽查 |
| 「像 Claude」被理解成「什麼都能編」 | 系統提示明訂：金融範圍、以據作答、坦承不確定；離題婉拒 |
| 追問建議把人帶出範圍 | 追問生成限定金融/研報主題 |
| 多輪 LLM 增加失敗面 | 每步 fail-open 退回現有一次性 RAG |

---

## 7. 分階段路線圖（每步可獨立上線、可回退）

1. **UX 補齊（低風險、感知提升最快）**：重新生成 / 停止 / 編輯重問 / 追問建議 / `ThinkingSteps` 可展開。純加法、可先行。
2. **範圍路由**：`intent.py` → 三態 `scope_router`，放寬金融相鄰題、離題婉拒更自然。
3. **共用地基接入**：把研報藍圖 Phase 0/1（型別化 row、`retrieval_pipeline`、`rerank`）接進問答的單輪路徑，先拿到 rerank 的精準度增益。
4. **Agentic 迴圈**：導入 `agentic_qa.py` 受控多輪 + 快速路徑豁免；用問答版 eval 題組驗證「答對率/覆蓋度上升、延遲在可接受範圍」。
5. **輕量 grounding**：數值主張抽查，先觀察誤標率再決定是否上 UI 徽章。

> 落地順序刻意把 **UX** 放最前：它風險最低、使用者最有感，且不阻塞後端 agentic 的開發。

---

## 8. 與研報藍圖的共用地基對照

| 能力 | 研報藍圖 | 問答藍圖 | 共用 |
|---|---|---|---|
| 型別化檢索 row | Phase 0 | 前置依賴 | ✅ `rows.py` |
| 共用檢索 helper | Phase 0 | 快速路徑 & 迴圈 | ✅ `retrieval_pipeline.py` |
| BGE-reranker | Phase 1 | 單輪 & 迴圈 | ✅ `rerank.py` |
| 查詢分解/規劃 | 深度版(6–8 面向) | 輕量版(1–2 輪) | ✅ `query_planner.py`（參數不同） |
| 忠實度/grounding | Phase 3 完整 | 迷你抽查版 | ✅ `faithfulness.py` 邏輯 |
| 串流狀態機 | Phase 0 抽出 | 沿用 | ✅ `SentinelStreamParser` |

**結論**：先做研報藍圖的 Phase 0/1 地基，問答就能低成本接上——兩條線共用同一套檢索與生成基礎設施，只是問答走「輕量、低延遲、聊天級」的參數配置。

---

### 一句話總結

維持「以研報為據、帶引用、聚焦金融」的靈魂，把離題**拒答**改成**分流**、把一次性檢索改成**受控 agentic 多輪**（含快速路徑護住聊天速度），前端把已有的 `ThinkingSteps`/`SourcesDrawer` 骨架補上重新生成/停止/編輯/追問——就能在不變成通用聊天機器人、不引入幻覺責任的前提下，做出「像 Claude 一樣好對話」的金融研報問答。
