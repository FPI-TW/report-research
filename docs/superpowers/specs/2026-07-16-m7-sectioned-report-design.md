# M7 研報生成重構（大綱 → 逐節） 設計規格

日期：2026-07-16。藍圖：`docs/REPORT_GEN_REDESIGN.md` §3 Phase 2、backlog：`docs/IMPLEMENTATION_PLAN.md` M7。
分支：`feat/m7-sectioned-report`，從 `origin/main`（`823b07d`，#81 已將 M5 agentic＋M6 多查詢/MMR 併回 main）開。依賴 **M4b（證據帳本，已在 main）** 與 **M6（多查詢分解＋MMR，已在 main）**；M8（忠實度查核）依賴本里程碑。

## 目標

把 `app/services/report.py::generate_report` 從「單次一口氣寫完整份」改為**薄編排層**，委派新 `app/services/report_writer.py`：

1. **大綱**：固定五章骨架（執行摘要／關鍵發現／重點分析／風險與展望／引用來源）＋動態子節（掛在五章之下、以 `###` 呈現）。
2. **逐節針對性檢索**：每節用 **M6** `plan_queries(profile="report")` 展多子查詢 → fan-out `hybrid_search` → 去重合併 → rerank → `select_reports`（含 MMR），只餵該節證據。
3. **逐節草稿**：各節對 `stream_completion` 呼叫一次，只在文字內寫 `[[ev:<id>]]` 佔位（用被分配的 evidence_id 子集）。
4. **依 M4b 證據帳本組裝**：單一協調任務把本次檢索全部 chunk 一次性入單一 `EvidenceLedger`，逐節依 outline 順序組裝成單一前導 `#` 標題的乾淨 markdown，對整份呼叫一次 `render_citations`（`n_unknown` 必為 0）。

新增 `research.report_run`（生成狀態機、冪等 `request_key`、checkpoint 續跑）與 `research.report_section`（逐節內容層，依 outline 順序原子 commit）兩表；`report_doc` 補 `outline`、`claim_evidence`、`current_revision_id`、`report_run_id` 四欄。SSE 以**純加法**新增 `section_draft`（可變草稿）與 `document_revision`（已定稿版）；`status/sources/token/error/done` 五事件形狀**位元不變**，舊前端與 M1b eval 兩道「未知即丟」保險自動忽略新事件。

**內容與版型解耦不變**：`markdown` 仍是唯一真相，`render_report_pdf`／`persist_report_doc`／`manifest_from_answer`／`strip_preamble`／`parse_external_refs`／`inject_kpi`／`inject_charts` 全部照舊，只吃最終組裝好的 markdown。

## 非目標（YAGNI）

- **不做 M8 忠實度查核**：`verifying` 狀態為 no-op pass-through（仍建 `document_revision`、`revision+1`），僅過 `render_citations` 的 `n_unknown==0` 把關；數值主張支持率屬 M8。
- **不建獨立 `report_revision` 表**：`current_revision_id` 為指標，內容落在 `report_section.final_markdown`＋`report_doc.markdown`；`markdown_hash` 只在 `document_revision` 事件負載內，DB 暫不存（待 M8 evaluation／M9b `report_rendition` 再評估）。
- **不做 chunk 級引用**：corpus 證據採**報告級**（對齊現行 `manifest_from_answer`／`answer.py`），避免 `[n]` 膨脹與跨路徑身分漂移。
- **前端串流消費為可選**（T9）：不做時舊前端自動忽略新事件、仍正常出報；要顯示逐節串流內文才擴 `ReportEvent` union。
- 不改 `report_gate.py`（純規則、不在生成路徑）。
- 不新增生成邏輯以外的 REST 端點（`/api/report` 只多流出加法事件）。

## 設計

### 1. 資料模型（`db/schema.sql`，冪等；接於檔尾）

