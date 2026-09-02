# 抽取層重構實作計畫

> **狀態：提案（v3，2026-08-31）。** 本檔是計畫，不是現況。「現況診斷」一節的每一條都指得到 repo 內既有檔案並可自行覆核；其餘各節描述的檔案**尚未存在**。本檔刻意不列入 `tests/test_docs_contract.py` 的 `LIVING_DOCS`——它對「未來的檔名」做敘述，放進去等於要求提案階段就先建空檔。
>
> **版本沿革**：v1（2026-08-28）的優先序判斷是對的，但成本模型建立在三個沒有量過的假設上，而那三個假設全部被實測推翻。v2 收斂了範圍、換掉抽取器選型、改了主指標。**v3 是 v2 經過一輪對抗式覆核後的修訂**——覆核提出 34 條、存活 26 條，其中一條 blocker（`locate_quote` 的前綴退讓層讓數值驗證漏判近半）會讓 `E4` 的整個驗證層失效，另有多處數字錯誤與一次未揭露的樣本刪節。逐條記在附錄 C。
>
> 所有數字的重跑指令在附錄 A，**效力邊界在 §A.1，兩者要一起讀**。

## 0. 一句話

把「PDF → 一個 `str`」換成「PDF → **帶版面結構的文件模型** ＋ **可量測的閱讀順序** ＋ **可回填的抽取版本**」；並讓欄位擷取從「截斷全文餵一次 LLM」改成「**定位 → 局部擷取 → 錨回驗證**」。

抽取品質不是「換一個更好的 library」能解決的問題，**是「壞掉時沒有人知道」的問題**。本計畫的優先序因此不是「先換 parser」而是「先讓壞掉可見」——`E0`（基準線）排在 `E1`（換抽取器）之前，是刻意的。

---

## 1. 現況診斷

| # | 症狀 | 根因 | 落點 | 實測規模 |
|---|---|---|---|---|
| 1 | 雙欄研報左右欄交錯；表格塌成無行列關係的數字串；頁首頁尾與免責文字混進正文 | `pypdf.extract_text()` 無版面模型，依 content stream 順序吐字 | `app/services/extract.py`（`_extract_pdf`） | 12 份跨券商樣本，pypdf 與 pdfplumber 的**去空白後順序相似度 0.275–0.884**（中位數 0.738） |
| 2 | 「抽到 3 頁」與「抽到 30 頁」在下游長得一模一樣 | 逐頁 `except Exception: continue` **靜默吞掉整頁**，且無頁級失敗記錄 | `app/services/extract.py` | 無法量測——這正是症狀 |
| 3 | 券商內部排版系統產的 PDF 帶 subset 字型且缺 ToUnicode map → 回空字串或私用區亂碼 | 無編碼健檢；`MIN_TEXT_CHARS = 100` 是唯一一道閘 | `app/services/extract.py` | **亂碼率 >20% 的檔案 0 筆**、>2% 僅 26 筆（見 §1.1） |
| 4 | 掃描檔是**終點站**：判為 `scanned` → 不寫 chunk → 整份研報在語料庫中不存在，且無回補路徑 | 無 OCR 分支、無 re-extract 佇列 | `app/services/extract.py`、`scripts/ingest_all.py` | 116 筆（佔 16,555 筆抽取紀錄的 **0.70%**） |
| 4b | **三道入庫閘的落點只存在於程式碼的 `if` 分支裡，沒有任何一張表記得** | `is_admin`／`scanned`／`is_research` 三閘各自 `continue`，不留紀錄 | `scripts/extract_all.py`、`scripts/tag_all_cli.py`、`scripts/ingest_all.py`、`scripts/sync_new_reports.py` | 16,555 筆抽取紀錄對上 15,089 列 `research_report`，**1,466 筆在中途蒸發** |
| 5 | 就算抽到了表格，到 chunk 階段也還原不回來 | `clean_extracted()` 移除 CJK 間所有空白——**同時抹掉表格欄界**（欄與欄之間靠的就是空白） | `app/services/textnorm.py` | 見 §4.2「表格序列化」 |
| 6 | 表格被切碎跨 chunk，檢索命中半張表 | `chunk_text` 是 600 字元／80 重疊的純字元切法，不認結構邊界 | `app/services/chunk.py` | 延後（§4.4） |
| 7 | 欄位擷取漏抽 | `extract_signals.py` 把 `full_text[:excerpt]`（預設 16000 字）餵一次 LLM 要它回完整 schema | `scripts/extract_signals.py`、`app/services/signal_extract.py` | 31.7% 的研報超過 16000 字；但**截斷只解釋 1.13% 的漏抽，8.73% 是「關鍵詞就在視窗內卻沒抽到」**（§1.1） |
| 8 | 換 parser 後無法針對性回填 | `report_takeaway`／`report_signal` 有 `extraction_version`，**抽取層本身沒有**：`research_report` 不記錄用哪個抽取器、抽得多好 | `db/schema.sql` | — |
| 8b | 抽取快取無法承載「同一檔重抽兩次」 | `data/extracted/all.jsonl` 是 422MB append-only、無去重無版本，**兩個讀取端 ＋ 兩個寫入端** | `data/extracted/all.jsonl` | 16,555 筆 |
| 9 | 任何改動都只能靠感覺說「好像好一點」 | 無標註集、無閱讀順序／欄位 coverage 指標 | 無 | — |

### 1.1 診斷 #4b 的精確分解（v2 這裡整段是錯的）

那 1,466 筆的去向**全部可以解釋，異常殘量是 0**：

| 落點 | 筆數 | 在哪裡被擋 |
|---|---|---|
| `is_admin`（行政件：通知函、封面頁…） | 205 | `app/services/filename.py:is_admin_doc()` → `extract_all.py:75`、`ingest_all.py:79`、`sync_new_reports.py:77`（`skip_before_tag`） |
| `scanned`（< `MIN_TEXT_CHARS`） | 116 | `extract.py`；`tag_all_cli.py:101` 一併排除 |
| （上兩者重疊） | −4 | |
| **小計：連標籤檔都沒有** | **317** | |
| `is_research = false` | 1,149 | `ingest_all.py:93`（`if not tag.is_research or not tag.market: continue`） |
| **合計** | **1,466** | 16,555 − 15,089 ✓ |

**v2 寫的「105 筆抽得出文字卻連標籤檔都沒有」是不存在的。** 它來自一個無效的算式 `16,555 − 16,334 − 116`：那是兩個集合的**淨差**而非集合差，`data/tags/` 裡有 **96 個孤兒標籤檔**（hash 不在 `all.jsonl` 內，推測是鏡像裡已刪／改名的舊檔）把數字壓低了。同一個錯誤讓 v2 的 1,466 分解也錯——`1,245` 是「`is_research=false` 的**標籤檔**數」不是「有抽取紀錄卻被擋掉的**檔案**數」，而 `1,245 − 1,149 = 96` 恰好等於孤兒數，兩個錯誤互相抵銷才讓總和看起來對得上。

**真正的診斷因此不是「有 105 筆不明」，而是「1,466 筆的原因只寫在程式碼裡」**——要回答「這個檔為什麼不在語料庫」，現在唯一的辦法是人去讀四支腳本的 `if` 分支。這是 `extraction_log`（§4.2）存在的理由，也是它的 `stopped_at` **必須包含 `skip_admin`** 的理由：`is_admin` 是現況最大的單一非研報落點，v2 完全沒發現它存在，還把「行政通知」這個 `is_admin_doc()` 的職責錯掛到 `is_research` 上。

### 1.2 v1 的三個假設，以及推翻它們的實測

| v1 的假設 | 實測 | 後果 |
|---|---|---|
| 「實測語料若如預期分佈（**九成 L0**）」，一成走 OCR／VLM | `scanned` 0.70%、亂碼率 >20% **0 筆**、>2% 僅 26 筆 → **L0 佔 ≥99.2%** | `E2` 的 60x–400x 階梯服務的是 ≤0.8% 的檔案。整條階梯**這一輪不建**（§4.4） |
| 「目標價／EPS 表常在封面或末頁；**截在中間就必漏**」 | 全母體 5,200 篇長報告（>16,000 字）：「目標價」首次出現在 16,000 字之後只有 **59 篇（1.13%）**；而有訊號的 2,944 篇裡，**關鍵詞就在視窗內、卻一筆 `target_price` 都沒抽到的有 257 篇（8.73%）** | `E4` 的做法（定位→局部→錨回）仍然正確，但**歸因要改**：主要失敗是「模型在長雜訊上下文裡沒抓到」，不是截斷。驗收指標跟著改 |
| 「文字召回率相對 `E0` 基準**提升 ≥25 個百分點**」 | pdfplumber 與 pypdf 抽出的字元數 12 份裡 11 份**逐字相等**（例：GS 40,929 vs 40,929） | 字元召回率量了不會動。主指標換成**閱讀順序正確率**（§5） |

> v2 這兩個比例寫的是 3.5% 與 24%，來自 `limit 800` 的樣本——**`LIMIT` 沒有 `ORDER BY` 不是隨機抽樣**，取到的是儲存順序。全母體重算後兩個數字都降了，**但兩者的比值（8:1）不變，「截斷不是主因」的結論反而更強**。

---

## 2. 目標與非目標

### 目標

1. **靜默失敗歸零**——頁級失敗、編碼異常、低覆蓋率、以及「被閘門擋下所以不存在」都必須落庫成可查詢的一列，而不是只存在於某個 `if` 分支。
2. **閱讀順序可量測**且對 golden set 達標（規則見 `E1`）。
3. **抽取版本可追溯**，parser 升級後可依 `extraction_version` 針對性回填。
4. **欄位擷取具備 evidence**：每個目標價／評等都能**逐字**錨回原文，錨不回即降級並記錄原因。

