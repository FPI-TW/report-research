# 深度研報版面問題診斷與修法

> **實作狀態（2026-07-28）**：P0-1、P0-2、P1-1～P1-4、P2-1、P2-2 已實作（見「已實作內容」）；
> **P2-4 的兩項也已實作**，改以不碰 prompt 的方式處理（見「追加實作：P2-4 生成層兩項」）。
> **P2-3（行長）亦已實作**（見「追加實作：P2-3 行長」）——量測後發現真正超標的是兩款
> 單欄模板（88／94 字元），而非原先以為的雙欄。診斷清單至此全部落地。

樣本：`report-0475a66d.pdf`（Typst 0.15.0、A4 雙欄、9 頁、locale=en、模板推測為 ib-classic）
方法：逐頁 90dpi 渲染檢視，並對照 `app/templates/ib-classic.typ`、`app/services/chart.py`、`app/services/typst_render.py`、`app/services/report_writer.py`。

版面量測（後面所有推算都基於此）：

```
頁寬 595.28pt − 左右邊距 2×15mm(42.52pt) = 內容寬 510.24pt
內容寬 510.24 − gutter 14pt = 496.24 ÷ 2 = 單欄約 248pt
單欄 248pt ÷ 10.5pt 正文 ≈ 每行 38–42 個西文字元
```

**單欄 248pt 是所有問題的共同放大器。** 兩端對齊的舒適行長是 45–75 字元，248pt 只有 38–42；圖表被壓到 0.39 倍；長 token 無處可斷。以下按嚴重度排序。

---

## P0-1　引用來源溢出欄外，資訊直接遺失（第 8 頁）

**症狀**：第 8 頁有 5 條參考文獻被裁切在頁面右緣之外，看得到的是殘句：

```
[10] Takeaway_Computex_2026_06_04_C.pdf(TW・2026-06-0     ← 被切
[15] 623565083880456567_260717_ubs_wistron.pdf(TW・202    ← 被切
[19] Radar Monthly_Semiconductor_2026_05_14_C_TW.pdf(     ← 被切
[21] daily story_2301_2026_04_29_C_TW.pdf(TW・2026-04-29  ← 被切
[26] daily story_3231_2026_04_13_C_TW.pdf(TW・2026-04-13  ← 被切
```

**根因**：`report_writer.py:585` 直接把 `ev.file_name` 當引用標籤：

```python
name = ev.file_name or ev.report_id or ("Report" if en else "研報")
meta = "·".join(x for x in (ev.market, ev.report_date) if x)
lines.append(f"[{i}] {name}{lp}{meta}{rp}" if meta else f"[{i}] {name}")
```

`623565083880456567_260717_ubs_wistron.pdf` 是一個 **44 字元的不可斷 token**。Typst 對沒有斷行機會的 token 不會強制斷字，它就直接越界，而且**不會報任何錯**——這是靜默的資訊遺失。

同一根因還造成第 8 頁的字距空洞（`[11]　　Radar　　Monthly_IT` 被 justify 拉開），因為那一行只有一個長 token 可以推。

**修法（三層，建議全上）**

*(a) 資料層——改成人看得懂的引用格式（推薦，順帶解決可讀性）*

`app/services/filename.py` 已有 `source_display` 在做券商解析（radar 在用）。引用行改為「券商 · 報告類型 · 日期」，檔名只在 hover/連結保留：

```python
# report_writer.build_references
from app.services.filename import source_display
label = source_display(ev.file_name) or ev.file_name or ...
# 產出 [15] UBS · Wistron · 2026-07-17  （而非 623565083880456567_260717_ubs_wistron.pdf）
```

*(b) 保底層——插入零寬斷點（3 行，立即止血）*

即使保留檔名，也要給 Typst 斷行機會。ZWSP（`\u200b`）在 Typst 是合法斷點且不佔寬度：

