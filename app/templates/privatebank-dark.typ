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
  above: 15pt, below: 7pt, breakable: false, sticky: true, width: 100%,
  stroke: (left: 2.5pt + gold), inset: (left: 8pt, y: 1pt),
  text(font: serif-cjk, size: 13pt, weight: 700, fill: gold,
       hyphenate: false, body),
)

// KPI 卡：深色卡片、鎏金主數字、淺色標籤。等高交給 grid。
#let kpi-card(value, label, change, dir, source, source-label: "來源") = {
  let chg-color = if dir == "up" { sem-up } else if dir == "down" { sem-down } else { ink-3 }
  let chg-mark = if dir == "up" { "▲ " } else if dir == "down" { "▼ " } else { "" }
  block(
    fill: panel, inset: 9pt, width: 100%, radius: 3pt,
    stroke: 0.5pt + gold-line,
    [
      // 卡片窄、標籤 8pt 起，繼承全域 justify 會把字距拉散（見 ib-classic）
      #set par(justify: false, leading: 0.5em)
      #set text(hyphenate: true)
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
        #text(font: sans-cjk, size: 6.5pt, fill: ink-4, source-label + " " + source)
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

// 圖表：chart.py SVG 為淺底，深頁上以白色卡片承載，避免衝突。
// span 為模板契約的一部分（雙欄模板據此跨欄置頂）；本模板單欄故忽略，但簽章必須
// 接受，否則換模板重出會編譯失敗。caption 靠左，避免尾段落單成孤行。
#let chart-figure(svg, caption, supplement: "圖", span: false) = figure(
  block(fill: rgb("#f6f7f9"), inset: 6pt, radius: 3pt,
    image(bytes(svg), format: "svg", width: 100%)),
  caption: text(size: 8pt, fill: ink-3, caption),
  supplement: [#supplement],
)

// 引用來源：清單排版（不兩端對齊、懸掛縮排）。理由見 ib-classic 的同名函式。
#let refs-block(body) = block(width: 100%, {
  set par(justify: false, hanging-indent: 1.1em, spacing: 0.45em)
  set text(size: 9.5pt, fill: ink-2, hyphenate: true)
  body
})

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
  footer-note: "自動生成",
  lang: "zh",
  region: "TW",
  kpi-source-label: "來源",
  body,
) = {
  set document(title: title)
  set page(
    // 邊距 28mm（原 18mm）：原本版面寬 493.2pt、正文 10.5pt ⇒ 每行約 94 個西文字元，
    // 是三款模板裡最長的，遠超舒適上限 75。收窄到 436pt、並把正文提到 11pt（深底上
    // 略大的字更好讀）⇒ 約 79 字元。
    paper: "a4", margin: (x: 28mm, y: 16mm), fill: bg,
    footer: context {
      set text(font: sans-cjk, size: 7.5pt, fill: ink-4)
      grid(
        columns: (1fr, auto),
        align(left, brand + " · " + footer-note),
        align(right, [#counter(page).display("1") / #counter(page).final().first()]),
      )
    },
  )
  set text(font: sans-cjk, size: 11pt, fill: ink-light, lang: lang, region: region)
  set par(justify: true, leading: 0.8em, spacing: 1.0em)
  // 表格框線：pandoc 產出的是裸 #table(...)，未設 set table 時 Typst 用預設**黑**框。
  // 深色模板上黑線對 #12181f 底的對比僅約 1.19:1（幾乎看不見的髒邊），淺色模板則
  // 與整份克制的線條語彙不一致。一律改用該模板既有的線色，不引入新色。
  set table(stroke: 0.5pt + gold-line, fill: (_, y) => if y == 0 { panel } else { none })
  // pandoc 轉出的 h2/h3 先前完全沒被模板接管（原本是 no-op），走 Typst 內建樣式，
  // 與 section-heading 的視覺語彙不搭、上下間距不足。blockquote 同樣先前無任何標示。
  // figure caption 預設置中，尾段「(來源 [11])」會落單成孤行；一律靠左。
  show figure.caption: it => align(left, it)
  // LLM 慣用「`**導語句。** 內文`」的 run-in 小標——那本來就是投行研報的標準寫法，
  // 所以不對抗它，而是正式承認它：給導語標題色 + 一個 run-in 間距。層級由**位置**
  // 承載（h2/h3 獨占一行、上方有間距、sticky），不靠字重壓過導語，故兩者不打對台。
  show strong: it => [#text(fill: gold, weight: 700, it.body)#h(0.28em, weak: true)]
  show heading.where(level: 2): it => block(
    above: 12pt, below: 4pt, sticky: true, width: 100%,
    {
      // 標題同樣繼承全域 justify：實測 h3「Platform Transitions and Customer
      // Concentration」被兩端對齊拉成「Platform　Transitions　and　Customer」。
      set par(justify: false)
      // h2/h3 用襯線：run-in 導語是 sans 粗體，同為藍色時只靠字級難分辨層級；
      // 字體對比最不含糊，且對齊本模板「標題襯線、正文無襯線」的既有語彙。
      text(font: serif-cjk, size: 10.5pt, weight: 700, fill: ink-light,
           hyphenate: false, it.body)
    },
  )
  show heading.where(level: 3): it => block(
    above: 9pt, below: 3pt, sticky: true, width: 100%,
    {
      // 同上：標題不兩端對齊
      set par(justify: false)
      // h2/h3 用襯線：run-in 導語是 sans 粗體，同為藍色時只靠字級難分辨層級；
      // 字體對比最不含糊，且對齊本模板「標題襯線、正文無襯線」的既有語彙。
      text(font: serif-cjk, size: 10pt, weight: 700, fill: gold,
           tracking: 0.3pt, hyphenate: false, it.body)
    },
  )
  show quote: it => block(
    above: 8pt, below: 8pt, width: 100%,
    stroke: (left: 1.5pt + gold-line), inset: (left: 9pt, y: 2pt),
    text(size: 10pt, fill: ink-2, it.body),
  )

  // ── 報頭（鎏金底線）──
  block(stroke: (bottom: 1.5pt + gold), inset: (bottom: 8pt), width: 100%, [
    #grid(
      columns: (1fr, auto),
      text(font: serif-cjk, size: 9.5pt, weight: 700, fill: gold, tracking: 1.4pt, brand),
      text(font: sans-cjk, size: 8pt, fill: ink-3, features: (tnum: 1), date),
    )
    #v(8pt, weak: true)
    #block(width: 100%, {
      set par(justify: false, leading: 0.42em)
      text(font: serif-cjk, size: 21pt, weight: 700,
           fill: ink-light, hyphenate: false, title)
    })
    #if subject != "" [
      #v(6pt, weak: true)
      #block(width: 100%, stroke: (left: 2.5pt + gold), inset: (left: 8pt, y: 1pt),
        text(font: sans-cjk, size: 8.5pt, fill: ink-2, subject))
    ]
  ])

  if kpi.len() > 0 { kpi-strip(kpi, source-label: kpi-source-label) }
  v(10pt)
  body

  if methods != "" {
    block(above: 12pt, width: 100%, text(font: sans-cjk, size: 7pt, fill: ink-3, methods))
  }
  // 免責是頁面 chrome，先前接在正文後 → 正文剛好填滿末頁時會多出一整頁空白。
  if disclaimer != "" {
    place(bottom, float: true, clearance: 10pt, disclaimer-block(disclaimer))
  }
}
