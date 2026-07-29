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
//   （量測：邊距 12.5mm + gutter 12pt 下單欄 256.2pt ⇒ 每行約 49 個西文字元，落在
//   兩端對齊的舒適區 45–75 內。降到 10pt 只換到 51 字元，不值得推翻 10.5pt 的定稿。）
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
// hyphenate: false —— 全域 `justify: true` 讓 Typst 的 `hyphenate: auto` 生效，標題
// 也會被連字符斷開（實測 20pt 主標被斷成 "Valuation Analy-sis"）。標題不斷字是排版
// 常規；正文的斷字必須保留（248pt 窄欄若不斷字，字距空洞會更嚴重）。
// sticky: true —— 雙欄一頁有 4 個欄底，標題落在欄底而內文翻到下一欄的機率是單欄的
// 兩倍。sticky 讓標題與後續內容綁在一起。
#let section-heading(body) = block(
  above: 11pt, below: 6pt, breakable: false, sticky: true, width: 100%,
  stroke: (left: 3pt + brand-gold), inset: (left: 6pt, y: 1pt),
  text(font: serif-cjk, size: 12pt, weight: 700, fill: brand-ink-deep,
       hyphenate: false, body),
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
      // 卡片寬度只有 (510 − 3×3pt) / 4 ≈ 125pt，7.5pt 標籤每行放 3–5 個詞。繼承全域
      // justify 的結果是字距被拉到「2026F　　AI　　server　　shipment」。卡片內一律
      // 靠左、允許斷字；字級不得縮（模板規範：不以縮小字級處理溢出）。
      #set par(justify: false, leading: 0.5em)
      #set text(hyphenate: true)
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
//
// span: true → 跨欄浮動置頂。這不是美觀選擇而是可讀性的唯一解：圖以 width:100%
// 嵌入，塞在 248pt 的單欄裡縮放比只有 0.39，chart.py 的 10px 軸標籤變成 3.9pt
// （模板規範的下限是 8pt）。跨欄後可用寬度 510pt、縮放比 0.80 → 8pt。
// caption 靠左（對齊規則在 report() 的 show figure.caption）：置中會讓最後一行只剩
// "[11])" 這種孤字。跨欄時圖寬取 92%、置中——100% 在 A4 上是 271pt 高（佔版面 35%），
// 92% 降到 249pt，仍遠高於可讀下限。
#let chart-figure(svg, caption, supplement: "圖", span: false) = {
  let fig(w) = figure(
    image(bytes(svg), format: "svg", width: w),
    caption: text(size: 8pt, fill: text-3, caption),
    supplement: [#supplement],
  )
  // place(auto)：讓 Typst 自行選頁首或頁尾，比固定 top 更貼近引用它的段落。
  if span {
    place(auto, scope: "parent", float: true, clearance: 10pt, align(center, fig(92%)))
  } else { fig(100%) }
}