```python
_BREAKABLE = re.compile(r"([_\-./])")
def _soft_break(name: str) -> str:
    return _BREAKABLE.sub("\\1\u200b", name)
```

注意：這一步要同時套用在 WeasyPrint 路徑（`pdf.py`）或抽成共用函式，否則兩軌漂移。

*(c) 模板層——References 節不要兩端對齊*

引用清單本質是清單而非散文，justify 只會製造空洞。在 `ib-classic.typ` 加：

```typst
#let refs-block(body) = block(width: 100%, {
  set par(justify: false, hanging-indent: 1.2em, spacing: 0.35em)
  set text(size: 9pt, hyphenate: true)
  body
})
```

`typst_render.py` 在 `sec.key == "references"` 時改用它包起來。更進一步：References 整節移出 `columns(2)` 用單欄呈現（欄寬 510pt，長檔名幾乎不會再溢出），也順帶修掉目前左右欄編號穿插的閱讀順序混亂。

---

## P0-2　圖表在窄欄內完全不可讀（第 2、3、4 頁）

**症狀**：`Fig. 1` / `Fig. 2` 的軸標籤、圖例、數值標籤全部細到看不清；圖上方還有一行更小的標題。

**根因（可精確推算）**：`chart.py:12` 的畫布是 `_W, _H = 640, 380`，文字用 `font-size="10"`（軸標籤、圖例）與 `font-size="14"`（標題）。模板以 `image(..., width: 100%)` 嵌進 248pt 的欄：

```
縮放比 = 248 / 640 = 0.39
→ 10px 軸標籤 → 3.9pt
→ 14px 標題   → 5.5pt
→  9px 數值   → 3.5pt
```

**3.9pt 是印不出來也看不清的字級**，而且直接違反模板自己寫的規範（`ib-classic.typ` 檔頭：「註腳 8pt 起，不以縮小字級處理溢出」）。

**修法 A（推薦）：圖表跨欄浮動置頂**

投行研報的圖表本來就多為跨欄。Typst 0.15 支援 `scope: "parent"` 的浮動：

```typst
#let chart-figure(svg, caption, supplement: "圖", span: false) = {
  let fig = figure(
    image(bytes(svg), format: "svg", width: 100%),
    caption: align(left, text(size: 8pt, fill: text-3, caption)),
    supplement: [#supplement],
  )
  if span { place(top, scope: "parent", float: true, fig) } else { fig }
}
```

跨欄後縮放比變成 `510 / 640 = 0.80` → 軸標籤 8pt，達到模板自訂的下限。

**修法 B：若要留在欄內，`chart.py` 必須為欄寬重新設計畫布**

不是改縮放，是改設計。`_W, _H = 640, 380` 是為全寬設計的比例，塞進 248pt 就註定不可讀：

```python
_W, _H = 300, 200                      # 縮放比 248/300 = 0.83
_PAD_L, _PAD_R, _PAD_T, _PAD_B = 38, 12, 22, 34   # 原本 72/28/48/64 在 300 寬會吃掉一半
# 字級：軸標籤 10 → 11、圖例 10 → 10、數值 9 → 10（縮放後 8.3–9.1pt）
# 並減少刻度密度：300pt 寬放不下 5 個 x 標籤 + 4 個 y 標籤
```

**修法 C：刪掉 SVG 內建標題（無論選 A 或 B 都要做）**

`chart.py:212-216` 在 SVG 裡畫標題，而 `chart_caption()` 又把同一個標題寫進 figure caption → 第 2 頁同時出現兩次：圖上方 5.5pt 的 `Global PC Shipment Growth by Segment (YoY, %)`，圖下方 `Fig. 1: Global PC Shipment Growth by Segment (YoY, %) (Source [11])`。

標題交給 figure caption（Typst 才能正確處理編號、間距、跨頁），SVG 只畫數據。同時 caption 目前是居中對齊，導致第 2 頁尾端出現孤立的 `[11])` 一行——改 `align(left)`。

---

