# 架構檢視與改進建議（2026-07-28）

檢視範圍：`app/services/`（11,238 行）、`db/schema.sql`、`web/`、`frontend/`、`scripts/`、`tests/`（85 檔 23,191 行）、`deploy/`、文件。
所有結論均以實際檔案內容查證，附「檔案:行號」。

> **快照聲明**：本文是 **commit `3ce718e`（2026-07-29 09:38）當下的檢視快照**，不是持續維護的現況文件——「當時看到什麼」正是它的價值，因此其後併入 main 的修正一律以**時點註記**補在對應段落（目前有第 7、14、18 節與 P3 資料完整性一節，共四處），論述本身不改寫。文中的行號、計數與預設值都是檢視當下的量測、事後未再校正（連 `3ce718e` 本身都未必逐一對得上，例如檢視範圍寫的 `tests/` 85 檔，在 `3ce718e` 實際是 88 檔）——**任何數字都請重數一次，不要引用**；要看現況請讀 `CLAUDE.md` 與 `README.md`。
>
> **逐條複驗（2026-07-29）**：本文全部主張已拆成 119 條逐一查證，結果在
> `docs/ARCHITECTURE_REVIEW_2026-07_VERIFY.md`——66 條仍屬實、37 條需更正數字或推論、
> 10 條已修、**5 條是錯的**、1 條需連 DB 才能定論。**動手前先讀那份**，特別是
> 「報告寫錯的 5 條」那節：其中 P1-12c 的處方與 P0-1 的建議修法照做都會讓現況變差。

---

## 總評

這個 repo 的工程判斷品質明顯高於同規模專案的平均水準：分層原則（Python 決定性／Claude 語意）貫徹得住，`reading/anchor.py` 的錨定退讓策略附 400 筆實測、`store.py` 用 MATERIALIZED CTE 防 planner 誤走 HNSW、`deps.py` 的 SSE 中斷清理鏈、`report-mark-alert.sh` 的「恆 exit 0」鐵律——這些註解記錄的是「為什麼」而非「做了什麼」，是罕見的。

問題集中在三個模式，而不是散落的個別缺陷：

1. **機制做對了，但最後一哩沒接上。** 寫了 LLM 耗時遙測但 logging 從未初始化；加了派生資產新鮮度監控但前端 zod 沒跟上；有 eval 門檻但四份 baseline 全是 `false`。
2. **規約寫在註解裡，沒有機械化。** 「不可同時跑」、「欄位須插中段」、「content_norm 表達式須與 Python 一致」、「不可開多 worker」——全部靠人記得。
3. **單機小語料的假設沒有守門。** 多數 SQL 是在 241 篇 / 1 萬 chunks 時代驗證的（`docs/向量搜索優化報告.md:119` 明說「一律全表掃描，23 毫秒」），現況已是 1.4 萬篇 / 50-100 萬 chunks。

以下按「風險 × 修復成本」排序。

---

## P0：立即處理（低成本、已有事故證據或可造成資料損失）

### 1. `make normalize` 是破壞性操作，且其冪等宣稱是錯的

`scripts/normalize_chunks.py:20` 用 `clean_text`，但 chunks 是由 `ingest_all.py:101` 的 `chunk_text(clean_extracted(raw))` 產生的。實測：

```python
clean_extracted('第一段 文 字。\n\n第二段文字')  # → '第一段文字。\n\n第二段文字'
clean_text(上式結果)                              # → '第一段文字。第二段文字'   ← 段落邊界消失
```

腳本 docstring:4 宣稱「重跑為 0 筆更新」，實際對現有語料是 **100% 更新**。跑一次會：摧毀所有 chunk 的段落結構（影響顯示與 LLM context 可讀性）、產生 70 萬列 UPDATE 讓表與 HNSW 索引雙倍膨脹、而 `content_norm` 完全不變（空白早已移除）＝零收益。

`Makefile:79` 還把它列為推薦指令。

**做法**：從 Makefile 移除 `normalize` 目標，腳本刪除或改用 `clean_extracted`。

> **2026-07-29 複驗**：破壞性完全屬實（對真實抽取文字取兩組獨立樣本實測：589/597＝98.66%
> 與 2111/2200＝95.95% 的 chunk 會被改動，換行 6112→0 與 17812→0，而 `norm_for_match`
> 前後不同者 **0**＝零收益；「100% 更新」的正確說法是「幾乎每一列」）。
> **但上面「或改用 `clean_extracted`」這個選項是錯的**——`textnorm._RE_CJK_GAP` 的
> `(?<=[CJK])\s+(?=[CJK])` 同樣會吃掉「前段結尾是 CJK、後段開頭是 CJK」的那個單一換行，
> 實測仍會破壞 1123/2200＝51.05% 的 chunk，一樣零收益。**正解是刪除。**

### 2. 完全沒有 Postgres 備份，而 DB 裡已有不可重建的資料

`pg_dump|backup|pgbackrest` 在 `Makefile / scripts / deploy / docs` 全部零命中。唯一副本是 docker volume。

| 資料 | 可重建？ |
|---|---|
| `report_chunk` + 向量 | 可，但數十小時 CPU（journald 顯示單輪同步曾耗 3h20m） |
| `report_signal` / `report_takeaway` / `summary` | 可，但要重付 LLM 費用；且 takeaway 只跑近 90 天、signal 只跑子集，**舊報告一旦清除即永久消失** |
| `qa_log`（含 evidence_manifest、讚倒讚） | **不可** |
| `report_doc.markdown` | **不可**（README 說 markdown 是真相來源，但它自己沒備份） |

更嚴重的是 `make ingest-lowio`（`Makefile:69`）會關閉 `fsync` / `full_page_writes`——**在一個沒有任何備份的資料庫上**。其論證依據（`ingest_lowio.sh:6-7`「本 DB 為衍生、可重建」）在 6 月只有 chunk 表時成立，現在已不成立。

**做法**：`make db-backup` → `pg_dump -Fc` 到 NAS 掛載點 + daily timer + 保留 7/30 份（先只含 `qa_log` / `report_doc` / `report_rendition` 也好，體積小）；`ingest_lowio.sh` 開頭強制檢查 24h 內有備份。

### 3. logging 從未初始化，所有 INFO 級遙測靜默消失

`basicConfig` / `dictConfig` 在 `app web scripts` 全部零命中。uvicorn 只設定自己的 logger，不動 root，因此 `app.services.*` 走 `logging.lastResort`（level=WARNING、格式只有裸訊息）。

被丟掉的包含最關鍵的兩行：`answer.py:1864` 的 `qa_timing total_ms=... thinking_ms=...`、`answer.py:1745` 的 `qa_agentic rounds=... degraded=...`。也就是說**分段耗時觀測寫了但一行都沒進 journald**；而 70 處 WARNING 雖會輸出，卻沒有時間戳、level、logger 名稱。

**做法**：`web/server.py` 在 load_env 之後 `logging.config.dictConfig`，格式含 `%(asctime)s %(levelname)s %(name)s`，level 由 `LOG_LEVEL` 控制。這是全清單投報率最高的一項——沒有它，其他所有問題都在盲飛。

### 4. `claude` CLI 沒有跨進程鎖，而排程每 3 小時開一次撞車窗口

CLAUDE.md 的 gotcha「Never run extract_takeaways and extract_signals concurrently」目前只存在於三處文字警告（CLAUDE.md、腳本 docstring、Makefile 註解）。實際查證：`scripts/` 內唯一的 lock 是兩支 shell 的自我重入防護（`sync_new_reports.sh:16`、`resume_corpus.sh:12`），四支會 spawn `claude` 的 Python 批次**完全沒有鎖**（`tag_all_cli.py:30` 的 `threading.Lock` 只保護 fail-log 寫入）。

而 `report-mark-sync.timer` 每 3 小時就會依序跑 summaries → takeaways。此時有人手動敲 `make signals`，就精準重現「多批次搶 CLI → 擷取被大量誤標 rejected」的事故，且沒有任何東西會阻止或事後告知。

