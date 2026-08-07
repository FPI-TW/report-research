import tokensCss from '../../styles/tokens.css?raw'
import brokerDotPlotCss from './BrokerDotPlot.module.css?raw'
import brokerDotPlotSource from './BrokerDotPlot.tsx?raw'
import brokerListCss from './BrokerList.module.css?raw'
import brokerListSource from './BrokerList.tsx?raw'
import brokerTimelineCss from './BrokerTimeline.module.css?raw'
import consensusSnapshotCss from './ConsensusSnapshot.module.css?raw'
import consensusSummaryCss from './ConsensusSummary.module.css?raw'
import coverageStripCss from './CoverageStrip.module.css?raw'
import instrumentPickerCss from './InstrumentPicker.module.css?raw'
import instrumentTableCss from './InstrumentTable.module.css?raw'
import radarHeaderCss from './RadarHeader.module.css?raw'
import radarPageCss from './RadarPage.module.css?raw'
import radarSkeletonCss from './RadarSkeleton.module.css?raw'
import radarSkeletonSource from './RadarSkeleton.tsx?raw'
import radarStatesCss from './RadarStates.module.css?raw'
import recentChangesCss from './RecentChanges.module.css?raw'
import selectPillCss from './SelectPill.module.css?raw'
import thesisCompassCss from './ThesisCompass.module.css?raw'
import windowSegmentedCss from './WindowSegmented.module.css?raw'