## P1-1　標題被連字符斷開（第 1 頁）

**症狀**：`Wistron Investment Outlook and Valuation Analy-sis`

**根因**：Typst 的 `hyphenate` 預設是 `auto`，在 `justify: true` 時啟用。模板 `set par(justify: true)` 是全域的，報頭標題（`ib-classic.typ` 的 `text(font: serif-cjk, size: 20pt, ..., title)`）也繼承了。20pt 標題在 510pt 寬剛好卡在兩行邊界 → 斷字。

**修法**：標題與所有 heading 一律關閉斷字與兩端對齊。

```typst
// 報頭標題
#block(width: 100%, {
  set par(justify: false)
  text(font: serif-cjk, size: 20pt, weight: 700, fill: text-1, hyphenate: false, title)
})

// section-heading 同樣加 hyphenate: false
text(font: serif-cjk, size: 12pt, weight: 700, fill: brand-ink-deep, hyphenate: false, body)
```

正文的斷字要**保留**（第 1 頁的 `momen-tum`、`company-spe-cific` 是正確行為，248pt 窄欄沒有斷字會更糟）。

---

## P1-2　KPI 卡片文字被兩端對齊拉散（第 1 頁）

**症狀**：`2026F　　AI　　server　　shipment growth`、`2026F　total　server　shipment growth`——四張卡的標籤都有明顯字距空洞。

**根因**：`kpi-card` 內的文字繼承全域 `set par(justify: true)`。卡片寬度 = `(510 − 3×3pt gutter) / 4 ≈ 125pt`，7.5pt 字在 125pt 寬每行只有 3–5 個詞，justify 必然拉散。

**修法**：卡片內關閉 justify、允許斷字、收緊行距。

```typst
#let kpi-card(value, label, change, dir, source, source-label: "來源") = {
  ...
  block(fill: brand-gold-panel, inset: 7pt, width: 100%, stroke: 0.5pt + brand-gold-line, {
    set par(justify: false, leading: 0.5em)
    set text(hyphenate: true)
    ...
  })
}
```

附帶問題：英文 locale 下標籤明顯比中文長（`2026F AI server shipment growth` vs「2026F AI 伺服器出貨成長」），四欄等寬在英文下容易變三行。建議 `kpi-strip` 在 `len(items) == 4 且 locale == "en"` 時改 2×2 網格，或把標籤字數上限納入 KPI 產生階段的 prompt 約束。

---

## P1-3　h2 / h3 沒有樣式，層級讀不出來（第 2、4 頁）

**症狀**：第 2 頁 `In-Depth Analysis`（h1，有金色豎線）下面緊接 `PC and Server Market Dynamics`（h2），兩者間距幾乎為零，h2 又緊貼下一段正文，看起來像是一段粗體句子而非小標。

**根因**：`ib-classic.typ` 裡只有一行

```typst
show heading: it => it        // ← no-op
```

h1 是 Python 端刻意在 pandoc 之前切出、用 `#section-heading(...)` 渲染的（`typst_render.py:480`），所以有樣式；但 **pandoc 轉出的 h2/h3 走的是 Typst 內建 heading 樣式**，完全沒有被模板接管。這還埋了一個風險：模板檔頭寫著「繁中無斜體，全檔不得使用 emph」，而 Typst 內建 heading 樣式在某些字重下會走合成字形——這條硬限制目前沒有覆蓋 pandoc 產出的 heading。

**修法**：明確接管 level 2/3，並用 `sticky` 防止標題落在欄底。

```typst
show heading.where(level: 2): it => block(
  above: 10pt, below: 3.5pt, sticky: true,
  text(font: sans-cjk, size: 10.5pt, weight: 700, fill: brand-ink-deep,
       hyphenate: false, it.body),
)
show heading.where(level: 3): it => block(
  above: 7pt, below: 2.5pt, sticky: true,
  text(font: sans-cjk, size: 9.5pt, weight: 700, fill: text-2,
       hyphenate: false, it.body),
)
```