**做法**：`scripts/_claude_lock.py` 提供 `with claude_cli_lock(owner=...)`，用 `data/.claude_cli.lock` 存 `{pid, script, started_at}`，取不到就 exit 1 並印出持有者。所有 spawn `claude` 的入口一律取鎖。把一條 gotcha 從「靠人記得」變成「機器擋住」。

### 5. `report-mark-sync.service:13` 的 `Environment=HOME=%h` 是錯的（有現場證據）

系統層 unit 的 `%h` 解析為 `/root`，而 ExecStart 用 `bash -lc` 會去讀 `/root/.bash_profile`。`data/unit_failures.log` 有直接證據：

```
7月 28 15:00:02 bash[3402003]: /usr/bin/bash: /root/.bash_profile: Permission denied
```

同一份 log 尾端還有一筆 **2026-07-28T15:01:23 `report-mark-sync.service` status=2**、倒在「增量匯入 delta」階段，尚未處理。對比 `report-mark-web.service:14` 寫的是硬編碼 `HOME=/home/kashionz`（正確）——同一目錄兩種寫法，其中一種壞掉。`uv` / `claude` 都吃 `$HOME`，這是那次失敗的可疑共因。

**做法**：改硬編碼路徑，`bash -lc` 換 `bash -c`（PATH 已由 `SYNC_PATH_EXTRA` 明確處理）。順帶把 `unit_failures.log` 的未讀計數接進 `/api/progress` 亮紅點——機制上線當天就抓到真故障，但只寫檔案沒人看。

### 6. 問答路徑的相關度門檻量綱錯配（真實缺陷，3 行可修）

`rerank.py:126-136` 在 rerank 成功時把 head 的 `fused` **覆寫成 sigmoid [0,1] 分數**，tail 保留原 fused。研報路徑透過 `retrieval_pipeline.py:262` 傳入 pre-rerank 的 `gate_scores` 快照來修正，但**問答路徑 `retrieval_pipeline.py:114-120` 不傳**，於是 `answer.py:504` 拿 sigmoid 分去比以 fused 尺度校準的 `ASK_RELEVANCE_FLOOR=0.62`。

`ASK_RERANK_ENABLED` 預設為 1，所以這是預設生產路徑。`answer.py:401-404` 的 docstring 已把它記為「finding 3」，但修正只套在研報路徑。

**做法**：`retrieval_pipeline.py:118` 一併計算並傳入 `gate_scores`。根本解見「結構性重構」第 3 項。

### 7. 文件有 8 處事實錯誤，正在誤導接手的人與 agent

> **2026-07-29 時點註記（複驗於 `9a93a15`）**：本節多數項目已由 PR #130（`f37fb53`「讓貢獻者文件與程式碼現況對齊」）修正。第 1–5 條（`node --test`、前端零工具鏈、`intent.py`、tunables 散落、PDF＝WeasyPrint）**皆已不成立**——`node --test web/static/app/*.test.mjs` 只剩 `AGENTS.md:26` 拿它當反例警告，`intent.py` 只剩 `docs/ROADMAP.md:30` 註明「前身，已改名」，另外三條零命中。以 `9a93a15` 為準還沒收乾淨的是下面三條，**但它們已在同日（2026-07-29）的文件同步中一併修掉**，此處保留只為說明本節第 6–8 條的來歷：
>
> - 第 6 條：`docs/ROADMAP.md` 已改對（其「尚未實作」表只剩 findb 整合／每日簡報／MCP server／對外 REST），但 `README.md` 的「尚未實作」句仍把結構化訊號算進「Phase 2」。
> - 第 7 條：`README.md` 的數字已從 25 改成 86，但複驗當下 `tests/test_*.py` 實測是 **92** 檔——修過一次又漂了，正說明手寫計數不該寫進文件。
> - 第 8 條：`agentic` 與 `faithfulness`（同在 `CLAUDE.md:60`）、`typst`（`:62,64`）都已補進架構段，只剩 radar 還停在 `:74` 的 `web/routers/` 路由清單裡。
>
> 附帶一提：`f37fb53`（07-28 16:16）早於本報告的 `3ce718e`（07-29 09:38）且已在同一棵樹上——第 1–5 條在寫下當天就已對不上自己的 checkout。
>
> 因此本節八條**現已全數不成立**（下方表格保留為當時記錄）；要判斷現況一律直接讀 `CLAUDE.md`／`README.md`／`docs/ROADMAP.md`，不要引用此處的行號與判定。

| 錯誤 | 出處 | 現況 |
|---|---|---|
| `node --test web/static/app/*.test.mjs` | CLAUDE.md、`README.md:401`、AGENTS.md ×2 | `web/static/app/` **不存在**，全 repo 0 個 `.test.mjs`。這條指令必然失敗 |
| 「前端＝原生 ES Module、零工具鏈、無打包步驟」 | `README.md:154,405`、AGENTS.md、`WORKFLOW.md:40,136` | 實際是 React 19 + Vite 8 + TS + Tailwind 4 + 91 個 vitest 檔 |
| `intent.py` | CLAUDE.md ×2、`README.md:222,298,388` | **檔案不存在**，已由 `scope_router.py` 取代 |
| 「Tunables 散落 os.getenv」 | CLAUDE.md | 反了。`app/config.py` 已集中 69 個鍵；services 只剩 `followups.py`(2)、`retrieval_pipeline.py`(2) 漏網 |
| PDF ＝ WeasyPrint | CLAUDE.md、`README.md:12,60,119,153,441` | 已是雙軌，**typst 為預設**、weasyprint 為 fail-open 回退 |
| 「Phase 2 訊號＋findb 尚未實作」 | `ROADMAP.md`、`README.md:429` | 訊號與雷達**已上線**（`radar/` 5 模組、前端 28 檔、測試 1,794 行），只是交付物改名（`/api/radar/*` 而非 `/api/consensus/*`） |
| 「tests/ 共 25 個檔」 | `README.md:410` | 實際 85 檔 |
| CLAUDE.md 架構段 grep `typst / radar / faithfulness / agentic` → **0 命中** | CLAUDE.md | 觀點雷達、Typst 渲染、忠實度查核、agentic QA、i18n 全部已上線但一字未提。照 CLAUDE.md 讀會漏掉一半架構 |

**做法**：先修這 8 處（約 30 分鐘）。再加一支測試掃 README / CLAUDE.md / AGENTS.md 裡的檔案路徑字樣，assert 路徑存在——這是最便宜的長期防線，未來刪檔重構會自動提醒改文件。

---

## P1：短期（一到兩個 PR）

### 8. 對外安全：session 無法撤銷、無稽核、僅單一共用密碼

- `web/auth.py:73-90` 的 token 是 `<exp>.<HMAC(exp)>`，**簽章訊息只有到期時間**——不含 session id、不含密碼指紋。加上 `server.py:114-121` 每個請求都滑動續期，結果是：改密碼不會登出任何人；無法撤銷單一 session；cookie 外洩即永久後門且無絕對存活上限。
- `web/auth.py` 全檔對登入成功/失敗**零 log**。暴力破解與異常登入在 journald 完全無痕跡。
- 限流 `_FAILS` 是 in-memory（`auth.py:119`），而 service 是 `Restart=always`，重啟即清零。
- `deploy/nginx.conf:2` 註記邊緣不再設 Basic Auth，`docker-compose.yml` 也沒有 Zero Trust——整個平台（含研報原始檔下載端點）僅靠一組共用帳密。

**做法**：(a) 既然已用 Cloudflare Tunnel，直接加一層 **Cloudflare Access**（Email OTP／IdP），零成本同時解掉「共用密碼＋無 MFA＋無稽核」；(b) token 改 `<ver>.<iat>.<exp>.<sig>`，sig 訊息納入密碼雜湊前綴 + 一個 `SESSION_EPOCH` 環境變數當全員登出開關，並加絕對上限；(c) 登入 ok/fail/locked 各記一行 WARNING。

附帶：`REPORT_MARK_TRUSTED_PROXY_CIDRS` 寫死 Docker 閘道 IP（`.env.example:19` 自承會隨 WSL 重開變動）。IP 一漂移就是「所有外網登入被擋」＋「全員共用同一限流 key，任何人 5 次失敗鎖住全公司」。建議改用 nginx 注入共享祕密 header 判定可信代理，不依賴 IP。

