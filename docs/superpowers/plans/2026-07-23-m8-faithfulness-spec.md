# M8 忠實度查核（claim grounding）— Spec

狀態：spec 拍板 2026-07-23。權威需求見 `docs/IMPLEMENTATION_PLAN.md` §M8（L244-257）；藍圖 `REPORT_GEN_REDESIGN.md` §3 Phase 3、`QA_REDESIGN.md` §3-D。

## 目標

對「生成內容」做 RAGAS 式 reference-free 忠實度查核：把內容拆成子主張、標記數值型主張、逐條對其**自己的證據**做 grounding（被支持／未被支持／無來源），分開記錄 `citation_coverage`／`numeric_support_rate`／`faithfulness_score`。分數只是「來源支持度／待複核」，**不是真實性保證**。全程 **fail-open**：查核異常不阻擋交付、僅不加分數。

## 不做什麼（明確排除）

- 不判斷主張的客觀真偽，只判斷「是否被分配到的證據支持」。
- 不把三個分數包裝成單一信心分。
- 不改任何內容生成邏輯（檢索、選篇、寫作）；只在生成後查核，並在研報路徑做「至多一輪」targeted 修正。

## 三個決策（已拍板 2026-07-23）

1. **切三個 micro-PR**：M8a 地基（純服務＋schema＋config，全單元測試、不接線）→ M8b 研報接線 → M8c 問答接線。
2. **研報路徑做「查核＋修正一輪」**：低分數值主張 → 重生所在節一輪 → 再查核 → 不論結果都交付（fail-open）。
3. **問答迷你查核預設開、僅落庫不上 UI**：非同步、僅含數字時觸發、抽樣；結果寫 `qa_log.evaluation`，暫不上 UI 徽章（先觀察誤標率）。

---

## 架構決策（基於實際程式碼）

### D1. 證據帳本只存身分、不存文字 → faithfulness 自行回查
`app/services/evidence.py` 的 `Evidence`（frozen dataclass）**無 content 欄位**；帳本職責僅「來源身分／去重／持久化／引用渲染」，「是否被支持」正是 M8 的責任。故 `faithfulness.py` 需一個 **evidence_id → 來源文字** 的解析器：
- corpus：以 `report_id` 回查 `research.report_chunk.content`（該報告全部 chunk 串接，有上限）或 `research_report.full_text`。**實務上 `chunk_id` 多為 NULL**（`manifest_from_answer`／`build_ledger` 只帶 report_id），故 grounding 落在 **report_id 粒度**——v1 已知限制，記錄於結果。
- external：帳本只有 `snapshot_ref`／`content_hash`，**快照內容不在庫內**。v1 以 `title`＋`url` 作弱信號，判為 `no_source`（無法在庫內驗證的外部數值主張不給「被支持」）。

### D2. 復用 eval 的 claim 拆解／grounding，方向反轉
`eval/ragas_metrics.py` 已有 `DECOMPOSE_SYS`／`GROUND_SYS`／`faithfulness()`，但在 `eval/`（生產層不可反向 import）。做法：**把共用核心（prompt 常數＋decompose＋ground-against-texts）放進 `app/services/faithfulness.py`，`eval/ragas_metrics.py` 反過來 import 它**（eval 可 import app）。eval 版 ground 的是 contexts 字串；M8 在此之上加 **數值主張標記** 與 **主張→evidence_id 對應**（全新）。

### D3. app/services 缺非串流 JSON 呼叫 → 自備小工具
`llm.py` 只有 `stream_completion`。faithfulness 需「drain 串流 → 容錯 JSON」。沿用 `app/services/query_planner.py::parse_plan_json` 的容錯解析先例，包一個 `_json_completion(prompt, *, system, model, timeout) -> dict|list|None`（fail-open：逾時／解析失敗回 None）。

### D4. schema：兩個 idempotent ALTER，無新表
- `ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS evaluation jsonb;`
- `ALTER TABLE research.qa_log ADD COLUMN IF NOT EXISTS evaluation jsonb;`
`report_run`／`report_section` 的 `status` CHECK **已含 `'verifying'`**，無需改；revision 由 `report_run.current_revision_id`+`revision(int)`+`report_section.final_markdown` 表示，**不需新 revision 表**。

### D5. config：`config.py` 加 M8 區塊
比照 M7 的「每里程碑一註解區塊」慣例，在 `Settings` dataclass 與 `_load()` 各加 `# 忠實度查核 / faithfulness（M8）`：
- `REPORT_FAITHFULNESS_ENABLED`（預設 1）、`ASK_FAITHFULNESS_ENABLED`（預設 1）
- `REPORT_FAITHFULNESS_MIN`（低分修正門檻，對映 ragas `FAITHFULNESS_MIN`）
- `ASK_FAITHFULNESS_SAMPLE_RATE`（問答抽樣率）
- `FAITHFULNESS_MODEL`（judge 模型，復用 haiku）、`FAITHFULNESS_TIMEOUT`

---

## `evaluation` jsonb 結構（report_doc 與 qa_log 共用形狀）
```json
{
  "citation_coverage": 0.0,     // [n] 引用中對得上來源的比率
  "numeric_support_rate": null, // 數值主張被支持率（無數值主張→null）
  "faithfulness_score": 0.0,    // 全部主張被支持率（total==0→null）
  "claims": [ {"text","is_numeric","evidence_ids","verdict"} ],  // verdict: supported|unsupported|no_source
  "note": "來源支持度／待複核，非真實性保證",
  "checked_at": "<iso>",
  "degraded": false             // fail-open 時 true，其餘欄位可為 null
}
```

## 驗收（對映 IMPLEMENTATION_PLAN §M8）
- 注入含**錯誤數字**的樣本能被標記為 `unsupported`（M8a 單元測試以假 judge + 真 grounding 邏輯驗；acceptance 樣本）。
- 不顯著惡化延遲：問答查核在 done 事件後、非同步／抽樣（M8c）。
- fail-open：judge 逾時／JSON 壞 → `degraded:true`、不阻擋交付（M8a 單元測試驗）。
- Commit（最終接線）：`feat(查核): claim grounding 忠實度檢查`。

---

## 本 PR（M8a）範圍
`app/services/faithfulness.py`（服務＋prompt 常數＋evidence 解析＋`_json_completion`）、`eval/ragas_metrics.py` 改 import 共用核心、`db/schema.sql` 兩個 ALTER、`app/config.py` M8 區塊、`tests/test_faithfulness.py`（decompose／ground／resolve／numeric 標記／fail-open／注入錯誤數字被標記）。**不接線 report/answer**（M8b/M8c）。