```sql
-- M7：研報生成狀態機（一列＝一次生成請求的完整生命週期）
CREATE TABLE IF NOT EXISTS research.report_run (
  id                    uuid PRIMARY KEY,               -- Python uuid4（比照 qa_log/report_doc）
  request_key           text NOT NULL,                 -- 冪等鍵：同鍵重送回同 run（端上合成 hash）
  status                text NOT NULL DEFAULT 'queued'
    CHECK (status IN ('queued','retrieving','outlining','drafting',
                      'verifying','rendering','completed','failed','cancelled')),
  input_config          jsonb NOT NULL DEFAULT '{}'::jsonb,  -- question/filters/model/prompt/renderer 快照
  evidence_manifest_hash text,
  outline               jsonb,                          -- 固定五章＋動態子節
  checkpoint            jsonb,                          -- 最後一致可續跑點：{outline, final_positions[], current_revision_id}
  error_detail          text,
  current_revision_id   uuid,
  revision              int NOT NULL DEFAULT 0,
  qa_id                 uuid,
  conversation_id       uuid,
  report_doc_id         uuid,
  created_at            timestamptz NOT NULL DEFAULT now(),
  updated_at            timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_report_run_request_key UNIQUE (request_key)
);
CREATE INDEX IF NOT EXISTS idx_report_run_status       ON research.report_run (status);
CREATE INDEX IF NOT EXISTS idx_report_run_conversation ON research.report_run (conversation_id, created_at);

-- M7：逐節內容層（一列＝大綱一節）
CREATE TABLE IF NOT EXISTS research.report_section (
  id             uuid PRIMARY KEY,
  run_id         uuid NOT NULL REFERENCES research.report_run(id) ON DELETE CASCADE,
  position       int NOT NULL,                          -- 組裝序 = [n] 首見序
  section_key    text,                                  -- 骨架節鍵（exec_summary/... ）或動態子節鍵
  heading        text,
  draft_markdown text,                                  -- 對應 SSE section_draft（可覆寫）
  final_markdown text,                                  -- 對應 document_revision 組裝
  evidence_ids   text[],                                -- 對齊 evidence.py 的 evidence_id（16 hex）
  status         text NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','retrieving','drafting','drafted','verifying','final','failed')),
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_report_section_run_pos UNIQUE (run_id, position)
);
CREATE INDEX IF NOT EXISTS idx_report_section_run      ON research.report_section (run_id, position);
CREATE INDEX IF NOT EXISTS idx_report_section_evidence ON research.report_section USING gin (evidence_ids);

-- M7：report_doc 補欄（nullable 無 default，歷史列 NULL 安全退化）
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS outline             jsonb;
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS claim_evidence      jsonb;  -- claim/KPI/chart → evidence_id 映射
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS current_revision_id uuid;
ALTER TABLE research.report_doc ADD COLUMN IF NOT EXISTS report_run_id       uuid;   -- 反向連結（plain uuid，非 FK）
-- 注意：evidence_manifest 已由 M4b 建於 schema.sql，M7 不重複定義。
-- 部署鐵律：先 make schema 再上碼（qa_log schema-drift 500 前科）。
```

`report_run` 對 `research_report` 刻意無 FK（生成流程史非語料衍生，`store.upsert_report` 先刪後插不應連帶清除 run）。`report_doc.report_run_id` 為 plain uuid（不建 FK，避免建表順序/CASCADE 糾纏）。

### 2. 狀態機

固定推進：`queued → retrieving → outlining → drafting → verifying → rendering → completed | failed | cancelled`。

