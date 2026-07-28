// ib-classic — 廷豐智能研報 深度研報 PDF 模板（A4 雙欄，國際投行密集風）
//
// 模板契約（M9a spec §4）：本檔匯出 `report(..)`，接收固定區塊，各自排版。
// 後續模板（M9b 的 broker-modern / privatebank-dark）一律實作同一組簽章。
//
// 排版規範（spec §5.1，設計定稿）：
// - 粗體節制：每段 1–2 個，優先給投資結論／核心數字／關鍵變化。
// - 金色節制：只用於品牌豎線、圖表中位數、核心強調。引用編號與次要標籤不用金色。
// - 色彩可列印：正負面不可只靠顏色，需搭配 +/− 或 ▲/▼；語意色低飽和。
// - 字級與對齊：正文 10.5pt、註腳 8pt 起，不以縮小字級處理溢出；數字 tabular-nums。
//
// 硬限制（M9a spike 實測）：
// - **繁中無斜體**：Noto Serif/Sans CJK TC 無斜體字重，Typst 不合成 → 全檔不得使用
//   `emph`／`text(style: "italic")`，強調只用粗體、色彩、字級。
// - 零 `@preview` 依賴 → 無網路編譯開箱即得。圖表為 chart.py 產出的 SVG，以 image() 嵌入。

#let brand-ink = rgb("#1e5175")
#let brand-ink-deep = rgb("#163a54")
#let brand-gold = rgb("#ae7415")
#let brand-gold-text = rgb("#8a5a0f")
#let brand-gold-line = rgb("#ead9ae")
#let brand-gold-panel = rgb("#fbf7ec")
#let text-1 = rgb("#212b33")
#let text-2 = rgb("#40525f")
#let text-3 = rgb("#6e7e89")
#let text-4 = rgb("#9aa5ae")
#let line-c = rgb("#e7e4dc")
#let divider = rgb("#efede7")
// 語意色：低飽和，且恆與 +/− 符號同時出現（可列印）
#let sem-up = rgb("#1f7a4c")
#let sem-down = rgb("#9c554a")

#let serif-cjk = ("Noto Serif CJK TC", "Noto Serif TC", "Georgia")
#let sans-cjk = ("Noto Sans CJK TC", "Noto Sans TC")

// 章節標題：襯線 + 鎏金豎線（品牌強調之一）
//
// 豎線用 block 的 left stroke，**不要**用 `rect(height: 100%)`：Typst 的 100% 不是
// CSS 的「父容器高度」，它會相對於整頁 → 每條豎線撐開一整頁、內容被推光（實測會
// 讓一份 1 頁的報告變成 6 頁空白頁，且編譯不報任何錯）。
#let section-heading(body) = block(
  above: 11pt, below: 6pt, breakable: false, width: 100%,
  stroke: (left: 3pt + brand-gold), inset: (left: 6pt, y: 1pt),
  text(font: serif-cjk, size: 12pt, weight: 700, fill: brand-ink-deep, body),
)

// KPI 卡：主數字襯線、標籤與來源列靠左對齊，卡內距一致（規範：基線對齊）
#let kpi-card(value, label, change, dir, source, source-label: "來源") = {
  let chg-color = if dir == "up" { sem-up } else if dir == "down" { sem-down } else { text-3 }
  // 可列印：方向以 +/− 符號承載，不單靠顏色
  let chg-mark = if dir == "up" { "▲ " } else if dir == "down" { "▼ " } else { "" }
  // 不要 height: 100% —— Typst 的 100% 相對於整頁而非父格，會把卡片拉滿一整頁。
  // 等高交給 grid 的 rows: auto（同列自然對齊）。
  block(
    fill: brand-gold-panel, inset: 7pt, width: 100%,
    stroke: 0.5pt + brand-gold-line,
    [
      #text(font: serif-cjk, size: 14pt, weight: 700, fill: brand-ink-deep,
            features: (tnum: 1), value)
      #v(1pt, weak: true)
      #text(font: sans-cjk, size: 7.5pt, fill: text-3, label)
      #if change != "" [
        #v(2pt, weak: true)
        #text(font: sans-cjk, size: 7.5pt, weight: 700, fill: chg-color,
              features: (tnum: 1), chg-mark + change)
      ]
      #if source != "" [
        #v(2pt, weak: true)
        #text(font: sans-cjk, size: 6.5pt, fill: text-4, source-label + " " + source)
      ]
    ],
  )
}