`sticky: true` 是這裡的關鍵：248pt 窄欄一頁有 4 個欄底，標題孤立在欄底的機率是單欄的兩倍。

---

## P1-4　免責聲明獨佔最後一頁（第 9 頁整頁空白）

**症狀**：第 9 頁只有頂部四行免責聲明，其餘 90% 空白。

**根因**：`disclaimer-block` 在 `columns(2, body)` 之後以普通 block 渲染，`above: 12pt`；正文剛好填滿第 8 頁 → 免責被推到新頁。

**修法（擇一）**

```typst
// 方案 1：貼在最後一頁底部（最穩，不再受正文長度影響）
if disclaimer != "" {
  place(bottom, scope: "parent", float: true, disclaimer-block(disclaimer))
}

// 方案 2：允許與正文同頁擠進去
block(above: 8pt, breakable: true, ...)   // 並把 above 從 12pt 降到 8pt
```

方案 1 語意也更對：免責聲明是頁面 chrome 而非文件內容流的一部分。

---

## P2-1　引用區塊沒有視覺標示（第 2 頁）

第 2 頁右欄中段有一塊縮排文字（`The structural takeaway for ODMs such as Wistron...`），前後與正文只差縮排，看起來像排版錯誤而不是刻意的引文。模板沒有 `show quote` 規則。

```typst
show quote: it => block(
  above: 7pt, below: 7pt, width: 100%,
  stroke: (left: 1.5pt + brand-gold-line), inset: (left: 8pt, y: 2pt),
  text(size: 9.5pt, fill: text-2, it.body),
)
```

## P2-2　段落首行縮排不一致（第 1 頁）

第 1 頁 Executive Summary 的第一段有首行縮排，後續段落沒有；第 2 頁之後全部沒有。混用兩種段落分隔法。窄欄雙欄應統一用**段距**而非首行縮排：

```typst
set par(justify: true, leading: 0.75em, first-line-indent: 0pt, spacing: 0.62em)
```

## P2-3　行長偏短造成的字距空洞

248pt / 10.5pt ≈ 38–42 字元，低於兩端對齊的舒適區（45–75）。除了前述長 token 問題，這是第 1、2 頁那些空洞的結構性成因。可調的旋鈕（建議前兩項）：

| 調整 | 效果 |
|---|---|
| 邊距 15mm → 12mm、gutter 14pt → 12pt | 單欄 248 → 268pt（+8%） |
| 正文 10.5pt → 10pt | 每行 42 → 44 字元 |
| 正文改 `justify: false`（ragged right） | 徹底消除空洞，但失去投行研報的密實感；對 CJK 影響小 |
| 改單欄 | 行長 510pt 過長，需要更大字級，頁數會膨脹——不建議 |

---

## P2-4　巨型段落與粗體偽小標（生成層，不是模板問題）

第 4 頁左欄有**一段佔滿整欄 40 行**的文字；同頁多處用 `**Mix shift favors white-box/ODM assembly.**` 這種粗體句子當小標。這是 LLM 輸出結構問題，模板無法救。三條路：

**(a) prompt 約束**（成本最低，但靠模型配合）
在 `report_writer.py` 的逐節 prompt（`_build_section_prompt` / `_build_section_prompt_en`）加：每段 3–5 句、超過即分段；小標一律用 `### `，不要用粗體句子代替小標。
注意：這四份 prompt 目前是手抄的平行副本（中/英 × single-shot/sectioned），改一處要改四處——這正是架構檢視裡建議抽 `prompts/blocks.py` 的理由。

**(b) 模板順勢而為**（推薦，因為 run-in heading 本來就是投行研報的標準做法）
不對抗 LLM 的習慣，而是把「段落開頭的粗體句 + 句號」正式渲染成 run-in 小標：

```typst
show strong: it => text(weight: 700, fill: brand-ink-deep, it.body)
```

