# 架構檢視報告（2026-07）逐條複驗結果

複驗時點：**2026-07-29**，基準 `9a93a15` ＋ 當時工作區的未提交文件修改。對象是
`docs/ARCHITECTURE_REVIEW_2026-07.md` 的全部主張，拆成 119 條逐一查證。

**為什麼要有這份**：那份報告在檔頭自己聲明是快照、且「任何數字都請重數一次」。
複驗結果證實這個聲明不是客套——**有 5 條是錯的**（其中 P1-12c 的處方照做會讓檢索變差），
另有 37 條主張成立但數字、行號或因果推論需要更正。這份文件是那次複驗的紀錄，
讓後續接手的人不必重跑一次。

**方法與限制**：13 個唯讀代理分段查證，每條都要求附「檔案:行號 + 原文片段」，
能實測的實測（例如 P0-1 直接跑 `textnorm` 對真實抽取文字取樣、P0-3 直接查
journald）。**這台機器沒有 psql／docker CLI**，凡是需要 `EXPLAIN`、實際列數、
pgvector 版本才能定論的一律標 `NEEDS_DB` 並附上該下的指令，沒有猜。

| 裁決 | 數量 | 意義 |
|---|---|---|
| CONFIRMED | 66 | 仍屬實 |
| PARTIAL | 37 | 主張成立，但數字／行號／推論有出入 |
| ALREADY_FIXED | 10 | 已被後續 PR 修掉 |
| FALSE | 5 | 報告寫錯 |
| NEEDS_DB | 1 | 需實際連 DB 才能定論 |

---

## 一、報告寫錯的 5 條

**這一節優先讀**：P1-12c 的診斷正確但處方有害，照著做會刪掉最常見查詢的字面召回。

### P1-9a｜研報 fanout（REPORT_FANOUT_CONCURRENCY=3）加上 SSE 長串流期間持有的 session，3-5 個併發使用者即可打滿連線池。

