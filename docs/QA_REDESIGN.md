# 廷豐智能研報 — 問答（Q&A）改善架構藍圖

> 定位（已確認）：**研報專用**、範圍聚焦**市場／股票／金融**、**體驗像 Claude**；以語料為據、帶 `[n]` 引用；本次聚焦兩條線——**對話體驗（UX）**與**回答智能（agentic）**。
>
> 前提：問答不像深度研報可以等數分鐘，**低延遲是硬約束**；一切建在《研報生成改善架構藍圖》(`docs/REPORT_GEN_REDESIGN.md`) 的共用地基上（型別化 row、共用檢索 helper、rerank、查詢分解），避免重造。
>
> **文件狀態（2026-07-14）**：共用檢索 helper、reranker、五類範圍路由，以及重新生成／停止／編輯重送／追問已接入；M3 資料模型與 API 細節以 `docs/superpowers/specs/2026-07-13-m3-qa-ux-design.md` 為準。時效資料 adapter、共用 evidence ledger 與 agentic 迴圈尚未實作；實際狀態與相依以 `docs/IMPLEMENTATION_PLAN.md` 為準。

---

## 0. 一句話定位

> 在**金融/市場/個股**範圍內，做一個「答得準、有出處、像 Claude 一樣好對話」的助理：語料內問題以研報為據並引用；語料不足時坦承限制，時效資料只由核准 adapter 補充並標示來源性質；完全離題（寫詩、閒聊）婉拒或簡短帶過。**不**變成什麼都能編的通用聊天機器人——金融場景裡，幻覺等於責任。

---

## 1. 現況 vs 目標

現有問答（`app/services/answer.py::answer_question`）已具備不錯的底子：

```
status(understanding) → [多輪則 condense_and_route] → overview 快速路徑偵測
  → scope_router（五類、fail-open；時效題目前安全婉拒）→ embed → hybrid_search(一次性) → rerank
  → build_context → stream_completion(SYSTEM_PROMPT, allow_web=False)
  → 解析 [n] / [EXT_SOURCES] → 寫 qa_log → report_gate 建議出研報
```

前端（`frontend/src/features/ask/`）已有 Claude-like 骨架與 M3 互動：`ThinkingSteps`、`SourcesDrawer`、`AssistantMessage`、`UserMessage`、`Composer`、`DeepReportPanel`、停止／重生／編輯／追問，以及對話歷史。

| 面向 | 現況 | 目標（像 Claude） | 缺口 |
|---|---|---|---|
| 範圍控制 | `scope_router.py` 五類分流（已完成） | 金融範圍內廣納；範圍外婉拒 | M4a 前時效題安全婉拒，不能把 WebSearch 當受信任資料 |
| 檢索 | **一次性** `hybrid_search` + rerank（已完成） | **自主多輪**：受控決定查幾次 | agentic RAG 迴圈（受控、有輪數上限） |
| 語料薄時 | corpus 問答明說不足；時效題婉拒 | 以核准資料補時效洞、來源可稽核 | M4a adapter + evidence ledger |
| 精準度 | dense+lexical 融合 + rerank（已完成） | 進脈絡片段更準 | agentic 迴圈的多輪證據補齊 |
| 對話體驗 | 串流、步驟、來源抽屜與 M3 互動（已完成） | 維持既有功能 | 只需回歸驗證 |
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
      LOOP[規劃器: 提子查詢與面向<br/>不得免除金融取證] --> RET[檢索 + rerank · 共用研報地基]
      RET --> ASSESS{夠了?}
      ASSESS -->|不足 且 未達輪數上限| LOOP
      ASSESS -->|語料薄 且 屬即時題| TRUSTED[受信任資料 adapter<br/>驗證截至時間與來源]
      TRUSTED --> ASSESS
      ASSESS -->|足夠| ANSWER
    end

    ONESHOT --> ANSWER[串流作答 + [n] 引用]
    ANSWER --> GROUND[輕量 grounding: 數值主張抽查]
    GROUND --> LOG[(qa_log)]
    LOG --> OFFER[report_gate 建議出研報]

    classDef fast fill:#e8f0fe,stroke:#4a7;
    class ONESHOT,OVERVIEW fast;