再加上段落偵測（開頭 strong 且以句號結尾 → 加 `above: 8pt`），第 4 頁那些偽小標就會變成有層次的 run-in heading。

**(c) Python 後處理**（決定性，不靠模型配合）
`report_writer` 組裝後對超過 N 字的段落在句界切分。優點是可測試、不受模型漂移影響；缺點是可能切在語意不該斷的地方，建議只對「超過 6 句」的極端段落動手。

---

## 建議施作順序

| 順序 | 項目 | 檔案 | 成本 | 效果 |
|---|---|---|---|---|
| 1 | 引用來源零寬斷點（止血） | `report_writer.py` + `pdf.py` 共用 | 3 行 | 第 8 頁不再遺失資訊 |
| 2 | 引用行改 `source_display` 格式 | `report_writer.py:585` | ~10 行 | 引用可讀，長度砍半 |
| 3 | 標題／heading 關 `hyphenate` | `ib-classic.typ` ×3 模板 | ~6 行 | 標題不再斷字 |
| 4 | KPI 卡關 `justify` | `ib-classic.typ` ×3 模板 | 2 行 | 字距空洞消失 |
| 5 | 圖表跨欄浮動 + 移除 SVG 內建標題 + caption 左對齊 | `ib-classic.typ`、`chart.py`、`typst_render.py` | ~30 行 | 軸標籤 3.9pt → 8pt |
| 6 | h2/h3 show rule + `sticky` | `ib-classic.typ` ×3 模板 | ~15 行 | 層級可讀、標題不孤立 |
| 7 | 免責改 `place(bottom, scope: "parent")` | `ib-classic.typ` ×3 模板 | 3 行 | 少一頁空白 |
| 8 | `show quote` + 段落間距統一 | `ib-classic.typ` ×3 模板 | ~10 行 | 引文可辨識 |
| 9 | 邊距／gutter／字級微調 | `ib-classic.typ` | 2 行 | 行長 +8% |
| 10 | run-in heading show rule | `ib-classic.typ` | ~8 行 | 偽小標變真層級 |
| 11 | 段落長度 prompt 約束或後處理 | `report_writer.py` | 中 | 消除巨型段落 |

1–4 是「資訊遺失或明顯出錯」，建議一個 PR 收掉；5–8 是版面品質，第二個 PR；9–11 涉及設計取捨與生成層，值得先出樣張比對。

---

## 施作注意

- **三個模板都要改**：`ib-classic.typ` / `broker-modern.typ` / `privatebank-dark.typ` 共用同一組函式簽名（`report` / `section-heading` / `kpi-strip` / `chart-figure`），任何契約變動（例如 `chart-figure` 增加 `span` 參數）三份都要同步，否則換模板重出會編譯失敗。建議把共用的 show rules 抽成 `app/templates/_common.typ` 再 import，避免三份手抄。
- **WeasyPrint 路徑會漂移**：`pdf.py` 是 fail-open 回退路徑，`_normalize_refs` 等已經是共用函式。零寬斷點與引用格式的改動要放在共用層，不要只改 Typst 側。
- **`report_rendition` 是不可變表**：改完模板後既有報告不會自動變好，需要走換皮重出（零 LLM）。可以拿這份 `report-0475a66d` 當回歸樣張，改動前後各出一次比對。
- **加一組視覺回歸**：`tests/fixtures/ib_classic_smoke.typ` 已經存在。建議擴成「固定 markdown 樣張 → 編譯 → 斷言頁數與『無內容超出 text region』」。Typst 對溢出不報錯，所以要靠檢查（例如比對渲染後右邊界像素是否有墨點）才擋得住 P0-1 這類問題再次發生。

---

# 已實作內容（2026-07-28）

## 改動檔案

