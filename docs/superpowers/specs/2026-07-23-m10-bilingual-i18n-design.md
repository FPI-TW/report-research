# M10 雙語（zh-Hant / en）i18n — 設計

**狀態**：M10a 進行中（後端輸出語言＋跨語言檢索）。M10b（前端 i18n）、M10c（英文研報模板 chrome）後續。

## 目標與非目標

**目標**：讓使用者以英文提問並取得英文回答／英文深度研報，同時沿用同一份中文研報語料。

**非目標（本階段）**：
- 不翻譯語料本身；證據、來源標題、公司名、代號一律保留原文。
- 不做查詢語言自動偵測——輸出語言由請求明確帶入的 `locale` 決定（未帶＝中文）。
- 不新增第三語系；`locale` 僅 `zh-Hant` / `en`，其餘一律 fail-open 回 `zh-Hant`。

## 關鍵事實：跨語言檢索已開箱即用

`store.search_chunks_meta` 的稠密召回走 BGE-M3 餘弦（多語模型），rerank 亦為多語——**英文查詢可直接召回中文研報**，無須改檢索。唯一不跨語言的是 `pg_trgm` 字面路徑（`content_norm LIKE %term%`）：英文詞對中文語料多半零字面命中，但 `hybrid_search` 以 `if terms:` 保護，稠密召回照常承接，**不會零結果、不拋錯**。故 M10a 檢索層零改動。

## 設計：locale 只影響「輸出文字」

`locale` 仿 M9b 的 `template_id` 方式貫穿請求，但落點不同——`template_id` 只到 PDF 渲染，`locale` 必須到**組 prompt 的文字**與**確定性使用者文案**。

### 核心模組 `app/services/locale.py`（領域無關，問答／研報共用）
- `resolve_locale(raw) -> str`：正規化到支援集合，無法解析 → `DEFAULT_LOCALE`（`zh-Hant`）。永不拋錯。
- `output_directive(locale) -> str`：回傳附加到系統提示尾端的語言覆寫指令。
  - **`zh-Hant`（預設）回空字串** → 既有 prompt 一字不動，**零回歸**。這正是 fail-open 的體現：未知/未帶 locale → `zh-Hant` → 無附加 → 行為與改動前完全相同。
  - `en` 回一段覆寫指令：要求全程英文、保留專有名詞與引用證據原文、固定 `[n]` 引用與 `[EXT_SOURCES]` sentinel 不變。
- `pick(locale, zh, en)`：固定字串挑選器（非 en 一律回中文）。

### 貫穿點（M10a-1 問答；M10a-2 研報）
- **問答**（`answer.py`）：`answer_question(..., locale=None)` 於頂端 `resolve_locale` 一次；主 LLM 系統提示尾端附加 `output_directive`；`OVERVIEW_SYSTEM_PROMPT` 同法；確定性文案（`NO_CONTEXT_MESSAGE`、off-topic／time-sensitive 婉拒、trusted 模板、`render_overview_text`）依 locale 切換。
- **研報**（`report.py`／`report_writer.py`，M10a-2）：`generate_report(..., locale=None)` → 分節大綱／逐節撰寫系統提示 + 單次系統提示皆附加 directive；章節骨架標題（如「本節網路來源」）依 locale。
- **路由**：`AskRequest` / `ReportRequest` 各加選填 `locale`，於單一呼叫點轉發。

### 歷史重播不變式
問答固定 notice（off-topic / time-sensitive）以**精確字串比對**判定 `is_offtopic`。新增英文版文案必須同時列入 `OFF_TOPIC_MESSAGES` / `NOTICE_MESSAGES`，否則英文婉拒重載後被誤當一般回答。`OFF_TOPIC_MESSAGES` 順序約定：`[0]` 現行中文、`[-1]` 舊版中文（既有測試以此定位），新語系插在中間。

## 切分

- **M10a-1**：locale 地基 + 問答路徑（`answer.py` 含 overview 子路徑 + `ask.py`）。端到端英文問答。
- **M10a-2**：研報路徑（`report.py` + `report_writer.py` + `report.py` router）。英文深度研報。
- **M10b**：前端 i18n（語言切換 UI、字串資源、把 `locale` 帶進 `/api/ask`、`/api/report`）。
- **M10c**：英文研報模板 chrome（頁首/頁尾/免責的英文版；依賴 M9b registry）。

M10a 可先於 M10b 合併：未接前端時 `locale` 恆為未帶 → `zh-Hant` → 零行為變化。
