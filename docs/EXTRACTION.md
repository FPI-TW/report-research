# 抽取層現況（E1）

本檔描述 `app/services/extraction/` 與 `app/services/extract.py` **現在**的行為，是 `tests/test_docs_contract.py` 掃描的 living doc。它取代 2026-09-18 移出 repo 的重構計畫（原檔名 docs/EXTRACTION_REDESIGN.md，見 git 歷史）；程式碼註解裡的「§N」全部指本檔章節。E1a–E1d 四個里程碑已於 2026-09-02 至 09-03 全部上線，夜間回填由 `report-mark-backfill.timer` 進行。版面層 v4（`ext-2026-09-18.v4`，§4、§10 診斷 #10–#13、§12）修正 v3 全庫實測出的四個系統性缺陷；bump 版本會讓回填把全庫重排一遍。

## 1. 一句話：文件模型是樞紐

把「PDF → 一個 `str`」換成「PDF → 帶版面結構的文件模型 ＋ 可量測的閱讀順序 ＋ 可回填的抽取版本」。`Document → Page → Block` 是整個抽取層的樞紐：切塊、欄位擷取、`full_text` 各自從它取自己要的東西，而 `full_text` 只是其中一個序列化 view（`app/services/extraction/model.py`）。

下游契約不變：`research_report.full_text` 仍是未清理的原始抽取文字，顯示一律走 `textnorm.clean_extracted()`；`report_chunk.content` 仍是 overlap merge 後的片段，不是全文子字串（`docs/ARCHITECTURE.md` 資料層一節）。

## 2. 抽取器選型與授權

| 抽取器 | 角色 | 授權 |
|---|---|---|
| `pdfplumber`（連帶 `pdfminer.six`、`pypdfium2`） | 版面層主軌（`EXTRACTOR=pdfplumber`） | MIT／BSD-3、Apache-2.0 |
| `pypdf` | 逐檔回退、docx 以外的舊路徑（`EXTRACTOR=pypdf`，程式預設） | BSD |
| `python-docx` | `.docx` 段落 | MIT |

**刻意不用 PyMuPDF**：AGPL-3.0／商用雙授權，而本站經 Cloudflare Tunnel 對外服務，AGPL 第 13 條的網路條款會被觸發（`pyproject.toml` 相依註解）。也**不用 pypdf 的 layout 模式**：實測靜默吃掉最多 87% 字元。

生產切換點在 sync 鏈的環境檔（`deploy/systemd/report-mark-sync.env.example` 的 `EXTRACTOR=pdfplumber`）。`app/config.py` 的 `_extractor` 驗證器對未知值退回 `pypdf` 並警告，不讓 typo 靜默切換抽取路徑。

## 3. 文件模型 `Document` / `Page` / `Block`

`app/services/extraction/model.py`：

- `BlockType` 七類：`title`、`paragraph`、`table`、`figure_caption`、`header`、`footer`、`footnote`。`DEFAULT_DROP = ("header", "footer")`。
- 所有集合都是 `tuple`，浮點 bbox 不參與序列化，`serialize()` 是決定性的。
- **表格序列化為 markdown（`|` 分隔）**：`textnorm.clean_extracted` 的 `_RE_CJK_GAP` 只吃空白，會把 CJK 儲存格之間的空白全刪掉、欄界消失（§10 診斷 #5）。改用可見分隔符就穿得過去，且不必動 `clean_extracted`（動它要重切重嵌全部 chunk）。`tests/test_extraction_model.py` 釘住這個地基。
- `serialize_with_index(doc, drop)` 回 `(text, tuple[BlockSpan])`，`BlockSpan = (type, page_no, start, end)`，區塊間以兩個換行接。索引對序列化字串算，所以 pdfplumber 路徑的 `extract_text` 用 `normalize=False`。
- 空頁保留，`pages_failed` 才算得出來（§10 診斷 #2）。

## 4. 抽取門面與回退規則

