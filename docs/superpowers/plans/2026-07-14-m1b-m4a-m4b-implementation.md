# M1b / M4a / M4b 實作計畫（2026-07-14）

> Spec：`docs/superpowers/specs/2026-07-14-m1b-report-eval-design.md`、`2026-07-14-m4a-trusted-data-design.md`、`2026-07-14-m4b-evidence-ledger-design.md`。
> 執行方式：單 session 循序 TDD（測試先行）；每里程碑完成後跑全 `uv run pytest -q`、對抗式審查、依 repo 慣例提交。
> 分支：`feat/m1b-report-eval`（off origin/main）→ `feat/m4a-trusted-data`（off origin/main）→ `feat/m4b-evidence-ledger`（stacked on M4a）。

## M1b — 研報評測契約與基準線

1. **Task 1 `report.py` persist 參數**：先寫 `tests/test_report.py` 的 `persist=False`（不寫 DB/不渲染/done 帶 markdown+context）與 `persist=True` 零變化測試 → 實作加法參數。
2. **Task 2 `eval/report_metrics.py`**：先寫 `tests/test_report_metrics.py`（八個指標的分母/缺資料邊界）→ 實作純函式 + `RULESET_VERSION=1`。
3. **Task 3 題集**：查語料市場分布與既有 report_doc 主題 → 草擬 `eval/report_questions.json`（8 正常 + 2 無資料題，`reviewed: false`）。
4. **Task 4 runner**：先寫 `tests/test_run_report_eval.py`（stub generate_report：收集、fail-open、timeout、聚合、寫檔）→ 實作 `eval/run_report_eval.py`（序列、broker 查詢 fail-open、config 快照）。
5. **Task 5 基準線**：DB/CLI 健檢後實跑 `eval/baselines/report-m1b.json`（可於三里程碑實作完成後回到本分支執行）；核對 `sufficient_n`。
6. Commit：`feat(評測): 建立研報專用評測契約與基準線`（spec/plan 一併入庫）。

## M4a — 受信任時效資料契約

1. **Task 1 契約模組**：先寫 `tests/test_trusted_market_data.py`（成功/過期/失敗/allowlist/TTL/速率/取消/總開關，三 category fake providers）→ 實作 `app/services/trusted_market_data.py` + `config.py` 的 `trusted_data_enabled`。
2. **Task 2 answer.py 接線**：先寫 `test_answer_trusted.py`（無 provider 回歸婉拒；有 provider 答案含資料時間/來源性質；retrieve_context 哨兵未被呼叫；ext_sources 結構化；續問路徑）→ 實作 `_answer_time_sensitive` 與兩個呼叫點分流。
3. 全測試 + 對抗式審查（重點：HTTP 端點縫、fail-open 是否誤吞 CancelledError、cache/rate 狀態隔離）。
4. Commit：`feat(問答): 建立受信任時效資料來源契約`。

## M4b — 共用證據帳本地基

1. **Task 1 `evidence.py`**：先寫 `tests/test_evidence.py`（round-trip/去重/舊資料退化/id 決定性與篡改/render 穩定/受控建構器）→ 實作。
2. **Task 2 schema**：`db/schema.sql` 兩個 `ADD COLUMN IF NOT EXISTS evidence_manifest jsonb`。
3. **Task 3 寫入接線**：先寫 `test_answer.py`/`test_report.py` 的 manifest 接線測試 → `_log_qa` 與 `persist_report_doc` 增參、主 RAG/時效/研報路徑建帳本、`parse_external_refs`。
4. 全測試 + 對抗式審查（重點：歷史列 NULL 退化、manifest 與 sources/ext_sources 一致性、schema 冪等）。
5. Commit：`feat(證據): 建立共用 evidence ledger`。

## 全域驗證

- 每里程碑：`uv run pytest -q` 全綠（M4 基準 497 tests）；不動 frontend（無需 vitest/build）。
- 最終：三分支整合審查（模擬 opus 整支審查），確認與未提交 docs（使用者 WIP）零衝突、`git diff --staged --stat` 範圍檢查。
- 部署備忘：純後端；M4b 需 `make schema`；重啟 `report-mark-web.service`。
