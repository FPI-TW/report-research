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
  above: 15pt, below: 7pt, breakable: false, sticky: true, width: 100%,
  stroke: (top: 0.75pt + line-c), inset: (top: 8pt),
  text(font: sans-cjk, size: 13pt, weight: 700, fill: brand-ink-deep,
       hyphenate: false, body),
)

// KPI 卡：主數字大、無襯線、下方細鎏金線；淺底細框。等高交給 grid（非 height:100%）。
#let kpi-card(value, label, change, dir, source, source-label: "來源") = {
  let chg-color = if dir == "up" { sem-up } else if dir == "down" { sem-down } else { text-3 }
  let chg-mark = if dir == "up" { "▲ " } else if dir == "down" { "▼ " } else { "" }
  block(
    fill: panel, inset: 9pt, width: 100%, radius: 3pt,
    stroke: 0.5pt + line-c,
    [
      // 卡片窄、標籤 8pt 起，繼承全域 justify 會把字距拉散（見 ib-classic）
      #set par(justify: false, leading: 0.5em)
      #set text(hyphenate: true)
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

// span 引數為模板契約的一部分（雙欄模板用它跨欄置頂）。本模板單欄、圖表本來就是
// 全寬，故忽略該引數——簽章仍須接受，否則換模板重出會編譯失敗。
// caption 靠左：置中會讓「(來源 [11])」這種尾段落單成孤行。
#let chart-figure(svg, caption, supplement: "圖", span: false) = figure(
  image(bytes(svg), format: "svg", width: 100%),
  caption: text(size: 8pt, fill: text-3, caption),
  supplement: [#supplement],
)

// 引用來源：清單排版。單欄下溢出風險較低，但空洞與可讀性問題相同（見 ib-classic）。
#let refs-block(body) = block(width: 100%, {
  set par(justify: false, hanging-indent: 1.1em, spacing: 0.45em)
  set text(size: 9.5pt, fill: text-2, hyphenate: true)
  body
})

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
    // 邊距 29mm（原 20mm）：原本版面寬 481.9pt、正文 11pt ⇒ 每行約 88 個西文字元，
    // 遠超兩端對齊的舒適上限 75（行太長，眼睛回行時容易跳行）。收窄到 431pt ⇒ 約 78
    // 字元。本模板的設計取向本來就是「留白充足、閱讀優先」，這一步才真的兌現它。
    paper: "a4", margin: (x: 29mm, y: 16mm),
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
  // 表格框線：pandoc 產出的是裸 #table(...)，未設 set table 時 Typst 用預設**黑**框。
  // 深色模板上黑線對 #12181f 底的對比僅約 1.19:1（幾乎看不見的髒邊），淺色模板則
  // 與整份克制的線條語彙不一致。一律改用該模板既有的線色，不引入新色。
  set table(stroke: 0.5pt + line-c)
  // pandoc 轉出的 h2/h3 先前完全沒被模板接管（原本是 no-op），走 Typst 內建樣式，
  // 與 section-heading 的視覺語彙不搭、上下間距不足。blockquote 同樣先前無任何標示。
  // figure caption 預設置中，尾段「(來源 [11])」會落單成孤行；一律靠左。
  show figure.caption: it => align(left, it)
  // LLM 慣用「`**導語句。** 內文`」的 run-in 小標——那本來就是投行研報的標準寫法，
  // 所以不對抗它，而是正式承認它：給導語標題色 + 一個 run-in 間距。層級由**位置**
  // 承載（h2/h3 獨占一行、上方有間距、sticky），不靠字重壓過導語，故兩者不打對台。
  show strong: it => [#text(fill: brand-ink-deep, weight: 700, it.body)#h(0.28em, weak: true)]
  show heading.where(level: 2): it => block(
    above: 12pt, below: 4pt, sticky: true, width: 100%,
    {
      // 標題同樣繼承全域 justify：實測 h3「Platform Transitions and Customer
      // Concentration」被兩端對齊拉成「Platform　Transitions　and　Customer」。
      set par(justify: false)
      text(font: sans-cjk, size: 11pt, weight: 700, fill: brand-ink-deep,
           hyphenate: false, it.body)
    },
  )
  show heading.where(level: 3): it => block(
    above: 9pt, below: 3pt, sticky: true, width: 100%,
    {
      // 同上：標題不兩端對齊
      set par(justify: false)
      text(font: sans-cjk, size: 10pt, weight: 700, fill: brand-ink,
           tracking: 0.3pt, hyphenate: false, it.body)
    },
  )
  show quote: it => block(
    above: 8pt, below: 8pt, width: 100%,
    stroke: (left: 1.5pt + brand-gold-line), inset: (left: 9pt, y: 2pt),
    text(size: 10pt, fill: text-2, it.body),
  )

  // ── 報頭（無襯線、細墨青底線）──
  block(stroke: (bottom: 1pt + brand-ink), inset: (bottom: 8pt), width: 100%, [
    #grid(
      columns: (1fr, auto),
      text(font: sans-cjk, size: 9pt, weight: 700, fill: brand-ink, tracking: 1.4pt, brand),
      text(font: sans-cjk, size: 8pt, fill: text-3, features: (tnum: 1), date),
    )
    #v(8pt, weak: true)
    #block(width: 100%, {
      set par(justify: false, leading: 0.42em)
      text(font: sans-cjk, size: 22pt, weight: 700,
           fill: text-1, hyphenate: false, title)
    })
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
  // 免責是頁面 chrome，先前接在正文後 → 正文剛好填滿末頁時會多出一整頁空白。
  if disclaimer != "" {
    place(bottom, float: true, clearance: 10pt, disclaimer-block(disclaimer))
  }
}