| 轉換 | 動作 |
|------|------|
| queued→retrieving | 進場以 `INSERT ... ON CONFLICT (request_key) DO NOTHING` 建 run（或撈回 in-flight run 續跑）；`started=time.monotonic()`（含檢索，沿用現況） |
| retrieving→outlining | `retrieve_context` 取全 run 共用的已編號 sources/context（此批決定 evidence_id 表）；yield `status:retrieving`→`sources`；空脈絡守門（context 空且 `REPORT_ENABLE_WEB=False`）→`error` 後轉 failed |
| outlining→drafting | LLM（`report_planner_model`，短逾時 `REPORT_OUTLINE_TIMEOUT`）產固定五章＋動態子節的 JSON outline，寫 `report_run.outline` 並展開 `report_section(position,section_key,heading,status=pending)`。outline 空/解析失敗且**尚未吐任何內容 token**→走 fallback（見 §5），不轉 failed |
| drafting→verifying | 依 outline position 逐節（各自 `REPORT_SECTION_TIMEOUT`）針對性檢索＋草稿，每節原子 commit `draft_markdown` 並 yield `section_draft`；全節 drafted 後由單一協調任務組裝 |
| verifying→rendering | **M8 未落地＝no-op pass-through**：仍建 `document_revision`、`revision+1`、寫 `final_markdown` 與 `current_revision_id`；`render_citations(full_text, ledger)` 斷言 `n_unknown==0` |
| rendering→completed | 組裝單一前導 `#` 標題 markdown→`render_report_pdf`→`write_report_pdf`→`manifest_from_answer`→`persist_report_doc`（補寫 outline/claim_evidence/current_revision_id/report_run_id），回填 `report_run.report_doc_id`；yield `done`（形狀不變） |
| →failed | 未捕捉例外／守門拒生成／`n_unknown` 把關耗盡；寫 `error_detail`、yield `error` |
| →cancelled | 客戶端斷線／asyncio 取消傳播；每個 `stream_completion` 迴圈 cancel-safe（子程序 kill 在 finally，逐節都要） |

**持久化規則**：每次狀態轉換與錯誤都在單一 DB 交易內原子 `UPDATE report_run.status + updated_at`（＋對應 outline/checkpoint/current_revision_id/revision/error_detail），並在同一持鎖區（web 層 `_REPORT_SEMAPHORE=1`）內按序 yield SSE。**續跑鐵律**：只能從最後一致 checkpoint 重試，不得改跑另一份完整內容。逐節 commit 必須依 outline position 順序（準備/檢索/草稿可並行，但寫回 `report_section` 與寫回共用 ledger 只能單一協調任務序列化）。

### 3. `app/services/report_writer.py`

- **大綱生成**：LLM 輸出固定五章骨架（對齊 `REPORT_SYSTEM_PROMPT` 與 eval `REQUIRED_SECTIONS` 分母=5）＋動態子節（`###`，**不得讓頂層 `##` 五章消失**，否則 `section_coverage` 破）；解析比照 `query_planner.parse_plan_json` 風格。
- **逐節針對性檢索**：`plan_queries(section_topic, profile="report")`（M6）→ 逐子查詢 `hybrid_search` → `ChunkRow.chunk_id` 去重合併 → rerank → `select_reports`＋MMR。**新增逐節配額旋鈕**（不可沿用整份 `max_reports=25`／`max_passages=6`／`max_chars=40000`／`rerank_candidates=120`）：`REPORT_SECTION_MAX_REPORTS`、`REPORT_SECTION_MAX_PASSAGES`、`REPORT_SECTION_MAX_CHARS`、`REPORT_SECTION_RERANK_CANDIDATES`（顯著下修，prod 實測 120 候選 ~93s，逐節串行×N 會爆延遲）。rerank 走 `asyncio.to_thread` 非阻塞。
- **逐節草稿**：各節 `stream_completion` 呼叫一次（每節複製一份 `SEARCH_EVENT`/reset_pending 的 `searching_web`↔`writing` 切換邏輯），只在文字內寫 `[[ev:<16hex>]]`，每節 commit `draft_markdown`＋yield `section_draft`；並發準備但依 position 順序 commit。
- **組裝**：協調任務先把本次檢索全部 chunk 一次性 `ledger.add_corpus(...)` 進**單一** `EvidenceLedger`（frozen `Evidence` 唯讀傳給並行節，**切勿在並行節內呼叫任何 `add_*`**，`_add` 非原子讀改寫無鎖）；外部/Web 參考彙整為單一「## 外部參考（網路）」節經 `add_external`/`from_ext_source`；組裝成單一前導 `#` 標題＋五章固定節名的乾淨 markdown（每節去重標題/正規化旁白，避免多個 `#`）→對整份呼叫一次 `render_citations`→用 `.text` 當最終內文、`.ordered` 產參考文獻、硬斷言 `.n_unknown==0`；`claim_evidence` 落 `report_doc`。

### 4. SSE 契約（純加法，向後相容）