// 引用來源：清單而非散文，兩端對齊只會製造空洞（整行只有一個長檔名可推時尤甚）。
// 懸掛縮排讓 [n] 對齊成一欄；hyphenate 與 soft_break_token 的零寬空格一起提供斷點。
#let refs-block(body) = block(width: 100%, {
  set par(justify: false, hanging-indent: 1.1em, spacing: 0.42em, leading: 0.62em)
  set text(size: 9pt, fill: text-2, hyphenate: true)
  body
})

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
    // 邊距 12.5mm（原 15mm）＋ gutter 12pt（原 14pt）＝ 單欄 248.1 → 256.2pt。
    // 兩端對齊的舒適行長是 45–75 字元；本模板原本 47，落在下界邊緣，長 token 一多
    // 就靠字距吸收。12.5mm 仍在印表機安全邊界內（一般下限 10–12mm）。
    paper: "a4", margin: (x: 12.5mm, y: 14mm),
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
  // first-line-indent: 0pt + 明確 spacing：先前首行縮排與段距混用，同一份 PDF 上第一段
  // 有縮排、後續段沒有（pandoc 的區塊結構讓 Typst 的「同 block 內第二段起縮排」規則
  // 落在不一致的位置）。窄欄雙欄一律用段距分隔，不用首行縮排。
  set par(justify: true, leading: 0.75em, first-line-indent: 0pt, spacing: 0.62em)
  // 表格框線：pandoc 產出的是裸 #table(...)，未設 set table 時 Typst 用預設**黑**框。
  // 深色模板上黑線對 #12181f 底的對比僅約 1.19:1（幾乎看不見的髒邊），淺色模板則
  // 與整份克制的線條語彙不一致。一律改用該模板既有的線色，不引入新色。
  set table(stroke: 0.5pt + line-c, fill: (_, y) => if y == 0 { brand-gold-panel } else { none })
  // figure caption 預設置中，尾段「(Source [11])」會落單成孤行；一律靠左。
  show figure.caption: it => align(left, it)
  // LLM 慣用「`**導語句。** 內文`」的 run-in 小標——那本來就是投行研報的標準寫法，
  // 所以不對抗它，而是正式承認它：給導語標題色 + 一個 run-in 間距。層級由**位置**
  // 承載（h2/h3 獨占一行、上方有間距、sticky），不靠字重壓過導語，故兩者不打對台。
  show strong: it => [#text(fill: brand-ink-deep, weight: 700, it.body)#h(0.28em, weak: true)]
  // 章節標題（h1）由 Python 端刻意在 pandoc 之前切出、以 #section-heading 渲染；但
  // **pandoc 轉出的 h2/h3 先前完全沒有被模板接管**（原本這裡是 `show heading: it => it`
  // 一個 no-op），於是走 Typst 內建樣式：與 section-heading 的視覺語彙不搭、上下間距
  // 不足，讀起來像一句粗體句子而不是小標。同時內建樣式在某些字重下會走合成字形，
  // 撞上本模板「繁中無斜體」的硬限制。
  show heading.where(level: 2): it => block(
    above: 11pt, below: 4.5pt, sticky: true, width: 100%,
    {
      // 標題同樣繼承全域 justify：實測 h3「Platform Transitions and Customer
      // Concentration」被兩端對齊拉成「Platform　Transitions　and　Customer」。
      set par(justify: false)
      // h2/h3 用襯線：run-in 導語是 sans 粗體，同為藍色時只靠字級難分辨層級；
      // 字體對比最不含糊，且對齊本模板「標題襯線、正文無襯線」的既有語彙。
      text(font: serif-cjk, size: 11pt, weight: 700, fill: brand-ink-deep,
           hyphenate: false, it.body)
    },
  )
  // h3 用「墨青 + 字距」而非縮小字級：LLM 慣用的粗體導語（`**小標。** 內文`）是 10.5pt
  // 近黑，h3 若只有 9.5pt 灰字會被它壓過去，層級讀起來是反的。
  show heading.where(level: 3): it => block(
    above: 8.5pt, below: 3pt, sticky: true, width: 100%,
    {
      // 同上：標題不兩端對齊
      set par(justify: false)
      // h2/h3 用襯線：run-in 導語是 sans 粗體，同為藍色時只靠字級難分辨層級；
      // 字體對比最不含糊，且對齊本模板「標題襯線、正文無襯線」的既有語彙。
      text(font: serif-cjk, size: 10pt, weight: 700, fill: brand-ink,
           tracking: 0.3pt, hyphenate: false, it.body)
    },
  )
  // blockquote 先前只有縮排、沒有任何標示，在 PDF 上看起來像排版錯誤而非刻意的引文。
  show quote: it => block(
    above: 7pt, below: 7pt, width: 100%,
    stroke: (left: 1.5pt + brand-gold-line), inset: (left: 8pt, y: 2pt),
    text(size: 9.5pt, fill: text-2, it.body),
  )

  // ── 報頭 ──
  block(stroke: (bottom: 1.5pt + brand-ink-deep), inset: (bottom: 7pt), width: 100%, [
    #grid(
      columns: (1fr, auto),
      text(font: serif-cjk, size: 9.5pt, weight: 700, fill: brand-ink-deep,
           tracking: 1.2pt, brand),
      text(font: sans-cjk, size: 7.5pt, fill: text-3, features: (tnum: 1), date),
    )
    #v(7pt, weak: true)
    // 主標不斷字、不兩端對齊（見 section-heading 的說明）
    #block(width: 100%, {
      set par(justify: false, leading: 0.42em)
      text(font: serif-cjk, size: 20pt, weight: 700, fill: text-1,
           hyphenate: false, title)
    })
    #if subject != "" [
      // 非 weak：主標改用 block 包裝（不斷字）後，weak 間距會與 block 的邊界折疊，
      // 兩行標題時副標會貼上 "Analysis" 的下緣。
      #v(7pt)
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
  columns(2, gutter: 12pt, body)

  // ── 方法論註記（可選）與免責（恆存在） ──
  if methods != "" {
    block(above: 10pt, width: 100%, text(font: sans-cjk, size: 7pt, fill: text-3, methods))
  }
  // 免責是頁面 chrome 而非內容流的一部分，卻先前以普通 block 接在正文之後 → 正文剛好
  // 填滿末頁時它被推到新的一頁，產生一整頁只有四行免責的空白頁（實測樣張如此）。
  // float: true 讓它優先貼在當前頁底部；真的塞不下才另起一頁（退化成改動前行為，
  // 不會與正文重疊）。
  if disclaimer != "" {
    place(bottom, float: true, clearance: 10pt, disclaimer-block(disclaimer))
  }
}