### 9. 連線池與查詢無任何上界

`app/services/db.py:18` 只有 `pool_pre_ping=True`：無 `pool_size` / `max_overflow` / `pool_recycle`（預設上限 15 條），且 `statement_timeout` / `lock_timeout` / `idle_in_transaction_session_timeout` 全 repo 零設定。

後果：研報生成的 fanout（`REPORT_FANOUT_CONCURRENCY=3`）加上 SSE 長串流期間持有的 session，3-5 個併發使用者即可打滿連線池，之後請求在 30 秒後 500；而一個失控查詢（見第 11 項）可無上限佔用連線，客戶端斷線也不會停。另外 DB 密碼硬編在 `db.py:13` 的預設值（`postgres:postgres`，超級使用者），`.env` 裡沒有覆寫，等於生產跑的就是它。

**做法**：顯式設池參數並與 PG `max_connections` 對齊；`connect_args` 加 `statement_timeout=15000`、`idle_in_transaction_session_timeout=60000`；DB 密碼移出程式碼、改用非 superuser 角色。

### 10. 併發限制假設單 process，但沒有守門

`ask.py:47` 的 `Semaphore(3)` 與 `report.py:45` 的 `Semaphore(1)` 都是 process-local。目前 ExecStart 沒有 `--workers` 所以有效，但這個假設只寫在註解裡。任何人為了效能加 `--workers 2`，併發上限立刻翻倍、BGE-M3 記憶體也翻倍（模型 per-process 常駐）。

另外排隊無上限、無逾時，且 `async with semaphore` 在 generator 內部——回應已送出 200 才開始排隊，第 4 個之後的使用者看到的是「連線建立但永遠沒有 token」，無法區分排隊與卡死。

**做法**：lifespan 偵測多 worker 就 `RuntimeError` fail-closed；acquire 改帶 `wait_for(..., 5)`，滿載回 429 + `Retry-After`，或先 yield 一個 `queued{position}` 事件。

### 11. 雷達與 overview 的查詢在現規模下是全表掃描

| 問題 | 證據 | 影響 |
|---|---|---|
| `:code = ANY(r.stock_targets)` 用不到 GIN 索引 | `radar/queries.py:151,158,161,204`、`overview.py:273` | PG 的 `array_ops` GIN 只支援 `@> / && / =`，`x = ANY(arr)` **不可索引**。`_COVERAGE_SQL` 有 4 個這樣的子查詢 ⇒ **一次雷達請求 = 4 次 research_report 全掃**。對照 `store.py:171` 的 `@> ARRAY[:it]::text[]` 寫法才是對的 |
| `_catalog_cte` 全表 unnest 且跑兩次 | `radar/queries.py:226-272,294-307`，`:277` 傳入全部 MARKETS ⇒ 條件恆真 | 每次打開雷達選單做兩次數萬列 hash aggregate（count + 分頁各一次） |
| overview 一題 7 次掃同一母體 | `overview.py:288-333` | 若 where 含 `company_name ILIKE '%…%'`（無 trgm 索引）就是 7 次 seq scan |
| `research_report.report_date / source / report_type` 全無索引 | 索引只有 `market` + 3 個 GIN | 瀏覽分頁每次全表排序 |
| `research_report` 從不 ANALYZE | `ingest_all.py:151` 等三處只 ANALYZE chunk 表 | planner 統計長期失真 |

**做法**：(a) 全部 `= ANY(stock_targets)` 改寫成 `stock_targets @> ARRAY[:code]::text[]`（純改寫法即讓現有索引生效，最快）；(b) 加 `report_date`、`source`、`report_type` 索引與 `company_name` 的 trgm 索引；(c) 刪 4 個確定多餘的索引（`idx_report_section_run`、`idx_report_takeaway_report` 與各自的 UNIQUE 完全相同；`idx_report_signal_report` 被 UNIQUE 前綴覆蓋；`idx_report_signal_instr_broker_date` 因券商過濾一律走 `COALESCE(...)` 跨表運算式而從未被使用）；(d) 雷達目錄改物化視圖，隨 signals 批次刷新。

### 12. lexical 路會隨語料成長靜默降低召回（最需要注意的一項）

`store.py:297` 的 `content_norm LIKE '%term%'`，term 來自 `retrieval.py:35` 的切詞。**pg_trgm 對 `%x%` 需要至少 3 字元才能抽出完整 trigram**——「台積」「鴻海」「ai」「eps」都是 2 字元 ⇒ 抽不出 trigram ⇒ 無法使用 `idx_report_chunk_content_trgm` ⇒ 在 70 萬列上 seq scan。

更關鍵的是 `store.py:302-308` 的 `LIMIT :cap`（2000/8000）**沒有 ORDER BY**：取哪 2000 列由 heap 物理順序決定。熱門詞在 70 萬 chunks 下命中數遠超 cap ⇒ 字面路召回被任意截斷、且結果隨 VACUUM 變動（同一查詢不同時間結果不同），`retrieval.py:8` 宣稱的「字面全中一律放行」硬保證在大語料下名存實亡。

**做法**：≥3 字元才走 LIKE，1-2 字元 CJK term 改走 tsvector/bigram 或只留語意路；cap 截斷前加穩定排序鍵，並在回應標記「字面路已截斷」（目前是靜默的）。

> **2026-07-29 複驗：診斷對，但上面這個處方有害，不要照做。**
> 「≥3 字元才走 LIKE」在 `retrieval.py` 的實際控制流下會造成兩種回退：
> (a) **單一 2 字元 term 的查詢**（實測「鴻海」「輝達」「財報」切詞後就只有一個 term）
> 過濾完 `patterns` 為空 ⇒ 字面路整條消失、只剩 dense——而那正是字面精確比對最有價值的
> 場景（公司簡稱、代碼）；(b) **混合查詢**丟掉短詞後 AND 條件變少 ⇒ LIKE 更不具選擇性
> ⇒ 命中列暴增 ⇒ **更早**撞上 `LIMIT :cap`，反而放大本節自己指出的截斷問題。
> 另有一處事實錯誤：**`eps` 是 3 字元**，`%eps%` 抽得出完整 trigram、本來就可索引。
>
> 正確順序是：先做本節後半的「cap 截斷可觀測化」拿到實據，確認短詞 seq scan 真是延遲
> 主因後，再加**獨立的 bigram 召回路**（讓短詞走新路、長詞維持 trgm，任何 term 都不被丟棄）。
> 任何動到 `extract_terms`／pattern 規則的改動都要用 `scripts/eval_retrieval.py` 前後比對
> ——它刻意直呼 `hybrid_search`，看得到這層改動。

### 13. 無 migration 工具的三個具體破口

28 條 `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`（`qa_log` 一張表就有 13 條，該表 62% 的欄位是補丁）。人工紀律目前有被遵守，但有三類變更**在既有 DB 上永遠補不上**，而 CI 沒有 DB（`ci.yml:35` 註明「測試不需要 DB」）所以新建 DB 上測試永遠綠：

- **CHECK 清單變更**：`CREATE TABLE IF NOT EXISTS` 對既有表是 no-op，也沒有對應的 `DROP/ADD CONSTRAINT`。日後在 `report_run.status` 加一個新狀態，新 DB 正常、生產 DB 仍是舊 CHECK ⇒ `advance_status` 500。
- **`content_norm` 的 GENERATED 表達式變更**：同樣是 no-op，PG 也不允許直接改。`schema.sql:67` 要求它與 `norm_for_match()` 逐字等價，但**沒有任何自動化在驗證**。一旦漂移，字面路靜默降低召回——不報錯、不 500，沒人會發現。
- **無版本記錄**：`schema_version / migrations / alembic` 零命中，無法回答「這個 DB 套過哪一版」；部署流程也不保證會跑 `make schema`（`sync_new_reports.sh` 完全不碰）。加了欄位卻忘記套用的部署會在 runtime 才炸。