| 事件 | payload | 相容性 |
|------|---------|--------|
| `section_draft`（新） | `{position, section_key, heading, markdown}`（**覆寫語意**：同 position 後蓋前） | 舊前端 `parseReportEvent` `default:return null`＋controller `if(!ev)continue` 自動忽略；M1b eval 只讀 `status.stage/sources/error.detail/done` |
| `document_revision`（新） | `{revision_id, revision, markdown_hash, markdown?}`（已查核最終版；在 `done` 前 yield） | **絕不取代 `done`**（controller 靠 `done\|error` 設 sawTerminal）；必為加法、`done` 必保留且形狀不變 |
| `status`（既有，零改動） | `{stage}` 維持 `retrieving/writing/searching_web/rendering` 封閉枚舉 | 逐節新階段**不塞入 status**（前端 `reportStage` zod enum 只認 4 值，未知→`width:NaN%`）；`outlining/drafting` 等資訊走新事件名 |

`sources/token/done/error` 形狀位元不變；`token` 仍逐段吐 raw markdown；`done` 保 `{report_id,title,download_url,thinking_ms}`（persist=True）與 `{report_id:None,title,markdown,context,thinking_ms}`（persist=False eval）兩形狀。`web/server.py` 轉發層對 event 名不設白名單，新事件自動流出、無需改後端。

### 5. Fallback 策略

**單一硬邊界＝「是否已 yield 過任一內容 token（含 `section_draft` 內文或 `token`）」。**

- **邊界之前失敗**（outline 空/解析失敗；或首節在吐第一個 token 前 `stream_completion` 拋 `LLMUnavailableError`）→退回既有**單次生成**路徑（`build_report_prompt`＋單次 `stream_completion(REPORT_SYSTEM_PROMPT, timeout=REPORT_TIMEOUT=600)`＋`strip_preamble`），對前端事件序**零差異**（前面只發過 `status:retrieving`/`sources`/`status:writing`）。
- **邊界之後**（已有草稿 section committed）→**不得改跑另一份完整內容**，只能：從最後一致 checkpoint 重試該節（`report_section` 依 `uq(run_id,position)` 更新該列）；或標 `failed`/`cancelled` 並 yield `error`。
- **個別節在邊界後失敗**：有界重試 N 次；耗盡後**動態子節→跳過**（保留其餘）、**五章骨架節→failed**（`section_coverage` 分母=5，頂層節不可缺）。
- **逾時預算重分配**：大綱 pass `REPORT_OUTLINE_TIMEOUT`（建議 30–60s）、每節 `REPORT_SECTION_TIMEOUT`（建議 120–180s）、可加全份 wall-clock 上限；保留 `stream_completion`「已串流即 fail-open 靜默截斷不拋錯」語義。
- **序列限流鐵律**：逐節多次 spawn claude CLI 須序列化（radar 多 run 搶 CLI、summary 冷啟動 I/O 風暴前科）；「並行準備」僅限不搶同一 CLI、不並寫同一帳本。
- **eval 模式（persist=False）**：`done.markdown` 必為**已 `render_citations` 的最終組裝稿**（非 `[[ev:]]` 中間態），否則 `citation_validity` 全 0。

### 6. `report.py` 薄編排與 patch 縫

`generate_report` 保留：起計時→`status:retrieving`→`retrieve_context`（全 run 一次）→yield `sources`→空脈絡守門→委派 writer 產串流→拿最終 markdown→`status:rendering`→PDF/manifest/persist→`done`。persist=False eval 旁路留在編排層。

**patch-where-used 陷阱**（M0/M3 前科，per-task 審查看不到模組縫）：把串流/檢索/渲染搬進 `report_writer.py` 後，`test_report.py` 以模組屬性替換的 mock（`rpt.retrieve_context`/`rpt.stream_completion`/`rpt.render_report_pdf`/`rpt.write_report_pdf`/`rpt.persist_report_doc`/`rpt.SessionFactory`/`rpt.REPORT_ENABLE_WEB`）會打不到真呼叫點。**對策**：report.py 保留這些可 patch 名（re-export／薄包裝），或同步改所有 patch 目標並人工核對真跑路徑。

## Open decisions（已拍板，供 review 覆核）