### 非目標（明確不做）

- **不追求 100% 版面還原**。目標是「檢索與擷取夠用」，不是重製 PDF。
- **不為每家券商寫 parser**，也不在本輪建券商 profile（延後，§4.4）。
- **不改檢索融合演算法**。本計畫止於 `report_chunk` 與 `report_signal` 的**寫入端**。
- **前端只動監控頁一處**：`/api/progress` 新增的抽取品質欄位需要同步 `frontend/src/features/monitor/progressSchema.ts` 與一個新 Panel 元件（§6.5）。問答／檢索／閱讀／雷達四條前端路徑都不動。
  > v2 把「不改前端」寫成無條件非目標，同時又在 §6.5 要求新增監控卡片——那是同一個矛盾換個地方復發。這裡把例外寫明，因為漏改是**靜默的**：`web/routers/monitor.py` 自己的註解就記著「zod 物件預設 strip，未宣告的鍵不報錯、直接安靜丟掉（takeaway/signal 就這樣從 P4 起一路送到前端卻從未進 DOM）」。
- **不改 `clean_extracted`**。理由**不是** v1／v2 說的「會動到 `content_norm` generated column」——那條耦合不存在（§8 表格有詳細更正）。真正的理由有兩條：它是 `report_takeaway.quote_start` 的錨定基準；而且它一改，全部 585,942 列 chunk 就得重切重嵌。表格欄界改用可見分隔符穿透（§4.2），繞開它。
- **不建 OCR／VLM 階梯**（§4.4）。

---

## 3. 目標架構

```
PDF/DOCX
   │
   ├─[A] profiling（純程式、無 LLM）
   │      有無 text layer / 每頁字元密度 / 私用區字元比例 / 欄數 / 頁級失敗
   │
   ├─[B] 抽取：pdfplumber/pdfminer.six 版面分析（單層，不是階梯）
   │
   ├─[C] 文件模型  Document → [Page] → [Block]
   │      Block{type, bbox, page_no, order, text | table_cells}
   │      type ∈ title|paragraph|table|figure_caption|header|footer|footnote
   │
   ├─[D] 品質量測（§5）→ 落 research.extraction_log，**不擋任何東西**
   │
   ├─[E] 序列化 view ──→ full_text（表格寫成 markdown，見 §4.2）
   ├─[F] 既有 chunk_text ──→ report_chunk（本輪不改切法）
   └─[G] 欄位擷取（定位 → 局部 LLM → 逐字錨回驗證）──→ report_signal
```

### 3.1 為什麼 `[C]` 是整個計畫的樞紐

現況所有下游都吃同一個 `str`，於是**版面資訊在第一步就永久遺失**：`clean_extracted` 想保留段落但只剩 `\n\n` 可依據，`chunk_text` 想切在語意邊界但只看得到字元數，`extract_signals` 想找目標價表但拿到的是一串數字。

改成 Block 模型之後，`[E]` 與 `[G]` 各自拿到它們真正需要的東西：序列化拿到章節與表格邊界，欄位擷取拿到 table cells，`full_text` 只是**其中一個序列化 view**——而不是唯一的 artifact。`[F]` 本輪仍吃序列化後的字串（`E3` 延後），但 Block 模型一旦存在，`E3` 就只剩改 `chunk.py` 一件事。

### 3.2 抽取器選型

| 候選 | 授權 | 實測（12 份樣本 / 199 頁，附錄 A） | 決定 |
|---|---|---|---|
| **pdfplumber / pdfminer.six** | MIT | 與 pypdf 字元數 11/12 逐字相等、**順序不同**（相似度 0.275–0.884，中位數 0.738）；平均 3.45 s/檔（0.64–15.76） | **版面主軌** |
| **pypdfium2** | Apache-2.0 / BSD-3 | 與 pypdf 順序相似度 **0.992–1.000**——**不解決閱讀順序**；但 0.65 s/檔且與前端 `@embedpdf` 同一個 PDFium 引擎 | **頁面 render ＋ 引文行為對齊**，不當文字主軌 |
| PyMuPDF | **AGPL-3.0** / 商用 | API 最好（`get_text("dict")` 直接給 block/bbox、內建 `find_tables()`） | **排除**：本站經 Cloudflare Tunnel 對外服務，AGPL §13 網路條款會被觸發（`docs/EXTERNAL_ACCESS.md`） |
| pypdf `extraction_mode="layout"` | BSD | **靜默吃掉字元**：citic 15,837 → 2,045（**少 87%**）、kgi 58,077 → 28,130（少 52%）、fubon 少 60% | **排除** |

**「零新相依的捷徑」已經被實測否決**——pypdf 的 layout 模式看起來免費，代價是無聲的資料遺失，正是本計畫存在的理由那一類問題。

**docx 走哪條路**：34 篇 `.docx` 維持現況的 `python-docx` 段落抽取，只是包成 Block 模型（每個 paragraph 一個 `type='paragraph'` Block，無 bbox）。版面分析與品質指標中依賴 bbox 的兩項（`layout_coverage`／`column_count`）對它們回 `null`，不參與 `quality_score`。

---

## 4. 里程碑

### 4.1 `E0` — 基準線與 golden set（**先做**）

沒有這個，`E1`／`E4` 全部無法驗收。

**交付**

- `eval/extraction_dataset.json`：**30–40 份**人工標註（v1 寫 80–150，在標註成本上不可行），跨券商、跨型態（個股報告／債券雙週報／產業策略／掃描檔／已知亂碼檔）。每份標註：
  - 欄位層：標題、報告日、券商、標的清單、評等、目標價、EPS 預估。
  - **順序層：8–15 條原文句子（含表格列）＋ 它們在紙本上的正確先後順序。** 這是主指標的來源。
- `scripts/eval_extraction.py`：跑抽取 → 對 golden set 算 §5 的四項指標 → 輸出 JSON，格式對齊 `eval/baselines/` 既有慣例。
- `scripts/profile_corpus.py`：唯讀掃過既有語料，輸出 profiling 分佈直方圖。

**為什麼是「有序關鍵句」而不是「全文 LCS」**：實測 pdfplumber 與 pypdf 的字元集幾乎完全相同，字元召回率量了不會動；而 v1 的 `E0` 只交「人工核對的**全文字元數**」，那是一個數字，**根本支撐不了 §5 定義的字元層級 LCS**——v1 這兩節互不相容。有序關鍵句同時解掉這兩個問題：它直接量到真正的缺陷（順序），標註成本每份約 10 分鐘。

**驗收**：對現況（pypdf 6.12.2）跑出一組基準數字並寫進本檔 §7。**基準線很難看是預期結果**，那正是它的用處。

**風險**：標註成本。緩解——標題／報告日／券商／標的可從既有 DB 欄位半自動預填，人工只覆核；**評等、目標價、以及順序層必須純人工**（前者不能拿待驗證的擷取結果當答案，後者本來就只有紙本上有）。

---

### 4.2 `E1` — pdfplumber 取代 pypdf、品質落庫、移除靜默吞例外

**交付**

- `app/services/extraction/layout.py`：pdfminer.six／pdfplumber 版面分析 → Block 模型；依 bbox 排閱讀順序；**跨頁重複 bbox 自動識別頁首頁尾**。
- `app/services/extraction/tables.py`：`page.extract_tables()` → `table_cells`。
- `app/services/extraction/quality.py`：§5 指標的唯一計算來源。
- `app/services/extraction/model.py`：Block／Page／Document dataclass ＋ **決定性序列化**（§6.1）。
- `app/services/extraction/render.py`：pypdfium2 頁面 render（供 profiling 與未來的 OCR 分支）。
- `app/services/extract.py` 改為門面：保留 `extract_text()` 簽章與 `ExtractResult` 供既有呼叫端，內部改走新管線。
- **抽取快取改成 per-hash**：`data/extracted/all.jsonl` → `data/extracted/<hash>.json`，對齊 `data/tags/<hash>.json` 的既有慣例（已跑著 16,334 個檔）。
  **四個端點都要改，不是兩個**——v2 只列了讀取端：

  | 端點 | 角色 | 檔案 |
  |---|---|---|
  | `scripts/extract_all.py` | **寫入**（`OUT`，以 `"w"` 開檔整檔重寫） | 全量抽取 |
  | `scripts/sync_new_reports.py` | **寫入**（`_append_all_jsonl()`，每 3 小時 append） | **生產路徑，最容易漏** |
  | `scripts/ingest_all.py` | 讀取（逐行串流） | 全量導入 |
  | `scripts/tag_all_cli.py` | 讀取（濾 `is_admin`／`scanned` 後為候選） | 標記 |

  漏掉 `sync_new_reports.py` 的後果是**完全靜默**：排程繼續寫一個沒有任何讀者的檔案，新研報的抽取紀錄從此對新管線不存在。CLAUDE.md 已經點名這支腳本「最容易被漏掉」。

- `db/schema.sql` 增欄（皆 `ADD COLUMN IF NOT EXISTS`，對齊既有慣例）：

  ```
  research_report.extractor          text      -- 'pdfplumber' | 'pypdf' | 'ocr_<engine>'
  research_report.extraction_version text
  research_report.quality_score      real      -- 0-1，§5 加權
  research_report.quality_flags      jsonb     -- {"garbled_ratio":0.31,"order_conf":0.42,...}
  research_report.page_count         int
  research_report.pages_failed       int[]     -- 頁級失敗清單，取代 except: continue
  research_report.needs_review       boolean   NOT NULL DEFAULT false
  ```