**做法**：(a) 立即加一個 CI job 起 `pgvector/pgvector:pg16`，把 `schema.sql` 跑兩次驗冪等，並斷言「DB 實際的 `content_norm` 表達式 == `norm_for_match` 參考實作」＋「CHECK 約束清單 == schema.sql 宣告」——這一步就關掉前兩個破口；(b) 中期引入 `schema_migrations` 表 + `db/migrations/`，`make schema` 退位為 `make migrate`，web 啟動時版本不符即 fail-fast。

另注意 `make schema` 在生產上不安全：`schema.sql:43` 的 HNSW `CREATE INDEX`（非 CONCURRENTLY）與 `:70` 的 `ADD COLUMN ... GENERATED STORED` 會在 70 萬列表上取 ACCESS EXCLUSIVE lock。

### 14. eval 是唯一的品質防線，但目前擋不住任何回歸

> **2026-07-30 時點註記**：本節的「沒有比較器」已修（`scripts/eval_compare.py` ＋ `make eval-compare`，讀兩份結果 JSON 逐指標算 delta、劣化超過容忍值即非零退出；三種結果形狀通吃）。**題集刻意還沒擴**——沒有比較器時擴題集只是讓人眼要比的數字變多，而擴題集要跑 LLM 才有 ground truth，成本在算力不在程式。另外本節有兩處數字要更正：
>
> - **「四份 baseline 的 `thresholds_pass` 全是 `false`」不精確**：是**三份 RAGAS** baseline 全 false，第四份 `report-m1b.json` 是研報 eval，**根本沒有 `thresholds_pass` 這個鍵**（它只有 `sufficient_n`，判的是有效題數而非品質門檻）。現在共五份，第五份 `baseline-2026-07-29.json` 也是 RAGAS、同樣 false。
> - **卡住的不是 `answer_relevancy`**：AR 門檻已由 PR #137 依實測分離度從 0.85 校準到 **0.55** 並自此通過（0.646）。以現行門檻重算，四份 RAGAS baseline 的未達標項**一律只有 `context_precision`**（0.679 / 0.723 / 0.769 / 0.777，門檻 0.8）——它才是從 M0 起就沒綠過的那一項。`5c15a47` 的 commit message 與本報告都寫成「唯一卡住的是 AR」，兩處都不準。
> - 也要提醒：`baseline-2026-07-29` 是**第一次乾淨量測**（errors=0），先前幾份有 judge 逾時掉題、掉的題不入均值，所以 CP 從 0.777「掉到」0.679 有一部分是先前虛高。
>
> 「接進 CI」則是**刻意不做**：跑一輪 RAGAS 要 spawn `claude` CLI，會與每 3 小時的 `report-mark-sync.timer` 搶同一個 CLI（`scripts/_claude_lock.py` 那把 flock 刻意不含 `llm.py`，而 eval 走 `llm.py`）。

- golden set 過小且三套互不相干：`eval/queryset.json` 14 案、`ragas_questions.json` 8 題、`report_questions.json` 10 題。8 題的均值對 faithfulness 這類指標沒有統計力，±0.05 全在噪音內。
- **門檻從未綠過等於沒有門檻**：四份 baseline 的 `thresholds_pass` 全是 `false`。
- **沒有比較器**：`before.json` / `after.json` / `baselines/*.json` 都在版控裡，但沒有任何腳本讀兩份做 diff。`eval_retrieval.py` 的用法就是「前後各跑一次、人眼看表」。
- baseline 停在 `m4-corpus-qa`，而 M5-M10（agentic QA、分節研報、faithfulness、Typst、i18n）都已上線。

**做法**：queryset 擴到 40-60 案、ragas 到 30 題；寫 `scripts/eval_compare.py --baseline X --candidate Y --tolerance 0.03` 回非零退出；門檻改成**相對 baseline** 而非絕對值；加 nightly 或 `workflow_dispatch` 的 self-hosted job，並要求每個 milestone 合併時更新 baseline。

### 15. CI 的三個洞是同一個洞

- `vite build` **從不在 CI 跑**（frontend job 只跑 `tsc --noEmit` + vitest）。
- 因此 `tests/test_spa_serving.py:26` 的 `pytest.skip("frontend/dist not built")` 在 CI **永遠 skip**。而 `spa.py` 的 docstring 自己說明「`/app/assets` Mount 必須註冊在 catch-all 之前，否則每個 Vite 雜湊資產都落到 catch-all、SPA 整頁白掉」——最貴的回歸剛好落在 skip 的那一側。
- `frontend/dist` 的產生**完全沒有自動化也沒進 README**：Makefile 無 build target、CI 無、systemd 無。缺席時 `spa.py` 直接 503 整站。唯一提到它的是兩份看起來已過期的設計文件。

**做法**：`Makefile` 加 `build-web`；CI frontend job 加 `npm run build` 並把 dist 當 artifact 傳給 backend job；skip 改成「dist 不存在就 fail，除非顯式 `SKIP_SPA_TESTS=1`」；README 補上部署步驟。一次解三個。

### 16. Python 端零 lint、零型別檢查；前端有 eslint 但 CI 不跑

`.ruff_cache/0.15.18` 與 `0.15.20` 兩個目錄存在——有人在本機跑過 ruff，只是沒進 `pyproject.toml`、沒進 CI、沒有 pre-commit。14,745 行 Python（services + scripts）沒有任何自動化守門，抓不到未使用 import、未定義名稱（`F821`，在這種大量動態 patch 的程式碼裡是真風險）。滿地 `# noqa: E402` 更說明有人假設規則存在卻沒人執行。

回傳型別標註覆蓋率抽樣：`retrieval_pipeline.py` **1/7**、`agentic_qa.py` 3/7、`report.py` 8/16。

前端 `eslint.config.js` 存在、`package.json` 有 `lint` script、devDeps 有 `eslint-plugin-react-hooks`，**CI 沒有這一步**——`exhaustive-deps` 失效，而這裡有 11 個自製 hook。`frontend/e2e/` 的 5 個 spec 576 行也從不執行（`playwright.config.ts` 沒有 `webServer`），是零價值資產。

**做法**：`ruff check` + `ruff format --check` 進 CI（先用 `--select E,F,I` 低噪音集）；frontend job 加 `npm run lint`；playwright 加 `webServer` 與獨立 job（可先 `continue-on-error`）。

### 17. 批次可觀測性有斷層（有停更 8 天的實際案例）

`/api/progress` 只認得 tagging 與 ingest 兩條（靠數 `data/tags/*.json` 與 tail log），`summaries` / `takeaways` / `signals` 三支**沒有即時進度**。`monitor.py:88` 的註解自己記載「2026-07 實測 takeaway 停更 8 天、signal 停更 12 天」——覆蓋率量測是事故後才加的。

而 `/api/progress` 前端每 5 秒輪詢、DB 快照 TTL 也是 5 秒 ⇒ 幾乎每次 miss，每 5 秒打 8 條 count/group-by；`_proc_alive` 每次呼叫都 **glob 整個 `/proc` 並讀 cmdline**，一次輪詢掃四遍。監控頁開著就是持續背景負載。

**做法**：統一 heartbeat 檔 `data/batch_<name>.progress.json`（`{script, pid, done, total, ok, rejected, fail, updated_at}`），批次每 N 筆覆寫；monitor 用一支通用 parser 取代兩套 log-tail/`/proc` hack，對「updated_at 逾時但 PID 還在」與「N 天沒跑」各出一個警示，接上既有的 `report-mark-alert@.service`。TTL 調到 15-30 秒。

### 18. 前後端契約是人工鏡像，且已經漂移了

契約定義散在三處無單一來源：SSE 框格式（`deps.py:52` ↔ `readSSE.ts:5`）、ask 事件（`answer.py` 的 yield ↔ `askSchemas.ts` 手寫 zod）、`/api/progress`（無 `response_model` ↔ `progressSchema.ts` 手寫 zod）、radar/reading（pydantic ↔ 341+141 行「逐字鏡像」的 zod）。`openapi` 在 scripts 與 package.json 零命中。

**已發生的斷裂（已修，留作範例）**：commit `3f926bf` 為了解決「摘錄停更 8 天沒人發現」而在 `/api/progress` 加了 `takeaway` / `signal` 覆蓋率，但只改了 5 個後端檔；`progressSchema.ts` 沒有這兩個欄位，zod 預設 strip 未知欄位 ⇒ **資料被靜默丟棄，監控頁上什麼都沒多出來**。修復動機達成了一半，失敗模式跟原 bug 一模一樣。