| # | 決策 | 拍板 |
|---|------|------|
| 1 | M6 前置 | **已解除**：#81 已把 M6 多查詢/MMR 併回 main，逐節檢索直接用 M6；殘留只有逐節 `rerank_candidates` 須顯著下修控延遲 |
| 2 | 節在邊界後失敗處置 | 有界重試→動態子節跳過、骨架節 failed |
| 3 | 引用粒度 | **報告級**（對齊 `manifest_from_answer`／`answer.py`；細粒度留 M8） |
| 4 | `n_unknown>0` 處置 | 有界重生違規節，耗盡→failed |
| 5 | `verifying`（M8 前） | no-op pass-through，保留節點供 M8 無縫接入 |
| 6 | `section_draft` 語意 | 覆寫（同 position 後蓋前，對齊單列更新） |
| 7 | `request_key` | `text NOT NULL UNIQUE`＋端上合成 `hash(question\|filters\|model\|conversation_id)`；`ReportRequest` 加可選欄，缺則合成 |
| 8 | `report_revision` 表 | 不建，`current_revision_id` 為指標 |
| 9 | `report_doc.report_run_id` | 加（plain uuid，雙向可追溯） |

**須使用者覆核的兩點**（其餘為內部機制）：
- **生成延遲**：逐節＝N 次序列 LLM＋N 次檢索，總時比單次長（品質優先、藍圖已註「數分鐘內可接受」）。逐節 rerank 候選下修是主要緩解。可接受否？
- **引用粒度報告級**：一節多 chunk 引同報告只得一個 `[n]`（與現況一致）。維持報告級否？

## 測試

`tests/test_report_writer.py`（新）：
- 狀態機：`queued→…→completed/failed/cancelled` 原子轉換、checkpoint 續跑、`ON CONFLICT` 冪等。
- 大綱：恆含五章頂層節、子節掛 `###`、空/失敗觸發 fallback 而非 failed。
- 逐節檢索：每節只餵該節證據、逐節配額不沿用整份、M6 `plan_queries` 有被呼叫。
- 組裝：多節單次 `render_citations` 連號一致（比照 `test_evidence.py` 凍結）、越界/變形 id 把關、`claim_evidence` 落庫。
- SSE：`section_draft` 可覆寫、`document_revision` 在 `done` 前、`done` 仍最後且形狀不變、每節迴圈 cancel-safe。
- fallback：首 token 前退單次對前端事件序零差異；邊界後只 checkpoint 重試/skip/failed。

`tests/test_report.py`（改）：薄編排回歸——`GenerateReportTests` 既有斷言 `kinds[0]==('status',retrieving)`、含 sources/token、`kinds[-1]=='done'` 全綠；新增「`done` 仍為最後」「`section_draft/document_revision` 為加法且舊 `parseReportEvent` 忽略」。persist 兩形狀契約不破。

## 驗收清單

- [ ] **T0（前置，本 session 進行中）**：以現行 config(sonnet-5) 重跑 `eval/baselines/report-m1b.json`，凍結為 M7 可比基準（現基準線產於 sonnet-4-6，模型漂移）。
- [ ] `make schema` 冪等；`report_run`/`report_section` 建立、`report_doc` 補四欄、歷史列 NULL 安全。
- [ ] 章節覆蓋率：最終 markdown 頂層五章全在（動態子節 `###` 掛下）→`section_coverage.rate=1.0`。
- [ ] 引用完整率：`render_citations(full_text,ledger).n_unknown==0`；正文 `[n]` 皆對應共用 sources。
- [ ] M1b 相對（sonnet-5 重跑）基準線：`section_coverage`/`citation_validity`/`source_citation_rate`/`facet_coverage`/`external_labeling`/`no_data_handled` 不退步；有效題數 ≥6。
- [ ] 狀態機一致：草稿/最終 revision/PDF/歷史重播/下載皆指向同一 `current_revision_id`；PDF 可由 `markdown` 重建。
- [ ] 冪等：同 `request_key` 重送回同一 run，不產生重複 doc。
- [ ] fail-open：首 token 前失敗退單次生成產出可下載研報；邊界後從 checkpoint 續跑或明確 failed。
- [ ] `GenerateReportTests` 全綠；新增事件契約回歸綠。
- [ ] Commit：`feat(研報): 大綱驅動逐節生成`。
