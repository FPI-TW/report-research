// broker-modern — 廷豐智能研報 深度研報 PDF 模板（A4 單欄，現代簡潔風）
//
// 模板契約（M9a spec §4）：匯出 report/section-heading/kpi-strip/chart-figure 四函式，
// 簽章與 ib-classic 完全一致（只換渲染層、不動內容）。設計取向刻意與 ib-classic 對比：
// - **單欄、留白充足**：一欄到底、行距寬鬆，適合閱讀優先而非密集資訊牆。
// - **無襯線為主**：標題與正文皆 sans，配細分隔線；克制用色（品牌墨青為主軸強調）。
// - 金色只留給 KPI 主數字下方細線與品牌，不喧賓奪主。
//
// 安全/相容約束（同 M9a spike 實測，不可違反）：
// - **繁中無斜體** → 全檔不得 emph / italic，強調只用粗體/色彩/字級。
// - **禁 rect(height: 100%)**：Typst 的 100% 相對整頁非父容器，會撐爆版面（1 頁變 6 頁
//   空白且不報錯）。豎/框線一律用 block 的 stroke，等高交給 grid rows: auto。
// - 零 @preview 依賴（無網路編譯）。圖表 SVG 以 image(bytes(svg), format: "svg") 嵌入
//   （image.decode 在 0.15 已移除）。

#let brand-ink = rgb("#1e5175")
#let brand-ink-deep = rgb("#163a54")
#let brand-gold = rgb("#ae7415")
#let brand-gold-line = rgb("#ead9ae")
#let text-1 = rgb("#1f2933")
#let text-2 = rgb("#45535f")
#let text-3 = rgb("#6b7883")
#let text-4 = rgb("#9aa5ae")
#let line-c = rgb("#e4e7ea")
#let panel = rgb("#f7f9fb")
#let sem-up = rgb("#1f7a4c")
#let sem-down = rgb("#9c554a")

#let serif-cjk = ("Noto Serif CJK TC", "Noto Serif TC", "Georgia")
#let sans-cjk = ("Noto Sans CJK TC", "Noto Sans TC")

// 章節標題：無襯線、墨青、上方細分隔線（現代乾淨；不用鎏金豎線，與 ib-classic 區隔）
#let section-heading(body) = block(
  above: 15pt, below: 7pt, breakable: false, width: 100%,
  stroke: (top: 0.75pt + line-c), inset: (top: 8pt),
  text(font: sans-cjk, size: 13pt, weight: 700, fill: brand-ink-deep, body),
)

// KPI 卡：主數字大、無襯線、下方細鎏金線；淺底細框。等高交給 grid（非 height:100%）。
#let kpi-card(value, label, change, dir, source, source-label: "來源") = {
  let chg-color = if dir == "up" { sem-up } else if dir == "down" { sem-down } else { text-3 }
  let chg-mark = if dir == "up" { "▲ " } else if dir == "down" { "▼ " } else { "" }
  block(
    fill: panel, inset: 9pt, width: 100%, radius: 3pt,
    stroke: 0.5pt + line-c,
    [
      #text(font: sans-cjk, size: 16pt, weight: 700, fill: brand-ink-deep,
            features: (tnum: 1), value)
      #v(1pt, weak: true)
      #block(width: 18pt, stroke: (bottom: 1.5pt + brand-gold), inset: (bottom: 3pt))
      #v(3pt, weak: true)
      #text(font: sans-cjk, size: 8pt, fill: text-3, label)
      #if change != "" [
        #v(2pt, weak: true)
        #text(font: sans-cjk, size: 8pt, weight: 700, fill: chg-color,
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
  block(above: 10pt, below: 4pt, width: 100%,
    grid(
      columns: items.map(_ => 1fr), gutter: 8pt,
      ..items.map(it => kpi-card(it.value, it.label, it.change, it.direction, it.source, source-label: source-label)),
    ),
  )
}

#let chart-figure(svg, caption, supplement: "圖") = figure(
  image(bytes(svg), format: "svg", width: 100%),
  caption: text(size: 8pt, fill: text-3, caption),
  supplement: [#supplement],
)

#let disclaimer-block(body) = block(
  above: 14pt, width: 100%,
  stroke: (top: 0.5pt + line-c), inset: (top: 8pt),
  text(font: sans-cjk, size: 7.5pt, fill: text-3, body),
)

// 模板契約入口（簽章與 ib-classic 一致）。單欄本文；KPI 帶置於本文之上（數字先行）。
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
    paper: "a4", margin: (x: 20mm, y: 16mm),
    footer: context {
      set text(font: sans-cjk, size: 7.5pt, fill: text-4)
      grid(
        columns: (1fr, auto),
        align(left, brand + " · " + footer-note),
        align(right, [#counter(page).display("1") / #counter(page).final().first()]),
      )
    },
  )
  // 正文較 ib-classic 略大、行距更寬（閱讀優先）
  set text(font: sans-cjk, size: 11pt, fill: text-1, lang: lang, region: region)
  set par(justify: true, leading: 0.85em, spacing: 1.05em)
  show heading: it => it

  // ── 報頭（無襯線、細墨青底線）──
  block(stroke: (bottom: 1pt + brand-ink), inset: (bottom: 8pt), width: 100%, [
    #grid(
      columns: (1fr, auto),
      text(font: sans-cjk, size: 9pt, weight: 700, fill: brand-ink, tracking: 1.4pt, brand),
      text(font: sans-cjk, size: 8pt, fill: text-3, features: (tnum: 1), date),
    )
    #v(8pt, weak: true)
    #text(font: sans-cjk, size: 22pt, weight: 700, fill: text-1, title)
    #if subject != "" [
      #v(6pt, weak: true)
      #text(font: sans-cjk, size: 9pt, fill: text-2, subject)
    ]
  ])

  // ── KPI 帶 + 單欄本文 ──
  if kpi.len() > 0 { kpi-strip(kpi, source-label: kpi-source-label) }
  v(10pt)
  body

  if methods != "" {
    block(above: 12pt, width: 100%, text(font: sans-cjk, size: 7pt, fill: text-3, methods))
  }
  if disclaimer != "" { disclaimer-block(disclaimer) }
}