const radarCssModules = import.meta.glob('./*.module.css', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 先剝掉 CSS 註解再解析。
 *
 * 不剝的話註解裡引用選擇器或 media query 的字串會被當成真的規則——RadarPage.module.css
 * 就有一段註解逐字引用了 `@media (max-width: 900px)`，解析器會從那裡往後找第一個 `{`，
 * 於是把**下一條規則**的內容當成它的區塊。舊版只取第一個匹配，剛好排在真規則之後才沒出事；
 * 註解要是寫在規則之前，驗的就是完全不相干的區塊，而且照樣是綠的。
 */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

/** 從 openBrace 起走到配對的右大括號，回傳區塊內容。 */
function braceBody(source: string, openBrace: number, label: string): string {
  let depth = 0
  for (let index = openBrace; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1
    if (source[index] === '}') depth -= 1
    if (depth === 0) return source.slice(openBrace + 1, index)
  }
  throw new Error(`${label} 缺少結尾大括號`)
}

/**
 * 取 marker 命中的**每一個**區塊。
 *
 * 回傳陣列而不是第一個匹配，是這支契約 2026-08-06 之前最大的破口：
 * **CSS 生效的是最後一次宣告，不是第一次。** 只看第一個匹配的話，在檔尾追加一條
 * 覆蓋規則（`.layout { grid-template-columns: 1fr; }`）版面整個毀掉，而每一條斷言
 * 都還是綠的——契約看起來在守，實際上守的是一份不生效的宣告。
 * ReportCssContracts 早就是這個作法（見該檔 `blocksFor` 的註解），這支一直沒跟上。
 */
function blocksAfter(source: string, marker: RegExp, label: string): string[] {
  const all = marker.flags.includes('g')
    ? marker
    : new RegExp(marker.source, `${marker.flags}g`)
  const out: string[] = []
  for (const match of source.matchAll(all)) {
    const openBrace = source.indexOf('{', match.index)
    expect(openBrace, `${label} 應有區塊`).toBeGreaterThanOrEqual(0)
    out.push(braceBody(source, openBrace, label))
  }
  return out
}

/**
 * 去掉所有 @media 區塊，留下基礎層。
 *
 * 沒有這一步，「整檔搜某個選擇器」會把 @media 裡的覆蓋規則跟基礎規則混在一起，
 * 而本檔多數選擇器正是「基礎層一份 ＋ 窄螢幕一份」（.grid／.thesis／.scroll／
 * .layout 都是）。要驗窄螢幕那份請顯式走 mediaBlock()。
 */
function baseLayer(source: string): string {
  let out = ''
  let index = 0
  while (index < source.length) {
    const at = source.indexOf('@media', index)
    if (at === -1) {
      out += source.slice(index)
      break
    }
    out += source.slice(index, at)
    const openBrace = source.indexOf('{', at)
    if (openBrace === -1) break
    const body = braceBody(source, openBrace, '@media')
    index = openBrace + body.length + 2
  }
  return out
}

function mediaBlock(source: string, maxWidth: number): string {
  const label = `@media (max-width: ${maxWidth}px)`
  const blocks = blocksAfter(
    stripComments(source),
    new RegExp(`@media\\s*\\(max-width:\\s*${maxWidth}px\\)`),
    label,
  )
  // 同一個查詢寫兩次＝後者覆蓋前者，此時「這個查詢裡宣告了什麼」沒有單一答案。
  expect(blocks.length, `${label} 應恰好出現一次，實際 ${blocks.length} 次`).toBe(1)
  return blocks[0]
}

/** 某選擇器在基礎層的所有規則區塊（依出現順序，後者層疊勝出）。 */
function ruleBlocks(source: string, selector: string): string[] {
  return blocksAfter(
    baseLayer(stripComments(source)),
    new RegExp(`(?:^|[}\\n])\\s*${escapeRegExp(selector)}\\s*\\{`),
    selector,
  )
}

/** 只在「該選擇器應該只有一個區塊」的斷言裡用；多於一個就無法判定，直接紅。 */
function ruleBlock(source: string, selector: string): string {
  const blocks = ruleBlocks(source, selector)
  expect(blocks.length, `${selector} 應恰好宣告一次，實際 ${blocks.length} 次`).toBe(1)
  return blocks[0]
}

/** 取區塊裡某屬性的所有宣告值（同屬性重複宣告一樣是後者生效）。 */
function declarations(block: string, property: string): string[] {
  const re = new RegExp(`(?:^|[;{])\\s*${escapeRegExp(property)}\\s*:\\s*([^;}]+)`, 'g')
  return [...block.matchAll(re)].map((match) => match[1].trim().replace(/\s+/g, ' '))
}

function expectDeclaration(
  source: string,
  selector: string,
  property: string,
  value: string,
): void {
  const values = ruleBlocks(source, selector).flatMap((block) =>
    declarations(block, property),
  )
  expect(values.length, `${selector} 應宣告 ${property}`).toBeGreaterThan(0)
  // 比最後一個而不是「有沒有出現過」：層疊生效的是最後一次宣告。
  expect(values.at(-1), `${selector} 的 ${property} 生效值`).toBe(value)
}

function tokenValue(name: string): string {
  const match = new RegExp(`${escapeRegExp(name)}:\\s*(#[0-9a-fA-F]{6})`).exec(tokensCss)
  expect(match, `${name} 應定義為六位 hex 色碼`).not.toBeNull()
  return match![1]
}

function relativeLuminance(hex: string): number {
  const channels = [1, 3, 5].map((offset) => {
    const encoded = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255
    return encoded <= 0.04045
      ? encoded / 12.92
      : ((encoded + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

function contrastRatio(foreground: string, background: string): number {
  const lighter = Math.max(relativeLuminance(foreground), relativeLuminance(background))
  const darker = Math.min(relativeLuminance(foreground), relativeLuminance(background))
  return (lighter + 0.05) / (darker + 0.05)
}

describe('Radar 390px 版型契約', () => {
  it('論點羅盤在 390px 維持 2x2，僅 340px 以下退為單欄', () => {
    const tablet = mediaBlock(thesisCompassCss, 1080)
    const narrow = mediaBlock(thesisCompassCss, 340)

    expectDeclaration(tablet, '.grid', 'grid-template-columns', 'repeat(2, minmax(0, 1fr))')
    expectDeclaration(narrow, '.grid', 'grid-template-columns', '1fr')
    expect(thesisCompassCss).not.toMatch(/@media\s*\(max-width:\s*(?:3[5-9]\d|[4-9]\d\d)px\)[\s\S]*?\.grid\s*\{\s*grid-template-columns:\s*1fr/)
  })

  /*
   * 骨架與真物的欄寬若不同步，資料到位當下右欄會跳一次寬度、整頁跟著位移。
   * 這一組把「ConsensusSummary.layout」與「RadarSkeleton.summary」釘成同一組值——
   * 兩邊各自寫一次是必然的（一個是真物、一個是佔位），所以由測試保證它們相等。
   */
  it('骨架的共識摘要欄寬與真物逐字相同', () => {
    const columns = 'minmax(0, 1fr) minmax(240px, 300px)'
    expectDeclaration(consensusSummaryCss, '.layout', 'grid-template-columns', columns)
    expectDeclaration(radarSkeletonCss, '.summary', 'grid-template-columns', columns)

    expectDeclaration(mediaBlock(consensusSummaryCss, 1080), '.layout', 'grid-template-columns', '1fr')
    expectDeclaration(mediaBlock(radarSkeletonCss, 1080), '.summary', 'grid-template-columns', '1fr')
  })

  it('骨架鏡射兩張點圖與右欄詳情面板', () => {
    // 少畫一張點圖 ≈ 少一整塊高度；右欄少一塊則載入完成當下整頁重排。
    expect(radarSkeletonSource).toMatch(/Array\.from\(\{\s*length:\s*2\s*\}[\s\S]*?styles\.plot/)
    expect(radarSkeletonSource).toContain('styles.summarySide')
    expect(radarSkeletonSource).toContain('styles.detail')
  })

  it('骨架論點在 390px 保持兩欄，僅 340px 以下退為單欄', () => {
    expectDeclaration(radarSkeletonCss, '.thesis', 'grid-template-columns', 'repeat(2, minmax(0, 1fr))')
    const narrow = mediaBlock(radarSkeletonCss, 340)
    expectDeclaration(narrow, '.thesis', 'grid-template-columns', '1fr')
  })

  it('底部留白涵蓋 56px 導覽、24px 呼吸空間與 safe area', () => {
    const mobile = mediaBlock(radarPageCss, 900)
    expectDeclaration(
      mobile,
      '.scroll',
      'padding',
      '20px 16px calc(80px + env(safe-area-inset-bottom, 0px))',
    )
  })

  /*
   * 吸頂列必須蓋滿捲動容器的上內距。
   *
   * `position: sticky` 的 top 是從 scrollport 的**內距邊**算起，所以 `top: 0` 會讓
   * 吸頂列停在 .scroll 那圈上內距的下方，上面留一條「內容照常捲過去、完全看得見」的縫
   * ——2026-08-07 回報的症狀是券商表格的目標價直接壓在麵包屑與標的名上。
   * 修法是讓 .top 以 --radar-gutter-top 把自己往上拉滿那圈內距，所以這個變數的值
   * **必須等於** padding 的第一個值；兩者分家時畫面上只會差幾個像素，沒有任何錯誤。
   */
  it.each([
    ['桌機', () => radarPageCss],
    ['≤900px', () => mediaBlock(radarPageCss, 900)],
  ])('%s：--radar-gutter-top 等於 .scroll 的上內距', (_label, layer) => {
    const block = ruleBlock(layer(), '.scroll')
    const gutter = /--radar-gutter-top:\s*([^;]+)/.exec(block)
    expect(gutter, '.scroll 應宣告 --radar-gutter-top').not.toBeNull()
    const padding = /(?:^|;)\s*padding:\s*([^\s;]+)/.exec(block)
    expect(padding, '.scroll 應宣告 padding').not.toBeNull()
    expect(gutter![1].trim()).toBe(padding![1].trim())
  })

  it('吸頂列以 gutter 變數上拉，而不是 top: 0', () => {
    const top = ruleBlock(radarHeaderCss, '.top')
    // `top:` 一定要錨在宣告開頭——不錨的話 `margin-top: calc(…)` 就會把這條斷言餵飽，
    // 把 top 改回 0 照樣全綠（實測如此，這條守門原本是假的）。
    // 需要 /m：宣告前面可能是註解的 `*/` 而不是上一條宣告的 `;`，只認 `;` 會誤判成缺漏。
    expect(top).toMatch(/(?:^|;)\s*top:\s*calc\(-1 \* var\(--radar-gutter-top/m)
    expect(top).toMatch(/(?:^|;)\s*margin-top:\s*calc\(-1 \* var\(--radar-gutter-top/m)
    // 自身內距把 gutter 分攤到上下，整條列的高度不變、內容置中
    expect(top).toMatch(/(?:^|;)\s*padding:\s*calc\(var\(--radar-gutter-top/m)
  })
})

describe('Radar 觸控目標契約', () => {
  it.each([
    ['窗期按鈕', windowSegmentedCss, '.btn'],
    ['市場篩選', instrumentPickerCss, '.chip'],
    ['錯誤重試', instrumentPickerCss, '.retry'],
    ['麵包屑返回', radarHeaderCss, '.crumbBtn'],
    ['查看全部', radarPageCss, '.sectionMeta'],
    ['事件報告連結', recentChangesCss, '.link'],
    // 券商面板的展開控制項收斂成一顆（原本另有 .collapse 與每個節點一顆 .evToggle）
    ['歷史報告展開', brokerTimelineCss, '.histToggle'],
    ['歷史報告連結', brokerTimelineCss, '.link'],
    ['狀態主要／次要操作', radarStatesCss, '.btn, .btnSecondary'],
    ['資料涵蓋說明展開', coverageStripCss, '.toggle'],
    ['排序／立場下拉', selectPillCss, '.pill'],
    ['標的列的查看連結', instrumentTableCss, '.go'],
    ['資料範圍切換', consensusSummaryCss, '.segBtn'],
    ['EPS 口徑選擇', consensusSummaryCss, '.fyTrigger'],
    ['切換表格檢視', consensusSummaryCss, '.viewToggle'],
    ['已選券商操作', consensusSummaryCss, '.detailBtn, .detailBtnGhost'],
  ])('%s 至少 44px 高', (_label, css, selector) => {
    expectDeclaration(css, selector, 'min-height', '44px')
  })

  /*
   * 點圖的圓點是唯一刻意**不套** 44px 的可點元素：十四家券商各一列，44px 會讓
   * 「一屏看完全部券商」這件事直接失效，而那正是這張圖存在的理由。本區塊只做桌機，
   * 所以退到 WCAG 2.2 的 24px 最小值並在這裡釘住——沒有這條，日後把它縮成 11px
   * （視覺圓點的大小）不會有任何東西會紅。
   */
  it('點圖圓點的命中區至少 24px（桌機專用，刻意不套 44px）', () => {
    const body = ruleBlock(brokerDotPlotCss, '.dot').replace(/\s+/g, ' ')
    const size = /(?:^|[^-])width: (\d+)px;/.exec(body)
    expect(size, '.dot 應明確設定 width').not.toBeNull()
    expect(Number(size![1])).toBeGreaterThanOrEqual(24)
    expectDeclaration(brokerDotPlotCss, '.dot', 'height', '30px')
  })

  it('麵包屑返回在兩軸都至少 44px', () => {
    expectDeclaration(radarHeaderCss, '.crumbBtn', 'min-width', '44px')
  })
})

describe('Radar token 存在性契約', () => {
  /*
   * 未定義的 CSS 自訂屬性是**完全靜默**的：var() 沒有 fallback 時，該宣告在
   * computed-value 階段失效、屬性退回 initial 值。瀏覽器不報錯，jsdom 測不到，
   * 版面也不會塌——只有那一個屬性悄悄消失。
   *
   * 這條守門是被兩個實際的 typo 逼出來的：
   *   --tf-success-soft  ⇒ 評等分布條「加碼」那一段與圖例色點的 background 失效，
   *                        整條五級分布靜默退化成四級（ConsensusSnapshot 與 InstrumentCard 兩處）
   *   --tf-radius-sm     ⇒ 展開鈕的 border-radius 失效，圓角變成直角
   * 兩者都活了很久，沒有任何測試看得到。
   */
  it('radar 各 module 引用的 --tf-* 都在 tokens.css 有定義', () => {
    const defined = new Set(
      Array.from(tokensCss.matchAll(/(--tf-[a-z0-9-]+)\s*:/gi), (match) => match[1]),
    )
    // 反向自保：正則若哪天失效而抓不到任何定義，下面的比對會全數「通過」成空清單。
    expect(defined.size).toBeGreaterThan(30)

    const missing: string[] = []
    for (const [path, css] of Object.entries(radarCssModules)) {
      for (const used of css.matchAll(/var\(\s*(--tf-[a-z0-9-]+)/gi)) {
        if (!defined.has(used[1])) missing.push(`${path} → ${used[1]}`)
      }
    }

    expect(missing).toEqual([])
  })
})

describe('Radar 語意色特異度契約', () => {
  /*
   * 評等升降那三個數字的方向色由 TSX 掛 .up/.down/.flat 決定，
   * 但 `.movement b` 的特異度是 (0,1,1)、`.down` 只有 (0,1,0)——只要 `.movement b` 自己帶了
   * color，它就會贏，掛在元素上的方向 class 完全不生效，而 DOM 看起來一切正常。
   * 元件測試看不到這件事（class 確實掛上去了），所以在這裡用靜態字串守。
   * （前身是 `.mvNet b`，同一個坑已經踩過一次。）
   */
  it('.movement b 不得自帶 color，否則方向 class 會被特異度蓋掉', () => {
    const body = ruleBlock(consensusSnapshotCss, '.movement b').replace(/\s+/g, ' ')
    expect(body).not.toContain('color:')
  })

  /*
   * 券商表格的同一個坑：`.table td` 自帶 color，特異度 (0,1,1)。評等的
   * .bull/.neu/.bear 只有 (0,1,0)，掛在 <td> 上會整組靜默失效——DOM 上 class 明明在，
   * 顏色就是不出現。所以 TSX 必須把它掛在 <span>；而報告日期那格是掛在 td 上的，
   * 就必須用 `td.dateCell` 把特異度墊高。兩條都只在靜態層看得見。
   */
  it('評等語意色掛在 span 上，不是掛在 td 上', () => {
    expect(brokerListSource).toMatch(
      /<span\s+className=\{`\$\{styles\.rating\}\s*\$\{styles\[RATING_BUCKET\[/,
    )
    expect(brokerListSource).not.toMatch(/<td[^>]*styles\[RATING_BUCKET\[/)
  })

  it('.dateCell 以 td.dateCell 取得高於 .table td 的特異度', () => {
    expect(brokerListCss).toMatch(/\.table\s+td\.dateCell\s*\{/)
  })
})

describe('Radar 點圖座標與可讀性契約', () => {
  /*
   * 券商名那一欄是**所有列共用的同一條 grid 軌道**，靠 .row / .axisRow 的
   * `display: contents` 把子元素提上去給 .rows 排。任何一個改成 flex/grid/block，
   * 每一列就會各自排版：名字欄逐列寬度不同 ⇒ 軌道起點逐列不同 ⇒ 同一個數值在不同列
   * 畫在不同位置，而軸的刻度只有一份。畫面看起來仍然「有圖」，只是全錯。
   */
  it('點圖各列與軸列共用 .rows 的欄軌道', () => {
    expectDeclaration(
      brokerDotPlotCss, '.rows', 'grid-template-columns',
      'minmax(0, max-content) minmax(0, 1fr)',
    )
    expectDeclaration(brokerDotPlotCss, '.row, .axisRow', 'display', 'contents')
  })

  /*
   * 中文的 min-content 是一個字：max-content 軌道遇上長券商名會把整條軌道吃光，
   * 點全部擠到右邊一小段。上限 + ellipsis 是唯一擋得住的組合（全名走 title）。
   */
  it('券商名欄有寬度上限與截斷，長名不會吃掉軌道', () => {
    expectDeclaration(brokerDotPlotCss, '.name', 'max-width', '132px')
    expectDeclaration(brokerDotPlotCss, '.name', 'text-overflow', 'ellipsis')
    expectDeclaration(brokerDotPlotCss, '.name', 'white-space', 'nowrap')
    expect(brokerDotPlotSource).toContain('title={point.broker}')
  })

  /* 同一個坑的表格版：只給 overflow-x 而不給 min-width，中文表格會被壓成逐字直排。 */
  it('表格檢視同時具備 overflow-x 與 min-width', () => {
    expectDeclaration(consensusSummaryCss, '.tableWrap', 'overflow-x', 'auto')
    expectDeclaration(consensusSummaryCss, '.table', 'min-width', '620px')
  })

  /*
   * 新鮮度的視覺編碼必須是**填色的有無**（實心／空心），不是色相——否則灰階列印、
   * 色覺缺陷與低對比螢幕上這條資訊直接消失，而需求明文禁止只靠顏色表達新鮮度。
   * 兩者共用同一個 border 顏色，差別只在 background 是墨色還是底色。
   */
  it('點的新鮮度靠實心／空心區分，不靠色相', () => {
    expectDeclaration(brokerDotPlotCss, '.recent .mark', 'background', 'var(--tf-graphite)')
    expectDeclaration(
      brokerDotPlotCss, '.stale .mark, .unknown .mark', 'background', 'var(--tf-surface)',
    )
    expectDeclaration(brokerDotPlotCss, '.mark', 'border', '2px solid var(--tf-graphite)')
    // 文字圖例與每個點的 aria-label／title 都要把新鮮度講出來，形狀只是輔助。
    expect(brokerDotPlotSource).toContain('FRESHNESS_LABEL.recent')
    expect(brokerDotPlotSource).toContain('FRESHNESS_LABEL.stale')
    expect(brokerDotPlotSource).toContain('aria-label={description}')
  })

  /*
   * 刻度線、刻度標籤與資料點三者的百分比必須來自**同一支** axisPosition。
   * 各算一次的話（例如刻度改用等分、點用真值），軸標與點會在同一張圖上分家——
   * ConsensusSnapshot 的中位指針就踩過這個坑（段寬座標系 vs 五級量表座標系）。
   */
  it('刻度與資料點共用同一支座標函式', () => {
    const uses = brokerDotPlotSource.match(/axisPosition\(/g) ?? []
    expect(uses.length).toBeGreaterThanOrEqual(3)
    expect(brokerDotPlotSource).not.toMatch(/left:\s*`\$\{\s*\(/)
  })
})

describe('Radar muted text 對比契約', () => {
  it('AA muted token 在白底與紙色底都至少達 4.5:1', () => {
    const muted = tokenValue('--tf-text-muted-aa')

    expect(contrastRatio(muted, tokenValue('--tf-surface'))).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(muted, tokenValue('--tf-canvas'))).toBeGreaterThanOrEqual(4.5)
  })

  it.each(['--tf-text-3', '--tf-text-4'])('Radar 不再使用未達 AA 的 %s token', (token) => {
    expect(Object.keys(radarCssModules).length).toBeGreaterThan(0)
    for (const [path, css] of Object.entries(radarCssModules)) {
      expect(css, path).not.toContain(`var(${token})`)
    }
  })
})