`app/services/extract.py`：

```python
extract_text(path: Path, extractor: str | None = None) -> ExtractResult
```

- 簽章不變，四個呼叫端（`scripts/extract_all.py`、`scripts/sync_new_reports.py`、`scripts/ingest_all.py` 經快取、`scripts/tag_all_cli.py` 經快取）不需改動。`extractor=None` 時讀 `get_settings().extractor`。
- `ExtractResult` 欄位：`file_hash`、`text`、`char_count`、`scanned`、`language`（`zh`／`en`／`mixed`／`unknown`）、`error`、`extractor`、`extraction_version`、`page_count`、`pages_failed`、`quality`、`blocks`。
- `MIN_TEXT_CHARS = 100`：`scanned = char_count < MIN_TEXT_CHARS`。
- **回退（逐檔）**：pdfplumber 拋例外、整份開不起來、或字數低於 `MIN_TEXT_CHARS` 才退回 pypdf，並在 `quality_flags` 記 `fallback_from` 與 `fallback_reason`（`error: …`／`below_min_chars`／`below_min_chars_both`）。刻意不做「兩邊字數比一比取多的」：原始字數比是假訊號（12 份樣本 11 份字元數逐字相等，差的是順序）。
- pypdf 路徑的逐頁例外不再無聲：失敗頁碼進 `pages_failed`，並填 `quality["pages_failed_ratio"]`。
- `.docx`：pypdf 模式用 `python-docx` 段落以單換行接；pdfplumber 模式每段一個 `paragraph` Block、單一 `Page`。`python-docx` 未安裝時回空字串或帶 `error` 的 Document。
- 版本識別在 `app/services/extraction/__init__.py`：`EXTRACTOR_NAME = "pdfplumber"`、`EXTRACTION_VERSION = "ext-2026-09-18.v4"`。**改動 `layout.py` 的排序或分類邏輯、`model.py` 的序列化、或 `quality.py` 的權重，就要 bump 這個字串**；回填腳本以「版本不等於目標」挑候選，bump 等於全庫重排。該 `__init__` 刻意不 import 子模組：`layout` 會拉進 pdfplumber／pdfminer.six，web 服務用不到。

版面層 `app/services/extraction/layout.py` 每頁依序做：

1. **取詞與相鄰詞合併**（v4）：`extract_words` 之後把同一行、水平間隙落在 (−1.5, 0.5) pt 的相鄰詞併回一個詞。券商 PDF 用空白字元做右對齊填充，填充空白覆蓋在數字上，pdfplumber 把「50,009.35」斷成「5」「0,009.35」；v3 全庫抽樣 30 份 277 頁，數值 token 有 16–64% 被切開（診斷 #10）。重疊超過 1.5pt 的是浮水印壓正文，不併。
2. **表格**：框線策略優先、文字對齊策略補無框線表。框線表通過驗收後再過兩道體檢（v4，診斷 #11）：同列框外 3–60pt 有數值詞達列數一半＝**殘缺**（YTD 整欄掉在框外）；含 3 個以上數值 token 的儲存格占比 ≥ 25%＝**欄位不足**（四個數值塞一格）。可疑的以合併後的詞在放寬後的區域重建（欄界＝正文列上沒有詞跨過、至少 4pt 寬的垂直空隙，表頭詞依中點歸欄），欄數變多才採用，否則整張退回文字流。刻意不用 pdfplumber 的文字策略重抽：它以字元定欄界，欄界會落在被填充空白切開的數字中間。
3. **圖區**（v4，診斷 #12）：`curves`、斜線（折線圖的資料段）、細長且高度不一的矩形（bar）以 12pt 格子聚成區域，至少 8 段小曲線或 5 支 bar 才算圖；區域內長度 ≤ 6 的 token 與數值 token（刻度、圖例、「Nov-24」）不進正文。**刻意不取 `images`**：凱基美股個股頁整個側欄墊著一張點陣圖，取它會把目標價整排丟掉。
4. **欄偵測**（直方圖投影，最多 3 欄）：窄欄與詞數太少的欄併回鄰欄；數值占比 ≥ 70% 的欄、或寬度 < 20% 且數值占比 ≥ 50% 的窄欄，一律併回**左邊**的標籤欄（v4：分母排除單一英文字母，帶單位的 `6M`／`3.5x` 算數值——凱基美股個股頁的側欄數值欄實測 0.69 對門檻 0.70，差這一點就被判成獨立一欄）；最後用行級證據驗證每條溝槽：兩側都有字的行裡超過一半是連續穿過的，那是段落內部的稀疏帶不是欄界（大摩首頁右上角的短段落）。
5. **行與段落**：分行以詞的垂直中點對行的移動平均分群（v4，不比 `top`：中文標籤與拉丁數字字框高度不同，一個高字框的隱形符號會把整列拆成兩行）；段落依行距與左緣。
6. **分類**：頁首頁尾（版面帶＋跨頁重複，首頁那份標題保留）、標題（字級比 1.15）、註腳、圖說。

