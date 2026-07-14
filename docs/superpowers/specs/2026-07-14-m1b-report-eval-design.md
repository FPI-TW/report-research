# M1b 研報評測契約與基準線 設計規格

日期：2026-07-14。藍圖：`docs/REPORT_GEN_REDESIGN.md` §3 Phase 5、backlog：`docs/IMPLEMENTATION_PLAN.md` M1b。
分支：`feat/m1b-report-eval`，自 `origin/main`（5f5ed12，M0–M4 已併入）切出。依賴 M0（`retrieve_context`）、M2（rerank）——皆已在 main。

## 目標

建立**研報專用**的凍結題集、離線 eval runner 與結構化指標基準線，讓 M6（檢索增強）與 M7（逐節生成）有可比較的相對基準。**不得重用 M1 的問答 runner 冒充研報評測**：問答題集量測單輪 RAG 短答；研報題集量測 `generate_report` 的深檢索與長文結構。

核心原則：M1b 的所有指標都必須**可由結構化資料確定性計算**（來源清單、Markdown 結構、引用編號），不得以 LLM 猜測取代。「數值主張支持率」保留給 M8 的 grounding 結果。

## 非目標（YAGNI）

- 不做 LLM 評審指標（Faithfulness / Context Precision / Answer Relevancy）——研報長文的 claim 級忠實度屬 M8；M7 落地前若需 Faithfulness 相對基準，屆時擴充本 runner 另跑。
- 不做 Context Recall（無人工參考答案）。
- 不改 `generate_report` 的生成行為、prompt 或檢索參數；唯一改動是**加法** `persist` 參數（見下）。
- 不進 `web/server.py` 請求路徑；純離線批次。
- 不做 CI gate：`thresholds` 僅觀察，比較一律相對基準線 + 容許誤差 + 人工抽樣。

## 設計

### 1. 題集 `eval/report_questions.json`（版本化，與問答題集分檔）

```json
{
  "version": 1,
  "generated_at": "<UTC ISO>",
  "reviewed": false,
  "count": 10,
  "questions": [
    {
      "id": "r001",
      "topic": "台積電先進製程與 AI 需求展望",
      "market": "TW",
      "expected_facets": [
        {"name": "財報/營運表現", "keywords": ["營收", "毛利率", "EPS"]},
        {"name": "先進製程", "keywords": ["先進製程", "3奈米", "2奈米", "CoWoS"]},
        {"name": "風險因子", "keywords": ["風險", "地緣", "關稅"]},
        {"name": "估值", "keywords": ["估值", "目標價", "本益比"]}
      ],
      "allowed_source_types": ["corpus", "web"],
      "no_data": false,
      "filters": {}
    }
  ]
}
```

- 每題含：主題（`topic`）、`market`（對齊 findb 代碼）、預期子題／面向（`expected_facets`，每面向帶**確定性比對關鍵詞**，關鍵詞是評分規則的一部分、隨題集版本凍結）、允許來源類型（`allowed_source_types`）、無資料情境標記（`no_data`）。
- 題目取材自語料市場分布（`make stats`）與既有 `report_doc` 實際主題，跨市場配置約 8 題正常題 + 2 題人工設計的無資料題（語料確定不涵蓋的主題）。
- `reviewed: false` 表示草稿；**人工過目後改 true 並凍結為 v1**（與 M1 問答題集「偶爾 refresh、人工過目後 commit」同語義）。
- 無資料題的 `expected_facets` 為空（facet coverage 不適用，記 None）。

### 2. `generate_report` 加法參數 `persist: bool = True`

`app/services/report.py::generate_report(..., persist: bool = True)`：

- `persist=True`（預設）：行為與現況**逐字節相同**（web 層不受影響）。
- `persist=False`（eval 專用）：檢索、prompt、串流全部照舊；跳過 `rendering` 階段（不渲染 PDF、不寫 `report_doc`、不落地檔案），`done` payload 加帶 `markdown`（strip_preamble 後全文）與 `context`（檢索脈絡原文，供檢索層指標計算），`report_id` 為 `None`。
- 理由：runner 必須「實際收集 `generate_report` 的完整 Markdown、來源與階段輸出」（走本尊、不走複製的旁路），但 eval 批次不得汙染 `report_doc`、不得耗費 WeasyPrint 渲染。

### 3. 指標 `eval/report_metrics.py`（純函式 + `RULESET_VERSION = 1`）

所有指標明訂**分母、缺資料行為、最低有效題數**：