```

**延遲設計核心**：不是每題都進 agentic 迴圈。**枚舉題**走既有 overview 純 SQL；**簡單、非時效的語料事實題**走單輪檢索快速路徑；報價與公告一律走受信任資料 adapter；只有**需要綜合、比較、多面向**的問題才進多輪迴圈。這是「體驗像 Claude 又不犧牲聊天速度」的關鍵。

---

## 3. 分層設計

### A. 範圍路由（把「拒答閘」升級為「分流器」）

已將舊 `intent.py` 的「離題就擋」語義遷至 `scope_router.py` 的「問題類型 + 工具政策」分流；維持金融知識問題的 fail-open，但不以 fail-open 代替時效與投資風險控管。

- **完全離題**（寫詩、翻譯、生活閒聊、非金融一般知識）→ 婉拒或簡短帶過（可比現在更客氣、Claude-like）。
- **語料型金融問題**（歷史研報觀點、產業／公司分析、比較、風險）→ corpus RAG；所有金融事實主張都必須檢索，不允許規劃器因「看似簡單」而免檢索直接作答。
- **時效型金融問題**（即時／最新報價、財報、公告、利率、政策）→ 僅走核准的受信任資料 adapter，答案必須顯示「截至時間」與外部來源；不得以舊研報片段或一般 WebSearch 片段充當即時資料。adapter 尚未可用、資料過期或來源驗證失敗時，安全婉拒。
- **個人化建議／交易指令**（買賣、倉位、風險承受度）→ 僅提供可追溯的研究資訊、風險與不同觀點，不輸出個人化交易指令；必要時提示使用者自行評估或諮詢合格專業人士。
- **枚舉/聚合**（有哪些券商、各市場幾篇）→ 續走 `overview.py` 純 SQL 快速路徑（零 LLM、零向量），已實作、保留。

> 產出：`scope_router` 回傳 `{off_topic | overview | corpus_qa | time_sensitive | advice_risk}`，並同時給出 `tool_policy`（只用語料／需要受信任外部資料／不提供個人化指令）。首輪與續問必須共用同一判準，防止「首輪 IN、續問 OUT」漂移。M4 已完成分類與安全婉拒；啟用時效作答以前，必須先完成 adapter 的來源 allowlist、資料欄位、交易所時區、最大資料年齡、快取 TTL 與故障退化契約。

#### 外部來源政策（唯一準則）

`allow_web` 不是資料信任邊界；模型不得自由挑選網站或把搜尋結果當成事實。下表同時約束問答與深度研報，所有外部證據都必須先由 Python 驗證並寫入 evidence ledger：

| 來源類型 | 問答 `corpus_qa` | 問答 `time_sensitive` | 深度研報 | 必備條件 |
|---|---|---|---|---|
| 入庫研報 chunk | 允許 | 僅可作歷史背景，不可當即時數值 | 允許 | `report_id`、`chunk_id`、內容版本 |
| `trusted_market_data` adapter | 不適用 | 僅允許此路徑 | 可引用已核准的結構化資料 | provider profile、截至時間、最大資料年齡 |
| `controlled_research_web` adapter | 不允許 | 不允許 | 僅限非時效補充，且 profile 明確核准 | allowlist、擷取/快取規則、快照與人工可稽核性 |
| 一般 WebSearch／模型自由瀏覽 | 不允許 | 不允許 | 不允許 | 不得繞過上述 adapter |

每個外部 profile 都要定義允許網域、用途、欄位、速率限制、TTL、最大資料年齡與失敗退化。adapter 必須產生 canonical payload，將原始內容的不可變快照寫入受控儲存，回傳 `snapshot_ref` 與 `content_hash`；只有 hash 或 URL 不足以在來源變動後重現證據。

### B. Agentic RAG 迴圈（受控多輪，取代一次性檢索）

新增 `app/services/agentic_qa.py`，**Python 編排、Claude 決策**，貼合「Python 管確定性、Claude 管語意」分層——**不**放任 claude CLI 自由呼叫任意工具，而是由 Python 驅動的顯式迴圈：

1. **規劃步**：Claude（Haiku）看問題（＋多輪脈絡與 `tool_policy`）→ 輸出 schema 驗證後的 1–N 個子查詢、面向與資料新鮮度需求；Python 負責正規化、去重、每題子查詢／字數／總逾時上限。除了 overview／純介面操作外，不可決定「免檢索直接答」。
2. **檢索步**：Python 執行 `hybrid_search` + **BGE-reranker**（共用研報藍圖 Phase 1 的 `rerank.py`）。
3. **評估步**：Claude 看目前證據 → 決定「足夠、作答」／「再查一輪（補面向）」／「依 `tool_policy` 要求 M4a 受信任資料 adapter」。Python 對每輪設定子查詢數、候選數、總逾時與取消傳播，避免三個 ask 併發時再放大為大量 CLI／DB 工作。
4. **作答步**：綜合證據、串流輸出；內部使用 M4b 共用 `evidence_id`，最後才渲染研報 `[n]` 或外部來源標示。外部論點必須來自已核准的 M4a adapter，顯示來源性質與截至時間，並保存 URL、取得時間、`snapshot_ref` 與 canonical payload 的內容雜湊，不與研報引用混用。

控制參數（config 化）：
- `QA_MAX_ROUNDS`（預設 **2**）：問答輪數上限，硬性防延遲失控。
- **快速路徑豁免**：只限 overview、純介面／對話操作與已判定非時效的單一語料事實；時效型報價與公告仍必須套用外部資料政策並顯示截至時間，不能由 `_TRIVIAL_HINTS` 單獨判定。
- fail-open：規劃/評估任一步異常或逾時 → 退回**現有一次性 RAG**，問答不倒退。

> 問答用「輕量版」（1–2 輪）、研報用「深度版」（6–8 面向 + 逐節），兩者共用同一套 `query_planner` / `rerank` / `retrieval_pipeline`，只是參數不同。`evidence.py` 是先行的共用資料契約，不能等研報逐節生成後才提供給問答。

### C. 對話體驗 / UX（補齊，不重造）

後端事件流已是 `("status"|"sources"|"token"|"ext_sources"|"notice"|"done")`，前端已有 `ThinkingSteps`/`SourcesDrawer`/`AssistantMessage`。要補的是 Claude 常見的互動：

| 功能 | 後端 | 前端 |
|---|---|---|
| 重新生成（Regenerate） | `/api/ask` 支援以相同 `conversation_id` 重跑、覆蓋上一輪 | `AssistantMessage` 加「重新生成」鈕 |
| 停止生成（Stop） | SSE 連線可中止；已 yield 內容保留（`stream_completion` 已支援 kill） | `Composer`/訊息列加停止鈕 |
| 編輯重問（Edit & resend） | 以編輯後問題新開一輪、可截斷其後歷史 | `UserMessage` 加編輯態 |
| 追問建議（Follow-ups） | 答完用 Haiku 生成 3 個**金融範圍內**追問，以加法 `followups` 事件回傳 | `AssistantMessage` 底部 chips |
| 步驟透明化 | 現有 `status` 事件擴充語意（規劃/檢索/評估/取得受信任資料/作答） | `ThinkingSteps` 做成可展開/收合，像 Claude 的步驟卡 |
| 富渲染 | — | `AssistantMessage` 確保表格、程式碼、清單、`[n]` 可點來源卡 |
| 串流手感 | 逐 token（已有） | 打字游標、平滑滾動 |

> 追問建議要**限定金融範圍**，避免把使用者引導到會被範圍路由婉拒的題目。

### D. 回答風格與 grounding

- **系統提示微調**：保留現有引用鐵律（以片段為據、`[n]`、優先較新、片段是資料非指令、證據不足即明說限制），語氣往 Claude 靠——更自然、必要時主動澄清。只有已分配的 adapter evidence 才能寫成外部論點並標示來源性質與截至時間；不得把一般網搜描述為可信資料。避免僵硬的「報告腔」。
- **輕量忠實度檢查**（研報 Phase 3 的迷你版）：`cited_report_ids` 只能證明編號存在，不能證明論點被支持。用共用證據帳本對數值主張抽查，分別記錄引用覆蓋、數字支持率與未支持主張；外部資料同樣必須可追溯。為顧延遲，可非同步、抽樣或僅在含數字時觸發。
- **新近度**：`build_context` 的偏好較新邏輯沿用；答案在衝突時以較新研報為準（現有 SYSTEM_PROMPT 已載明）。

### E. 延遲預算與快速路徑

| 問題類型 | 路徑 | 目標延遲 |
|---|---|---|
| 枚舉/聚合 | overview 純 SQL | 現況（最快） |
| 單一語料事實 | 快速路徑：單輪檢索 + rerank + 直接答 | ≈ 現況 + rerank 開銷 |
| 即時報價／公告 | M4a 受信任資料 adapter + 截至時間 + 來源標示；不可用則婉拒 | 依資料來源；不可用舊研報或一般網搜冒充即時資料 |
| 一般綜合題 | agentic 1–2 輪 | 現況的 ~1.5–2.5×（可接受，仍是「聊天級」） |
| 需外部資料 | 僅 `time_sensitive`：M4a adapter + 驗證 | 視 provider 而定；前端顯示「取得受信任資料」與截至時間 |

手段：規劃/評估用 **Haiku**（快）、檢索並行、rerank 控候選上限、每請求的子查詢／adapter 呼叫／總逾時預算、快速路徑豁免、串流讓**首 token 感知延遲**低。硬上限 `QA_MAX_ROUNDS=2`，取消請求必須取消尚未開始的規劃／檢索／adapter 工作。

---

## 4. 模組對應

新增/改寫（大量複用研報藍圖地基）：

```
app/
  services/
    scope_router.py     # 問題類型 + tool_policy（off_topic/overview/corpus/time/advice）
    agentic_qa.py       # 新：受控多輪 RAG 迴圈（規劃→檢索→評估→作答）
    followups.py        # 已有：Haiku 生成金融範圍追問建議
    evidence.py         # M4b：共用證據帳本、來源持久化、最後引用渲染
    # 共用自研報藍圖：
    retrieval_pipeline.py / rerank.py / query_planner.py（輕量版參數）
  config.py             # QA_MAX_ROUNDS、快速路徑閾值等集中
