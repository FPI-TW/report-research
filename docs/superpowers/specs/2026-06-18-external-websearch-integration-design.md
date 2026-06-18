# 設計：外部網路搜尋整合（內部研報 + 外部網搜取得平衡）

- 日期：2026-06-18
- 範圍：RAG 問答 `/api/ask` 的回答層。加入外部網路搜尋，補研報的**時效**與**覆蓋**缺口；
  內部優先、外部補洞，雙軌明確區分來源。
- 不碰：`hybrid_search`（內部檢索照舊）、意圖閘門（離題照擋）、檢索頁。

## 決策（brainstorming 定案）

1. 外部來源 = **claude CLI 內建 WebSearch**（吃訂閱、免額外金鑰）。
2. 混合策略 = **內部優先、外部補洞**（研報不足/可能過時/問題要即時資料時才搜）。
3. 來源標示 = **雙軌明確區分**（研報 `[n]` 卡片 + 外部「外部參考」連結清單）。

## 能力探測（已驗證，2026-06-18）

- `claude -p --allowedTools WebSearch --output-format stream-json` 在 headless 可用：
  模型呼叫 WebSearch（`tool_use`）、最終答案走 `text_delta`（現有 parser 只取此、忽略
  thinking/tool 事件，相容）。
- 模型會自然在答案末尾輸出來源連結清單（探測中為 `Sources:` markdown 連結）。
  → 改以 sentinel + `- 標題 | 網址` 的嚴格格式輸出，便於後端解析。

## 流程

1. 意圖閘門（不變）：離題擋下；金融但「不在研報」的題通過 → 由網搜補洞。
2. 內部檢索（不變）：`hybrid_search` → `build_context` 取研報脈絡（編號 `[n]`）。
3. 回答模型開 WebSearch：system prompt「內部優先：先用研報片段；不足/過時/需即時資料才
   上網搜尋補充；研報論點標 `[n]`、網路論點標『（網路）』」。
4. 外部來源輸出：模型若用到網路來源，最後另起一行 sentinel `[EXT_SOURCES]`，其後每行
   `- 標題 | 網址`；正文不放裸網址。
5. 串流切割：`answer.py` 邊串流邊偵測 sentinel——之前的 body 正常送前端；見 sentinel 後
   停止送 body、收集其餘，收尾 `split_external_sources()` 解析成 `{title,url}` 清單，
   發 `ext_sources` 事件再 `done`。前端永不收到 sentinel（無正文閃現）。

## 後端元件

- `app/services/llm.py`：`stream_completion(..., allow_web: bool = False)`；為真時 cmd 加
  `--allowedTools WebSearch`。意圖閘門（`classify_intent`）**不開**網搜。
- `app/services/answer.py`：
  - `split_external_sources(text) -> tuple[str, list[dict]]`：以 sentinel `[EXT_SOURCES]`
    切出 (body, [{title,url}])；無 sentinel → (text, [])；畸形行（無 `|` 或非 http(s)）跳過。
  - `answer_question`：回答呼叫 `stream_completion(..., allow_web=ASK_ENABLE_WEB)`；
    用一個邊界緩衝逐段送 body、攔截 sentinel；收尾 yield `("ext_sources", [...])`（可空）
    後再 `("done", {...})`。
  - sentinel 緩衝：保留長度 = len(sentinel) 的尾段，避免送出半截 sentinel。
- 設定 `ASK_ENABLE_WEB`（`os.getenv`，預設 `"1"`/開）；關閉則 `allow_web=False`，行為同今天。
- `done` 仍帶 `qa_id`；外部來源是否寫入 `qa_log` 暫不做（YAGNI；日後要再加欄）。

## 前端元件

- `web/static/app/ask.js`：
  - 處理 `ext_sources` 事件 → 存清單；收尾若非空，動作列多一顆「外部參考 (M)」切換。
  - 「外部參考」與「資料來源」並列、皆預設收合（沿用 `.ask-sources` 收合模式，另開 `#askExtSources`）。
  - 外部來源卡：標題 + 網域 + 外部連結圖示，`target=_blank rel="noopener noreferrer"` 開新分頁；
    「網路」徽章區分於研報卡。URL 僅接受 `http(s):`（前端再驗一次，避免 javascript: 等）。
  - 每次新提問 `resetActions()` 一併清空外部來源與收合狀態。
- `web/static/index.html`：加 `#askExtSources` 容器與外部來源卡 CSS（沿用設計 token、無 emoji）。

## 可信 / 平衡

- 研報 `[n]`（可點開原檔）與網路（可點原連結）雙軌分離，使用者一眼分辨、各自可查證。
- 內部優先由 system prompt 保證；網路僅補洞。

## 錯誤處理（fail-safe）

- 網搜失敗/關閉/無結果 → 無 sentinel → `ext_sources` 空 → 無「外部參考」鈕 → 純研報回答，不報錯。
- WebSearch 在 CLI 內部出錯時，串流仍會產出（純內部）答案 → 不影響使用者。

## 延遲

- 只在模型判斷需要時才搜（內部優先），約 +10–30s，由既有「檢索研報並思考中…」指示涵蓋。

## 測試

- 單元（`tests/`，unittest）：
  - `split_external_sources`：有 sentinel（正確切 body+清單）、無 sentinel（原樣、空清單）、
    畸形行跳過、非 http(s) 跳過、body 尾端空白修整。
  - `stream_completion`：`allow_web=True` 時 cmd 含 `--allowedTools WebSearch`；False 時不含。
  - `answer_question`（monkeypatch stream_completion 吐含 sentinel 的文字）：body token 不含
    sentinel 之後內容、發出 `ext_sources` 事件、`done` 在最後。
- 端到端（Playwright）：問時效題（如「NVIDIA 最新一季財報營收」）→「外部參考」出現可點連結；
  問研報已覆蓋題 → 少/無網路來源、研報 `[n]` 照常。

## 不做（YAGNI）

- 不接專用搜尋/金融 API、不改 `hybrid_search`、不把外部來源寫進 `qa_log`、不做網頁全文抓取與快取。