- `db/schema.sql` 新增 **`research.extraction_log`**（現況 `research` schema 有 10 張表，這是**第十一張**）。以 `file_hash` 為鍵，**不管有沒有進 `research_report` 都寫一列**：

  ```
  file_hash          text PRIMARY KEY
  file_names         text[] NOT NULL   -- 同內容不同檔名者全部收在這裡，見下方「為什麼不是 text」
  extractor          text NOT NULL
  extraction_version text NOT NULL
  page_count         int
  pages_failed       int[]
  char_count         int
  quality_score      real
  quality_flags      jsonb NOT NULL DEFAULT '{}'
  stopped_at         text NOT NULL
  updated_at         timestamptz NOT NULL DEFAULT now()
  CHECK (stopped_at IN ('ingested','skip_admin','scanned','not_research','extract_error'))
  ```

  **為什麼要獨立一張表**：v1 的七個新欄位全掛在 `research_report` 上，但實測有 **1,466 筆檔案根本沒有那一列**（§1.1）。目標 #1「靜默失敗歸零」在 v1 自己提的 schema 上做不到。這張表與 P4／P5 監控同一種分工：**只記錄事實，判定交給消費端**。

  **`stopped_at` 必須有 `skip_admin`**：`is_admin` 是現況最大的單一非研報落點（205 筆），而且它是**兩條入庫路徑的第一道閘**（`ingest_all.py:79` 與 `sync_new_reports.py:75` 的 `skip_before_tag()`，後者排在 `scanned` 之前）。v2 的列舉值漏了它，等於一上線就有 205 筆無處可歸。

  **`file_names` 是陣列不是 `text`**：鏡像裡有 **122 組同內容不同檔名的檔案**（券商同一份報告用兩種命名各投一次，共 244 檔）。以 `file_hash` 當主鍵時它們必然塌成同一列，用單一 `text` 欄會後寫覆蓋前寫，直接讓其中一個檔名消失——與「每個檔案都有落點」的目標 #1 相牴觸。

  **新增 CHECK 約束記得同步 `db/expected_constraints.txt`**（golden 清單）——改 CHECK 在既有庫是完全的 no-op，那份清單是唯一守門（`tests/test_schema_constraints.py`）。

**表格序列化（決定 §1 診斷 #5 的走法）**

Block(type=table) 序列化成 **markdown 表格**（`| 台積電 | 買進 | 1200 |`）。

`textnorm._RE_CJK_GAP` 是 `(?<=[CJK])\s+(?=[CJK])`，**只吃空白**；只要欄界是一個可見字元就穿得過去（`台積電 ｜ 買進` → `台積電｜買進`，分隔符留著）。因此**不必改 `clean_extracted`**——但要注意這條**跟 `content_norm` generated column 無關**（見 §8 的更正），真正的好處是不必重切既有的 585,942 列 chunk。

**逐字引文的防護（表格進 `full_text` 的副作用）**

`frontend/src/features/report/pdf/quoteNeedle.ts` 的三階階梯（折疊空白／最長無空白片段／前 24 字元）**沒有一階會剝掉 `|`**。表格列被當成 quote 時有兩種退化，取決於那一列有沒有 ≥6 字元的 token：

- 有（`台積電(2330)`、`1,234.56`、英文欄名——實務上多數如此）→ 第 2 階產生一個關鍵字**並且很可能命中**，但那一階**沒有唯一性要求**，使用者會被帶到該關鍵字在全文中的**任意一處**。
- 沒有（`| 台積電 | 買進 |`，最長片段「台積電」3 字元 < `MIN_FRAGMENT = 6`）→ 三階全落空，顯示「原文中找不到這段文字」。

兩種都是使用者看得見的退化（現況命中率：原句 95.3%、階梯補到 98.6–99.1%，全語料 3,407 條實測）。

處置：**`scripts/extract_takeaways.py` 的 excerpt 在組裝時就濾掉 table Block**，模型看不到表格列，也就不可能引用它。純 Python 決定性、不改前端、不改 prompt。表格裡的事實（目標價、EPS）不會成為摘錄——那本來就是 `E4` 的職責。

**品質閘的行為：一律入庫，只標記不擋**

- 抽得出文字的檔案**全部**寫 `research_report` 與 `report_chunk`，低分者 `needs_review=true` ＋ `quality_flags` 記原因。檢索端不過濾（壞文字檢不中就不會出現，不需要額外防線）。
- 真的抽不出東西（< `MIN_TEXT_CHARS`）寫 `extraction_log`（`stopped_at='scanned'`）但不寫 chunk。**這與「一律入庫」不衝突**：前者說的是「品質分數低不擋」，後者說的是「連文字都沒有就無從切塊」——兩條閘的判準不同。
- `is_admin` 閘與 `is_research` 閘**都保留**（205 ＋ 1,149 筆維持不入庫），但一律寫 `extraction_log` 記對應的 `stopped_at`。

v1 的 §3.2 最後一列寫「品質不過 → `needs_review`，**不入庫**」，那跟診斷 #4 抱怨的是同一種病，只是換了判定條件。**不入庫不會讓失敗歸零，只會讓它換一張紙躺著。**

**驗收**

- `eval_extraction.py` 的**閱讀順序正確率相對 `E0` 基準提升**，且 **每一家券商子集都不得退步**（反轉守門）。
  絕對門檻在基準線量出來之前不預設；**這條規則是本輪的決定，量完不得為了讓數字好看而放寬。**
  > v2 說這是「對齊 `docs/ROADMAP.md` 對 eval 門檻的既有立場」——**引錯了**。「政策決定、不擅自更動」那句在 `CLAUDE.md` 與 `README.md`，而且講的是 RAGAS 那三個絕對門檻；`docs/ROADMAP.md` 寫的其實是「回歸偵測靠 `scripts/eval_compare.py` 比兩份結果，**不靠絕對門檻**」，還記載了 `answer_relevancy` 門檻由 0.85 校準到 0.55 的前例。這條規則不借外部權威，它就是這一輪自己定的。
- **`pages_failed` 非空的檔案數 > 0**——若為 0，八成是新的例外處理又把失敗吞掉了，這條是反轉守門。
- **`extraction_log` 的列數 == 抽取快取的 unique `file_hash` 數**，且 `stopped_at` 各組加總 == 該數；`stopped_at='ingested'` 的列數 == `research_report` 的列數。
  > v2 這條寫的是「`extraction_log` 的列數 == 鏡像目錄的檔案數（16,846）」，**在自己指定的 schema 下永遠不可能成立**：122 組重複內容檔會塌成同一列、2 個 `.jpg` 不在 `EXTS = {".pdf", ".docx", ".doc"}` 內從不進管線，hash-keyed 的上限是 **16,722** 列，恆比 16,846 少 124。而且鏡像本身每 3 小時就增長（本次覆核期間就從 16,846 長到 16,850）。**拿一個結構上不可能相等的等式當驗收，等於上線第一天就製造一個永久紅燈**——CLAUDE.md 記載過那種告警兩週內就會被當成背景噪音。
- 既有 `tests/` 全綠；`full_text` 序列化對同一份 PDF 兩次執行結果**逐字元相同**。

**風險**：`full_text` 內容改變 → `report_takeaway.text_sha256` 全數失效（見 §6.1）。

---

### 4.3 `E4` — 欄位擷取改為「定位 → 局部擷取 → 錨回驗證」

**交付**

- `app/services/extraction/locate.py`：關鍵詞 ＋ 券商別名詞表，定位「評等／目標價／TP／Rating／EPS／預估」出現的 Block，取周邊 ±N 字**加上該頁 table cells** 組成 evidence window。
- `app/services/signal_extract.py` 改為：每份研報 **N 個小 window** 的擷取，不再是一次 16000 字的截斷。
- 驗證層：LLM 必須回傳原文 span → 用 `app/services/reading/anchor.py` 的 `locate_quote()` 錨回，**且只接受 `Anchor.method in ("exact", "normalized")`**。
- **錨不回（含 `method == "prefix"`）的政策：降級為 `partial` ＋ 把「卡在哪一關」寫進 `error_detail`，不是 `rejected`。**
- 數值合理性檢查：目標價／報告日收盤價落在 0.2x–5x 之外即 `partial` 並記 flag；幣別與市場一致。

**為什麼必須排除 `prefix` 層（這是覆核抓到的 blocker）**

`locate_quote()` 是三層退讓：精確 → 正規化 → **正規化前綴**。第三層只要引文的**前 48／32／20 個正規化字元**唯一命中就回傳 `Anchor(..., "prefix")`，**尾巴完全不驗**：

```python
for k in PREFIX_STEPS:              # (48, 32, 20)
    if len(nq) <= k: continue
    pos = _find_unique(ntext, nq[:k])
    if pos is not None:
        return Anchor(*_to_orig(idx, pos, k), "prefix")
```

而 `E4` 要防的正是「模型把目標價寫錯或編出來」，**目標價幾乎總在句尾**。實測（400 筆帶數字的真實 `report_takeaway.quote`，錨回基準為 `clean_extracted(full_text)`）：

| 情境 | 結果 |
|---|---|
| 原引文可錨回 | 379 / 400（`exact` 237、`normalized` 142、**`prefix` 0**） |
| 把末位數字竄改成 `99999` 後**仍判「錨得回」** | **286 ＝ 75.5%** |
| 那 286 筆走的退讓層 | **全部是 `prefix`**；`exact`／`normalized` 誤中 **0** 筆 |
| **拒收 `prefix` 之後的攔截率** | **24.5% → 100.0%**，且對合法引文**零誤殺** |