frontend/src/features/ask/
    AssistantMessage.tsx # + 重新生成 / 停止 / 追問 chips
    UserMessage.tsx      # + 編輯重問
    ThinkingSteps.tsx    # 可展開步驟卡（規劃/檢索/評估/取得受信任資料/作答）
web/server.py           # /api/ask 支援 regenerate / stop 語意；done 帶 followups
```

`answer.py::answer_question` 變薄：`scope_router` → (`overview` | 快速路徑 | `agentic_qa`) → grounding → log → offer。串流狀態機（EXT_SENTINEL）沿用研報藍圖 Phase 0 抽出的 `SentinelStreamParser`。

---

## 5. 相容性與部署

- **SSE 契約**：新增事件（如 `followups`、更細的 `status.stage`）採**加法**、舊前端忽略即可，不破壞相容；串流已開始後不得在背景改跑另一條完整回答路徑，避免同一輪重複內容。
- **併發**：`/api/ask` 現為 3 併發上限；agentic 多輪使單題耗時上升，需觀察併發下延遲，必要時調整上限或佇列。
- **`claude` CLI**：規劃/評估/追問全走現有 `stream_completion`（Haiku），不新增外部依賴；問答一律不啟用自由網搜，時效資料只能走獨立、受控的 M4a adapter；`allow_web` 不能被視為可信資料層或 adapter 的替代品；PATH/systemd 同現況。
- **reranker/torch**：CPU-only 既定；問答快速路徑對 rerank 延遲敏感，候選上限要比研報保守。
- **`make serve` 無 reload**：後端改動需重啟 `report-mark-web.service`；問答 React SPA 修改後需重新 build `frontend/dist`，不是 `web/static` 的即時靜態更新。
- **範圍路由與 findb**：市場代碼對齊不受影響。

---

## 6. 風險與緩解

| 風險 | 緩解 |
|---|---|
| agentic 多輪拖慢聊天 | 快速路徑豁免 + `QA_MAX_ROUNDS=2` + Haiku 規劃 + 並行檢索 |
| 範圍路由誤判（把金融題當離題） | fail-open（判不準就放行）＋首輪/續問共用判準＋寬範例 |
| 語料外作答導致幻覺或來源不可追溯 | tool policy 限制外部來源；只接受 adapter 的 canonical payload，持久化 URL、來源性質、發布／取得時間、`snapshot_ref` 與內容雜湊；外部論點不混入研報引用 |
| 把過期研報當即時資料 | `time_sensitive` 在 M4a 前固定婉拒；M4a 後僅接受有 allowlist、截至時間與最大資料年齡驗證的 adapter 回應 |
| 個人化交易指令造成不當依賴 | `advice_risk` 路由只提供研究資訊與風險觀點，不給個人化買賣／部位指令 |
| 「像 Claude」被理解成「什麼都能編」 | 系統提示明訂：金融範圍、以據作答、坦承不確定；離題婉拒 |
| 追問建議把人帶出範圍 | 追問生成限定金融/研報主題 |
| 多輪 LLM 增加失敗面 | 每步 fail-open 退回現有一次性 RAG |

---

## 7. 分階段路線圖（每步可獨立上線、可回退）

1. **已完成：UX 補齊、範圍路由、型別化 row／共用 `retrieval_pipeline`／rerank。** 僅做回歸驗證，不得重做。
2. **M4a 受信任時效資料**：先落實 provider allowlist、canonical payload／不可變快照、結構化來源回應與不可用時的婉拒。
3. **M4b 共用證據帳本**：在問答 agentic 與研報逐節流程之前完成穩定 ID、來源 profile、快照參照、持久化與引用渲染。
4. **Agentic 迴圈**：導入 `agentic_qa.py` 受控多輪 + 有新鮮度保護的快速路徑；用凍結問答題組驗證忠實度、精準度、有效題數、錯誤率與延遲分佈。
5. **輕量 grounding**：數值主張抽查，先觀察誤標率再決定是否上 UI 徽章。

> 落地順序刻意把 **UX** 放最前：它風險最低、使用者最有感，且不阻塞後端 agentic 的開發。

---

## 8. 與研報藍圖的共用地基對照

| 能力 | 研報藍圖 | 問答藍圖 | 共用 |
|---|---|---|---|
| 型別化檢索 row | Phase 0 | 前置依賴 | ✅ `rows.py` |
| 共用檢索 helper | Phase 0 | 快速路徑 & 迴圈 | ✅ `retrieval_pipeline.py` |
| BGE-reranker | Phase 1 | 單輪 & 迴圈 | ✅ `rerank.py` |
| 查詢分解/規劃 | 深度版(6–8 面向) | 輕量版(1–2 輪) | 待實作 `query_planner.py`（參數不同） |
| 證據帳本 | Phase 0.5 | 問答與研報共用 | 待實作 `evidence.py`（M4b） |
| 忠實度/grounding | Phase 3 完整 | 迷你抽查版 | 待實作 `faithfulness.py` |
| 串流狀態機 | Phase 0 抽出 | 沿用 | ✅ `SentinelStreamParser` |

**結論**：Phase 0/1 的檢索地基已可共用；在接入 agentic 前，仍須完成 M4a 的時效資料安全邊界與 M4b 的證據帳本。兩條線之後才共用同一套可稽核基礎設施，只是問答走「輕量、低延遲、聊天級」的參數配置。

---

### 一句話總結

維持「以研報為據、帶引用、聚焦金融」的靈魂，把離題**拒答**改成**分流**、把一次性檢索改成**受控 agentic 多輪**（含快速路徑護住聊天速度），前端把已有的 `ThinkingSteps`/`SourcesDrawer` 骨架補上重新生成/停止/編輯/追問——就能在不變成通用聊天機器人、不引入幻覺責任的前提下，做出「像 Claude 一樣好對話」的金融研報問答。