| 檔案 | 改動 |
|---|---|
| `app/services/textnorm.py` | 新增 `soft_break_token()`：在 `_ - . / \ ·` 後插入零寬空格。兩軌共用。 |
| `app/services/report_writer.py` | 新增 `_ref_label()`（去副檔名＋軟斷點）；`build_references` 的語料引用與外部 URL 都改走它。 |
| `app/services/chart.py` | 移除 SVG 內建標題（標題只留在 figure caption，先前兩處都畫）；畫布 `640×380 → 640×340`、`_PAD_T 48 → 22`（標題移走後不必留白）。 |
| `app/services/typst_render.py` | `#chart-figure(...)` 一律帶 `span: true`；`references` 節包進 `#refs-block[...]`；import 清單加 `refs-block`。 |
| `app/templates/ib-classic.typ` | 主標／`section-heading`／h2／h3 一律 `hyphenate: false` 且 `justify: false`；`section-heading` 與 h2/h3 加 `sticky: true`；KPI 卡內 `justify: false` + 允許斷字；新增 `refs-block`；`chart-figure` 加 `span`（跨欄浮動、寬 92%）；新增 h2/h3/quote/`figure.caption` 的 show rules（先前 `show heading: it => it` 是 no-op）；`par` 明確 `first-line-indent: 0pt` + `spacing`；免責改 `place(bottom, float: true)`。 |
| `app/templates/broker-modern.typ`<br>`app/templates/privatebank-dark.typ` | 同上，依各自色彩／字級調整。單欄故忽略 `span`，但**簽章必須接受**（否則換版型重出會編譯失敗）。 |

## 測試

新增（都是「壞掉時不報錯、只是 PDF 變醜或掉字」的類型，只能靠測試釘住）：

- `test_chart.py::test_title_lives_in_caption_not_in_svg` — 標題只能出現一次，且來源標記不得消失。
- `test_report_writer.py::test_long_filename_gets_soft_break_points` — 引用標籤不得含超過 30 字元的無斷點 token。
- `test_new_templates.py::test_chart_figure_accepts_span` / `test_no_noop_heading_show_rule` — 三款模板的契約。
- `test_report_chrome_locale.py::LayoutContractTests` — emitter 必須帶 `span: true`、必須且只能把 references 包進 `refs-block`。

修改既有期望值 3 處（都是行為刻意改變，非測試遷就實作）：

1. `test_chart.py` 原本斷言 SVG 內含標題 → 改為斷言標題在 caption、不在 SVG。
2. `test_report_writer.py` 兩處斷言 `[1] a.pdf（TW·…）` → 改為 `[1] a（TW·…）`（副檔名已移除）。

`tests/fixtures/layout_sample_en.md` 為新增的版面壓力樣張（由真實研報反向重建，含所有會壓垮版面的成分），檔頭有重出指令。

## 樣張比對結果

同一份樣張、同一條管線（ib-classic／en／A4 雙欄），改動前後各出一次：

| | 改動前 | 改動後 |
|---|---|---|
| 頁數 | 8（第 8 頁只有 4 行免責，其餘空白） | 8（第 8 頁是引用來源尾段＋底部免責） |
| 引用來源溢出 | 5 條被裁切在欄外，`[31]` 與 `[30]` 的文字**互相重疊** | 31 條全部完整，懸掛縮排，無重疊 |
| 引用來源佔用 | 1.5 欄，大量字距空洞 | 1 欄，無空洞 |
| 圖表軸標籤 | 3.9pt（縮放比 0.39） | 約 8.4pt（跨欄、縮放比 0.74） |
| 圖表標題 | 圖上 5.5pt 一份 + caption 一份（重複） | 只在 caption，單行靠左 |
| 主標 | `Valuation Analy-sis`（斷字） | 兩行，不斷字 |
| KPI 卡標籤 | `2026F　　AI　　server　　shipment`（3 行） | 正常字距（2 行，卡片變矮） |
| h2/h3 | 無樣式，看起來像粗體句子 | 墨青粗體、有間距、`sticky` 不落欄底 |
| blockquote | 只有縮排，像排版錯誤 | 左側鎏金細線 + 小一級字 |