照 v2 的寫法直接用 `locate_quote()`，四分之三的竄改數值會被判定成「有原文依據」，§5 的「幻覺率」會系統性偏低，而 `E4` 驗收的 `accuracy ≥ 0.95` 會被這個假綠掩護。

修法是一行條件，不必動 `anchor.py`——`Anchor` 本來就回傳 `method` 欄位（它存在的理由就是「供稽核錨定品質」）。**閱讀頁的摘錄仍然照收 `prefix`**：那裡的代價只是跳轉位置略偏，與數值驗證是兩回事。
**這組實測的邊界**：樣本是摘錄引文，它們由 `extract_takeaways.py` 以「逐字引用」的指令產生，所以原文命中率高、零 `prefix`。`E4` 的 evidence span 來自不同 prompt，可能較常被模型改寫，因此「零誤殺」這一格要在 `E0` 上重驗——若 `E4` 的合法 span 有相當比例落在 `prefix`，該檢討的是擷取 prompt，不是放寬驗證。

**為什麼是 `partial` 不是 `rejected`**

`app/services/radar/queries.py` 的 `VALID_STATUSES = ["valid", "partial"]`——雷達的讀取端兩種都吃，`partial` 只讓 `coverage_state` 降級標記；`rejected` 則是整筆消失。現況存量是 12,629 列訊號（valid 10,352／partial 2,218／**rejected 僅 59，0.5%**），全表 `target_price IS NOT NULL` 共 5,630 列（其中 5,624 列狀態為 `valid`）。新增嚴格錨回驗證後這個比例一定會跳，而**錨不回的主因很可能是模型自己做了繁簡轉換、千分位或全半形正規化，不是幻覺**。直接 `rejected` 會讓雷達覆蓋率斷崖，而且分不出兩者。

**但 `partial` 不是完全免費**：`app/services/radar/queries.py:282` 的 `count(DISTINCT broker) FILTER (WHERE extraction_status = 'valid')` 只數 `valid`，所以 `valid → partial` 會壓低 `sig_brokers`，而 `sig_brokers < broker_count` 就會讓 `/api/radar/instruments` 的 `coverage_state` 標成 `partial`。驗收要看這個數字。
> v2 說它「連帶影響 `--min-brokers` 的取材」——**那條因果鏈不存在**。`--min-brokers` 是 `scripts/extract_signals.py` 的子集門檻，算的是 `count(DISTINCT r.source)`，來源表是 `research.research_report`，SQL 裡完全沒有 `report_signal` 更沒有 `extraction_status`。

**驗收**：`E0` 標註集的目標價 coverage ≥ 0.90、accuracy ≥ 0.95；`partial` 率可解釋（每一筆都能指出是哪一關卡掉的）。

**設計理由（v1 歸錯因，此處更正）**：v1 說失敗來自 16000 字截斷。全母體實測：**截斷只解釋 1.13%**（5,200 篇長報告中 59 篇的關鍵詞首次出現在視窗之後），**8.73% 是「關鍵詞就在視窗內、卻一筆 `target_price` 都沒抽到」**（有訊號的 2,944 篇中 257 篇）。也就是說主要失敗是**模型在長雜訊上下文裡沒抓到**。定位→局部仍然是對的解法——它解的是「聚焦」不是「解截斷」——但驗收指標要照真正的失敗模式設計。

---

### 4.4 延後的里程碑，以及延後的理由

| v1 里程碑 | 決定 | 理由 |
|---|---|---|
| `E2` OCR／VLM 階梯 | **不建**。降級為「處理那 116 檔的一次性小工具」，本輪不做 | 實測 `scanned` 0.70%、亂碼率 >20% **0 筆**。60x–400x 的成本服務 ≤0.8% 的檔案。**另外本機是 20 核／19GB RAM／CPU-only torch，L2 VLM 本地根本跑不動**，走外部 API 又是另一條成本與資料外流決策 |
| `E3` 結構感知切塊 | **延後** | 它要求全量重新嵌入（見 §6.3 的 56 小時），而 `E1` 已經帶著同一筆成本。兩件事綁在一起會讓「順序變好」與「切法變好」無法歸因。Block 模型在 `E1` 就位之後，`E3` 只剩改 `chunk.py` 一件事 |
| `E5` 券商 profile ＋ fingerprint | **延後** | v1 寫「語料量前 20 家皆有 profile」，但**全語料只有 30 個 source，前 4 家（kgi 5,383／masterlink 3,814／sinopac 1,338／yuanta 1,208）已佔 77.8%**，累積到第 7 家（fubon）是 88.5%。真要做時規模是 6–7 家不是 20 家。且 v1 的 drift 告警**沒有指定消費端**，接不上 `scripts/incident_handler.sh` 就會重演 CLAUDE.md 記載的「854 筆告警沒人讀」 |

---

## 5. 品質指標定義

全部純程式可算，無 LLM。`app/services/extraction/quality.py` 是唯一計算來源（兩份實作必然漂移）。

| 指標 | 定義 | 用途 |
|---|---|---|
| `chars_per_page` | 抽出字元數 ÷ 頁數 | 粗篩空白／掃描 |
| `garbled_ratio` | (私用區 U+E000–F8FF ＋ U+FFFD) ÷ 總字元 | 偵測 CID 缺 ToUnicode |
| `layout_coverage` | 抽出文字 bbox 總面積 ÷ 頁面內容區面積 | **偵測「漏抽整塊」最靈敏的單一指標**（pypdf 無 bbox，故此欄的基準線只能從 `E1` 起算；docx 回 `null`） |
| `column_count` | 每頁 word bbox 的水平覆蓋直方圖中，落在頁寬 25–75% 之間的最長空白帶 ≥ 5% 頁寬即判 2 欄 | 界定「雙欄子集」，`E1` 的驗收要分子集看。**閾值待 `E0` 校準**（見 §A.1） |
| `pages_failed_ratio` | 頁級失敗數 ÷ 頁數 | 取代靜默吞例外 |

`quality_score` = 上列加權（權重寫在 `quality.py`，可調，變動時 bump `extraction_version`）。

**v1 的 `cross_extractor_delta` 拿掉了。** 它要求 PyMuPDF 與 pdfplumber 兩套都跑（與 v1 §3.2「L0 成本 ~0」自相矛盾），而實測 12 份樣本裡兩個抽取器**全部不一致**——這條指標會直接飽和成雜訊，且只能偵測「不一致」不能判「誰對」。

驗收層另有四項（`scripts/eval_extraction.py`，需 golden set）：

- **閱讀順序正確率**（主指標）：golden 關鍵句在抽取結果中的 (a) 命中率、(b) 相對順序正確率（逆序對比例 / Kendall tau）
- **欄位 coverage**：有值的欄位數 ÷ golden 有值的欄位數
- **欄位 accuracy**：值正確的欄位數 ÷ 有值的欄位數
- **幻覺率**：evidence span 錨不回原文的比例。**計算時 `Anchor.method == "prefix"` 一律算「錨不回」**——理由見 §4.3，用完整的 `locate_quote()` 語意會讓這個指標系統性偏低。

**coverage 與 accuracy 必須分開看**：只看 coverage 會獎勵亂猜，只看 accuracy 會獎勵什麼都不填。

**文字召回率不再是主指標**：實測 pdfplumber 與 pypdf 抽出的字元數 12 份裡 11 份逐字相等，它量不到本計畫要修的東西。仍會記錄，但只當健檢（大幅下降＝新抽取器在吃字，如同 pypdf layout 模式那樣）。

---

## 6. 相容性與回填

### 6.1 `full_text` 契約

`db/schema.sql` 已寫明：`report_takeaway.quote_start`/`quote_end` **錨定於 `clean_extracted(full_text)`**，並用 `text_sha256` 驗章防 offset 漂移。

`E1` 改變抽取器 ⇒ `full_text` 逐字元改變 ⇒ **既有摘錄的 `text_sha256` 全數失效**。這不是 bug，驗章正是為了在這一刻報出來。處置：

1. `full_text` 仍存**未清理的序列化文字**，維持「錨點基準字串／餵 LLM 的 excerpt／API 回傳文字三者同源」的既有契約——**不要趁機改這條**，一次只改一件事。
2. 序列化必須是**決定性**的（同一份 PDF、同一 `extraction_version` → 逐字元相同），否則驗章會變成隨機紅燈。`E1` 驗收含這一條。
3. 摘錄會自動重擷取，**不需要遷移腳本**——但**機制不是 v1 說的 `ON DELETE CASCADE`**（重抽是 UPDATE 不是 DELETE，CASCADE 根本不會觸發）。真正的機制是 `scripts/extract_takeaways.py` 的 checkpoint 判斷 `_is_done()`：它比對 `extraction_version` **與 `text_sha256`**，sha 不符就重跑。凡是靠 sha 比對做 checkpoint 的批次都適用這條，寫錯機制會讓人漏掉其他同型批次。
4. 重跑範圍是 **1,096 篇 / 5,432 列**（不是全語料 15,089 篇——摘錄覆蓋率目前只有 7%），比 v1 暗示的成本小得多。但它會 spawn `claude` CLI 搶 `scripts/_claude_lock.py` 那把 flock，**排在 sync timer 時段之外並帶 `--limit`**。