> **2026-07-29 時點註記（複驗於 `9a93a15`）**：這個實例已由 PR #131（`603a572`）修掉——`frontend/src/features/monitor/progressSchema.ts:56-58` 補上 `takeaway` / `signal` / `evaluation`（刻意用 `optional()`，讓滾動部署期間缺鍵不會整頁 parse 失敗），`progressSchema.test.ts:52-65` 有回歸測試釘住。`603a572` 不在本報告快照的 `3ce718e` 樹裡，所以上一段在寫下時是對的，是併進 main 後才過時。**但這只修掉一個實例：契約仍無單一來源，下一次加欄位還是會這樣**，本節其餘論點全數維持（下方「做法」只有 (a) 已隨之完成，(b)(c)(d) 都還沒做）。

`response_model` 覆蓋率也不均：radar 4、reading 3、search 2，而 `ask / report / monitor / qa_history / report_file` **全部 0**。

**做法**：(a) 先補 `progressSchema.ts` 把 P4 的價值真正交付；(b) SSE 加「後端 golden fixture → 前端 zod 解析」的雙向測試（後端把每種事件的真實 payload dump 成 JSON 進 repo，前端 vitest 讀同一份），比 codegen 便宜且擋得住這類漂移；(c) 補齊 `response_model` 後用 `openapi-typescript` 產型別並 `git diff --exit-code`；(d) 前端 zod `safeParse` 失敗不要靜默 `return null`，至少 `console.warn`。

---

## P2：結構性重構（分批進行，每步可獨立驗證）

### 1. `answer.py` 1900 行拆解（前三大檔佔服務層 40%）

`answer.py` / `report_writer.py` / `report.py` = 4,522 行，佔 `app/services/` 的 40%。`answer.py` 至少有 5 種職責，其中最刺眼的是 **L803-1293 那 491 行 DB 層**（23 個 `text()` SQL，比 `store.py` 的 14 個還多）。後果：`web/routers/qa_history.py:15` 直接從 `answer` import CRUD，意思是「列一個對話清單」必須載入整個 RAG 引擎（含 embed、rerank、LLM）。

建議拆解順序（風險由低到高）：

| 步驟 | 內容 | 效果 |
|---|---|---|
| 1 | `app/repositories/qa_log.py` ← `answer.py:803-1293` | 純搬移、零邏輯改動，一步砍掉 26% |
| 2 | `app/services/context.py` ← `Source` / `SelectedReport` / `select_reports` / `build_context`（約 430 行） | 連帶解掉循環依賴（見下） |
| 3 | `selection_mmr.py` ← `answer.py:541-693` | |
| 4 | `app/prompts/qa.py` ← `answer.py:101-262` | |

`report_writer.py` 同理：`:720-980`（261 行 DB CRUD）→ repository、`:981-1277`（297 行 prompt）→ `prompts/`，1787 行可降到約 900。

### 2. 解開 `answer` ↔ `retrieval_pipeline` 的真循環依賴

`retrieval_pipeline.py:16` 頂層 import `answer.Source / build_context`，而 `answer.py:1654` 用函式內 import 反向取回。服務層共 **9 處函式內 import** 都是為了掩蓋循環（`answer.py:409` 的註解甚至寫著「契約：不動頂部 import 區（M5/M6 平行）」——把里程碑並行開發的臨時規約固化成了永久架構債）。副作用是 import 錯誤會延遲到 request 執行時才炸。

抽出 `context.py` 後 9 處可全數回到頂層。

### 3. 用型別消滅「同一欄位兩種尺度」與「從渲染文字反解析」

檢索結果目前以無元素型別的 `tuple[list, str]` 傳遞，候選是匿名的 `(tier, fused, row)` 三元組。這直接造成兩個實質問題：

- P0 第 6 項的量綱錯配（`rerank_scored` 原地把第 2 項換成 sigmoid 分）。
- `agentic_qa.merge_retrievals`（`:38-125`，88 行）用 regex `^\[\d+\] 報告：` 從**已渲染的 prompt 字串**切回 blocks、去重、再用字串 replace 重新編號。這是全系統第三套合併實作（另兩套在 chunk 級與 report 級），且與 `build_context` 的中文字面量隱式硬耦合——`build_context` 一改頭部字樣，`_batch_valid` 就靜默回 False，整批被丟掉，表現為「agentic 多輪檢索靜默退化成單輪」而不報錯。

建議引入：

```python
@dataclass(frozen=True)
class Candidate:
    tier: int
    fused: float
    rerank_score: float | None   # None = 未重排，從根上消滅雙尺度
    row: ChunkRow

@dataclass(frozen=True)
class RetrievalResult:
    sources: list[Source]
    selected: list[SelectedReport]   # agentic 改在此層合併，regex 那 88 行可刪
    context: str
    degraded: bool = False
    gate_scores: dict[str, float] | None = None   # 隨結果走，不會漏傳
```

同時把 `retrieve_context` 與 `retrieve_context_multi` 合併為單一進入點（差異用參數表達）。目前兩者的 docstring 聲稱 `len<=1` 等價，實際在 `gate_scores`、`build_context` 是否在 event loop 內執行、retier/MMR 三項上都不等價。

### 4. LLM 呼叫層：補 `complete()` 與 `wall_timeout`

`llm.py` 只導出串流 API，於是「drain 成字串」這段在 **7 處手抄**（`scope_router` ×2、`query_planner`、`faithfulness`、`followups`、`agentic_qa`、`report_writer`），其中只有 1 處有 wall-clock 保護。

更嚴重的是 `timeout` 是 per-attempt 語意（`retries=2` 預設 + 退避）⇒ **真實牆鐘上界是 `3T + 4.5s`**。`report_writer.py:1336` 的註解正是這個問題的事故報告（「最壞 1050s」），但修法只套用在逐節路徑。其餘 8 個呼叫端的實際上界：

| 呼叫端 | 名義 | 實際上界 | wall guard |
|---|---|---|---|
| `answer.py:1820`（問答主答案） | 120（硬編碼，`config.py` 沒有 `ASK_TIMEOUT`） | **~365s** | 無 |
| `report.py:774` | 600 | **~1805s** | 無 |
| `faithfulness.py:161` | 60 | ~185s | 無 |
| `scope_router.py:176,252` | 20 | ~65s | 無 |

**做法**：`llm.py` 增 `complete(prompt, *, model, timeout, retries, wall_timeout) -> str`，內部負責 drain 與 `asyncio.timeout`；`timeout` 更名 `attempt_timeout` 讓語意自明；所有呼叫端顯式傳 `wall_timeout`；加一條測試斷言每個生產呼叫點都顯式帶 `timeout=` 與 `retries=`。

另有一個相關的資源洩漏：`retrieve_for_section` 的檢索額度是 60s（`REPORT_SECTION_WALL × 0.25`），但內傳的 `rerank_timeout` 是 **180s**，而 `_rerank_stage` 用 `asyncio.shield` 逾時不取消。結果 `wait_for` 在 60s 砍掉檢索，rerank 執行緒卻繼續燒到 180s 並**持續持有容量為 1 的 semaphore** ⇒ 後續章節排隊等一個已被放棄的工作 ⇒ 連鎖降級。建議讓 budget 沿呼叫鏈傳遞（`rerank_timeout = min(設定值, budget * 0.6)`），取代每層各拍一個數字。

### 5. Prompt 集中化

系統提示常數散在 **19 處、9 個模組**。研報 prompt 有 4 份高度重疊變體共約 570 行（中/英 × single-shot/sectioned），`report_writer.py:1039` 的註解坦承「規則語意與中文版逐條對應…不得順手改動任何契約」——這句話本身就是「有四份必須手動同步的副本」的自白。prompt-injection 防禦句在 5 處各寫了措辭不同的版本。

在地化策略也有 4 種並存（尾部覆寫指令／整份平行 prompt／行內 `if en:`／每訊息一個 helper），而為第 4 種寫的統一 helper `locale.pick` **零呼叫端**。