每頁的統計（`words_raw`、`words_merged`、`chart_words_dropped`、`tables_suspect`／`tables_retried`／`tables_rejected`）加總進 `Document.meta["layout"]`，由 §5 寫進 `quality_flags`。這些步驟錯了不會拋例外，只會讓輸出「比較亂」，所以品質靠 §5 量、不靠例外；每個門檻的來由寫在 `layout.py` 的常數註解裡。

## 5. 品質指標

`app/services/extraction/quality.py` 是**唯一計算來源**，全部純程式、零 LLM（每檔一次 LLM 呼叫成本不成比例，且評分會變得不可重現）。

| 指標 | 定義 |
|---|---|
| `chars_per_page` | 抽出字元數 ÷ 頁數（下限常數 200） |
| `garbled_ratio` | 私用區 U+E000–F8FF 與 U+FFFD 的字元占比，偵測 CID 缺 ToUnicode |
| `pages_failed_ratio` | 頁級失敗數 ÷ 頁數 |
| `max_columns`、`multi_column_pages` | 版面欄數，界定「雙欄子集」 |
| `layout_coverage` | 文字 bbox 覆蓋的墨水格比例。v4 起 `extract.py` 對每份 PDF 都開 pypdfium2 算（實測 32 頁多 0.4 秒，抽取本身 4.9 秒）；render 開不起來退回 `None`（docx 恆為 `None`）。抽樣 50 份的分布：p5 0.38–0.46、p25 0.60–0.69、中位 0.72——圖多的頁面天生偏低，絕對值要對著門檻讀 |
| `quality_score` | 權重 garbled 0.35、density 0.25、failed 0.25、coverage 0.15 的加權平均；**coverage 缺席時把它的權重拿掉重新正規化**（v4），不再白送滿分——v3 生產從未算 coverage，9,263 篇全部 ≥ 0.9、5,804 篇恰好 1.0、`needs_review` 零篇 |
| `words_merged_ratio`、`chart_words_dropped`、`tables_retried`、`tables_rejected` | v4 版面層統計（§4），不進分數、供稽核：合併率異常高＝該版型大量用填充空白；`tables_rejected` 多＝框線表體檢常失敗，去 `compare_extractors.py` 看 |

`Quality.as_flags()` 的鍵與 `research_report.quality_flags` 對齊。`store.needs_review(quality_score, pages_failed, review_min, flags)`：`pages_failed` 非空、分數低於 `EXTRACTION_REVIEW_MIN`（預設 0.6）、`layout_coverage` 低於 `EXTRACTION_REVIEW_MIN_COVERAGE`（預設 0.30，取在抽樣 p5 之下，標到的是「整塊漏抽」不是「圖多」）、或 `garbled_ratio` 高於 `EXTRACTION_REVIEW_MAX_GARBLED`（預設 0.02，v3 全庫只有 27 篇過線）即標記；分數或旗標 `None` 不算低分。總分是加權平均，單一嚴重缺陷會被稀釋（coverage 0.3 的檔總分仍有 0.89），所以逐項門檻不可省。**只標記不擋**。