**另一個 v1 高估的地方**：閱讀頁**已經不靠 `quote_start` 錨點跳轉**了，點摘錄是拿逐字引文跑 PDFium 搜尋（CLAUDE.md 記載，全語料實測 99.3%）。`quote` 本身是原 PDF 裡的句子，換抽取器不會讓它失效。所以 `full_text` 變動的實際衝擊面比 v1 §6.1 描述的窄——真正要小心的是新引文的**來源**（§4.2 的表格防護），不是舊引文的錨點。

### 6.2 回填順序：解耦兩段

```
E0 基準線
  →  E1 第一段：只寫 full_text ＋ 品質欄位 ＋ extraction_version ＋ extraction_log
     （純 CPU 抽取，16,555 檔 × 3.45s ÷ 16 workers ≈ 1 小時；不碰向量）
  →  E4 立刻受益（它吃的是 full_text，不是 chunk）
  →  E1 第二段：重切 ＋ 重嵌入，排成可中斷的背景批次，依 extraction_version 逐批推進
  →  摘錄重擷取（1,096 篇，避開 sync 時段）
```

**這段期間 `full_text` 與 `report_chunk` 來自不同抽取版本。** 結構上不衝突（CLAUDE.md 已記載「`report_chunk.content` 本來就不是 `full_text` 的子字串」），代價是**檢索品質的改善會延後到第二段跑完**。這是刻意的取捨：欄位擷取的改善不必等 56 小時。

**混版是躲不掉的，不是這個方案的缺點。** `scripts/sync_new_reports.sh` 每 3 小時就會用新抽取器寫入新研報，所以只要 `E1` 上線，語料庫當下就同時存在 v1 與 v2。**`extraction_version` 因此必須是一等公民**，這在任何回填策略下都成立。

### 6.3 為什麼不一次做完

BGE-M3 在本機實測 **2.9 chunk/s**（20 核、CPU-only torch、batch 8；冷載入 86.7 秒）。**585,942 列 chunk 全量重嵌入 ≈ 56 小時連續滿載。**

一次到底的問題不只是 2.3 天服務降級，而是它正好落在 `scripts/ingest_lowio.sh` 最危險的窗口：該腳本 `ALTER SYSTEM SET fsync=off` 並以 `trap ... EXIT` 還原，**而 trap 擋不住 SIGKILL**（OOM killer、WSL 被收掉），`ALTER SYSTEM` 又寫進容器內 pgdata 的 `postgresql.auto.conf`、重啟也不會恢復。**長時間高 I/O 作業正是最可能觸發 OOM killer 的情境。**

分批推進讓每一批都短到可以在單次維護窗內跑完並 `make restore-durability`。無論如何，**回填結束後務必跑 `make restore-durability` 並以 `make db-audit` 覆核**。

### 6.4 回滾

轉檔到 `data/extracted/<hash>.json` 時，**把 `data/extracted/all.jsonl` 複製一份帶時間戳的唯讀副本**（例如 `data/extracted/archive/all-pypdf-<date>.jsonl`）作為封存。這是本計畫唯一的回滾路徑：`full_text` 是覆蓋寫入、不另加 `full_text_prev` 欄位（15,089 × ~20k 字元的重複儲存不值得），但只要封存還在，pypdf 版本的文字隨時可以重放回去。

**必須是複製而不是原地留存**：`scripts/sync_new_reports.py` 每 3 小時就往 `all.jsonl` append 一列，原地留存等於把一個會被生產排程持續改寫的檔案當成不可變封存。（v2 就是這樣寫的。）

§9 第 3 步的「只讀不寫並排比對」是**改動生產路徑之前**的關卡；封存是**之後**的保險。兩者都要。

### 6.5 監控落點

品質欄位進 `web/routers/monitor.py` 的 `/api/progress`，監控頁新增一張「抽取品質」卡片（`needs_review` 計數、`pages_failed` 非空計數、`extraction_log` 按 `stopped_at` 分組）。

**這是本計畫唯一的前端改動，而且不是選配**：`/api/progress` 是 SPA `/monitor` 頁的資料來源（`frontend/src/features/monitor/useProgress.ts` 每 5 秒輪詢、以 `progressSchema.ts` 的 zod 物件解析），**zod 預設 `strip`，未宣告的鍵不報錯、直接安靜丟掉**。只改後端等於資料一路送到前端卻從未進 DOM——`monitor.py` 的註解記載 `takeaway`／`signal` 兩個欄位就是這樣從 P4 起一路靜默至今。所以這條要三件一起做：

1. `web/routers/monitor.py` 補鍵
2. `frontend/src/features/monitor/progressSchema.ts` 補宣告 ＋ 新增 Panel 元件掛進 `MonitorPage.tsx`
3. `make build-web`

`make freshness`（`scripts/check_batch_freshness.py`）**不動**——它量的是「派生資產有沒有停更」，抽取品質是另一個維度，塞進去會讓那支偵測器的四碼退出碼語意變糊。

---

## 7. 基準線（`E0` 第一批，2026-09-02）

量測工具 `scripts/eval_extraction.py`，golden set `eval/extraction_dataset.json`（15 份：kgi 4／masterlink 4／sinopac 3／yuanta 3／goldman_sachs 1；184 條有序片段、1,059 組配對；欄位層 25 筆目標價、33 筆評等）。結果檔 `eval/baselines/extraction-pypdf-2026-09-02.json`，可用 `make eval-compare` 與後續版本比。

**標註狀態**：15 份已於 2026-09-02 人工覆核（`annotation_status = reviewed`）。覆核以模型草標為底（看頁面渲染圖＋ pdfplumber 逐行文字），覆核未改動任何片段或欄位，數字與草標版相同；草標版結果檔留作 `extraction-pypdf-2026-09-02-draft.json`，兩份因 `n_reviewed` 不同刻意不可比。覆核用 `scripts/review_extraction_golden.py`：它把每頁渲染成圖、在圖上框出每條片段並標序號，同時標出 pypdf 找不到（打字有出入）與出現多處（位置有歧義）的片段——後者評測照首次出現算，首次就是要的位置（目次項、頁首、封面標題在後頁重複）可以不改，否則延長到唯一。

| 指標 | pypdf 6.12.2（現況） | pdfplumber spike（`ext-2026-08-31.v1`） | pdfplumber `ext-2026-09-02.v3` | E1 | E4 |
|---|---|---|---|---|---|
| 閱讀順序：關鍵句命中率（全體） | **1.000** | 0.962（漏 7 句，見下） | **1.000** | | |
| 閱讀順序：相對順序正確率（全體配對） | **0.805** | 0.879 | **0.922** | | |
| 閱讀順序：相對順序正確率（**同一文字流內**配對） | **0.940** | 0.989 | **1.000** | | |
| 閱讀順序：相對順序正確率（雙欄子集，13 檔） | 0.798 / 同流 0.948 | 0.871 / 同流 0.986 | 0.917 / 同流 1.000 | | |
| 各券商：kgi（4） | 0.758 / 1.000 | 0.914 / 0.948 | 0.938 / 1.000 | | |
| 各券商：masterlink（4） | 0.972 / 1.000 | 0.873 / 1.000 | 0.972 / 1.000 | | |
| 各券商：sinopac（3） | 0.816 / 0.880 | 0.833 / 1.000 | 0.833 / 1.000 | | |
| 各券商：yuanta（3） | 0.726 / 0.919 | 0.938 / 1.000 | **1.000 / 1.000** | | |
| 各券商：goldman_sachs（1） | **0.321** / 0.731 | 0.833 / 1.000 | 0.744 / 1.000 | | |
| 目標價 coverage（現況 `report_signal`） | 0.680（17/25） | 同左（欄位層與抽取器無關） | 同左 | | |
| 目標價 accuracy | 1.000（17/17） | 同左 | 同左 | | |
| 評等 coverage / accuracy | 0.545 / 1.000 | 同左 | 同左 | | |
| 幻覺率（evidence 錨不回比例，`prefix` 計為錨不回） | 0.059（1/17） | 0.176（3/17）＊ | 0.118＊ | | |
| 文字召回率（健檢：去空白字元合計） | 260,611 | 264,424 | 264,456 | | |

\* pdfplumber 那欄的幻覺率**不是**幻覺變多：現況 evidence 是模型對 pypdf 全文寫的，拿去錨 pdfplumber 的序列化文字，表格被轉成 markdown 後原句就不再逐字存在。這欄要等 `E4` 用新抽取文字重擷取後才有意義。

**基準線說了什麼**（都是 draft 標註上的暫定結論）：

