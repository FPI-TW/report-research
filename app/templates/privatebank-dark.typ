// privatebank-dark — 廷豐智能研報 深度研報 PDF 模板（A4 單欄，深色高階私銀風）
//
// 模板契約（M9a spec §4）：匯出 report/section-heading/kpi-strip/chart-figure 四函式，
// 簽章與 ib-classic 完全一致（只換渲染層、不動內容）。設計取向：
// - **深色背景**：深炭青底、淺色正文、鎏金強調——高階私人銀行的數位交付質感。
// - 襯線標題（精緻），無襯線正文（清晰）；金色承載品牌與主數字。
// - 註記：深底為數位交付導向；列印時背景不印是常態，語意仍以 +/− 承載不靠純色。
//
// 安全/相容約束（同 M9a spike 實測，不可違反）：
// - **繁中無斜體** → 全檔不得 emph / italic，強調只用粗體/色彩/字級。
// - **禁 rect(height: 100%)**：Typst 的 100% 相對整頁非父容器，會撐爆版面。框線用
//   block stroke，等高交給 grid rows: auto。
// - 零 @preview 依賴。圖表 SVG 以 image(bytes(svg), format: "svg") 嵌入。
//   注意：chart.py 的 SVG 為淺底黑字，深色頁面上以淺色卡片承載（見 chart-figure），
//   避免圖表與深底衝突（不改圖表產生器，M9b 不動內容/圖表）。

#let bg = rgb("#12181f")           // 頁面深炭青底
#let panel = rgb("#1b232c")        // 卡片/區塊底（略淺）
#let gold = rgb("#c9a24b")         // 深底適用的亮鎏金
#let gold-line = rgb("#4a3f27")
#let ink-light = rgb("#eef1f4")    // 主要淺色正文
#let ink-2 = rgb("#c2ccd4")
#let ink-3 = rgb("#8f9aa4")
#let ink-4 = rgb("#6b7580")
#let sem-up = rgb("#5fbf88")
#let sem-down = rgb("#d98a7e")

#let serif-cjk = ("Noto Serif CJK TC", "Noto Serif TC", "Georgia")
#let sans-cjk = ("Noto Sans CJK TC", "Noto Sans TC")

// 章節標題：襯線、鎏金、左側金色豎線（私銀質感）。豎線用 block stroke（禁 rect 100%）。
#let section-heading(body) = block(
  above: 15pt, below: 7pt, breakable: false, width: 100%,
  stroke: (left: 2.5pt + gold), inset: (left: 8pt, y: 1pt),
  text(font: serif-cjk, size: 13pt, weight: 700, fill: gold, body),
)

// KPI 卡：深色卡片、鎏金主數字、淺色標籤。等高交給 grid。
#let kpi-card(value, label, change, dir, source) = {
  let chg-color = if dir == "up" { sem-up } else if dir == "down" { sem-down } else { ink-3 }
  let chg-mark = if dir == "up" { "▲ " } else if dir == "down" { "▼ " } else { "" }
  block(
    fill: panel, inset: 9pt, width: 100%, radius: 3pt,
    stroke: 0.5pt + gold-line,
    [
      #text(font: serif-cjk, size: 15pt, weight: 700, fill: gold,
            features: (tnum: 1), value)
      #v(2pt, weak: true)
      #text(font: sans-cjk, size: 7.5pt, fill: ink-3, label)
      #if change != "" [
        #v(2pt, weak: true)
        #text(font: sans-cjk, size: 7.5pt, weight: 700, fill: chg-color,
              features: (tnum: 1), chg-mark + change)
      ]
      #if source != "" [
        #v(2pt, weak: true)
        #text(font: sans-cjk, size: 6.5pt, fill: ink-4, "來源 " + source)
      ]
    ],
  )
}

#let kpi-strip(items) = {
  if items.len() == 0 { return }
  block(above: 10pt, below: 4pt, width: 100%,
    grid(
      columns: items.map(_ => 1fr), gutter: 8pt,
      ..items.map(it => kpi-card(it.value, it.label, it.change, it.direction, it.source)),
    ),
  )
}

// 圖表：chart.py SVG 為淺底，深頁上以白色卡片承載，避免衝突。
#let chart-figure(svg, caption) = figure(
  block(fill: rgb("#f6f7f9"), inset: 6pt, radius: 3pt,
    image(bytes(svg), format: "svg", width: 100%)),
  caption: text(size: 8pt, fill: ink-3, caption),
  supplement: [圖],
)

#let disclaimer-block(body) = block(
  above: 14pt, width: 100%,
  stroke: (top: 0.5pt + gold-line), inset: (top: 8pt),
  text(font: sans-cjk, size: 7.5pt, fill: ink-4, body),
)

#let report(
  title: "",
  date: "",
  subject: "",
  brand: "廷豐智能研報",
  disclaimer: "",
  methods: "",
  kpi: (),
  body,
) = {
  set document(title: title)
  set page(
    paper: "a4", margin: (x: 18mm, y: 16mm), fill: bg,
    footer: context {
      set text(font: sans-cjk, size: 7.5pt, fill: ink-4)
      grid(
        columns: (1fr, auto),
        align(left, brand + " · 自動生成"),
        align(right, [#counter(page).display("1") / #counter(page).final().first()]),
      )
    },
  )
  set text(font: sans-cjk, size: 10.5pt, fill: ink-light, lang: "zh", region: "TW")
  set par(justify: true, leading: 0.8em, spacing: 1.0em)
  show heading: it => it

  // ── 報頭（鎏金底線）──
  block(stroke: (bottom: 1.5pt + gold), inset: (bottom: 8pt), width: 100%, [
    #grid(
      columns: (1fr, auto),
      text(font: serif-cjk, size: 9.5pt, weight: 700, fill: gold, tracking: 1.4pt, brand),
      text(font: sans-cjk, size: 8pt, fill: ink-3, features: (tnum: 1), date),
    )
    #v(8pt, weak: true)
    #text(font: serif-cjk, size: 21pt, weight: 700, fill: ink-light, title)
    #if subject != "" [
      #v(6pt, weak: true)
      #block(width: 100%, stroke: (left: 2.5pt + gold), inset: (left: 8pt, y: 1pt),
        text(font: sans-cjk, size: 8.5pt, fill: ink-2, subject))
    ]
  ])

  if kpi.len() > 0 { kpi-strip(kpi) }
  v(10pt)
  body

  if methods != "" {
    block(above: 12pt, width: 100%, text(font: sans-cjk, size: 7pt, fill: ink-3, methods))
  }
  if disclaimer != "" { disclaimer-block(disclaimer) }
}
