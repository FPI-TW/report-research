# M4 範圍路由（離題閘升級三態 scope_router）設計規格

日期：2026-07-13。藍圖：`docs/QA_REDESIGN.md` §3-A、backlog：`docs/IMPLEMENTATION_PLAN.md` M4。
分支：`feat/m4-scope-router`，自 `merge/m0-m3-into-main` tip（cc80d7b，= PR #64 併入後的 main 內容）切；#64 合併後即等同疊於 main。

## 目標

把 `intent.py` 的「離題就擋」布林閘升級為三態範圍路由 `off_topic | corpus_qa | overview`：
1. 放寬金融相鄰題（個股/產業/總經/期貨/匯率/加密/報價/ETF/債券一律 IN）。
2. 首輪與續問共用同一判準（既有 `INTENT_CRITERIA` 結構保證，擴充時同步）。
3. 完全離題的婉拒文案改客氣、Claude-like（**使用者已拍板：固定文案改寫**，不做動態生成）。

## 非目標（YAGNI）

- 不做 LLM 三分類：overview 態仍由既有確定性 `detect_overview` + `resolve_filters.any()` 產生（零 LLM、零向量，藍圖明說保留）。
- 不重構 answer_question 拓撲：首輪「意圖 ∥ 檢索」並行、續問單次 Haiku 改寫+判定，全數保留（完全抽取式 router 留給 M5 agentic 重構，避免丟棄工）。
- 不做 qa_log schema 變更、不做 DB 回填。
- 零檢索路徑改動、SSE 事件 payload 形狀不變。

## 設計

### 1. `app/services/scope_router.py`（git mv 自 `intent.py`）

- `Scope = Literal["off_topic", "corpus_qa", "overview"]`；常數 `OFF_TOPIC / CORPUS_QA / OVERVIEW`。
- `classify_scope(question, *, model, timeout) -> Scope`（原 `classify_intent`）：LLM 輸出仍為 IN/OUT 二元；IN→`corpus_qa`、OUT→`off_topic`；任何錯誤/逾時/空回應/解析失敗 fail-open → `corpus_qa`。
- `condense_and_route(history_text, question, *, model, timeout) -> tuple[str, Scope]`（原 `condense_and_classify`）：fail-open 回 `(原問題, corpus_qa)`。
- `parse_intent`（bool）保留為內部解析 helper（寬鬆兜底邏輯不變）；`classify_scope`/`condense_and_route` 內部映射 bool → Scope，不另設 `parse_scope`（YAGNI）。
- `INTENT_CRITERIA` 放寬：IN 明列「個股、產業、總經、期貨、匯率、加密貨幣、即時報價/收盤價、ETF、債券、大宗商品」；範例補「美元兌台幣走勢如何」「比特幣近期表現」「聯準會升息對科技股影響」→IN。OUT 維持「消費推薦、生活閒聊、寫作/翻譯、與投資無關的一般知識、非研報任務」。首輪 prompt 與 condense prompt 繼續引用同一常數。

### 2. `answer.py` 消費端

- import 改自 `scope_router`；`in_domain: bool|None` → `scope: Scope|None`（None=首輪尚未判定）。
- `if not in_domain:` → `if scope == OFF_TOPIC:`；其餘流程（overview 分支、RAG、並行判定）不動。overview 命中分支在語意上即三態的 `overview`（可在註解標明），不改行為。

### 3. 婉拒文案（sentinel 相容是關鍵）

- `OFF_TOPIC_MESSAGE` 改為客氣版固定文案（定案文字，實作逐字使用）：
  「這裡是廷豐研報的投資研究問答，這個問題超出我能引據回答的範圍。歡迎改問特定市場、個股、期貨、匯率或總經主題，我會依研報內容為你解讀。」
- **舊 qa_log 列存舊文案** → 新增 `OFF_TOPIC_MESSAGES: tuple[str, ...]`（新+舊），5 處字串比對消費端全改：
  1. `answer.py::_conversation_item`：`answer == OFF_TOPIC_MESSAGE` → `answer in OFF_TOPIC_MESSAGES`。
  2. `answer.py::load_recent_turns` SQL：`IS DISTINCT FROM :offtopic` → `answer != ALL(:offtopics)`（bindparam 陣列）。
  3. `answer.py::list_conversations` SQL FILTER ×2：同上。
  4. `web/server.py`（history SQL）：同上。
  5. `eval/dataset.py`：`a in (OFF_TOPIC_MESSAGE, NO_CONTEXT_MESSAGE)` → `a in (*OFF_TOPIC_MESSAGES, NO_CONTEXT_MESSAGE)`。

### 4. 測試

- `tests/test_intent.py` → `tests/test_scope_router.py`：既有 14 測改名沿用 + 新增路由分類測試（判準文字含收盤價/匯率/加密/總經/ETF；parse 寬鬆兜底；fail-open → corpus_qa；condense 共判準）。
- `tests/test_answer.py`：gate 測試 mock 點改（`ans.classify_intent` → scope_router 對應名）；離題事件序斷言改新文案。
- 舊 sentinel 相容測試：舊文案列在 `_conversation_item` 仍標 `is_offtopic`、SQL 過濾雙文案皆排除。

### 5. eval 迴歸

不重跑 RAGAS：檢索與作答路徑零改動，M1 queryset 全為 in-domain 題，閘門放寬不影響其結果（rationale 記錄於此）。

### 6. 驗收（對齊 backlog）

- 既有 `tests/test_answer.py` 相容；新增路由分類測試（含「收盤價」漂移案例）。
- 枚舉題仍正確走 `overview.py`。
- Commit 主體：`refactor(問答): 離題閘升級為三態範圍路由`。