1. **現況的問題不是「抽不到」，是「順序」**——184 句 pypdf 全部命中，但配對正確率只有 0.805；即使不計跨欄慣例、只看同一文字流內部，仍有 6.0% 的句對顛倒。最差的是高盛雙欄（0.321）與凱基 daily story（華通 0.545）。這與 §1.2 「字元召回率量了不會動、主指標要換成順序」的判斷一致。
2. **pdfplumber 把同流配對從 0.940 拉到 0.989、高盛從 0.321 拉到 0.833**，但**不是全面更好**：masterlink 全體配對從 0.972 掉到 0.873（側欄與主文的相對位置變了），且 v1 漏抽 7 句。v2（2026-09-02）逐條追出三個成因並修掉，命中率回到 1.000：(a) `台股一週大勢分析`／`公司拜訪快報` 是文件標題同時當後頁頁眉，「重複＋位置」把首頁那份真標題一起丟了——改成頁首帶重複文字**第一次出現保留**；(b) 元大早報 p1 左欄清單的「評等」欄只有 20pt 寬卻詞數夠多，欄偵測把整頁切成三欄、每列的「買進」被甩到另一條文字流——加 `_MIN_COL_WIDTH_FRAC`，窄欄併回空隙較小的相鄰欄；(c) 高盛 Key Data 是真表格，序列化成 markdown 後多了 `|`——那是序列化產物不是版面順序資訊，評測比對前拿掉分隔符。v3（同日）再追 masterlink 全體配對 0.972→0.878 的差距：**不是慣例、是欄偵測缺陷**——三份首頁全部沒被切成雙欄，側欄與主文依 y 交錯。成因四個：(d) 投影用布林「有沒有墨」，版心帶內 4–18 個跨欄詞（占 0.8–2.9%）就把溝槽填滿——改為計數、容許 `_GUTTER_MAX_CROSS_FRAC` 以下的跨欄詞；(e) 英文版側欄只占 33%，溝槽落在 35.5% 被 `_GUTTER_BAND` 下限 0.18 擋掉——降到 0.15；(f) 側欄「標籤…數值」之間的稀疏帶被投影成第二條溝槽，之後因詞數不足**整頁**退回單欄——弱欄改成併回鄰欄，並新增「七成以上是數字就不是一欄」（`_NUMERIC_COL_MAX_FRAC`）；(g) 「跨欄＝第 0 欄」讓凱基頁尾橫幅表格排到右欄之前——`Block` 加 `band`，跨欄元素把頁面切成上下幾段、段內才分欄，另加「頁首帶右上角的報告類型標籤歸第 0 欄」的脫離規則（要求與同欄下一段有明顯間距，否則版心從頁頂開始的頁會把右欄首段搬走）。結果：**同流配對每家 1.000，全體配對每家不低於 pypdf**（masterlink 持平 0.972），達到 §4.2 的驗收規則。全體配對剩下的差距（sinopac 0.833、高盛 0.744）是側欄相對主文的位置慣例：永豐與高盛的側欄實際在主文之後才被讀到，golden 標「左側欄先於主文」，這條是標註約定不是抽取錯誤。
3. **欄位層漏抽比錯抽嚴重得多**：目標價 accuracy 1.000、coverage 0.680；評等 coverage 只有 0.545。8 筆漏掉的目標價全部在多標的檔（元富晨訊、元大早報、凱基 Radar）——一次餵 16,000 字要模型回完整 schema，它挑了幾檔就停，和 §1.2 「主要失敗是模型在長雜訊上下文裡沒抓到」一致。`E4` 的定位→局部擷取正是對這個。
4. **幻覺率 0.059 是現況的樂觀值**：只有 17 筆 evidence 可查，而且 golden set 沒有「報告無目標價卻抽出數值」的樣本被命中（同欣電 golden 為 null、現況也沒抽）。

> 附錄 A 那組數字是**語料層級的統計**，不是 golden set 上的指標，兩者不可互相代入。golden set 第二批要補：掃描檔、亂碼檔、`.docx`、以及元富晨訊裡本批因段落標題與目標價句分行而未對上的其餘標的。

---

## 8. 明確不做的事與理由

| 不做 | 理由 |
|---|---|
| 用 PyMuPDF | AGPL-3.0，而本站經 Cloudflare Tunnel 對外服務（§3.2） |
| 用 pypdf 的 `layout` 模式 | 實測靜默吃掉最多 87% 字元（§3.2） |
| 一次換到 MinerU／Docling／Marker 當主軌 | 它們是 VLM 路線的候選，而 VLM 路線服務的是 ≤0.8% 的檔案，且本機 19GB RAM／CPU-only 跑不動 |
| 為每家券商寫 parser | 券商會改版，parser 分支只會累積不會刪除。真要做時走 YAML config（延後，§4.4） |
| 用 LLM 做 profiling／品質評分 | 每檔一次 LLM 呼叫，成本與延遲都不成比例，而且**評分本身變得不可重現**。§5 全部純程式是刻意的 |
| 修 `clean_extracted` 的 CJK 空白邏輯 | **理由更正**：v1 與 v2 都說它「會動到 `content_norm` generated column 與 `tests/test_content_norm_equivalence.py`」——**那條耦合不存在**。`content_norm` 的 GENERATED 表達式對應的是 `norm_for_match()`（只用 `_RE_ALL_WS`），與 `clean_extracted`（用 `_RE_CJK_GAP`）在 `textnorm.py` 裡是兩個獨立函式、不共用任何 regex；`tests/test_content_norm_equivalence.py` 全檔沒有出現過 `clean_extracted` 一次。真正的理由是：它是 `report_takeaway.quote_start` 的錨定基準，而且一改就要重切重嵌 585,942 列 chunk。表格欄界改用可見分隔符穿透（§4.2）繞開它 |
| 把 `is_admin`（205 筆）與 `is_research=false`（1,149 筆）入庫 | 行政通知、封面頁進了語料庫只會汙染檢索。改為寫 `extraction_log` 記對應的 `stopped_at`，可查可回收（§4.2） |
| 追那 96 個孤兒標籤檔的根因 | `data/tags/` 裡有 96 個 hash 不在抽取快取內的標籤檔（推測是鏡像已刪／改名的舊檔）。per-hash 轉檔時它們自然會被隔離出來，那是第一步；根因留給下一輪 |
| 把抽取評測放進 CI | 對齊 `docs/ROADMAP.md` 對 RAGAS 的既有決定：會與定時同步互搶 `claude` CLI。手動跑 ＋ `eval_compare` 比兩份結果 |
| 把本檔加入 `LIVING_DOCS` | 它對尚未存在的檔名做敘述，加進去等於要求提案階段就先建空檔 |

---

## 9. 開工順序（可直接執行）

1. **`scripts/profile_corpus.py`**——唯讀，不動任何既有路徑。輸出每檔的欄數、亂碼率、頁級失敗、字元密度分佈。附錄 A 的一次性腳本可以直接長成它。**已做（PR #235）。**
2. **`eval/extraction_dataset.json` 的前 15 份標註**（先窄後寬，優先蓋 kgi／masterlink／sinopac／yuanta 這四家＝77.8% 語料），配 `scripts/eval_extraction.py` 跑出 pypdf 基準，填進 §7。**已做（2026-09-02，含人工覆核）。**
3. **`app/services/extraction/layout.py` ＋ `quality.py`，只讀不寫**：對同一批檔案跑新舊兩路，把差異印出來人工看 20 份。**已做（PR #235，`scripts/compare_extractors.py`）**；§7 的 pdfplumber v1 欄指出的三個漏抽成因已於 v2（2026-09-02）修掉，golden set 命中率回到 1.000、同流配對 0.989。
4. 差異可接受後才動 `app/services/extract.py`、`db/schema.sql`、與抽取快取的 per-hash 轉檔（四個端點，§4.2）。**拆四個 PR**（E1a 門面與 `EXTRACTOR` 旗標、E1b schema 與 `extraction_log`、E1c per-hash 快取、E1d 切換與回填），前三個合併後生產行為不變。**E1a 已做（2026-09-02）**：`extract_text()` 簽章不變、預設仍 pypdf；`ExtractResult` 加 `extractor`／`extraction_version`／`page_count`／`pages_failed`／`quality`／`blocks`；pypdf 路徑的逐頁例外改記 `pages_failed`；pdfplumber 路徑只在拋例外或低於 `MIN_TEXT_CHARS` 時退回 pypdf 並記原因。**E1b 已做（同日）**：`research_report` 七欄、`extraction_log` 第十一張表（`stopped_at` 詞彙由 `store.STOPPED_AT` 與 schema CHECK 逐字對齊、golden 清單同步）、`ingest_all` 與 `sync_new_reports` 每一道閘都寫一列、`needs_review` 由 `EXTRACTION_REVIEW_MIN`（0.6）與 `pages_failed` 決定且只標記不擋。**合併後要 `make schema`**（新表與新欄都是冪等補丁，既有庫直接套）。
5. `E4`。

第 3 步的「只讀不寫並排比對」不要跳過。它是唯一能在改動生產路徑**之前**發現「新抽取器在某類檔案上更差」的機會——而實測已經證明 pdfplumber 與 pypdf 的輸出**必然不同**（相似度 0.275–0.884），「不同」不等於「更好」，那正是第 2 步的基準線要回答的。

---

## 附錄 A：v3 引用的實測數字與重跑方式

量測日期 **2026-08-31**。所有指令唯讀。