**做法**：建 `app/prompts/` 套件，`blocks.py` 提供 `chart_spec_rule(locale)` / `kpi_spec_rule(locale)` / `injection_guard(locale)` / `citation_rule(locale, scheme)` 等可組裝片段，四份 prompt 改為組裝。好處：prompt 變更的 diff 不再混在邏輯 diff 裡；injection guard 有單一來源可寫測試斷言「每個對外 system prompt 都含 guard」；支撐 prompt 版本化與 A/B。同時選定**一種**在地化策略（建議「整份平行 prompt」，因為 `report_writer.py:1029` 記錄了策略 1 在 8 節研報中實測失守的生產事故），其餘統一遷移。

### 6. 例外體系與失敗策略顯性化

- `LLMUnavailableError` 定義了卻**全庫零捕捉**。fail-open 端用 `except Exception` 一律吞掉；主答案路徑不捕捉 ⇒ 冒到 router 的泛用 handler ⇒ 使用者看到「問答服務發生錯誤」，且**該輪 qa_log 完全不落庫**（`_log_qa` 在串流之後）。**API 529 過載與程式 bug 在監控上完全無法區分**。
- 48 個 `except Exception` + 3 個 `except BaseException`，同一份程式碼有至少 5 種失敗語意（回原物件／回 degraded 物件／回 None／回空／fail-closed），辨識方式只有讀 docstring。其中 `retrieval_pipeline.py:78` 用**物件同一性**（`is`）判定 rerank 是否套用，而 `rerank.py:139` 的註解寫著「改動此同一性即默默破壞降級偵測」——任何無害的 `list(...)` 包裝都會靜默改變研報路徑的降級行為。
- 6 個自訂例外型別繼承基底各不相同，沒有共同根，因此 web 層無法把「已知領域錯誤」與「未預期 bug」分流。

**做法**：`app/errors.py` 定義 `ReportMarkError` 根 + `LLMError / RetrievalError / EvidenceError / StateError`；引入 `Outcome[T]` / `Degraded[T]`（帶 `reason`）取代 `is` 身分訊號；web 層依型別分流 HTTP status 與 SSE error code；LLM 失敗時仍寫一筆 `qa_log(status="llm_unavailable")`；所有 fail-open 帶結構化 log + counter（目前無法回答「上週有多少 % 的問答走了降級路徑」）。並寫一頁 `docs/failure_policy.md`——這些決策目前只存在於 40 幾條中文註解裡。

### 7. 設定與資料契約收尾

- `config.py` 114 個扁平鍵、按里程碑分區（`report_*` 前綴 38 個鍵，語意上分屬 6 個子系統）。建議改嵌套 dataclass。
- `answer.py:77-145` 有 28 個、`report.py:40-76` 有 20 個模組級 `XXX = _S.xxx` alias，在 import time 求值並被當函式預設參數 ⇒ **測試無法用 monkeypatch env 改變行為**。建議函式簽名改 `None` 預設、函式體內取值，或加 `reset_settings()`。
- `config.py` 只有一處值驗證。114 個鍵若拿到非數字字串會在 import time 拋 ValueError 導致**整個 app 起不來且無提示**；語意約束（如 `REPORT_FINALIZE_RESERVE < REPORT_DRAFT_BUDGET`，`config.py:214-219` 用 6 行註解說明）完全沒有程式檢查。建議加 `_validate(settings)` 並在啟動時 log 有效設定摘要。
- 4 個漏網 env：`retrieval_pipeline.py:26,28`（`REPORT_MARK_RERANK_*`，命名前綴也與慣例不一致，且那個 30s 後備值正是「M1b 基準線 10/10 逾時」事故的原始值）、`followups.py:14,15`（模型 ID 寫死日期後綴，與 config 不一致）。
- 市場詞彙表分散在 `tagging.py`（被 6 個不相干模組依賴，名不符實）與 `overview.py:37`（第二份 `_MARKET_SYNONYMS`）。新增一個市場要改 4 處，漏一處是靜默行為差異。建議抽 `taxonomy.py`。
- 死碼：`scope_router.route_question`（生產零呼叫，只有測試用；而 `answer.py:1617,1720` 因缺公開入口去 import 私有的 `_decision`）、`faithfulness.faithfulness()`（只被 eval 用，應移出生產模組）。跨模組 import 私有符號還有 `typst_render.py:28,34`。

### 8. `ChunkRow` 的位置契約：守門範圍太窄

`rows.py` 34 行裡有 12 行是事故記錄（`eval_retrieval.py` 寫死 `_RID, _CONTENT = 1, 14`，插入 `file_hash` 後無聲指向錯欄位——沒有例外，只有變成垃圾的評測分數）。目前已用 `_make()` 與 `_fields.index()` 緩解，`tests/test_rows.py` 也有寬度守門（值得保留）。

但範圍太窄：`store._meta_columns` 是一段 f-string SQL，與 17 個欄位靠**人工註解**對齊，沒有測試斷言欄數與欄序；而其他地方仍是裸 `row[i]`——`radar/types.py:179-195` 有 **17 個連續 `row[0]`…`row[16]`**（且 `row[14/15/16]` 正是 docstring 警告的那種寫死尾端索引）、`report_writer.py:941-948`、`report.py:423`、`answer.py:803,1113`。

**做法**：(1) 加一個 5 行守護測試比對 `_meta_columns` 的欄數與尾段名稱 vs `_fields`（把 12 行註解變成 CI 擋牆）；(2) `radar/types.py` 引入 `SignalRow(NamedTuple)`；(3) 根本解是所有 `text()` 改用 `.mappings()` 具名取值，位置契約整體消失、`rows.py` 那 12 行警告可以刪掉。

> **2026-07-29 複驗：做法 (1) 已經存在，不要重做。**「沒有測試斷言欄數與欄序」是錯的
> ——`tests/test_rows.py` 的 `MetaColumnsAlignmentTests` 有三題把欄序與欄數都釘死了
> （含 `cols[2:] == ChunkRow._fields[2:-1]` 與 `len(cols) + 1 == len(_fields)`），
> 且比這裡建議的 5 行版本更完整。該測試隨 2026-07-17 的閱讀頁 PR 進來，**比本報告早 11 天**
> ——也就是本節寫下當天就已經錯了。本文「值得肯定」那節（`tests/test_rows.py:56-82`
> 把 `_meta_columns` 欄序釘死的守門）才是對的，兩處自相矛盾。
> 順帶更正：`_meta_columns` 是 **16** 欄不是 17，第 17 欄 `distance` 由各查詢自行 append。
> 做法 (2)(3) 仍然成立。

---

## P3：資料完整性與 schema 細節

