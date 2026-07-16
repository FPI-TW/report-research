# M7 研報生成重構（大綱 → 逐節） 實作計畫

規格：`docs/superpowers/specs/2026-07-16-m7-sectioned-report-design.md`。分支：`feat/m7-sectioned-report`（從 `origin/main` `823b07d0` 開）。
每個任務**可獨立驗收、樹不破**；核心以 flag／fail-open 包裹，隨時可退回既有單次生成。

## 任務序列與相依

```
T0 ✅ baseline 凍結(sonnet-5) ── 已 commit 5e0b836
T1 schema ──▶ T2 狀態機地基 ──▶ T3 大綱 ──▶ T4 逐節檢索 ──▶ T5 帳本組裝 ──▶ T6 SSE/串流 ──▶ T7 薄編排+fallback ──▶ T8 eval 擴充
                                                                                                              T9(可選) 前端消費
```

實作鐵律：每完成一個任務跑 `uv run --with pytest --with httpx pytest -q` 綠燈才進下一個；影響生成品質的任務（T4/T5/T7）完成後對 M1b 凍結題集抽驗、比 5e0b836 基準線不退步。

## T1 — Schema（`db/schema.sql`）

- 依規格 §1 於檔尾接 `report_run`/`report_section` 兩表（冪等 `CREATE TABLE IF NOT EXISTS`＋索引）與 `report_doc` 補 `outline`/`claim_evidence`/`current_revision_id`/`report_run_id` 四欄（`ADD COLUMN IF NOT EXISTS`）。`status` CHECK 清單與 §2 狀態機逐字對齊。
- **DoD**：`make schema` 冪等重跑通過（先套後上碼）；歷史 `report_doc` 列 NULL 安全；`evidence_manifest` 不重複定義。

## T2 — `report_writer.py` 狀態機地基

- 新 `app/services/report_writer.py`：`report_run` upsert（`ON CONFLICT (request_key) DO NOTHING`）、狀態原子推進 + `updated_at`、checkpoint 讀寫、`report_section` 依 position commit。以 `app/services/db.py` 的 `SessionFactory`。
- 純資料層先行（不接 LLM）：狀態轉換函式、checkpoint 序列化、冪等 upsert。
- **DoD**：`tests/test_report_writer.py` 覆蓋 `queued→…→completed/failed/cancelled` 原子轉換、checkpoint 續跑、`ON CONFLICT` 冪等；狀態常數與 CHECK 逐字同步。

## T3 — 大綱生成

- `report_writer.py`：LLM（`report_planner_model`、`REPORT_OUTLINE_TIMEOUT`）產固定五章＋動態 `###` 子節 JSON outline，寫 `report_run.outline` 並展開 `report_section`。解析比照 `query_planner.parse_plan_json`（fail-open）。
- 新 config：`REPORT_OUTLINE_TIMEOUT`（`app/config.py`，預設 45s）。
- **DoD**：outline 恆含五章頂層節、子節掛 `###`；空/失敗**觸發 fallback 旗標而非 failed**（T7 接單次生成）。

## T4 — 逐節針對性檢索 + 逐節配額

- `report_writer.py`：每節 `plan_queries(section_topic, profile="report")`（M6）→ 逐子查詢 `hybrid_search` → `chunk_id` 去重合併 → rerank(`to_thread`) → `select_reports`+MMR，只餵該節。
- 新 config：`REPORT_SECTION_MAX_REPORTS`/`_MAX_PASSAGES`/`_MAX_CHARS`/`_RERANK_CANDIDATES`（顯著下修，控逐節串行延遲）＋`REPORT_SECTION_TIMEOUT`（預設 150s）。
- **DoD**：每節只餵該節證據；逐節配額不沿用整份 25/6/40000/120；rerank 非阻塞；M6 `plan_queries` 有被呼叫（可 spy）。

## T5 — 證據帳本組裝 + `render_citations` 單次

- 協調任務：本次檢索全部 chunk 一次性 `ledger.add_corpus(...)` 進**單一** `EvidenceLedger`（frozen `Evidence` 唯讀傳並行節，**不得在並行節 `add_*`**）；外連彙整單一「## 外部參考（網路）」節；組裝單一前導 `#` 標題＋五章 markdown → 整份 `render_citations` 一次 → 硬斷言 `n_unknown==0`；`claim_evidence` 落 `report_doc`。**引用報告級**（對齊 `manifest_from_answer`）。
- **DoD**：多節組裝單次 render 連號一致（比照 `test_evidence.py` 凍結）；越界/變形 id 把關；`claim_evidence` 落庫。

## T6 — 逐節草稿串流 + SSE 加法事件

- 每節 `stream_completion`（複製 `SEARCH_EVENT`/reset_pending 切換）、commit `draft_markdown`＋yield `section_draft`（覆寫語意）；組裝後在 `done` 前 yield `document_revision`。
- **DoD**：`section_draft` 可覆寫、`document_revision` 在 `done` 前、`done` 仍最後且形狀不變；每節迴圈 cancel-safe（子程序 kill 在 finally）。

## T7 — 薄編排 + persist/fallback（`report.py`）

- `generate_report` 委派 writer；保留起計時/`status:retrieving`/`retrieve_context`(全 run 一次)/`sources`/空脈絡守門/`status:rendering`/PDF/manifest/persist/`done`；persist=False eval 旁路留編排層。
- **fallback 硬邊界＝首個內容 token**：之前失敗退單次生成（前端事件序零差異）；之後只 checkpoint 重試/skip/failed。
- **patch 縫**：report.py re-export/薄包裝 `retrieve_context`/`stream_completion`/`render_report_pdf`/`write_report_pdf`/`persist_report_doc`/`SessionFactory`/`REPORT_ENABLE_WEB`（防 `test_report.py` patch-where-used 假綠）。
- **DoD**：退單次對前端事件序零差異；persist 兩形狀契約不破；`GenerateReportTests` 全綠＋新事件契約回歸綠。

## T8 — eval 指標擴充 + 重跑

- `eval/report_metrics.py`：加確定性 **evidence link coverage**（以 `claim_evidence`/`evidence_id`）；`RULESET_VERSION 1→2`；確認 harness 讀已 render 的最終 markdown（非 `[[ev:]]` 中間態）。
- 重跑 `report-m1b.json`（ruleset v2）為 M7 後基準；section_coverage 對動態子節不誤判。
- **DoD**：新指標純函式；RULESET+1；同 config 重跑；相對 5e0b836 各指標不退步、有效題數≥6。

## T9 —（可選）前端消費

- `askSchemas.ts`/`askReducer.ts`/`DeepReportPanel.tsx`：擴 `ReportEvent` union + `parseReportEvent` case + 累積 `section_draft` 顯示逐節串流；不破既有 `done` 終結。不做則舊前端自動忽略新事件。

## 審查與收尾

- 全任務綠後：`code-reviewer` 子代理審整支 diff（重點：狀態機原子性、並發帳本安全、patch 縫、SSE 向後相容、fail-open 邊界）；材料 finding 修掉。
- 對抗式覆核 M1b 抽樣（生成一份真研報，人工看五章/引用/KPI）。
- 開 PR（base main），附 baseline 前後對比。Commit 主體：`feat(研報): 大綱驅動逐節生成`。