| 數字 | 值 | 怎麼重跑 |
|---|---|---|
| 鏡像檔案數 | 16,846（覆核期間增為 16,850——**這是移動標的**） | `find 研報自動匯入 -type f \| wc -l` |
| 鏡像副檔名 | pdf 16,810 / docx 34 / jpg 2 | 同上 ＋ `sed 's/.*\.//'` |
| 鏡像可支援副檔名（`EXTS`） | 16,844 | `.pdf/.docx/.doc` 過濾 |
| 鏡像 unique sha256 | 16,724（**122 組重複內容，涵蓋 244 檔**） | 逐檔 sha256 後 group |
| hash-keyed 表的可達上限 | **16,722** 列（恆比鏡像檔數少 124） | 16,724 − 2 個 jpg |
| 抽取快取 unique `file_hash` | 16,555 | `data/extracted/all.jsonl` |
| `scanned` | 116（0.70%） | 掃 `all.jsonl` 的 `scanned` 旗標 |
| `is_admin` | 205（與 `scanned` 重疊 4） | 掃 `all.jsonl` 的 `is_admin` 旗標 |
| 標籤檔 | 16,334 | `ls data/tags \| wc -l` |
| **無標籤檔的抽取紀錄** | **317**（＝116 ＋ 205 − 4，**異常殘量 0**） | 集合差，不是 16,555 − 16,334 |
| 孤兒標籤檔（hash 不在快取內） | 96 | 反向集合差 |
| `is_research=false` 標籤檔 | 1,245（其中 hash 在快取內者 **1,149**） | 掃 `data/tags/*.json` |
| `market` 為 null 的標籤檔 | **3**（三筆同時 `is_research=false`，故此閘從未獨立擋掉任何一筆） | 同上 |
| `research_report` | 15,089（PDF 15,055／docx 34） | `select count(*) from research.research_report` |
| `report_chunk` | 585,942 | `select count(*) from research.report_chunk` |
| 摘錄 | 5,432 列 / 1,096 篇（覆蓋率 7.3%） | `report_takeaway` |
| 訊號 | 12,629 列（valid 10,352／partial 2,218／rejected 59）/ 5,384 篇 | `report_signal` group by `extraction_status` |
| `target_price IS NOT NULL` | **5,630**（其中 `valid` 5,624） | `report_signal` |
| 亂碼率 >20% / >2% | **0** / 26 | 對快取的 text 算 `[U+E000–F8FF, U+FFFD] ÷ len` |
| 亂碼率 p50/p90/p99/max | 0 / 0.0035 / 0.0134 / 0.080 | 同上 |
| 超過 16,000 字 / 32,000 字 | 5,211（31.7%）/ 2,473（15.0%） | 同上 |
| 長報告（>16,000 字） | 5,200 篇，其中含「目標價」3,062 篇 | `length(full_text)>16000` |
| **「目標價」首次出現在 16k 之後** | **59 篇＝全體 1.13%**（含關鍵詞者的 1.93%） | 對 `regexp_replace(full_text,'\s','','g')` 取 `position('目標價' in t)` |
| **關鍵詞在視窗內但一筆 `target_price` 都沒抽到** | **257 篇＝有訊號長報告（2,944）的 8.73%** | 同上，left join `report_signal` 後以研報為單位聚合 |
| BGE-M3 吞吐 | 2.9 chunk/s（冷載入 86.7s） | `embed_texts` 32 筆計時 |
| 全量重嵌入 ETA | **56.1 小時** | 585,942 ÷ 2.9 ÷ 3600 |
| 券商 | 30 個 source；累積 top4 77.82%、top7 88.45%（第 8 名是 `source` 為空的 331 筆） | `select source, count(*) ... group by 1` |
| 硬體 | 20 核 / 19GB RAM / CPU-only torch | `nproc`、`free -g` |
| `locate_quote()` 對竄改數值的漏判 | 400 筆引文中 379 筆可錨回（`prefix` 0）；竄改末位數字後 **286＝75.5% 仍過關，全部走 `prefix`** | 取 `report_takeaway.quote` 對 `clean_extracted(full_text)` 跑 `locate_quote()`，再把最後一個數字串換成 `99999` 重跑 |

**抽取器並排實測**（**12 份**跨券商樣本，199 頁；`norm` ＝去除所有空白後比較，相似度用 `difflib.SequenceMatcher.ratio()` 取前 6000 字元）：

| 券商 | 頁 | 判雙欄頁 | pypdf 字元 | pypdf layout | pdfplumber | pypdfium2 | pypdf 秒 | plumber 秒 | pdfium 秒 | sim(pypdf,plumber) | sim(pypdf,pdfium) |
|---|---|---|---|---|---|---|---|---|---|---|---|
| citic | 18 | 0/18 | 15,837 | **2,045** | 15,837 | 15,837 | 0.71 | 1.45 | 0.38 | **0.275** | 1.000 |
| goldman_sachs | 12 | 0/12 | 40,929 | 40,089 | 40,929 | 40,929 | 0.69 | 3.16 | 0.41 | 0.514 | 1.000 |
| morgan_stanley | 19 | 3/19 | 52,687 | 40,667 | 52,695 | 52,686 | 0.86 | 2.79 | 0.45 | 0.593 | 0.999 |
| kgi | 31 | 3/31 | 58,077 | 28,130 | 58,077 | 58,035 | 2.52 | 7.11 | 0.48 | 0.629 | 1.000 |
| nomura | 10 | 1/10 | 36,788 | 36,788 | 36,788 | 36,788 | 0.36 | 1.31 | 0.20 | 0.672 | 1.000 |
| cathay | 20 | 1/20 | 20,014 | 19,072 | 20,014 | 20,014 | 1.80 | 5.65 | 0.66 | 0.727 | 0.999 |
| yuanta | 16 | 1/16 | 17,574 | 14,233 | 17,574 | 17,574 | 0.97 | 1.15 | 0.10 | 0.749 | 1.000 |
| masterlink | 7 | 0/7 | 7,252 | 6,554 | 7,252 | 7,252 | 0.55 | 0.64 | 0.49 | 0.768 | 1.000 |
| president | 24 | 2/24 | 8,824 | 6,051 | 8,824 | 8,824 | 1.39 | 0.71 | 0.10 | 0.812 | 0.992 |
| fubon | 8 | 3/8 | 10,469 | 4,207 | 10,469 | 10,469 | 0.53 | 0.96 | 0.03 | 0.834 | 1.000 |
| ubs | 29 | 7/29 | 125,010 | 120,059 | 125,010 | 124,988 | 1.21 | 15.76 | 4.45 | 0.847 | 0.999 |
| sinopac | 5 | 4/5 | 9,962 | 9,941 | 9,962 | 9,962 | 0.42 | 0.69 | 0.06 | 0.884 | 1.000 |
| **平均／範圍** | 199 | | | | | | **1.00** | **3.45** | **0.65** | **0.275–0.884**（中位 0.738） | **0.992–1.000** |

### A.1 這組數字的效力邊界

**必須連同限制一起讀，否則會被當成基準線用。**

- **12 份不是基準線。** 順序相似度 0.275–0.884 只證明「新舊有實質差異」，**不證明新的比較好**——那正是 `E0` 要回答的。`difflib.SequenceMatcher.ratio()` 也不是正式的順序指標，只是快速判別工具。
- **v2 那張表只列 9 份，是輸出被 `tail -20` 截斷造成的未揭露刪節**，而被砍掉的三筆（citic 0.275、cathay 0.727、fubon 0.834）**正好包含最極端的一筆**。v2 §A.1 據此寫「goldman_sachs 是全組最低 0.514」也因此是錯的。這一條記在這裡，是因為它示範了本計畫要修的正是同一種病：**沉默的資料遺失比錯誤的資料更危險**。
- **`column_count` 偵測器偏保守。** 上表的 citic（相似度全組最低 0.275）與 goldman_sachs（0.514）都被判 0 頁雙欄——偵測器八成漏判。閾值要在 `E0` 上重新校準。
- **亂碼率是用現況 pypdf 的輸出量的。** pypdf 回空字串的檔案會被歸進那 116 筆 `scanned` 而不是 garbled；而「ToUnicode 錯映射到合法但錯誤的 CJK」用私用區 regex 抓不到。**116 是下界不是精確值。**
- **抽取耗時受機器負載影響大。** 同一批檔案兩次量測 pdfplumber 平均為 1.61 與 3.45 s/檔。§6.2 的「約 1 小時」用的是較保守的 3.45。
- **`select count(*) ... where not exists (report_chunk)` 為 0 是假的乾淨。** 它為 0 只是因為被擋掉的檔案連 `research_report` 那一列都沒有。這個 0 正是 `extraction_log` 存在的理由。

---

## 附錄 B：v1 → v2 的修正

**被實測推翻的假設**（詳見 §1.2）：九成 L0（實為 ≥99.2%）、截斷是漏抽主因（實為 1.13%）、文字召回率可提升 25pp（新舊字元集幾乎相同）。

**內部矛盾**

| v1 位置 | 矛盾 | v2/v3 處置 |
|---|---|---|
| `E0` 交付 vs §5 | `E0` 只交「人工核對的全文**字元數**」，§5 卻要「字元層級 **LCS**」——一個數字支撐不了 LCS | 主指標換成有序關鍵句（§5） |
| §2 非目標 vs `E3` | 「不改前端」對上「chunk 帶 `page_no` 供檢索結果標示來源頁」 | `E3` 延後；**但監控卡片仍是前端改動**，v3 把例外寫進 §2 而不是宣稱矛盾消失 |
| §3.2 vs §5 | L0 標「成本 ~0」，但 `cross_extractor_delta` 要兩套 parser 都跑 | 拿掉該指標（§5） |
| `E3` 驗收 | 「`context_precision` 不退步」，但現行 baseline `eval/baselines/baseline-2026-08-18.json` 是 **n=8 且 CP=0.666**（本來就沒過 0.8 門檻），n=8 偵測不出退步 | `E3` 延後；未來重啟時要先擴題集 |
| §8 vs `E1` | 「明確不做：修 `clean_extracted`」對上「`E1` 交付表格 Block」——表格抽對了仍會在入庫時被 `_RE_CJK_GAP` 抹掉 | 表格序列化成 markdown，用可見分隔符穿透（§4.2） |
| §3.2 vs 目標 #1 | 「品質不過 → 不入庫」對上「靜默失敗歸零」——不入庫就是繼續讓它不存在 | 一律入庫只標記不擋（§4.2） |
| `E1` schema vs 目標 #1 | 七個新欄位全掛 `research_report`，但 1,466 筆檔案沒有那一列 | 新增 `research.extraction_log`（§4.2） |

**過時或算錯的數字**