## 6. 快取格式

`app/services/extraction/cache.py`：`data/extracted/<file_hash>.json`，一檔一筆（E1c）。

- `RECORD_VERSION = 1`；紀錄鍵：`version`、`file_hash`、`file_name`、`file_path`、`text`、`char_count`、`scanned`、`language`、`error`、`is_admin`、`stock_code`、`company_name`、`source`、`report_date`、`report_type`、`extractor`、`extraction_version`、`page_count`、`pages_failed`、`quality`、`blocks`。**`blocks` 只存索引不存 bbox**。
- 寫入原子：先寫 `.tmp` 再 `os.replace`。
- `iter_records` 依檔名排序；壞檔 yield `{"_unreadable": True, ...}` 不中斷。
- `strip_tables(text, blocks, expected_len)` 只刪 `table` 區間；長度或索引對不上時原樣回傳。`scripts/extract_takeaways.py` 用它把表格從摘錄 excerpt 拿掉，但 `text_sha256` 仍對 canonical 全文算。
- 舊格式 `data/extracted/all.jsonl` 由 `scripts/migrate_extraction_cache.py` 一次性轉檔（`record_from_legacy` 補 `extraction_version = "pypdf-legacy"`）。

## 7. `extraction_log` 與監控

`research.extraction_log`（`db/schema.sql`）：每個進過管線的 `file_hash` 一列，不管有無入 `research_report`。`file_names` 是陣列且累加不覆蓋（同內容不同檔名的情形實測 122 組）。

`stopped_at` 詞彙由 `store.STOPPED_AT` 與 schema 的 CHECK 逐字對齊：`ingested`、`skip_admin`、`scanned`、`not_research`、`extract_error`。`store.upsert_extraction_log` 在寫入前驗證。`scripts/ingest_all.py` 與 `scripts/sync_new_reports.py` 的每一道閘都寫一列（`tests/test_extraction_log.py`）。

`research_report` 的七個抽取欄：`extractor`、`extraction_version`、`quality_score`、`quality_flags`、`page_count`、`pages_failed`、`needs_review`。

監控落點：`web/routers/monitor.py` 的 `/api/progress` 回 `extraction` 區塊（版本分布、`needs_review` 數、回填進度），單一 UNION ALL 查詢；缺表時該區塊為 `null`，前端 `frontend/src/features/monitor/ExtractionPanel.tsx` 降級而非整頁 500。

回填會改寫正典文字，`store.reanchor_takeaways` 重算摘錄錨點時錨不回的置 NULL。v3 回填 15 晚實測 5,532 條摘錄掉了 15%（448／1,116 篇至少掉一條），而總結列沒有這個數字。v4 起 `backfill_extraction.py` 的總結列多印「摘錄錨定 a/b（x%）」；補救走 `scripts/lost_anchors_to_delta.py --out data/takeaway_reanchor_delta.txt`，再 `scripts/extract_takeaways.py --hashes-file 該檔 --reextract`（每篇一次 LLM，可 `--limit` 分批）。

## 8. golden set 與評測

- 資料集 `eval/extraction_dataset.json`：15 份跨券商樣本，每案帶 `fields`（評等、目標價等欄位真值）、`order`（閱讀順序關鍵句）、`two_column`、`annotation_status`（`prefilled`／`draft`／`reviewed`）。15 份已於 2026-09-02 人工覆核。
- 覆核輔助 `scripts/review_extraction_golden.py`：把每頁渲染成圖、框出 `order` 片段並標序號，同時標出找不到與出現多處的片段。順序層必須純人工，人工的部分是「看」不是翻 PDF 找句子。
- 評測 `scripts/eval_extraction.py`（唯讀，需 DB 取訊號）：
  - `order_hit_rate`：句子經 `norm_for_match` 後在抽取文字裡找得到的比例。
  - `order_pair_acc`：命中句子兩兩配對，在抽取文字裡先後與 golden 一致的比例（另有 `order_pair_acc_within` 與 Kendall tau）。
  - 欄位 coverage 與 accuracy 分開看：只看 coverage 獎勵亂猜，只看 accuracy 獎勵什麼都不填。
  - 幻覺率：evidence span 錨不回原文的比例，`Anchor.method == "prefix"` 一律算錨不回。