## 已知取捨與未做

- **圖表跨欄後可能與引用它的段落分開**（浮動元素的固有性質，用 `place(auto)` 讓 Typst 自選頁首／頁尾以盡量靠近）。研報慣例是圖表浮動、以編號引用，可接受；若要嚴格圖文相鄰就得放棄跨欄，代價是回到 3.9pt。
- **免責改 `float: true` 只是「優先貼當前頁底部」**，塞不下仍會另起一頁（退化成改動前行為，但不會與正文重疊）。要完全消除，得把免責移進頁尾邊界或縮短文案。
- **未做視覺回歸**：Typst 對溢出不報錯，本次靠「引用標籤不得有無斷點長 token」這條源頭測試擋住 P0-1。真正的像素級回歸（渲染後檢查文字區右界是否有墨點）仍是待辦；試過用 pypdf 的 `visitor_text` 取字元座標，但 text run 的寬度只能粗估，判準不夠穩，值得單獨做一次。
- **P2-3 行長**（邊距 15mm→12mm、gutter 14pt→12pt、正文 10.5pt→10pt）沒動：會影響整體密度與既有觀感，建議看完這版樣張再決定。
- **P2-4 巨型段落與粗體偽小標**沒動：屬生成層（prompt 或後處理），且四份研報 prompt 目前是手抄的平行副本，改動應與 `prompts/` 收斂一起做。

---

# 追加實作：P2-4 生成層兩項（2026-07-28）

原本列為「未做」的兩項，改以不碰 prompt 的方式處理。

## (b) run-in 導語正式化 —— 模板層

LLM 慣用 `**導語句。** 內文` 代替 `### 小標`。這本來就是投行研報的標準寫法，所以不對抗它：

- `show strong:` 給導語標題色 + `h(0.28em)` 的 run-in 間距（三款模板皆加）。
- **h2/h3 改用襯線**（ib-classic / privatebank-dark；broker-modern 刻意全無襯線故不動）。
  這一步是渲染後才發現需要的：h3 與 run-in 導語同為藍色粗體、只差 0.5pt，並排時層級讀不出來。
  字體對比最不含糊，且對齊模板既有語彙（標題襯線、正文無襯線）。

結果：h3 = 襯線 10pt brand-ink，run-in = 無襯線 10.5pt brand-ink-deep + 間距，兩者一眼可分。

## (c) 巨型段落句界切分 —— Python 決定性後處理

`pdf.split_long_paragraphs()`，接在兩條收尾路徑（`report._finalize_sectioned` 與單次路徑的
`strip_preamble` 之後）。兩條都要接，否則「單次」與「逐節」出來的 PDF 段落密度不一樣。

**門檻用顯示寬度而非句數**：樣張裡有「4 句、2765 寬度單位（約 59 行）」的段落——句數少
不代表段落短。A4 雙欄單欄約 248pt、正文 10.5pt ⇒ 每行約 47 個寬度單位（西文 1、CJK 2）。
切分門檻 900（≈19 行）、目標 600（≈13 行）。

實作上的三個坑：

1. **中文句子之間沒有空白**。初版句界統一要求 `\s+`，中文段落因此完全切不動（`_sentences`
   回傳 1 句）。現在中西分兩支：CJK 在 `。！？` 後即為句界，西文才要求空白 + 下句首大寫。
2. **只看「已達 target」會切得很不平均**。實測 344/619/1429 三句會變成 963|1429 兩段。
   加了前瞻：若再加下一句會大幅超標且當前已達 target 的一半，就在此收段。