> **2026-07-30 時點註記**：13 條逐條對生產複驗後修了 8 條，5 條刻意沒做。**幾個數字要更正**：
>
> - **NULL `embedding` 目前是 0 列**（實測 568,349 chunk）。「檢索主路會 500」的缺陷屬實（字面路 CTE 確實沒有 `IS NOT NULL`，`float(None)` 會 TypeError），但它是**潛在而非現行**故障。已補兩層守門並記數。
> - **`is_research` 14,674 列全部是 `true`**，零 NULL 零 false。所以三種過濾寫法今天結果完全相同——本節說的「靠 ingest 閘門巧合一致」是對的，但要知道那意味著修正純屬防禦。已收斂成 `NOT NULL DEFAULT true` 並統一唯一那處 `= true`。
> - **孤兒是真的，有現場證據**：14 列 `report_doc` 有 **2 列**（14%）孤兒；`report_run` 與 `report_rendition` 皆 0。已改為連刪 ＋ 清磁碟 PDF。
> - **`ef_search` 那條要更正**：檢索主路（`store.search_chunks_meta`）**已經**有 `ef_search` 與 `iterative_scan`。本節指的沒設的是 `search_chunks`，而那支的唯一消費端是 `scripts/search.py` 這個 CLI，不在服務路徑上。真正缺 `iterative_scan` 的是閱讀頁 `fetch_similar`（已補）。
> - **版本風險屬實但已不成立於現況**：實測 PG 16.14 / pgvector **0.8.2**，`iterative_scan` 可用。Makefile 確實只釘 `pgvector/pgvector:pg16`（沒釘 pgvector 版本），已補 `db.assert_pgvector_version()` 於 lifespan fail-closed。
> - **`advance_status` 屬實，而且比本節寫的更嚴重**：除了 check-then-update，`target = status or current` 讓 `status=''`（只更新附帶欄位）把讀到的舊狀態**寫回去**，併發下等於靜默倒退狀態機。
> - **「`raw_payload` 無 TTL」的表不是本節暗示的那個**：本 repo 沒有 raw 層（那是 FinDB 的 `raw.market_payload`）；`raw_payload` 是 `report_signal` 與 `report_takeaway` 的欄位，且 takeaway 每列存的是**自己那一條** item 而非整份回應，所以「同一份 payload 重複存 3-5 次」不準。
>
> **刻意沒做的 5 條**，都是「需要對 57 萬列做 DDL 或全量重跑」或「需要先決定資料遷移」，不在「只改 repo 內檔案」的範圍：HNSW 建構參數與 `halfvec`（要重建索引）、embedding 改二進位寫入（要全量重跑）、`followups`/`stages` 改 `text[]` 與 `eps_estimates` 拆子表（要遷移既有資料）、`text_sha256` 上移與閱讀頁 `_DOC_SQL` 去掉 `full_text`（要新增欄位＋回填批次）、ingest 的「tag 較新⇒更新 metadata」路徑。
>
> 另新增 `make db-audit`（`scripts/db_audit.py`）作為本節「無完整性檢查」那條的答案：九條唯讀 SQL 斷言 ＋ `content_norm` 取樣比對，首跑即抓到上述 2 列孤兒。

| 項目 | 問題 | 建議 |
|---|---|---|
| 孤兒資料 | `delete_conversation`（`answer.py:1196`）只刪 `qa_log`；`report_doc` / `report_run` / `report_rendition` 列與磁碟 PDF **永久孤兒**，無清理路徑。三者都刻意無 FK | `report_rendition.report_id → report_doc(id) CASCADE`；刪除改為單一交易內連刪並回傳 pdf_path 供刪檔 |
| 母體不一致 | `is_research` 可 NULL，三種過濾寫法並存（`= true` / `IS NOT FALSE` / 完全不過濾）⇒ 檢索頁、總覽題、閱讀頁的母體不同。目前靠 ingest 閘門巧合一致 | 統一語意並加 NOT NULL + DEFAULT |
| 檢索主路會 500 | `embedding` 可 NULL，但 `retrieval.py:120` 直接 `float(row.distance)`。字面路召回到 embedding 為 NULL 的 chunk ⇒ `float(None)` TypeError ⇒ 整個查詢 500（`reading/queries.py:232` 有防護，主路沒有） | 主路加 `embedding IS NOT NULL` |
| 併發狀態機 | `advance_status`（`report_writer.py:817`）docstring 寫「原子推進」，實際是 check-then-update，無 `FOR UPDATE`、UPDATE 也不帶 status 條件 ⇒ 兩個併發呼叫可同時通過守門。對照 `create_run:781` 就正確用了條件式 UPDATE | `UPDATE ... WHERE id = :id AND status = :current` 並檢查 rowcount |
| jsonb 用錯 | `qa_log.followups` / `stages` 存的是純字串陣列（同一份 schema 裡 `report_section.evidence_ids text[]` 才是正解）；`report_signal.eps_estimates` 是重複群組，註解強調「numeric 不可用 float」卻把 EPS 塞進 jsonb ⇒ **精確性保證對 EPS 失效**，且跨券商共識永遠只能在 Python 算 | `followups`/`stages` 改 `text[]`；`eps_estimates` 拆 `report_signal_eps` 子表 |
| 重複儲存 | `report_takeaway.text_sha256` 每報告 3-5 列各存一份同樣的 sha（該掛在 `research_report` 上）；`raw_payload` 無 TTL 永久保留，takeaway 同一份 payload 重複存 3-5 次 | sha 上移；raw_payload 加保留期 |
| 閱讀頁重工 | `reading.py` 骨架端點 docstring 明寫「不含全文」，但 `_DOC_SQL` 仍 `SELECT full_text`，然後對平均 25KB 的文字跑 `clean_extracted` 多個 regex + sha256；`/text` 端點再做一次 | `text_sha256` / `text_chars` 做成 `research_report` 實體欄位，ingest 時算好 |
| 標籤永久凍結 | `ingest_all.py:86` 對已存在的 file_hash 一律 skip；`is_research` / `market` 只在首次入庫寫入 ⇒ 標註模型升級後既有 1.4 萬列的標籤**永久凍結**（`data/tag_failures.log` 有 9,082 行，說明重跑很常見） | 加「tag 較新 ⇒ 更新 metadata」路徑 |
| 無完整性檢查 | `orphan` / `integrity` 在 scripts 與 web 零命中。沒人在查：孤兒 report_doc、NULL embedding、`report_signal.market != research_report.market`、chunk_index 重複、`text_sha256` stale 比例、`content_norm` 表達式漂移 | `make db-audit` 每日跑，接上既有告警管道。`monitor.py:85-115` 對「無聲腐化」的量測思路已是好範例，擴展成一組斷言即可 |
| HNSW 參數 | 建構參數全用預設（`m=16, ef_construction=64`，1024 維 70 萬列下召回偏低）；`ef_search` 三處值不同且 `store.py:113` 的 `search_chunks` 完全沒設（預設 40，`top_k>40` 時靜默截斷）；`reading/queries.py` 有 `ef_search` 但**沒開 `iterative_scan`**，而它的 `report_id <> :rid` 正是需要 iterative_scan 的情境 ⇒ 相似研報靜默召回不足 | 重建索引帶 `ef_construction=200`；統一 ef_search 與 iterative_scan；評估 `halfvec(1024)`（省一半空間，召回損失通常 <1%）；刪除或對齊 `search_chunks` 遺留碼 |
| 版本風險 | `hnsw.iterative_scan` 需要 pgvector ≥ 0.8，但容器 image 未鎖版且無最低版本檢查。若在 0.7 環境重建，`SET LOCAL` 直接 `unrecognized configuration parameter` ⇒ **每次檢索 500** | 鎖 image tag + 啟動時檢查 `extversion` |
| embedding 寫入 | 走文字字面（1024 個 `%.7f` ≈ 10KB/chunk vs 二進位 4KB），70 萬 chunks 全量多傳約 4GB 並讓 PG 逐字元 parse | `pgvector.asyncpg.register_vector` 送二進位；全量重建用 `COPY ... FORMAT BINARY` |
| 執行緒安全 | `embed.py:9` 的 Lock **只保護建構**，`model.encode` 是無鎖的共享狀態呼叫，而所有呼叫端走 `asyncio.to_thread`（預設池 `min(32, cpu+4)` 條）⇒ 多執行緒同時 encode 同一個 BGE-M3 實例。且無 embed 併發上限、無 `torch.set_num_threads`（零命中）⇒ CPU-only 推論嚴重超額訂閱 | `embed_texts` 加 semaphore 或改用單執行緒 executor；啟動設 `torch.set_num_threads` 與 `TOKENIZERS_PARALLELISM=false`。（正面：所有呼叫點都包了 `to_thread`，沒有阻塞 event loop，這點做對了） |

---

## P3：測試與工程流程細節