- 比較兩份結果用 `scripts/eval_compare.py`（指標方向已登錄在 `METRIC_SPECS`）。基準線在 `eval/baselines/`，**現行基準是 21 份 reviewed 的兩份**：`extraction-pypdf-2026-09-18.json`（pypdf）、`extraction-pdfplumber-2026-09-18.json`（版面層 v3，以 v3 程式碼對同一份 21 案資料集量出）；`extraction-pypdf-2026-09-02.json` 是 15 案時期的舊基準，`eval_compare` 對覆核樣本數不同的兩份不給結論，- 2026-09-18 實測（21 份 reviewed）：pypdf 配對 0.820／tau 0.641 → v3 0.947／0.894 → v4 0.949／0.899，命中率三者皆 1.0，欄位層與幻覺率不變（`make eval-compare` 退出碼 0）。**順序指標量不到 v4 修的東西**（數字切開、表格欄位、側欄數值歸屬都在同一句之內），所以 v4 的驗收另有 §10 診斷 #10–#13 的抽樣數字；順序評測在這裡的角色是「沒有退化」。
- 2026-09-18 新增 6 份（凱基美股個股版型 2、大摩 2、元大早報 2，`id` 以 `kgi-2344-`、`kgi-5289-`、`ms-`、`yuanta-morning-2024`／`20251231` 開頭）：v3 全庫 1,177 篇 `max_columns=3` 的版型在原 15 份裡是零覆蓋。草標由模型看頁面圖產生、每句驗證在抽取文字裡恰出現一次；覆核由**另一個獨立模型**看 `review_extraction_golden.py` 的覆核頁圖核對順序、框選、逐字、欄位後改為 `reviewed`——不是人工，`annotator_notes` 以「AI 覆核」標明並列出改動與未決點（title 一律改 DB 預填的顯示標題、跨頁片段改 `pN`、元大 20251231 補回東元的評等與目標價）。要升級為人工覆核時從那些未決點看起。ed-only` 不吃它們，比較基準線時以 reviewed 為準。覆核時的已知歧義寫在各案 `annotator_notes`（大摩兩份沒有 Risk Reward 三欄頁；元大目次項要抄含頁碼的整行才唯一）。

## 9. 開工順序與上線步驟（已完成，留作重跑依據）

前置的兩支唯讀比對工具仍可用：

1. `scripts/profile_corpus.py`：語料 profiling，不碰生產路徑、不碰 DB、不碰 `data/extracted/`，輸出 `data/extraction/profile.jsonl`。
2. `scripts/compare_extractors.py`：pypdf 與 pdfplumber 並排比對，輸出自包含 HTML 供人工看 20 份。它是唯一能在改動生產路徑之前發現「新抽取器在某類檔案上更差」的工具。

四個 PR 的落點：

| 里程碑 | 內容 | 落點 |
|---|---|---|
| E1a | `extract_text` 門面與 `EXTRACTOR` 旗標；`ExtractResult` 加六欄；pypdf 逐頁例外改記 `pages_failed` | `app/services/extract.py`、`app/config.py` |
| E1b | `research_report` 七欄、`extraction_log` 表、每道閘寫一列、`needs_review` 只標記不擋 | `db/schema.sql`、`app/services/store.py` |
| E1c | per-hash 快取、四個端點改讀寫新格式、`all.jsonl` 轉檔 | `app/services/extraction/cache.py`、`scripts/migrate_extraction_cache.py` |
| E1d | 回填腳本與夜間 timer、sync 環境檔切 `EXTRACTOR=pdfplumber` | `scripts/backfill_extraction.py`、`deploy/systemd/report-mark-backfill.service` |

上線步驟（需 sudo，依序）：`make schema` → `uv run python scripts/migrate_extraction_cache.py --apply` → `/etc/default/report-mark-sync` 加 `EXTRACTOR=pdfplumber` → 複製 backfill unit 到 systemd 並 `enable --now report-mark-backfill.timer` → 隔天看 `journalctl -u report-mark-backfill.service` 與各版本的 `research_report` 列數。

回填 `scripts/backfill_extraction.py`：依 `report_date` 由新到舊、`--max-minutes` 限時（timer 給 240）、每篇一交易、原地更新不換 `report_id`、`store.reanchor_takeaways` 重算摘錄錨點與 `text_sha256`；新抽取器抽不出字則保留舊文只標版本與 `needs_review`。零 LLM，不取 `claude` 鎖。跑完由人手動 disable timer。

回滾：`EXTRACTOR=pypdf` 即回到舊路徑；已回填的列靠 `extraction_version` 可辨識，語料層可重跑。

## 10. 原始診斷紀錄

重構前對 pypdf 單一路徑的診斷，保留編號供程式碼註解引用：

| # | 症狀 | 根因 | 現況 |
|---|---|---|---|
| 1 | 雙欄左右交錯、表格塌成數字串、頁首頁尾混進正文 | `pypdf.extract_text()` 無版面模型 | 版面層（§4）；12 份樣本去空白後順序相似度只有 0.275–0.884 |
| 2 | 「抽到 3 頁」與「抽到 30 頁」下游長得一樣 | 逐頁 `except: continue` 靜默吞頁 | `pages_failed` 與 `pages_failed_ratio`（§3、§5） |
| 3 | subset 字型缺 ToUnicode 回空或私用區亂碼 | 無編碼健檢 | `garbled_ratio`（§5）；實測亂碼率大於 2% 僅 26 筆 |
| 4 | 掃描檔是終點站，無回補路徑 | 無 OCR、無重抽佇列 | `extraction_log.stopped_at = scanned`（§7）；OCR 分支未做 |
| 4b | 三道入庫閘的落點只在 `if` 分支裡，沒有表記得 | 各自 `continue` | `extraction_log`（§7）；重構前 16,555 筆抽取對 15,089 列，1,466 筆中途蒸發 |
| 5 | 抽到的表格到 chunk 階段還原不回來 | `clean_extracted` 抹掉 CJK 間空白，同時抹掉欄界 | 表格改 markdown（§3） |
| 6 | 表格被切碎跨 chunk | `chunk_text` 純字元切法 | v4：markdown 表格依列切、每塊帶表頭、不套 overlap（§12） |
| 7 | 欄位擷取漏抽 | 截斷全文餵一次 LLM | 延後（E4「定位 → 局部擷取 → 錨回驗證」未做）；截斷只解釋 1.13% 的漏抽 |
| 8 | 換 parser 後無法針對性回填 | 抽取層沒有版本欄 | `extraction_version`（§7） |
| 8b | 快取無法承載同檔重抽 | append-only `all.jsonl` | per-hash 快取（§6） |
| 9 | 改動只能靠感覺 | 無標註集 | golden set（§8） |
| 10 | 數字被空白切開（「5 0,009.35」），忠實度數值比對與目標價證據錨不回 | 右對齊填充的空白字元覆蓋在數字上，pdfplumber 斷詞 | v4 相鄰詞合併（§4 步驟 1）；抽樣 30 份數值 token 被切率 16–64% → 0.3% |
| 11 | 框線表殘缺（YTD 整欄掉到框外）、欄位不足（四個數值塞一格）；側欄「標籤／數值」被判成兩欄，目標價與標籤分家 | 框線只圈到部分表格；數值欄擦邊通過欄偵測門檻 | v4 表格體檢與詞重建、數值側欄併回、溝槽驗證（§4 步驟 2、4）；抽樣 25 份 max_columns=3 的檔 25 → 7，餘下是三欄並排的無框線清單（元大外資／投信買賣超、凱基行事曆），不是誤判 |
| 12 | 圖表刻度與圖例（「50 40 30 20 10 0」「J a n」）進正文與 chunk，還把圖表頁判成三欄 | 版面層不知道哪裡是圖 | v4 圖區（§4 步驟 3） |
| 13 | 每篇尾端的據點地址、評等定義、免責聲明整段進 chunk；嚴格樣式命中 12,110 個 chunk | 頁首頁尾偵測只看版面帶與跨頁重複 | v4 跨文件樣板字典（§12） |

## 11. 明確不做的事

| 不做 | 理由 |
|---|---|
| PyMuPDF | AGPL（§2） |
| pypdf layout 模式 | 靜默吃字 |
| MinerU／Docling／Marker 當主軌 | VLM 路線服務的是不到 1% 的檔案，且本機 CPU-only 跑不動 |
| 每家券商一支 parser | 只會累積不會刪除 |
| LLM 做 profiling 或評分 | 不可重現、成本不成比例 |
| 修 `clean_extracted` 的 CJK 空白邏輯 | 表格改走 markdown 就不需要；且它與 `content_norm` 沒有耦合（後者對應 `norm_for_match`） |
| 把 `is_admin` 與 `is_research=false` 入庫 | 汙染檢索；改記 `extraction_log` |
| 抽取評測進 CI | 與定時同步互搶 `claude` CLI |
| 用 `images` 當圖區、用 pdfplumber 文字策略重抽可疑表格 | 前者把側欄底圖當圖、目標價整排消失；後者以字元定欄界，欄界落在被切開的數字中間（v4 實測，§4） |
| 樣板段落從 `full_text` 拿掉 | 閱讀頁與摘錄錨點都建立在完整正典文字上；樣板只是不進 chunk（§12） |

## 12. 切塊與樣板（v4）

**表格依列切**（`app/services/chunk.py`）：段落若整段都是 `|` 開頭的行就當 markdown 表格——依列裝到 `CHUNK_SIZE`，每塊重複表頭（首列＋分隔列），單列不切；表格塊不接前一塊的尾巴、也不把尾巴給下一塊。散文之間的 overlap 合併維持原樣（`reading/anchor.py` 的 head-drop 補償依賴它）。判定用內容不用 `blocks` 索引：入庫切的是 `clean_extracted` 之後的正典文字，索引是對原始序列化字串算的，位移對不上。v3 全庫 20,155 個以 `|` 開頭的 chunk 有 18,462 個沒有表頭。

**跨文件樣板字典**（`app/services/boilerplate.py`、`scripts/build_boilerplate.py`）：掃 `data/extracted/` 的 per-hash 快取，段落經 `norm_for_match` 取 hash，同一 `source` 下出現在 ≥ max(8, 0.5%·文件數) 篇、且橫跨 ≥ 3 個不同 `stock_code`（或出現在夠多沒有 `stock_code` 的總經／策略報告）的段落就是樣板，寫成 `data/boilerplate/<source>.json`（gitignored，一 source 一檔，原子寫入）。跨標的那條擋住單一公司的公司簡介。入庫端（`ingest_all.py`、`sync_new_reports.py`、`backfill_extraction.py`、`run_ingest.py`）切塊前 `strip_boilerplate(canonical, source)`；`full_text` 不動。**全部 fail-open**：字典不存在或壞掉不剔除；剔除後剩不到兩成就退回原文（否則全是樣板的檔會被當掃描檔跳過）。字典不進 sync 鏈，`make boilerplate` 手動重建：新券商上線、既有券商換版型時跑一次即可。