3. **絕不切壞結構**。圍欄（含 ```chart 內部的空行，故圍欄狀態要跨「以空行分塊」追蹤）、
   標題、清單、引用、表格、`[n]` 引用行一律原樣；整段只有一個句子時原樣回傳（沒有安全切點）。

樣張效果（ib-classic／en）：

| | 改動前 | 改動後 |
|---|---|---|
| 段落數 | 16 | 38 |
| 最長段落 | 2765（59 行） | 1532（33 行） |
| 平均段落 | 1533（33 行） | 645（14 行） |
| 頁數 | 8 | 8 |

殘留的 1532 是**單一句子**（樣張重建時把幾句黏成一句的產物），函式正確地拒絕在句中切開。

## 測試

`tests/test_paragraph_split.py`（7 條，**刻意不放在 test_pdf.py**——該檔有
`pytest.importorskip("weasyprint")`，而這是兩軌共用的純字串邏輯，沒有理由因缺原生庫被略過）：
句界切分、冪等、短段落不動、圍欄/清單/引用/表格/`[n]`/標題原樣、縮寫與小數不成為句界、
單句長段落不動、CJK 寬度算兩倍。

另以 AST 掃過 7 個既有測試檔的 72 個字串常值，確認切分對它們全是 no-op（不會動到既有期望值）。

## 已知限制

- **單一超長句子無解**：函式不會在句中切，遇到 30 行的單句就原樣留著。要處理只能回到
  prompt（要求句子長度）或接受。
- **切分會寫進持久化的 markdown**（它是真相來源，PDF 只是渲染）。既有研報不會回頭改；
  重出 PDF 走 `report_rendition` 會用當時存的 markdown，所以舊報告仍是舊段落。
- **`show strong` 影響所有粗體**，包括句中的 `**+61% YoY**` —— 它們也會拿到標題色與
  0.28em 尾距。實測不突兀（模板規範本來就要求「粗體節制：每段 1–2 個」），但若哪天粗體
  被濫用，這條規則會讓版面偏藍。

---

# 追加實作：P2-3 行長（2026-07-28）

先量測，才發現原本的判斷錯了：**問題最嚴重的不是雙欄，是兩款單欄模板。**

| 模板 | 原本版面/欄寬 | 原本每行字元 | 判定 |
|---|---|---|---|
| ib-classic（雙欄 10.5pt） | 248.1pt | 約 47 | 落在舒適區 45–75 的**下界邊緣** |
| broker-modern（單欄 11pt） | 481.9pt | 約 88 | **超出上界** |
| privatebank-dark（單欄 10.5pt） | 493.2pt | 約 94 | **三款中最長，明顯超標** |

行太長的問題和行太短一樣真實：眼睛回行時容易跳行。而兩款單欄模板從來沒被量過。

## 改動

| 模板 | 改動 | 結果 |
|---|---|---|
| ib-classic | 邊距 15mm → **12.5mm**、gutter 14pt → **12pt** | 248.1 → 256.2pt，47 → **49** 字元 |
| broker-modern | 邊距 20mm → **29mm** | 481.9 → 430.9pt，88 → **78** 字元 |
| privatebank-dark | 邊距 18mm → **28mm**、正文 10.5 → **11pt** | 493.2 → 436.5pt，94 → **79** 字元 |

**正文字級：ib-classic 維持 10.5pt。** 原計畫要降到 10pt，但量測顯示只換到 2 個字元
（49 → 51），不值得推翻「正文 10.5pt」這條已定稿的規範。量測結論寫進了模板檔頭的規範註解，
下一個人不必重算。privatebank-dark 反而**提高**到 11pt——深底上略大的字更好讀，且收窄後有空間。

12.5mm 仍在印表機安全邊界內（一般下限 10–12mm）。

## 代價（必須知道）

收窄單欄模板會增加頁數。同一份樣張：

| 模板 | 頁數（收窄前 → 後） |
|---|---|
| ib-classic | 8 → 8 |
| broker-modern | 10 → 11 |
| privatebank-dark | 9 → 10 |

這是可讀性換頁數。broker-modern 的設計取向本來就寫著「留白充足、閱讀優先」，這一步才真的
兌現它；但若你認為頁數更重要，把邊距改回去是一行的事（模板檔頭有量測數字可據以取捨）。