| v1 寫的 | 實際 |
|---|---|
| 57 萬列 chunk | 585,942 |
| 既有 674+ 篇的 `text_sha256` 全數失效 | 1,096 篇 / 5,432 列（覆蓋率只有 7.3%，重跑成本遠小於暗示） |
| `report_takeaway` 隨 `ON DELETE CASCADE` 清除 | **機制錯**：重抽是 UPDATE，CASCADE 不觸發。真正的機制是 `extract_takeaways.py` 的 `text_sha256` checkpoint（§6.1） |
| `E5` 涵蓋語料量前 20 家 | 全語料只有 30 個 source，前 4 家已佔 77.8%、累積到第 7 家 88.5% |

**v1 未提的遺漏**：PyMuPDF 的 AGPL 授權（§3.2）、`data/extracted/all.jsonl` 的交接面（§4.2）、回滾路徑（§6.4）、`E5` drift 告警的消費端（§4.4）、本機硬體天花板（§4.4）。

---

## 附錄 C：v2 → v3 的修正（對抗式覆核結果）

覆核方式：五個維度平行檢查（路徑符號／數字重跑／內部一致性／技術可行性／決策忠實度），每條發現再由獨立的懷疑者試圖反駁，反駁不掉的才留下。**提出 34 條、存活 26 條**，全部經人工重跑覆核。

| 嚴重度 | v2 的錯誤 | v3 的更正 |
|---|---|---|
| **blocker** | `E4` 直接用 `locate_quote()` 做錨回驗證 | 它的第三層是**前綴退讓**，只比對前 48/32/20 個正規化字元，**數值在句尾時完全不驗**：400 筆真實引文竄改末位數字後 **75.5% 仍判「錨得回」，且全部走 `prefix`**。改為只接受 `method in ("exact","normalized")`，攔截率 24.5% → 100%、合法引文零誤殺（§4.3、§5） |
| **blocker** | 驗收「`extraction_log` 列數 == 鏡像檔案數（16,846）」 | 在 hash-keyed schema 下**結構上不可能成立**（122 組重複內容檔塌成同一列、2 個 jpg 不在 `EXTS` 內、鏡像每 3 小時增長）。改為對抽取快取的 unique hash 數（§4.2） |
| major | 「不改 `clean_extracted`」的理由是「會動到 `content_norm` generated column 與其等價性測試」（重複三處） | **那條耦合不存在**：`content_norm` 對應 `norm_for_match()` 不是 `clean_extracted()`，兩者不共用 regex，測試檔全篇沒有 `clean_extracted`。改寫成真正的理由（§2、§8） |
| major | 「105 筆抽得出文字卻連標籤檔都沒有」 | **不存在**。317 筆無標籤檔 ＝ 116 `scanned` ＋ 205 `is_admin` − 4 重疊，**異常殘量 0**。原算式把集合淨差當集合差，被 96 個孤兒標籤檔汙染（§1.1） |
| major | 1,466 筆分解為「116 ＋ 105 ＋ 1,245」 | 正確分解是 **116 ＋ 205 ＋ 1,149 − 4 重疊**。原分解兩項錯誤剛好互相抵銷（1,245 − 1,149 = 96 = 201 − 105）所以總和看似對得上（§1.1） |
| major | `extraction_log.stopped_at` 列舉值 | 漏掉 **`skip_admin`**（205 筆，現況最大的單一非研報落點，且是兩條入庫路徑的第一道閘）。同時 v2 把「行政通知」錯掛到 `is_research` 上（§1.1、§4.2） |
| major | 「per-hash 轉檔要改**兩個**消費端」 | 是**四個端點**：漏了兩個寫入端 `scripts/sync_new_reports.py`（每 3 小時 append，生產路徑）與 `scripts/extract_all.py`（§4.2） |
| major | §6.4 把 `all.jsonl` 原地留存為「不可變封存」 | 與上一條直接衝突——生產排程每 3 小時往它 append。改為複製帶時間戳的唯讀副本（§6.4） |
| major | §2「不改前端」對上 §6.5「新增監控卡片」 | 同一個矛盾換地方復發。`/api/progress` 是 SPA `/monitor` 的資料來源，zod 預設 strip，只改後端等於資料從不進 DOM。改為在 §2 寫明唯一例外並在 §6.5 列出三件配套（§2、§6.5） |
| major | 抽取器實測表只列 9 份 | 真實樣本是 **12 份**，缺的三筆未揭露，且**包含相似度最低的 citic 0.275**。連帶 §A.1「goldman_sachs 是全組最低」也錯（§A.1） |
| minor | 「截斷只解釋 3.5%」「在視窗內卻沒抽到 24%」 | 來自 `limit 800`——**`LIMIT` 無 `ORDER BY` 不是隨機抽樣**。全母體重算為 **1.13%** 與 **8.73%**（比值不變，結論更強）（§1.2、§4.3） |
| minor | 「`partial` 會連帶影響 `--min-brokers` 的取材」 | 因果鏈不存在。`--min-brokers` 算 `research_report.source`，不碰 `report_signal`。真正的消費端是雷達目錄的 `coverage_state`（§4.3） |
| minor | 引 `docs/ROADMAP.md` 當「門檻定死不得改」的先例 | 該句在 `CLAUDE.md`／`README.md`；ROADMAP 寫的是「**不靠絕對門檻**」並記載門檻曾被校準。改為不借外部權威（§4.2） |
| minor | `extraction_log` 是「第十二張表」 | `research` schema 現有 **10** 張（schema.sql、生產庫、逐名列出三路一致），這是第十一張。錯誤源頭是 CLAUDE.md 的「十一張表」，已於 `41f8edf` 一併更正（§4.2） |
| minor | 「有目標價 5,624」 | 全表 `target_price IS NOT NULL` 是 **5,630**，5,624 是 `valid` 子集（§4.3、附錄 A） |
| minor | 「`market` 為空 0 筆」 | 實際 **3** 筆（結論仍成立——三筆同時 `is_research=false`，該閘確實從未獨立擋掉任何一筆）（附錄 A） |
| minor | 「全量重嵌入 57 小時」 | 585,942 ÷ 2.9 ÷ 3600 = **56.1** 小時（附錄 A） |
| minor | 「前 8 家約 88%」 | 88.45% 是**前 7 家**；第 8 名是 `source` 為空的 331 筆，不是券商（§4.4、附錄 A） |
| minor | 「表格列一旦被當成 quote，閱讀頁會顯示找不到」 | 只在該列沒有 ≥6 字元 token 時成立。多數券商評等表有（`台積電(2330)`、`1,234.56`），第 2 階會產生一個**沒有唯一性要求**的關鍵字 → 跳到任意一處。兩種退化都寫進 §4.2 |
| minor | docx（34 篇）走哪條路沒交代 | 補在 §3.2 |

---

## 附錄 D：§9 第 1／3 步的執行結果（2026-09-02）

第 1 步（`scripts/profile_corpus.py`）只跑了每家券商 3 份的抽樣（79 檔），**不是全語料**；第 3 步（`scripts/compare_extractors.py`）跑了 133 檔，報告在 `data/extraction/compare.html`。**第 2 步的 golden set 尚未開始**，所以下面全是語料層級的觀察，不是 §7 的指標。

| 觀察 | 值 |
|---|---|
| 閱讀順序相似度 `order_sim` | p05 0.241 / p50 0.583 / p95 0.906（預期：新舊必然不同） |
| 頁級失敗 | 0 檔 |
| 亂碼率 | max 0.006 |
| Block 型別占比 | paragraph 65%、**title 14%**、**footnote 9%**、header 5%、table 3%、figure_caption 2%、footer 1% |
| 舊判 `scanned`、新抽取器抽到字 | 1 檔（macquarie，42,288 字） |

**三條要在 §9 第 4 步之前處理的發現：**

1. **原始字數比是假訊號。** 133 檔裡 13 檔原始字數比 <0.9（最低 jpmorgan 0.727、宏遠 4 檔 0.79–0.81），逐檔重抽後：宏遠 5 檔、kgi、fubon、凱基期貨雙週報是 `DEFAULT_DROP` 刻意丟掉的頁首頁尾（「Company Report／公司研究報告／免責聲明」每頁重複），其餘 5 檔**去空白後字元數相等或新側更多**（勤誠 5,785＝5,785；中信 4,779＝4,779；MS 54,317 → 54,846；JPM 48,346 → 50,731）。差異全來自 pypdf 對表格每格換行、pdfplumber 序列化成 markdown 一列一行。`compare_extractors.py` 的 `char_ratio` 已改為去空白口徑；**舊報告裡那一欄不要再拿來判漏抽**。
2. **title／footnote 分類過鬆。** 一份 36 頁的 JPM 報告判出 183 個 title、136 個 footnote；全體 title 占 14%。`_TITLE_SIZE_RATIO=1.15` 與 `_FOOTNOTE_SIZE_RATIO=0.85` 把粗體副標與表格小字都收進去了。現在不影響 `serialize()`（只丟 header／footer），但 `E1` 之後任何拿 title 切章節、拿 footnote 排除的用途都會踩到。**校準要等 golden set，不要憑這 133 檔調門檻。**
3. **kgi 的「資料來源：Bloomberg、凱基債信團隊彙整」被當 footer 丟掉**（跨頁重複 ＋ 落在頁尾帶）。它是圖表的資料來源說明，對檢索價值低，但屬於「重複的正文」而非樣板——`_mark_repeated_headers_footers` 的兩個條件對這類文字仍會誤判。golden set 標順序層時把這種列標進去，才量得到。

**下一步不變**：先做 §4.1 的 golden set（前 15 份優先 kgi／masterlink／sinopac／yuanta），跑出 pypdf 基準填 §7，再進 §9 第 4 步。

