# 抽取層現況（E1）

本檔描述 `app/services/extraction/` 與 `app/services/extract.py` **現在**的行為，是 `tests/test_docs_contract.py` 掃描的 living doc。它取代 2026-09-18 移出 repo 的重構計畫（原檔名 docs/EXTRACTION_REDESIGN.md，見 git 歷史）；程式碼註解裡的「§N」全部指本檔章節。E1a–E1d 四個里程碑已於 2026-09-02 至 09-03 全部上線，夜間回填由 `report-mark-backfill.timer` 進行。

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
- 版本識別在 `app/services/extraction/__init__.py`：`EXTRACTOR_NAME = "pdfplumber"`、`EXTRACTION_VERSION = "ext-2026-09-02.v3"`。**改動 `layout.py` 的排序或分類邏輯、`model.py` 的序列化、或 `quality.py` 的權重，就要 bump 這個字串**。該 `__init__` 刻意不 import 子模組：`layout` 會拉進 pdfplumber／pdfminer.six，web 服務用不到。

版面層 `app/services/extraction/layout.py` 做欄偵測（直方圖投影，最多 3 欄）、行與段落聚合、頁首頁尾（版面帶＋跨頁重複）、標題（字級比 1.15）、註腳、圖說、表格（框線策略與無框線文字策略兩套）。它們錯了不會拋例外，只會讓輸出「比較亂」，所以品質靠 §5 量、不靠例外。

## 5. 品質指標

`app/services/extraction/quality.py` 是**唯一計算來源**，全部純程式、零 LLM（每檔一次 LLM 呼叫成本不成比例，且評分會變得不可重現）。

| 指標 | 定義 |
|---|---|
| `chars_per_page` | 抽出字元數 ÷ 頁數（下限常數 200） |
| `garbled_ratio` | 私用區 U+E000–F8FF 與 U+FFFD 的字元占比，偵測 CID 缺 ToUnicode |
| `pages_failed_ratio` | 頁級失敗數 ÷ 頁數 |
| `max_columns`、`multi_column_pages` | 版面欄數，界定「雙欄子集」 |
| `layout_coverage` | 文字 bbox 覆蓋的墨水格比例；只在傳入 pypdfium2 渲染器時計算，否則 `None`（docx 也是 `None`） |
| `quality_score` | `0.35·(1−min(1, garbled×5)) + 0.25·min(1, chars_per_page/200) + 0.25·(1−failed_ratio) + 0.15·(coverage or 1.0)`，夾到 [0, 1] |

`Quality.as_flags()` 的鍵與 `research_report.quality_flags` 對齊。`store.needs_review(quality_score, pages_failed, review_min)`：`pages_failed` 非空或分數低於 `EXTRACTION_REVIEW_MIN`（預設 0.6）即標記；分數 `None`（pypdf 路徑）不算低分。**只標記不擋**。

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

## 8. golden set 與評測

- 資料集 `eval/extraction_dataset.json`：15 份跨券商樣本，每案帶 `fields`（評等、目標價等欄位真值）、`order`（閱讀順序關鍵句）、`two_column`、`annotation_status`（`prefilled`／`draft`／`reviewed`）。15 份已於 2026-09-02 人工覆核。
- 覆核輔助 `scripts/review_extraction_golden.py`：把每頁渲染成圖、框出 `order` 片段並標序號，同時標出找不到與出現多處的片段。順序層必須純人工，人工的部分是「看」不是翻 PDF 找句子。
- 評測 `scripts/eval_extraction.py`（唯讀，需 DB 取訊號）：
  - `order_hit_rate`：句子經 `norm_for_match` 後在抽取文字裡找得到的比例。
  - `order_pair_acc`：命中句子兩兩配對，在抽取文字裡先後與 golden 一致的比例（另有 `order_pair_acc_within` 與 Kendall tau）。
  - 欄位 coverage 與 accuracy 分開看：只看 coverage 獎勵亂猜，只看 accuracy 獎勵什麼都不填。
  - 幻覺率：evidence span 錨不回原文的比例，`Anchor.method == "prefix"` 一律算錨不回。
- 比較兩份結果用 `scripts/eval_compare.py`（指標方向已登錄在 `METRIC_SPECS`）。基準線在 `eval/baselines/`（`extraction-pypdf-2026-09-02.json`）。**刻意不進 CI**：與定時同步互搶 `claude` CLI。
- 文字召回率不是主指標：pdfplumber 與 pypdf 抽出的字元數 12 份裡 11 份逐字相等，它量不到要修的東西。

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
| 6 | 表格被切碎跨 chunk | `chunk_text` 純字元切法 | 延後 |
| 7 | 欄位擷取漏抽 | 截斷全文餵一次 LLM | 延後（E4「定位 → 局部擷取 → 錨回驗證」未做）；截斷只解釋 1.13% 的漏抽 |
| 8 | 換 parser 後無法針對性回填 | 抽取層沒有版本欄 | `extraction_version`（§7） |
| 8b | 快取無法承載同檔重抽 | append-only `all.jsonl` | per-hash 快取（§6） |
| 9 | 改動只能靠感覺 | 無標註集 | golden set（§8） |

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