#let kpi-strip(items, source-label: "來源") = {
  if items.len() == 0 { return }
  block(above: 9pt, below: 3pt, width: 100%,
    grid(
      columns: items.map(_ => 1fr), gutter: 3pt,
      ..items.map(it => kpi-card(it.value, it.label, it.change, it.direction, it.source, source-label: source-label)),
    ),
  )
}

// 圖表：chart.py 的 SVG。figure 中文編號「圖 N」。
//
// 用 `image(bytes(svg), format: "svg")`——`image.decode` 在 Typst 0.15 已移除
// （錯誤訊息：function `image` does not contain field `decode`）。
#let chart-figure(svg, caption, supplement: "圖") = figure(
  image(bytes(svg), format: "svg", width: 100%),
  caption: text(size: 8pt, fill: text-3, caption),
  supplement: [#supplement],
)

// 免責：模板固定文字、恆定存在，不依賴 LLM 產出（spec D1）。
// 文字由 Python 端以單一常數傳入，與 WeasyPrint 路徑同源，避免兩軌漂移。
#let disclaimer-block(body) = block(
  above: 12pt, width: 100%,
  stroke: (top: 0.5pt + line-c), inset: (top: 7pt),
  text(font: sans-cjk, size: 7.5pt, fill: text-3, body),
)

// 模板契約入口。kpi 為跨欄置頂的 KPI 帶（設計定稿：數字先行，讀者第一眼看到
// 目標價／立場／EPS）——**必須在 columns() 之外渲染**，否則會被壓進單一欄。
#let report(
  title: "",
  date: "",
  subject: "",
  brand: "廷豐智能研報",
  disclaimer: "",
  methods: "",
  kpi: (),
  footer-note: "自動生成",
  lang: "zh",
  region: "TW",
  kpi-source-label: "來源",
  body,
) = {
  set document(title: title)
  set page(
    paper: "a4", margin: (x: 15mm, y: 14mm),
    footer: context {
      set text(font: sans-cjk, size: 7.5pt, fill: text-4)
      grid(
        columns: (1fr, auto),
        align(left, brand + " · " + footer-note),
        align(right, [#counter(page).display("1") / #counter(page).final().first()]),
      )
    },
  )
  // 正文 10.5pt、行高 1.72（規範）；lang/region 讓 Typst 走 CJK 斷行規則
  set text(font: sans-cjk, size: 10.5pt, fill: text-1, lang: lang, region: region)
  set par(justify: true, leading: 0.75em)
  // 表格框線：pandoc 產出的是裸 #table(...)，未設 set table 時 Typst 用預設**黑**框。
  // 深色模板上黑線對 #12181f 底的對比僅約 1.19:1（幾乎看不見的髒邊），淺色模板則
  // 與整份克制的線條語彙不一致。一律改用該模板既有的線色，不引入新色。
  set table(stroke: 0.5pt + line-c, fill: (_, y) => if y == 0 { brand-gold-panel } else { none })
  // 引用來源用固定 tab stop（規範）；此處以懸掛縮排落實
  show heading: it => it

  // ── 報頭 ──
  block(stroke: (bottom: 1.5pt + brand-ink-deep), inset: (bottom: 7pt), width: 100%, [
    #grid(
      columns: (1fr, auto),
      text(font: serif-cjk, size: 9.5pt, weight: 700, fill: brand-ink-deep,
           tracking: 1.2pt, brand),
      text(font: sans-cjk, size: 7.5pt, fill: text-3, features: (tnum: 1), date),
    )
    #v(7pt, weak: true)
    #text(font: serif-cjk, size: 20pt, weight: 700, fill: text-1, title)
    #if subject != "" [
      #v(6pt, weak: true)
      // 副標左側鎏金線（品牌豎線，規範允許的金色用途）；同樣用 stroke 而非 rect。
      #block(
        width: 100%, stroke: (left: 2.5pt + brand-gold), inset: (left: 7pt, y: 1pt),
        text(font: sans-cjk, size: 8.5pt, fill: text-2, subject),
      )
    ]
  ])

  // ── KPI 帶（跨欄置頂）與雙欄本文 ──
  if kpi.len() > 0 { kpi-strip(kpi, source-label: kpi-source-label) }
  v(8pt)
  columns(2, gutter: 14pt, body)

  // ── 方法論註記（可選）與免責（恆存在） ──
  if methods != "" {
    block(above: 10pt, width: 100%, text(font: sans-cjk, size: 7pt, fill: text-3, methods))
  }
  if disclaimer != "" { disclaimer-block(disclaimer) }
}