- **零測試的關鍵模組**：`extract.py`（92 行，PDF/docx 抽字，整條管線源頭）、`embed.py`（53 行，檢索唯一入口）、`tagging.py`（252 行，市場代碼映射是 **ingest 的 gate 判準**，改錯會靜默丟掉整批研報，而 CLAUDE.md 用一整段強調要對齊 findb 卻沒有一條 assert）、`chunk.py`（`CHUNK_OVERLAP=80` 的合併邏輯是那個著名陷阱的成因，有 400 樣本實測數據但沒有單元測試鎖住不變量）。
- **pytest 零設定**：`pyproject.toml` 無 `[tool.pytest.ini_options]` ⇒ `sys.path.insert` 樣板在大量測試檔重複 + 滿地 `# noqa: E402`。另外 dev 群組宣告了 `pytest-asyncio>=1.4`，但全 repo **0 個 `pytest.mark.asyncio`、0 處 `asyncio_mode`**（37 個檔用 `unittest.IsolatedAsyncioTestCase`）——死依賴。
- **mock 未集中**：`make_row` / `_FakeSession` / LLM 串流 stub 在各檔各寫一份。`ChunkRow` 是 17 欄 NamedTuple，每次加欄要巡所有自製 builder。建議 `tests/_helpers.py` 集中三件。
- **前端測試缺口**：`readingApi.ts` 是唯一沒測試的 API 層檔案；`useDeleteConversation`（破壞性操作）、`useStats`、`useConversations`、`useReading` 零測試；`TextPane.tsx`（144 行，閱讀頁引文高亮/跳轉的渲染核心，對應「錯了會靜默跳到錯位置」的不變量）零測試。
- **批次樣板重複**：`call_cli` 有 5 份手寫實作，timeout 150/180 不一、只有 `tag_all_cli` 有 retry。而 `app/services/llm.py` 本來就是 CLI 包裝器，且是唯一處理了「CLI 有時只在最終 result 事件回全文」、「僅 529 重試」、「已串流則 timeout fail-open」的地方——這些教訓沒有套到批次。建議抽共用 `call_claude`。
- **失敗清單無法重跑**：三份格式不同的 fail log，要重跑只能 `--reextract`（付一次完整 LLM 成本）。建議統一 JSONL + 各批次加 `--retry-failures`。
- **`generate_summaries` 的 checkpoint 是 `summary IS NULL`**，prompt 改版後無法重跑（只能手動 `UPDATE ... SET summary = NULL`）。建議升級成 `extract_signals` 那種「版本 + text_sha256」的 checkpoint（三套續跑機制中最好的設計）。
- **`ingest_lowio.sh` 的耐久性狀態不可觀測**：被 SIGKILL 就留在 `fsync=off` 且沒有任何地方看得出來。建議 `/healthz` 或 `/api/progress` 加 `SHOW fsync` 檢查。
- **無 log 輪替**：`data/` 下 `summary_failures.log` 與 `tag_failures.log` 各 975KB、每日一份 `sync_run_*.log`、`unit_failures.log` 持續 append，`logrotate` 零命中。而 `monitor.py:173` 每 5 秒對 `data/*.log` 做 glob + stat 排序，檔案越多越慢。
- **缺 secret scan**：`.gitignore` 為擋 `.env` 寫了很細緻的規則（註解還記載「一次 `git add -A` 就會提交上去」的顧慮），值得再加一層 `gitleaks`。
- **無 `--reload` 的維運摩擦**：生產不加 `--reload` 是對的，但每改一行就要重啟並重載 BGE-M3 + reranker（分鐘級），這正是「直接在生產機上改檔然後懶得重啟」的溫床（`docs/production_resilience.md:78` 已記錄過一次 unit 漂移）。建議加 `make serve-dev` 與 `SKIP_WARMUP=1` 捷徑。
- **`web/server.py` 的墓碑**：`:42, :167-169, :176, :205-206` 是一批只為舊測試 import 存在的 re-export，加上 4 段「已拆至 X」的空洞註解。建議改測試 import 路徑並刪除，讓 `server.py` 只剩 lifespan、middleware、router 清單。
- **文件結構**：README（460 行）、CLAUDE.md、AGENTS.md、`docs/WORKFLOW.md`（301 行）在「指令清單、目錄結構、測試怎麼跑、風格慣例」四件事上**四度重複**，沒有單一真相來源——這正是前述 8 處事實錯誤的成因。建議：指令只寫在 `Makefile`（`make help` 已 self-documenting），其餘一律改成「見 `make help`」；架構只寫在 CLAUDE.md。`docs/` 頂層 5 份無日期設計文件（1,480 行）讀起來像現況但已被實作超越，加 front-matter 標狀態或搬 `docs/archive/`；`docs/superpowers/` 89 份 plan/spec 是合理的 append-only 決策記錄，只需一份 README 說明「這些是歷史，不代表現況」。另 `AGENTS.md` 要求所有指令加 `rtk` 前綴是 Codex 專屬設定，混在通用貢獻規範裡會誤導其他 agent 與人類。

---

## 建議執行順序

**本週**（都是低成本、且有事故證據）

1. 移除 `make normalize`（P0-1，一個指令就能摧毀 70 萬列 chunk）
2. `logging.dictConfig`（P0-3，十幾行，解除盲飛）
3. 修 `HOME=%h`（P0-5，現在就有一筆未解決的 sync 失敗）
4. `make db-backup` + daily timer（P0-2）
5. `retrieval_pipeline.py:118` 補 `gate_scores`（P0-6，3 行）
6. 修 8 處文件事實錯誤（P0-7，30 分鐘）
7. `= ANY(stock_targets)` → `@> ARRAY[...]`（P1-11a，純改寫法即讓索引生效）

**本月**

8. `claude` CLI 跨進程鎖（P0-4）
9. Cloudflare Access + 登入稽核 log（P1-8）
10. 連線池參數 + `statement_timeout`（P1-9）
11. CI：frontend build + dist fail-fast + `Makefile build-web`（P1-15，一次解三個洞）
12. ruff 進 CI + 前端 `npm run lint`（P1-16）
13. 補 `progressSchema.ts` 的 takeaway/signal（P1-18a，把已完成的後端能力真正交付）
14. schema 一致性 CI job：`content_norm` 表達式 + CHECK 清單斷言（P1-13a）
15. 索引整理：刪 4 個多餘、加 5 個缺的（P1-11b/c）
16. 批次 heartbeat + 停更告警（P1-17）

**本季**

17. `answer.py` 拆解（先做 repository 那 491 行，風險最低）
18. `llm.complete()` + `wall_timeout` 收斂 7 處呼叫端
19. `RetrievalResult` / `Candidate` 型別化，刪掉 `agentic_qa` 的 regex 反解析
20. eval 比較器 + 擴 golden set + 相對門檻（P1-14）
21. `prompts/` 套件與 blocks 收斂
22. `app/errors.py` + `Outcome[T]`，取代 `is` 身分訊號
23. `schema_migrations` + `db/migrations/`
24. lexical 路 2 字元 term 處理 + cap 截斷可觀測化（唯一會隨語料成長而靜默降低檢索品質的問題）

---

## 值得肯定（改動前務必先讀這些註解）

以下都記錄了真實踩過的坑，而非「做了什麼」的複述。它們是這個 repo 最有價值的資產：

- `reading/anchor.py` 開頭的錨定退讓設計與 400 筆實測數據（1% → 42% → 100%）；`_find_unique` 的「多處出現即放棄」寧缺勿錯策略
- `reading.py` 的 `text_sha256` 驗章降級（不可跳，而非跳到錯位置）
- `store.py` lexical 路用 MATERIALIZED CTE 防 planner 誤走 HNSW；`_BROWSE_SORT` 白名單
- `radar/queries.py` 與 `reading/queries.py` 對 `CAST(:x AS text[])` bind 陷阱的記錄（SQLAlchemy 負向前瞻回溯成短名）
- `deps.py` 的 from-import patch 陷阱分析與 `_with_heartbeat` 的中斷清理鏈（cancel → aclose → 子程序 kill，設計正確）
- `/healthz` 的「資訊不洩漏／抗打／有界」三項取捨
- `report-mark-alert.sh` 的「恆 exit 0」鐵律
- `monitor.py:85-115` 對「無聲腐化」的量測思路，與「先前只量 summary，而 summary 恰好是唯一有排程的」這個教訓
- `tests/test_rows.py:56-82` 把 `_meta_columns` 欄序釘死的守門
- `extract_takeaways.py:200-207` 對「變長列表不可用 upsert」的分析
- `ci.yml` 用 `pull_request` 合併結果跑測試的理由（擋 PR #92/#93 相隔 14 秒併入的語意衝突）

上述問題清單基本都是兩類：**「機制做對了但最後一哩沒接上」** 與 **「單機小語料的假設沒有守門」**。核心設計沒有需要推翻的地方。