**實測**：沒有任何路徑在 SSE 串流期間持有 session。以 AST 掃描 app/services/{answer,report,report_writer,retrieval_pipeline}.py 與 web/routers/*.py 全部 44 個 `async with SessionFactory()` 區塊，**沒有一個區塊內含 yield，也沒有一個含 LLM 串流呼叫**；四個較長的區塊（report_writer.py:756-826、847-895、920-953、answer.py:872-929、report.py:378-417、web/routers/monitor.py:41-157）內的 await 全部只是 `session.execute` / `session.commit`。檢索路徑更是刻意短連線：app/services/retrieval_pipeline.py:101 `async with SessionFactory() as session: # 短連線：檢索完即釋放`，rerank（可達 60-180s）與 LLM 串流都發生在區塊之外。fanout 亦然：app/services/retrieval_pipeline.py:233-237 `async with sem: async with SessionFactory() as session:` 只包 `hybrid_search`。REPORT_FANOUT_CONCURRENCY 預設值確認為 3（app/config.py:187 `report_fanou…

**處置**：不需要修程式；建議修的是報告本身——把「SSE 長串流期間持有的 session」與「3-5 個併發使用者即可打滿」刪掉，改成「無 statement_timeout ⇒ 單一慢查詢可無上限佔用一條連線，配合 P1-11 的全表掃描才是真正的耗盡風險」。若要留下數字，必須先真的壓測（例如以 15 個並行 /api/search 觀察 `pg_stat_activity`），否則就是把估算當實測。

### P1-12c｜報告的處方「≥3 字元才走 LIKE，1-2 字元 CJK term 改走 tsvector/bigram 或只留語意路」是安全可行的。

**實測**：依 `retrieval.py:98-111` 的實際控制流，`if terms:` 為真才會呼叫 `search_chunks_lexical`，patterns 由 `terms` 一對一產生。若加上「≥3 字元才走 LIKE」的過濾，會出現兩種明確有害的情形：(1) **單一 2 字元 term 的查詢**（實測 `'鴻海' → ['鴻海']`、`'輝達'`、`'財報'` 皆如此）過濾後 `patterns` 為空 ⇒ 字面路整條消失 ⇒ 只剩 dense 召回。這正好打在「字面精確比對最有價值」的一類查詢（公司簡稱、代碼），是 `retrieval.py:8` 那條硬保證的核心場景。(2) **混合查詢**（`'台積電 CoWoS 進度' → ['台積電','cowos','進度']`）丟掉 `進度` 後，SQL 的 AND 條件變少 ⇒ LIKE 更不具選擇性 ⇒ 命中列暴增 ⇒ 更早撞上 `LIMIT :cap`（`store.py:307/332`）⇒ 截斷更嚴重。也就是說這個處方會讓 P1-12b 的問題變差，而不是變好。

**處置**：**不建議按報告原文修**。理由如上：「≥3 字元才走 LIKE」對單一 2 字元 term 的查詢等於刪除字面召回，對混合查詢等於放大 cap 截斷，兩者都是可觀測的檢索品質回退，而收益（避免一次 seq scan）未經量測。建議改成三步：(1) 先做 P1-12b 的可觀測化，拿到「哪些查詢真的被截斷／真的慢」的實據；(2) 若 2 字元 term 的 seq scan 確認是延遲主因，加**獨立的 bigram 召回路**（Python 端產生 2-gram tsvector 欄位 ＋ GIN），讓短詞從新路走、長詞維持 trgm，任何 term 都不被丟棄；(3) 任何動到 `extract_terms`／pattern 產生規則的改動，都必須用 `eval/`（`eval/queryset.json` ＋ `scripts/eval_retrieval.py`）前後各跑一次比對——注意 `scripts/eval_retrieval.py` 是刻意直呼 `hybrid_search`、不經 `retrieval_pipeline`，所以它**看得到**這層改動（與 rera…

### P1-16e｜「回傳型別標註覆蓋率抽樣：retrieval_pipeline.py 1/7、agentic_qa.py 3/7、report.py 8/16」

**實測**：用 AST 實數（`ast.walk` 取所有 FunctionDef/AsyncFunctionDef，看 `n.returns is not None`）：`app/services/retrieval_pipeline.py` **6/8**（缺 `_run_rerank` L32、`_retier_to_question` L144）、`app/services/agentic_qa.py` **8/8**、`app/services/report.py` **16/16**。只取模組頂層則是 5/7、7/7、16/16——**分母完全吻合報告，分子全錯**。根因可重現：`grep -cE '^(async )?def .*->'` 三檔各得 **1、3、8**，與報告數字逐字相同 ⇒ 報告是用單行 grep 量的，凡是簽章換行、`-> X:` 落在後續行的函式全被漏算。三個檔自 `3ce718e` 至今零改動（`git diff --stat 3ce718e HEAD -- …` 空輸出），所以不是「後來補上的」。

**處置**：刪掉整段「回傳型別標註覆蓋率抽樣」，或改為實測值（6/8、8/8、16/16）並改寫結論為「回傳標註覆蓋良好、但沒有 mypy 去驗證它們是否正確」。要留住原意的話，真正該量的是「有標註但從未被檢查」而不是「沒標註」。

### P2-7a｜config.py 114 個扁平鍵，report_* 前綴 38 個鍵。

**實測**：AST 實測 app/config.py 的 `Settings` frozen dataclass 只有 **80** 個 AnnAssign 欄位（檔案共 268 行，`os.getenv` 出現 70 次）。前綴分布：report 42、ask 26、qa 7、faithfulness 2、reports 1、rerank 1、trusted 1。CLAUDE.md 也寫「約 80 個欄位」。

**處置**：改嵌套 dataclass 這件事**不建議現在做**：80 個欄位的扁平結構目前可讀（config.py 以 `# ASK_*（answer.py）` / `# report.py` 等註解分區），而改嵌套會同時觸及 answer.py 的 22 個與 report.py 的 19 個模組級 alias（見 P2-7b），是一次高擾動、零使用者可見收益的重構。真要動就等 P2-7b 的 alias 問題一起解。若只是要修報告，把 114/38 改成 80/42。

### P2-8a｜store._meta_columns 是一段 f-string SQL，與欄位靠人工註解對齊，沒有測試斷言欄數與欄序。

**實測**：**測試存在且正是在斷言欄數與欄序**。tests/test_rows.py:47-52 有解析器 `_meta_column_names()`（`_meta_columns(alias).split(",")` → 去 alias 與 `::text`），:55 起的 `class MetaColumnsAlignmentTests` docstring 直接寫「把「欄序即契約」釘死：_meta_columns 與 ChunkRow 靠位置對齊（_make 不驗名）。錯位不會拋錯，只會靜默把值塞進錯欄位——故這裡的斷言是唯一的守門員。」三個測試：:61 `test_file_hash_is_not_in_last_two_columns`（:66 `self.assertEqual(cols[-2:], ["chunk_index", "content"])`）、:71 `test_file_hash_sits_right_after_report_id`（:73 `self.assertEqual(cols[2], "file_hash")`）、:76 `test_column_order_matches_chunkrow_fields`（:79 `self.assertTrue(_meta_columns("c").startswith("c.id::text, r.id::text,"))`、:81 `self.assertEqual(cols[2:], list(ChunkRow._fields[2:-1]))`、:83 `self.assertEqual(len(…

**處置**：不必修程式碼——建議的守護測試已經存在且比建議的更完整（報告建議「5 行比對欄數與尾段名稱」，現況是三個測試連同「file_hash 必須在中段」都釘死了）。要修的是 docs/ARCHITECTURE_REVIEW_2026-07.md:326 那句「沒有測試斷言欄數與欄序」，以及 :326 的「17 個欄位」應為 16。


### 另一條：診斷對、但報告的建議修法也錯（P0-1，裁決 CONFIRMED）

`make normalize` 的破壞性完全屬實（實測樣本 A 589/597＝98.66%、樣本 B 2111/2200＝95.95% 的 chunk
會被改動，換行 6112→0 與 17812→0，而 `norm_for_match` 前後不同者 **0**＝零收益）。

但報告建議的「腳本改用 `clean_extracted`」**不會變成 no-op**：`textnorm._RE_CJK_GAP` 的
`(?<=[CJK])\s+(?=[CJK])` 同樣會吃掉「前段結尾是 CJK、後段開頭是 CJK」的那個單一換行。
實測樣本 B 改用 `clean_extracted` 仍會改動 **1123/2200＝51.05%**——一樣是零收益的破壞。
**正解是刪除，不是換函式。**
---

## 二、需要更正的數字與推論（PARTIAL 重點）

| 編號 | 更正 |
|---|---|
| `P2-3a` | 「原地」用詞不精確：`rerank_scored` 並未就地修改（沒有任何 mutation）——它建新 tuple、新 list，輸入的 `scored` 物件原封不動，而且這個「不動＋回傳新物件」正是 :132-135 明訂的 fail-open 同一性契約所依賴的性質。若照字面理解成 in-place mutation，會誤導讀者以為呼叫端手上的 list 也被改了（實際沒有），進而寫出錯… |
| `P1-8a` | 報告的兩個後果是互斥的：能觸發限流就代表沒漂移，漂移就永遠到不了限流那行。要讓「全員共用 key」真的發生，需要「peer 在信任名單內、但 X-Real-IP 缺席」（例如換掉 nginx 或有人直連 :8097 且閘道在名單內）——那是另一個情境，報告沒描述。另注意 .env.example 裡該行是**註解掉**的；repo 根的真實 .env 依規則未讀，故「已寫死某個 172.x」只能… |
| `P1-11b` | 「全表 unnest」與「跑兩次」完全屬實。「條件恆真」不精確：`r.market = ANY(:markets)` 會濾掉 `market IS NULL` 的列，也會濾掉不在這 9 碼內的髒值——所以是「近乎恆真」而非恆真，且它其實**擋掉了未標市場的列**，不能無腦刪掉（刪了會改變結果集）。另外 `signal_base` 的 `:236` `s.market = ANY(:markets… |
| `P1-11e` | 前半「從不顯式 ANALYZE」CONFIRMED；後半「planner 統計長期失真」**未經證實且很可能過度推論**：DB 跑的是 `pgvector/pgvector:pg16` 官方映像（`Makefile:38`），沒有任何 postgresql.conf 覆寫（`Makefile:32-38` 只帶 POSTGRES_* 與 -p -v），因此 autovacuum/autoanal… |
| `P1-12a` | 報告有一個事實錯誤與一處因果混淆。(1) **「eps」是 3 字元不是 2**，`%eps%` 能抽出完整 trigram `eps`，是可索引的——報告的四個例子裡只有「台積」「鴻海」「ai」真的是 2 字元。(2) 報告把兩種失效模式混為一談：**罕見的 2 字元 term** 才是真正的延遲風險（全表掃 70 萬列 chunk、content 欄位很寬、找不到幾列、不會提早結束）；**熱… |
| `P1-13` | 核心事實全部成立，只有總數 28→30 要更正。qa_log 13 條、CI 無 DB 兩個關鍵數字都對。行號 ci.yml:35 vs 實際 :33 屬報告檔頭已聲明的量測誤差，不影響結論。 |
| `P1-13-b2` | 報告在快照時點是對的，是併進 main 後才過時。現況精確描述應為：**參考實作等價性測試已經有了，缺的只是 CI 的 DB 環境**，所以建議 (a) 的後半其實已完成大半，剩下的是 20-30 行 CI YAML。另外靜態那三題強度偏弱（只驗子字串存在，改不到位也可能過），且該檔的 `_SCHEMA_EXPR`（:30）與 `_pg_norm`（:49-55）是未被引用的死碼。 |
| `P1-13-lock` | 我把它判 PARTIAL 而非 CONFIRMED，只因為列數與實際阻塞時間需要連 DB 才算得準；SQL 缺 CONCURRENTLY 這件事是靜態確定的。要定論請在生產跑：`SELECT reltuples::bigint FROM pg_class WHERE oid='research.report_chunk'::regclass;` 與 `SELECT count(*) FROM r… |
| `P1-14-thresholds` | 「四份全 false」不精確——正確說法是「三份 RAGAS baseline 全 false，第四份是研報 eval、根本沒有布林門檻」。另一個報告沒說到的細節：卡住的不只 answer_relevancy，**context_precision 0.723/0.769/0.777 也三份都沒過 0.8**（5c15a47 的 commit message 說『唯一卡住的是 answer_re… |
| `P1-14-stale` | 要更正成「RAGAS baseline 停在 M4（2026-07-14），研報 baseline 跟到 M7（2026-07-17），M8-M10 兩套都沒重跑」。順帶：report-m1b.json 的 notes 自己承認它是**混合結果**（『r003/r005/r009 於修復後單獨重跑並合併，其餘 7 題為修復前所跑 … 乾淨的同 config 重跑留待部署後或 M8 前』），所以連… |
| `P1-15-hole` | 這條要拆成兩半看：**mount 順序這個最貴的回歸現在在 CI 有真守門**（test_pre_split_guards.py 無條件跑、不 skip），洞已明顯縮小；仍然 skip 的是 test_spa_serving 那題「已 build 的真 dist 能正常送出 shell 且 cache-control=no-cache」，那是較廉價的回歸（真 build 產物的存在性與快取標頭）… |
| `P1-15-readme` | 時序要說清楚：`git show 3ce718e:README.md \| grep -c 'npm run build'` = **1**，且那一筆只在 :154 的技術棧表格裡（「需 npm run build 產出 dist」），**沒有部署步驟**——所以報告在快照時點大致公允；是這次未提交的文件更新（README.md 已 modified）把它補成完整步驟的。「503 整站」的機制本身… |
| `P1-16b` | 14,745 這個數字在報告寫下的當天就對不上它自己宣告的定義（services + scripts），最接近的是 `app/` 單獨的 14,369——推測是把 `app/`（含 services 以外）誤標成「services + scripts」。實質結論不受影響：規模是 1.7 萬行量級、守門為零。 |
| `P1-16c` | 「抓不到未使用 import」屬實且有價值（ruff 一次就把 CLAUDE.md 記載了好幾個里程碑的 answer.py 死 import canary 抓出來）。但「F821 是真風險」在現況**沒有任何實例**——這是報告的推測，不是量測；引用它去論證 lint 的必要性會誇大。另外 21/40 的 F401 是 deps.py 的刻意 re-export，直接開 F401 進 CI 會… |
| `P1-16f` | 「CI 不跑 lint ⇒ 沒有守門」是硬事實；「exhaustive-deps 失效」則要分兩層說：規則有設定、但沒人執行 ⇒ 沒有守門為真；然而**實跑一次的違規數是 0**，所以「失效」目前沒有造成任何已知缺陷。11 這個數字漂了近一倍（現況 20-21，且工作區還有他人 WIP 的 `frontend/src/features/search/useHomeExtras.ts` 未被 gr… |
| `P1-17d` | 「掃四遍」是誤數（把寫死的 `"web": True` 也算成一次呼叫）。更重要的是報告把成本歸咎的對象搞錯了：**/proc 掃描（10 ms）不是熱點，drvfs 上的 glob（31 ms）才是**，而真正的地雷是 `_count_tag_files()`——見 P1-17e。另外實測時發現 `_proc_alive` 是**裸子字串比對整條 cmdline**，我自己的 shell 指令… |
| `P1-17e` | 報告的「TTL 調到 15-30 秒」方向對但只解一半，且它把負載歸因給 `_proc_alive`（實測 10 ms）而漏掉真正會爆的 `_count_tag_files`（187 ms、且是永久性的 latent 條件）。「監控頁開著就是持續背景負載」量級要修正：現況約 0.8% 一顆核心 + 每 5 秒 9 條 DB 查詢，稱不上「持續背景負載」；但 `_count_tag_files` … |
| `P2-1` | 報告這條的「病灶」（檔案過大、DB 層與 RAG 編排混住）屬實，但「後果」段落的措辭（『必須載入整個 RAG 引擎（含 embed、rerank、LLM）』）會讓人以為有可觀的執行期代價，實際上零模型載入、且在 web 行程裡邊際成本為零。用這個理由去排優先序會排錯。真正的代價是測試與心智負擔：任何只想動 qa_log CRUD 的測試都得把 scope_router／faithfulness… |
| `P2-2` | 報告的核心判斷（環是真的、context.py 能解）正確，錯在計數與歸因：把 12 處說成 9 處，且把「M5/M6 凍結區約定」與「typst/pandoc 延遲載入」一起算成循環。這個歸因錯誤有實務後果——照報告去做會以為抽完 context.py 就能機械式把 12 處全上提，但 `report.py:282` 那處是刻意的（避免 web.server 匯入預算吃到 typst-py/p… |
| `P2-5b` | 「約 570 行」高估約 40%——實測 335 行（含 outline 對亦僅 396）。但核心指控（4 份必須手動同步的高重疊副本）完全成立。**刻意分歧的部分是可辨識且有限的**：(a) en 版多一條 rules[4]「5. Write the entire section in fluent English…」，其上方 report_writer.py:1105 有註解「這條是本變體存… |
| `P2-5d` | 報告說「5 處」低估了出現次數（實際 10 處）；「措辭不同的版本」數則大致對（zh 5 種）。**另有一個報告沒提但更值得注意的缺口**：overview.py:350 的 `OVERVIEW_SYSTEM_PROMPT` 與 faithfulness.py:35/42 的 `DECOMPOSE_SYS`/`GROUND_SYS` **完全沒有 injection guard**，而它們都吃使… |
| `P2-7b` | 報告的 28 與 20 都略高（實際 22 與 19），行號區間也小幅漂移。但「import time 求值 + 當預設參數 + 沒有 reset 入口 ⇒ monkeypatch env 無效」這個因果鏈三個環節我都實地驗到了，主張成立。 |
| `P2-7d` | 報告只掃了 app/services 的一部分，漏掉同目錄的 db.py 以及整個 web/ 與 eval/。CLAUDE.md 說的「八個檔」與我 grep 的結果完全一致，應以 CLAUDE.md 為準。另外要注意 CLAUDE.md 明說 web/auth.py 那五個與 REPORT_MARK_RERANK_* 是**刻意帶前綴且 live 的，不要當成命名錯誤改掉**。 |
| `P3-3-null-embedding-500` | 精確的結論是：**程式碼缺口屬實，觸發前提不存在**。要真的 500，必須先有 embedding 為 NULL 的 chunk 列，而本 repo 至今沒有任何路徑能產生它（只有手動 SQL 能）。所以報告標題「檢索主路會 500」是誇大的——它是潛在缺口，不是現存故障。 另補兩個報告沒說、但影響修法優先序的細節：(1) 就算真有 NULL 列，預設路徑 `ORDER BY distance`… |
| `P3-5-jsonb-misuse` | 三個子主張裡，型別選擇與「共識只能在 Python 算」屬實，歸因那一句寫錯。另外「共識只能在 Python 算」其實是**本專案刻意的設計**而非缺陷：CLAUDE.md 明文寫「四分位、跨券商共識、跨期變動全由 Python 決定性計算（在 radar/compute.py）」，把它列成 schema 缺陷是把設計決定當 bug。 實務影響也要說清楚：followups/stages 目前*… |
| `P3-data-6-重複儲存` | 報告把兩件不同的重複混為一談，且提出的修法方向會破壞既有的驗章不變量。text_sha256 的實際浪費量極小（64 bytes × 每報告 3-5 列 × ~1.4 萬報告 ≈ 4 MB 量級），raw_payload 才是真正會長大的那個（它另外複製了 claim+quote 全文）。實際佔用大小需連 DB 才知道。 |
| `P3-data-9-無完整性檢查` | 七個列舉項裡有一項（content_norm 漂移）已在報告寫成後被 PR #131 補上 CI 守門，其餘六項仍完全無人查。另外報告建議裡引用的「monitor.py:85-115 對無聲腐化的量測思路已是好範例」現在對應到 web/routers/monitor.py:76-120（s_done/tk_done/sig_done 三段覆蓋率查詢，:88-93 有一整段講「先前只量 summa… |
| `P3-data-10-HNSW參數` | 三個子項成立（建構參數、search_chunks 無 SET、相似研報缺 iterative_scan），兩個講法不精確（「三處」實為兩處＋一個隱含預設；「遺留碼」實為 make search 的活躍後端）。最關鍵的落差是嚴重性：報告把 search_chunks 寫得像有風險，實際上它完全不在生產服務路徑上、預設 top_k=10 遠低於 40。真正值得動的是 (f)：queries.py:… |
| `P3-data-13-執行緒安全` | 必須誠實區分兩件事：**「無鎖 + 無上限 + 無 torch 執行緒設定」逐條成立且可證**；**「BGE-M3 encode 非執行緒安全」我無法證實**——實際路徑在 @torch.no_grad() 下，共享寫入都是同值冪等。所以這條的真實危害是 CPU 超額訂閱與延遲（實測背書：MEMORY 有一筆「rerank 30s 全逾時」的同型事故），不是資料正確性。另外兩件報告沒寫、但改變判… |
| `P3-1` | 報告在 extract.py／embed.py／tagging.py 三項上完全屬實（tagging 尤其危險：它是 ingest 的 gate，而 CLAUDE.md 用一整段講對齊 findb，程式面零 assert）。但 chunk.py 那一項寫錯了——不變量有守門，只是守門住在 tests/test_reading_anchor.py 而不是專屬檔。報告作者可能是用「有沒有 test_… |
| `P3-4` | 報告對 readingApi.ts 與四個 hook 的判定成立，但把「沒有同名 .test 檔」等同「零測試」，在 TextPane／useReading 這兩項上明顯高估風險——那正是「錯了會靜默跳到錯位置」的不變量，而它恰恰是本 repo 覆蓋最密的前端區塊之一。 另一個必須說明的落差：任務描述把 frontend/src/features/search/ 的 BentoWall／Feat… |
| `P3-5` | 重複本身屬實且值得收斂，但報告的兩個佐證（retry 只有一份、llm.py 教訓沒下放）都不準：retry 是 4/5 都有，llm.py 的三個教訓有兩個是串流路徑專屬、批次不需要。若照報告原文去「抽共用 call_claude 把 llm.py 的教訓套下來」，反而會把 stream-json 的 envelope 問題帶進批次解析，那是 extract_takeaways 註解明確警告過… |
| `P3-7` | 缺陷屬實，但報告把兩支腳本的 checkpoint 設計搞混了——照它寫的去抄 extract_signals，只會拿到「版本」這一半，拿不到「內容變了自動失效」那一半（而後者才是它稱讚的「三套續跑機制中最好的設計」）。修的時候要抄的是 extract_takeaways。 |
| `P3-9` | 報告的行號（monitor.py:173）與 glob 對象（data/*.log）都不對——模組已搬到 web/routers/monitor.py，pattern 也只有三個。而且實際成本目前趨近零：那三個 pattern 在 data/ 目前是 **0 命中**（全量管線的 log 早就沒在產），且整段包在 `/api/progress` 的 `await asyncio.to_threa… |
| `P3-11` | 缺陷方向對，但痛感被高估了一倍：現況是「重啟即通、首查較慢」而非「分鐘級全黑」。另外報告建議的 SKIP_WARMUP=1 在現有架構下收益很小——暖機本來就不擋啟動，關掉它只是把等待從背景挪到第一個請求，反而更糟。真正有價值的只有 serve-dev。 |
| `P3-12` | 報告的判斷「只為舊測試存在」對 4 個成立，但它的建議語氣（「刪除」）會誤導成可以直接動——那 4 個現在還被 4 個測試檔實際 import，必須先改測試。另外 3 個是純死碼，報告沒區分。註解段數 4 → 8。這一項屬於整潔性問題，沒有正確性風險。 |
| `P3-13` | 這一項受工作區未提交修改影響最大，要分開講：(a) 重複本身仍在，屬實；(b) 但「沒有單一真相來源 → 靜默漂移」這個**後果**已被機械化守門大幅壓縮——未追蹤的 tests/test_docs_contract.py 對五份 living docs（:45-50 LIVING_DOCS = CLAUDE.md／AGENTS.md／README.md／docs/WORKFLOW.md／doc… |

---

## 三、逐條裁決總表

| 編號 | 裁決 | 主張 | 成本 | 風險 |
|---|---|---|---|---|
| `P0-1` | CONFIRMED | `make normalize` / `scripts/normalize_chunks.py` 是破壞性操作：它用 `clean_text` 更新由 `chunk_text(clean_… | trivial | low |
| `P0-2` | CONFIRMED | 完全沒有 Postgres 備份機制，唯一副本是 docker volume；而 DB 內已有不可重建的 `qa_log` / `report_doc.markdown`。`make in… | medium | low |
| `P0-3` | CONFIRMED | logging 從未初始化，`app.services.*` 走 `logging.lastResort`，所有 INFO 級遙測（`qa_timing`、`qa_agentic`）靜默消… | small | low |
| `P0-4` | CONFIRMED | claude CLI 沒有任何跨進程鎖；scripts/ 內唯一的鎖是兩支 shell 的自我重入 PID lock，會 spawn claude 的 Python 批次完全沒鎖，而 re… | small | medium |
| `P0-5` | CONFIRMED | report-mark-sync.service:13 的 Environment=HOME=%h 是錯的（系統層 unit 的 %h 解析為 /root），有 data/unit_fai… | trivial | low |
| `P0-6` | CONFIRMED | 問答路徑（retrieve_context）在 rerank 成功後，把 cross-encoder 的 sigmoid [0,1] 分數當成 fused 分送進 select_repor… | small | medium |
| `P0-7-guard` | CONFIRMED | 報告建議「加一支測試掃 README / CLAUDE.md / AGENTS.md 裡的檔案路徑字樣，assert 路徑存在」。請確認 tests/test_docs_contract.… | trivial | low |
| `P1-10` | CONFIRMED | 併發限制假設單 process 但沒有守門：ask.py 的 Semaphore(3) 與 report.py 的 Semaphore(1) 都是 process-local，ExecSt… | small | medium |
| `P1-10a` | CONFIRMED | 排隊無上限、無逾時，且 async with semaphore 在 generator 內部 ⇒ 回應已送出 200 才開始排隊，第 4 個之後的使用者看到「連線建立但永遠沒有 toke… | small | low |
| `P1-11-fix-c1` | CONFIRMED | `idx_report_section_run` 與 `idx_report_takeaway_report` 分別與各自表上的 UNIQUE 約束「完全相同」，屬多餘索引可刪。 | trivial | low |
| `P1-11-fix-c2` | CONFIRMED | `idx_report_signal_report` 被 `uq_report_signal_report_instr` 的前綴覆蓋，屬多餘索引。 | trivial | low |
| `P1-11a` | CONFIRMED | 雷達與 overview 的 `:code = ANY(r.stock_targets)` 寫法用不到 `idx_rr_stock_targets` GIN 索引，`_COVERAGE_S… | trivial | low |
| `P1-11a-semantics` | CONFIRMED | （報告未展開，本次補判）`:code = ANY(arr)` 改寫成 `arr @> ARRAY[:code]::text[]` 在這些 WHERE 用法下語意等價，可當純改寫法安全套用。 | trivial | low |
| `P1-11c` | CONFIRMED | overview 一題對同一母體掃 7 次；若 where 含 `company_name ILIKE '%…%'`（無 trgm 索引）就是 7 次 seq scan。 | medium | medium |
| `P1-11d` | CONFIRMED | `research_report.report_date / source / report_type` 全無索引，索引只有 `market` ＋ 3 個 GIN；瀏覽分頁每次全表排序。 | trivial | low |
| `P1-12b` | CONFIRMED | `LIMIT :cap`（2000/8000）沒有 ORDER BY，取哪 cap 列由 heap 物理順序決定，字面路召回被任意截斷且結果隨時間變動，`retrieval.py:8` 的… | small | low |
| `P1-13-b1` | CONFIRMED | 破口一：CHECK 清單變更在既有 DB 上永遠補不上——CREATE TABLE IF NOT EXISTS 對既有表是 no-op，schema.sql 也沒有任何 DROP/ADD … | small | low |
| `P1-13-b3` | CONFIRMED | 破口三：無版本記錄——schema_version / migrations / alembic 零命中，部署流程不保證會跑 make schema（sync_new_reports.sh… | medium | medium |
| `P1-13a` | CONFIRMED | 建議 (a) 的可行性：CI 加一個 pgvector service container、把 schema.sql 跑兩次驗冪等、並斷言「content_norm 表達式 == norm… | small | low |
| `P1-14-comparator` | CONFIRMED | 沒有比較器：before.json / after.json / baselines/*.json 都在版控裡，但沒有任何腳本讀兩份做 diff。 | small | low |
| `P1-14-size` | CONFIRMED | golden set 過小且三套互不相干：queryset.json 14 案、ragas_questions.json 8 題、report_questions.json 10 題。 | medium | low |
| `P1-14-threshold-calibration` | CONFIRMED | （任務指定複驗）最近兩個動過 eval 的 commit 是否已調整門檻、是否已有比較器。 | trivial | low |
| `P1-15-build` | CONFIRMED | vite build 從不在 CI 跑（frontend job 只跑 tsc --noEmit + vitest）。 | trivial | low |
| `P1-16a` | CONFIRMED | Python 端零 lint／零型別檢查：.ruff_cache 兩個版本目錄存在（有人本機跑過 ruff），但 ruff 沒進 pyproject.toml、沒進 CI、沒有 pre-c… | small | low |
| `P1-16d` | CONFIRMED | 「滿地 `# noqa: E402` 更說明有人假設規則存在卻沒人執行」 | trivial | low |
| `P1-16g` | CONFIRMED | frontend/e2e/ 的 5 個 spec 576 行從不執行（playwright.config.ts 沒有 webServer），是零價值資產 | medium | medium |
| `P1-16h` | CONFIRMED | （本節「做法」的可行性）ruff check --select E,F,I 進 CI ＋ frontend npm run lint 進 CI 的實際成本 | small | low |
| `P1-17a` | CONFIRMED | /api/progress 只認得 tagging 與 ingest 兩條（靠數 data/tags/*.json 與 tail log），summaries / takeaways / … | medium | low |
| `P1-17b` | CONFIRMED | monitor.py:88 的註解記載「2026-07 實測 takeaway 停更 8 天、signal 停更 12 天」——覆蓋率量測是事故後才加的 | trivial | low |
| `P1-17c` | CONFIRMED | /api/progress 前端每 5 秒輪詢、DB 快照 TTL 也是 5 秒 ⇒ 幾乎每次 miss，每 5 秒打 8 條 count/group-by | trivial | low |
| `P1-17f` | CONFIRMED | （本節「做法」）對「updated_at 逾時但 PID 還在」與「N 天沒跑」各出一個警示，接上既有的 report-mark-alert@.service | small | low |
| `P1-18` | CONFIRMED | 前後端契約是人工鏡像、無單一來源：SSE 框格式、ask 事件、/api/progress、radar/reading 四處各自手寫鏡像；openapi 在 scripts 與 packa… | large | medium |
| `P1-18-events` | CONFIRMED | 後端會送、但前端 parser 沒宣告的 SSE 事件與欄位（含 web/report_runs.py 的 _VOLATILE/_SLIM_KEEP 白名單是否與前端對得上）。 | small | low |
| `P1-18b` | CONFIRMED | 建議 (b)：SSE 加「後端 golden fixture → 前端 zod 解析」的雙向測試。 | medium | low |
| `P1-18c` | CONFIRMED | response_model 覆蓋率不均：radar 4、reading 3、search 2，ask/report/monitor/qa_history/report_file 全部 0… | large | medium |
| `P1-18d` | CONFIRMED | 建議 (d)：前端 zod safeParse 失敗不要靜默 return null，至少 console.warn。 | trivial | low |
| `P1-8` | CONFIRMED | 對外安全：session token 只簽 exp（無 session id、無密碼指紋）＋每請求滑動續期 ⇒ 改密碼不登出、無法撤銷單一 session、cookie 外洩即永久後門；a… | small | medium |
| `P1-9` | CONFIRMED | 連線池與查詢無任何上界：db.py 只有 pool_pre_ping=True（預設上限 15），且 statement_timeout / lock_timeout / idle_in_… | trivial | medium |
| `P1-9b` | CONFIRMED | DB 密碼硬編在 db.py:13 的預設值（postgres:postgres，超級使用者），.env 裡沒有覆寫，等於生產跑的就是它。 | medium | medium |
| `P2-3` | CONFIRMED | 檢索結果以無元素型別 tuple[list, str] 傳遞、候選是匿名 (tier, fused, row) 三元組；agentic_qa.merge_retrievals 用 rege… | medium | medium |
| `P2-3b` | CONFIRMED | （P2-3 附帶主張）retrieval_pipeline.py:78 用物件同一性（is）判定 rerank 是否套用，且 rerank.py:139 的註解寫著「改動此同一性即默默破壞… | trivial | low |
| `P2-4` | CONFIRMED | llm.py 只導出串流 API，drain 在 7 處手抄、只有 1 處有 wall guard；timeout 是 per-attempt（retries=2＋退避）⇒ 真實牆鐘上界 … | small | low |
| `P2-5a` | CONFIRMED | 系統提示常數散在 19 處、9 個模組。 | large | medium |
| `P2-5c` | CONFIRMED | report_writer.py:1039 的註解坦承「規則語意與中文版逐條對應…不得順手改動任何契約」。 | trivial | low |
| `P2-5e` | CONFIRMED | 在地化策略有 4 種並存，而為第 4 種寫的統一 helper locale.pick 零呼叫端。 | trivial | low |
| `P2-6a` | CONFIRMED | LLMUnavailableError 定義了卻全庫零捕捉；主答案路徑不捕捉，冒到 router 泛用 handler，使用者看到「問答服務發生錯誤」，且該輪 qa_log 完全不落庫（_… | small | low |
| `P2-6b` | CONFIRMED | 48 個 except Exception + 3 個 except BaseException。 | large | medium |
| `P2-6c` | CONFIRMED | retrieval_pipeline.py:78 用物件同一性（is）判定 rerank 是否套用，rerank.py:139 註解寫著「改動此同一性即默默破壞降級偵測」。 | small | low |
| `P2-6d` | CONFIRMED | 6 個自訂例外型別繼承基底各不相同，沒有共同根，web 層無法把「已知領域錯誤」與「未預期 bug」分流。 | small | low |
| `P2-7c` | CONFIRMED | config.py 只有一處值驗證；114 個鍵拿到非數字字串會在 import time 拋 ValueError 導致整個 app 起不來且無提示；語意約束（REPORT_FINALI… | small | low |
| `P2-7e` | CONFIRMED | 市場詞彙表分散在 tagging.py（被 6 個不相干模組依賴，名不符實）與 overview.py:37（第二份 _MARKET_SYNONYMS）。新增一個市場要改 4 處。 | medium | medium |
| `P2-7f` | CONFIRMED | 死碼：scope_router.route_question（生產零呼叫，只有測試用；answer.py:1617,1720 因缺公開入口去 import 私有的 _decision）、f… | trivial | low |
| `P2-8b` | CONFIRMED | 其他地方仍是裸 row[i]——radar/types.py:179-195 有 17 個連續 row[0]…row[16]（且 row[14/15/16] 正是 docstring 警告… | medium | low |
| `P2-8c` | CONFIRMED | rows.py 34 行裡有 12 行是事故記錄；目前已用 _make() 與 _fields.index() 緩解，tests/test_rows.py 也有寬度守門。 | small | low |
| `P3-1-orphan` | CONFIRMED | delete_conversation 只刪 qa_log，report_doc / report_run / report_rendition 列與磁碟 PDF 永久孤兒，無任何清理路徑… | medium | medium |
| `P3-10` | CONFIRMED | 缺 secret scan：.gitignore 為擋 .env 寫了很細緻的規則（註解還記載「一次 git add -A 就會提交上去」的顧慮），值得再加一層 gitleaks。 | trivial | low |
| `P3-2` | CONFIRMED | pyproject.toml 無 [tool.pytest.ini_options] ⇒ sys.path.insert 樣板大量重複 + 滿地 # noqa: E402；且 pytest… | small | low |
| `P3-2-is_research` | CONFIRMED | is_research 可 NULL，三種過濾寫法並存（= true / IS NOT FALSE / 完全不過濾），導致檢索頁、總覽題、閱讀頁母體不同；目前靠 ingest 閘門巧合一致。 | small | low |
| `P3-3` | CONFIRMED | mock 未集中：make_row / _FakeSession / LLM 串流 stub 在各檔各寫一份；ChunkRow 是 17 欄 NamedTuple，每次加欄要巡所有自製 b… | medium | low |
| `P3-4-advance-status-race` | CONFIRMED | advance_status docstring 寫「原子推進」，實際是 check-then-update，無 FOR UPDATE、UPDATE 也不帶 status 條件，兩個併發呼… | trivial | low |
| `P3-6` | CONFIRMED | 失敗清單無法重跑：三份格式不同的 fail log，要重跑只能 --reextract（付一次完整 LLM 成本）。 | medium | low |
| `P3-8` | CONFIRMED | ingest_lowio.sh 的耐久性狀態不可觀測：被 SIGKILL 就留在 fsync=off 且沒有任何地方看得出來。建議 /healthz 或 /api/progress 加 S… | trivial | low |
| `P3-data-11-版本風險` | CONFIRMED | hnsw.iterative_scan 需要 pgvector ≥ 0.8，但容器 image 未鎖版且無最低版本檢查。若在 0.7 環境重建，SET LOCAL 直接 unrecogni… | trivial | low |
| `P3-data-12-embedding寫入` | CONFIRMED | embedding 走文字字面（1024 個 %.7f ≈ 10KB/chunk vs 二進位 4KB），70 萬 chunks 全量多傳約 4GB 並讓 PG 逐字元 parse。建議 … | medium | medium |
| `P3-data-7-閱讀頁重工` | CONFIRMED | reading.py 骨架端點 docstring 明寫「不含全文」，但 _DOC_SQL 仍 SELECT full_text，然後對平均 25KB 的文字跑 clean_extract… | small | low |
| `P3-data-8-標籤永久凍結` | CONFIRMED | ingest_all.py:86 對已存在的 file_hash 一律 skip；is_research / market 只在首次入庫寫入 ⇒ 標註模型升級後既有 1.4 萬列的標籤永久… | medium | medium |
| `P1-11b` | PARTIAL | `_catalog_cte` 對 research_report 全表 unnest，且同一份 CTE 在一次請求跑兩次；`:277` 一律傳入全部 MARKETS 使 `r.market… | medium | medium |
| `P1-11e` | PARTIAL | `research_report` 從不 ANALYZE（三處只 ANALYZE chunk 表），導致 planner 統計長期失真。 | trivial | low |
| `P1-12a` | PARTIAL | `content_norm LIKE '%term%'` 的 term 來自 `retrieval.py` 的切詞，2 字元 term 抽不出完整 trigram，無法使用 `idx_re… | medium | medium |
| `P1-13` | PARTIAL | schema.sql 有 28 條 ALTER TABLE ... ADD COLUMN IF NOT EXISTS（qa_log 一張表 13 條），而 CI 沒有 DB（ci.yml … | trivial | low |
| `P1-13-b2` | PARTIAL | 破口二：content_norm 的 GENERATED 表達式變更同樣是 no-op，schema.sql:67 要求它與 norm_for_match() 逐字等價，但「沒有任何自動化… | small | low |
| `P1-13-lock` | PARTIAL | make schema 在生產上不安全：schema.sql:43 的 HNSW CREATE INDEX（非 CONCURRENTLY）與 :70 的 ADD COLUMN ... GE… | small | medium |
| `P1-14-stale` | PARTIAL | baseline 停在 m4-corpus-qa，而 M5-M10 都已上線。 | medium | low |
| `P1-14-thresholds` | PARTIAL | 門檻從未綠過等於沒有門檻：四份 baseline 的 thresholds_pass 全是 false。 | trivial | low |
| `P1-15-hole` | PARTIAL | 因此 test_spa_serving.py:26 的 pytest.skip 在 CI 永遠 skip，而「最貴的回歸剛好落在 skip 的那一側」（/app/assets Mount … | trivial | low |
| `P1-15-readme` | PARTIAL | frontend/dist 的產生完全沒有自動化也沒進 README：Makefile 無 build target、CI 無、systemd 無；唯一提到它的是兩份看起來已過期的設計文件。 | trivial | low |
| `P1-16b` | PARTIAL | 「14,745 行 Python（services + scripts）沒有任何自動化守門」 | trivial | low |
| `P1-16c` | PARTIAL | 「抓不到未使用 import、未定義名稱（F821，在這種大量動態 patch 的程式碼裡是真風險）」 | small | low |
| `P1-16f` | PARTIAL | 前端 eslint.config.js 存在、package.json 有 lint script、devDeps 有 eslint-plugin-react-hooks，但 CI 沒有這… | trivial | low |
| `P1-17d` | PARTIAL | _proc_alive 每次呼叫都 glob 整個 /proc 並讀 cmdline，一次輪詢掃四遍 | small | low |
| `P1-17e` | PARTIAL | （本節「做法」）TTL 調到 15-30 秒；監控頁開著就是持續背景負載 | small | low |
| `P1-8a` | PARTIAL | 附帶推論：REPORT_MARK_TRUSTED_PROXY_CIDRS 的 IP 一漂移，就是「所有外網登入被擋」＋「全員共用同一限流 key，任何人 5 次失敗鎖住全公司」。 | small | low |
| `P2-1` | PARTIAL | answer.py/report_writer.py/report.py 共 4,522 行佔 app/services 的 40%；answer.py L803-1293 是 491 行… | large | medium |
| `P2-2` | PARTIAL | retrieval_pipeline.py:16 頂層 import answer.Source/build_context、answer.py:1654 反向函式內 import 取回；… | medium | medium |
| `P2-3a` | PARTIAL | （P2-3 附帶主張）rerank_scored「原地」把 (tier, fused, row) 三元組的第 2 項換成 sigmoid 分，造成同一欄位承載兩種尺度。 | medium | medium |
| `P2-5b` | PARTIAL | 研報 prompt 有 4 份高度重疊變體共約 570 行（中/英 × single-shot/sectioned）。 | medium | medium |
| `P2-5d` | PARTIAL | prompt-injection 防禦句在 5 處各寫了措辭不同的版本。 | small | low |
| `P2-7b` | PARTIAL | answer.py:77-145 有 28 個、report.py:40-76 有 20 個模組級 XXX = _S.xxx alias，在 import time 求值並被當函式預設參數… | small | low |
| `P2-7d` | PARTIAL | 4 個漏網 env：retrieval_pipeline.py:26,28（REPORT_MARK_RERANK_*）、followups.py:14,15（模型 ID 寫死日期後綴）。 | small | medium |
| `P3-1` | PARTIAL | extract.py / embed.py / tagging.py / chunk.py 四個關鍵模組零測試，其中 chunk.py 的 CHUNK_OVERLAP=80 合併邏輯有 4… | small | low |
| `P3-11` | PARTIAL | 無 --reload 的維運摩擦：每改一行就要重啟並重載 BGE-M3 + reranker（分鐘級），是「直接在生產機改檔然後懶得重啟」的溫床；建議加 make serve-dev 與 … | trivial | low |
| `P3-12` | PARTIAL | web/server.py 的墓碑：:42, :167-169, :176, :205-206 是一批只為舊測試 import 存在的 re-export，加上 4 段「已拆至 X」的空洞… | trivial | low |
| `P3-13` | PARTIAL | README（460 行）、CLAUDE.md、AGENTS.md、docs/WORKFLOW.md（301 行）在指令清單／目錄結構／測試怎麼跑／風格慣例四件事上四度重複；docs/ 頂… | medium | low |
| `P3-3-null-embedding-500` | PARTIAL | embedding 可 NULL，retrieval.py:120 直接 float(row.distance)；字面路召回到 embedding 為 NULL 的 chunk 會 flo… | trivial | low |
| `P3-4` | PARTIAL | 前端測試缺口：readingApi.ts 是唯一沒測試的 API 層檔案；useDeleteConversation（破壞性操作）、useStats、useConversations、us… | small | low |
| `P3-5` | PARTIAL | call_cli 有 5 份手寫實作，timeout 150/180 不一、只有 tag_all_cli 有 retry；app/services/llm.py 的三個教訓沒套到批次。 | medium | medium |
| `P3-5-jsonb-misuse` | PARTIAL | qa_log.followups / stages 存純字串陣列（同 schema 的 report_section.evidence_ids text[] 才是正解）；report_si… | medium | medium |
| `P3-7` | PARTIAL | generate_summaries 的 checkpoint 是 summary IS NULL，prompt 改版後無法重跑；建議升級成 extract_signals 那種「版本 +… | medium | medium |
| `P3-9` | PARTIAL | 無 log 輪替：summary_failures.log 與 tag_failures.log 各 975KB、每日一份 sync_run_*.log、unit_failures.log… | trivial | low |
| `P3-data-10-HNSW參數` | PARTIAL | HNSW 建構參數全用預設（m=16, ef_construction=64）；ef_search 三處值不同且 store.py:113 的 search_chunks 完全沒設（預設 … | small | medium |
| `P3-data-13-執行緒安全` | PARTIAL | embed.py:9 的 Lock 只保護建構，model.encode 是無鎖的共享狀態呼叫，而所有呼叫端走 asyncio.to_thread（預設池 min(32, cpu+4) 條… | trivial | low |
| `P3-data-6-重複儲存` | PARTIAL | report_takeaway.text_sha256 每報告 3-5 列各存一份同樣的 sha（該掛在 research_report 上）；raw_payload 無 TTL 永久保留… | small | low |
| `P3-data-9-無完整性檢查` | PARTIAL | orphan / integrity 在 scripts 與 web 零命中。沒人在查孤兒 report_doc、NULL embedding、report_signal.market !… | small | low |
| `P1-11-fix-c3` | NEEDS_DB | `idx_report_signal_instr_broker_date` 因券商過濾一律走 `COALESCE(...)` 跨表運算式而從未被使用。 | trivial | low |
| `P0-7` | ALREADY_FIXED | 文件有 8 處事實錯誤正在誤導接手者；報告的時點註記聲稱八條現已全數不成立。 | trivial | low |
| `P0-7-1` | ALREADY_FIXED | 文件寫 `node --test web/static/app/*.test.mjs`，但該目錄不存在、全 repo 0 個 .test.mjs，指令必然失敗。 | trivial | low |
| `P0-7-2` | ALREADY_FIXED | 文件寫「前端＝原生 ES Module、零工具鏈、無打包步驟」，實際是 React 19 + Vite + TS + vitest。 | trivial | low |
| `P0-7-3` | ALREADY_FIXED | 文件多處指向 `app/services/intent.py`，該檔已改名為 scope_router.py 而不存在。 | trivial | low |
| `P0-7-4` | ALREADY_FIXED | CLAUDE.md 寫「Tunables 散落 os.getenv」，說反了——實際已由 app/config.py 集中。 | trivial | low |
| `P0-7-5` | ALREADY_FIXED | 文件寫「PDF ＝ WeasyPrint」，實際 REPORT_RENDERER 預設 typst、WeasyPrint 只是 fail-open 回退。 | trivial | low |
| `P0-7-6` | ALREADY_FIXED | ROADMAP/README 寫「Phase 2 結構化訊號＋findb 尚未實作」，但訊號與雷達已上線。 | trivial | low |
| `P0-7-7` | ALREADY_FIXED | README.md 寫「tests/ 共 25 個檔」，與實際不符。 | trivial | low |
| `P0-7-8` | ALREADY_FIXED | CLAUDE.md 架構段 grep typst / radar / faithfulness / agentic 全部 0 命中，照 CLAUDE.md 讀會漏掉一半架構。 | trivial | low |
| `P1-18a` | ALREADY_FIXED | commit 3f926bf 加了 /api/progress 的 takeaway/signal 覆蓋率但 progressSchema.ts 沒宣告，zod strip 靜默丟棄；報告… | trivial | low |
| `P1-12c` | FALSE | 報告的處方「≥3 字元才走 LIKE，1-2 字元 CJK term 改走 tsvector/bigram 或只留語意路」是安全可行的。 | medium | high |
| `P1-16e` | FALSE | 「回傳型別標註覆蓋率抽樣：retrieval_pipeline.py 1/7、agentic_qa.py 3/7、report.py 8/16」 | trivial | low |
| `P1-9a` | FALSE | 研報 fanout（REPORT_FANOUT_CONCURRENCY=3）加上 SSE 長串流期間持有的 session，3-5 個併發使用者即可打滿連線池。 | trivial | low |
| `P2-7a` | FALSE | config.py 114 個扁平鍵，report_* 前綴 38 個鍵。 | medium | medium |
| `P2-8a` | FALSE | store._meta_columns 是一段 f-string SQL，與欄位靠人工註解對齊，沒有測試斷言欄數與欄序。 | trivial | low |

---

## 四、需要 DB 才能定論的

- **P1-11-fix-c3**：`idx_report_signal_instr_broker_date` 因券商過濾一律走 `COALESCE(...)` 跨表運算式而從未被使用。
  - 「broker 欄位在任何查詢中都不可能被當成過濾條件」是靜態可證的（CONFIRMED）；但「從未被使用」是關於執行期的斷言——planner 仍可能為某個只用 (market, instrument_code) 的查詢挑中這個索引（兩個索引前綴相同，選誰看成本估算）。所以「這個索引沒有存在價值」成立，「idx_scan = 0」則必須查 DB。要定論請下：`SELECT relname, indexrelname, idx_scan, idx_tup_read, pg_size_pretty(pg_relation_size(indexrelid)) FROM pg_stat_user_indexes WHERE schemaname='research' AND relname='report_signal';`（同時可一併驗 c1/c2 的三個索引的 idx_scan）。