| 層 | 指標 | 定義 | 分母 | 缺資料行為 |
|---|---|---|---|---|
| 檢索 | `facet_coverage` | 命中面向數 / 預期面向數；面向命中 = 任一 keyword 經 `norm_for_match` 正規化後出現在檢索脈絡 | `len(expected_facets)` | facets 空（no_data 題）→ None，不計均值 |
| 檢索 | `source_diversity` | `n_reports`（去重報告數）、`n_brokers`（DB 查 `research_report.source` 去重；查詢失敗 → None）、`n_markets` | — | sources 空 → 各值 0 |
| 檢索 | `date_diversity` | `date_span_days`（最新−最舊）、`n_months`（去重年月）、`n_undated` | 有日期的來源數 | 全部無日期 → span/months None |
| 生成 | `section_coverage` | 出現的固定骨架章節數 / 5（執行摘要、關鍵發現、重點分析、風險與展望、引用來源；`## ` 標題文字包含即命中） | 5 | markdown 空 → 0 |
| 生成 | `citation_validity` | 有效引用數 / 全部 `[n]` 引用數；有效 = `1 <= n <= len(sources)`。計算範圍為**正文**（剔除「引用來源」「外部參考」兩節，避免列表式編號灌水） | 正文 `[n]` 總數 | 正文無 `[n]` → None，另記 `n_citations=0` |
| 生成 | `source_citation_rate` | 正文實際引用的去重有效編號數 / `len(sources)` | `len(sources)` | sources 空 → None |
| 生成 | `external_labeling` | 網搜標示一致性（bool→1.0/0.0）：正文含「（網路）」 ⇔ 存在「## 外部參考（網路）」節且至少一行 `- [標題](網址)` | 「用到網路」的題（正文有（網路）或有外部參考節） | 未用網路的題 → 不入分母 |
| 行為 | `no_data_handled` | 無資料題的安全行為：yield `error`，或（正文零無效 `[n]` 引用 且 網搜標示一致）。註：`select_reports` 有 `min_reports=3` 保底，冷門主題檢索仍會回 ≥3 篇弱相關來源，故不能以 `sources==0` 為判準；內容層真偽屬 M8 與人工抽樣 | `no_data` 題數 | — |

- `MIN_VALID_QUESTIONS = 6`：正常題有效結果（無 error）低於此數時 `summary.sufficient_n = false`，基準線不得用於比較。
- 聚合：各指標均值僅計非 None 值，並回報 `n_valid` per 指標；summary 另含 `n`、`n_errors`、`n_no_data`、`ruleset_version`、`dataset_version`。

### 4. Runner `eval/run_report_eval.py`

- CLI：`uv run python eval/run_report_eval.py --dataset eval/report_questions.json --out eval/baselines/report-m1b.json [--limit N] [--question-timeout 900]`。
- **序列執行**（concurrency=1 固定）：研報生成是長 LLM 工作（實測 3–5 分/題），claude CLI 多併發實證會觸發限流（M4 Task 7 教訓），且 prod 端 `REPORT_SEMAPHORE` 本就序列化。
- 逐題流程：消費 `generate_report(topic, filters=filters, persist=False)` 事件流 → 收集 `sources`／stages／`done.markdown`／`done.context`／`error`；外層 `asyncio.wait_for(question_timeout)` 護欄。逐題 fail-open：任何例外記 `{"error": ...}` 不中斷批次。
- 逐題結束後以一個短連線查 `research_report.source`（broker 多樣性）；DB 失敗 → `n_brokers: None`。
- 輸出 `{"summary", "cases", "config"}`；`config` 快照當時 env（`report_enable_web`、`report_rerank`、`report_deep_k` 等），供跨基準線比較時核對條件一致。

### 5. 基準線與比較規則

- 首次執行產出 `eval/baselines/report-m1b.json` 並 commit（含逐題結果）。
- 後續里程碑（M6/M7）比較規則：同題集同版本、同 config；結構化指標波動主要來自 LLM 生成隨機性，容許誤差 ±0.05（比率類）；`n_errors` 不得上升超過 1；配合人工抽樣 2–3 題全文。
- 單次執行不作絕對 gate。

## 測試

- `tests/test_report_metrics.py`：每個指標的純函式測試（含分母邊界：無 facets、無日期、無 `[n]`、引用節剔除、標示一致/不一致、no_data 兩種安全形態）。
- `tests/test_run_report_eval.py`：stub `generate_report`（假事件流）驗 runner 的收集、逐題 fail-open、timeout 護欄、聚合與寫檔。
- `tests/test_report.py` 增：`persist=False` 不寫 DB／不渲染 PDF、`done` 帶 `markdown`+`context`、`persist=True` 事件序不變。

## 驗收清單

- [ ] 題集、逐題結果、評分規則（keywords + RULESET_VERSION）皆版本化並 commit。
- [ ] 每個指標有明確分母、缺資料行為；`MIN_VALID_QUESTIONS` 生效。
- [ ] `uv run python eval/run_report_eval.py` 可跑出基準線並落地 `eval/baselines/report-m1b.json`。
- [ ] `persist=True` 路徑行為零變化（既有 `tests/test_report.py` 全綠）。
- [ ] 題集標記 `reviewed: false`，交使用者人工審核後凍結。
- [ ] Commit：`feat(評測): 建立研報專用評測契約與基準線`。
