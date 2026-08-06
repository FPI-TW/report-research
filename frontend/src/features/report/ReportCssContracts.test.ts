import reportPageCss from './ReportPage.module.css?raw'
import reportSkeletonCss from './ReportSkeleton.module.css?raw'
import reportPageSource from './ReportPage.tsx?raw'
import tokensCss from '../../styles/tokens.css?raw'

/*
 * 閱讀頁版面契約。
 *
 * 存在理由：#192 把 `.main` 從 flex 改成 grid，讓文件欄跨滿兩列，使免責只佔左欄底部的
 * 高度（改之前它是全寬底帶，橫跨兩欄、把一整條高度從研報內文一起扣走）。那次是用臨時的
 * 無頭瀏覽器 harness 量出來驗證的（1440 寬：doc 高 692 ＝ main 高 692；disc 頂緣 ＝ intel
 * 底緣；1023 寬：disc.top ＝ doc.bottom），但 harness 是拋棄式的，合併後就零守門。
 *
 * **這份契約的極限，先講清楚，免得後人誤以為版面被驗過了。**
 * jsdom 不做 layout、不求值 @media、不求值 @container，所以這裡驗的是「CSS 原始碼有沒有
 * 寫出那個結構」，**不是**「瀏覽器真的排成那樣」。它抓得到的是「有人把 grid 改回 flex」
 * 「把 doc 從某一列拿掉」「窄版順序被改動」這類**結構性**破壞；抓不到的是
 * 「宣告都在但實際排版壞了」——例如祖先層的 height 鏈斷掉、某個 grid 項目被別處的
 * 更高特異度規則覆蓋、或瀏覽器對 minmax 的實作差異。要驗那些只能開真的排版引擎。
 *
 * 兩件**刻意不在這裡驗**的事：
 *  - 「免責在 DOM 裡只有一份」由 ReportPage.test.tsx 的三條「頁底免責恆存在」守——
 *    它們用 `getByText`，多一份就會拋 found multiple elements。那比字串比對強，不重複。
 *  - padding、字級、邊框顏色等視覺微調刻意不釘：釘了會讓正常重構寸步難行，而它們壞掉
 *    是看得見的，不像版面帳壞掉是「內文少了一條高度」那種沒人會注意的退化。
 */

/** 先剝註解：下面的區塊切割是裸的大括號計數，註解裡出現一個 { 或 } 就會切錯位置。 */
function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

function esc(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 把 CSS 切成「頂層」與各個 at-rule 區塊。
 * 分層是必要的：`.main` 在頂層與 @media 內各有一份，混在一起比對會分不出改到哪一份。
 */
function layers(raw: string): { base: string, media: Map<string, string> } {
  const css = stripComments(raw)
  const base: string[] = []
  const media = new Map<string, string>()
  let i = 0
  while (i < css.length) {
    const at = css.indexOf('@', i)
    if (at === -1) { base.push(css.slice(i)); break }
    base.push(css.slice(i, at))
    const open = css.indexOf('{', at)
    if (open === -1) { base.push(css.slice(at)); break }
    let depth = 0
    let j = open
    for (; j < css.length; j += 1) {
      if (css[j] === '{') depth += 1
      else if (css[j] === '}') { depth -= 1; if (depth === 0) break }
    }
    media.set(css.slice(at, open).trim().replace(/\s+/g, ' '), css.slice(open + 1, j))
    i = j + 1
  }
  return { base: base.join('\n'), media }
}

/**
 * 取某一層裡某個選擇器的**所有**規則區塊。
 *
 * 回傳陣列而不是第一個匹配，是這份契約與 RadarCssContracts 最重要的差別：
 * **CSS 生效的是最後一次宣告，不是第一次。** 只取第一個匹配的解析器，遇到「在檔尾追加
 * 一條覆蓋規則」時會完全無感——例如在 ReportPage.module.css 末尾補一行
 * `.main { display: flex; }`，版面帳整個毀掉，而每一條斷言都還是綠的。
 * 所以下面一律走 `only()`：同一層出現兩次即紅，並在訊息裡說明為什麼無法判定。
 */
function blocksFor(layer: string, selector: string): string[] {
  const re = new RegExp(`(?:^|[}\\n])\\s*${esc(selector)}\\s*\\{`, 'g')
  const out: string[] = []
  for (const m of layer.matchAll(re)) {
    const open = layer.indexOf('{', m.index)
    let depth = 0
    for (let j = open; j < layer.length; j += 1) {
      if (layer[j] === '{') depth += 1
      else if (layer[j] === '}') {
        depth -= 1
        if (depth === 0) { out.push(layer.slice(open + 1, j)); break }
      }
    }
  }
  return out
}

/** 取某個區塊裡某個屬性的所有值。同屬性重複宣告同樣是「後者生效」，一樣要擋。 */
function declsFor(block: string, prop: string): string[] {
  const re = new RegExp(`(?:^|[;{])\\s*${esc(prop)}\\s*:\\s*([^;}]+)`, 'g')
  return [...block.matchAll(re)].map(m => m[1].trim().replace(/\s+/g, ' '))
}

function only<T>(items: T[], what: string): T {
  expect(
    items.length,
    `${what} 應恰好出現一次，實際 ${items.length} 次。`
    + 'CSS 以最後一次宣告為準，出現多次時本契約無法判定生效值——請合併成一處。',
  ).toBe(1)
  return items[0]
}

/** `grid-template-areas` 的值 → 逐列的區域名陣列。 */
function areaRows(value: string): string[][] {
  return [...value.matchAll(/['"]([^'"]*)['"]/g)].map(m => m[1].trim().split(/\s+/))
}

function tokenHex(name: string): string {
  // tokens.css 也要剝註解：被註解掉的舊值同樣會被正則撈到，那是假綠。
  const m = new RegExp(`${esc(name)}:\\s*(#[0-9a-fA-F]{6})`).exec(stripComments(tokensCss))
  expect(m, `${name} 應在 tokens.css 定義為六位 hex`).not.toBeNull()
  return m![1]
}

function luminance(hex: string): number {
  const ch = [1, 3, 5].map((o) => {
    const v = Number.parseInt(hex.slice(o, o + 2), 16) / 255
    return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
}

function contrast(fg: string, bg: string): number {
  const [a, b] = [luminance(fg), luminance(bg)]
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

const page = layers(reportPageCss)
const NARROW = '@media (max-width: 1023px)'

describe('閱讀頁寬版：文件欄跨滿兩列，免責只佔左欄', () => {
  it('.main 是 grid（不是 flex）', () => {
    const main = only(blocksFor(page.base, '.main'), '頂層的 .main 規則')
    expect(only(declsFor(main, 'display'), '.main 的 display')).toBe('grid')
  })

  it('grid-template-areas 讓 doc 出現在每一列——這就是「免責不從內文扣高度」的本體', () => {
    const main = only(blocksFor(page.base, '.main'), '頂層的 .main 規則')
    const rows = areaRows(only(declsFor(main, 'grid-template-areas'), '.main 的 grid-template-areas'))

    expect(rows, '寬版應為兩列').toHaveLength(2)
    // 這一條是整份契約的核心：doc 少出現在任何一列，文件欄就不再跨滿，
    // 免責那一列的高度會重新從研報內文身上扣走——而畫面上只會「內文短了一截」，沒人會發現。
    for (const [i, row] of rows.entries()) {
      expect(row, `第 ${i + 1} 列必須含 doc（文件欄要跨滿兩列）`).toContain('doc')
    }
    expect(rows[0], '第一列應是 intel').toContain('intel')
    expect(rows[1], '第二列應是 disc').toContain('disc')
  })

  it('第一軌是 minmax(0, …)：否則 intel／doc 的 overflow-y 永遠捲不起來', () => {
    // 格線項目預設 min-height: auto，內容撐得比軌道大時會把軌道頂開。
    const main = only(blocksFor(page.base, '.main'), '頂層的 .main 規則')
    const tracks = only(declsFor(main, 'grid-template-rows'), '.main 的 grid-template-rows')
    expect(tracks).toMatch(/^minmax\(\s*0/)
  })

  it.each([
    ['.intel', 'intel'],
    ['.doc', 'doc'],
    ['.disc', 'disc'],
  ])('%s 掛在對應的 grid-area', (selector, area) => {
    const block = only(blocksFor(page.base, selector), `頂層的 ${selector} 規則`)
    expect(only(declsFor(block, 'grid-area'), `${selector} 的 grid-area`)).toBe(area)
  })

  it.each(['.intel', '.doc'])('%s 有 min-height: 0（同上，否則捲不起來）', (selector) => {
    const block = only(blocksFor(page.base, selector), `頂層的 ${selector} 規則`)
    expect(only(declsFor(block, 'min-height'), `${selector} 的 min-height`)).toBe('0')
  })

  it('.doc 自己是捲動容器（文件要從吸頂工具列底下捲過去）', () => {
    const block = only(blocksFor(page.base, '.doc'), '頂層的 .doc 規則')
    expect(only(declsFor(block, 'overflow-y'), '.doc 的 overflow-y')).toBe('auto')
  })
})

describe('閱讀頁窄版：堆疊成單欄，免責落到最末', () => {
  it('順序是 intel → doc → disc', () => {
    const narrow = page.media.get(NARROW)
    expect(narrow, `${NARROW} 區塊應存在`).toBeDefined()
    const main = only(blocksFor(narrow!, '.main'), `${NARROW} 內的 .main 規則`)
    const rows = areaRows(only(declsFor(main, 'grid-template-areas'), '窄版 .main 的 grid-template-areas'))
    // 免責夾在側欄與研報之間會變成一段擋路的法遵文字，所以它必須在最末。
    expect(rows).toEqual([['intel'], ['doc'], ['disc']])
  })
})

describe('閱讀頁免責的合規對比', () => {
  it('.disc 的顏色對畫布至少 4.5:1（它是必須讀得到的合規文字）', () => {
    const disc = only(blocksFor(page.base, '.disc'), '頂層的 .disc 規則')
    const value = only(declsFor(disc, 'color'), '.disc 的 color')
    const token = /var\(\s*(--[\w-]+)/.exec(value)
    expect(token, '.disc 的 color 應走 token 而非寫死色碼').not.toBeNull()
    // --tf-text-4 對畫布只有 3.2:1、--tf-text-3 也在 AA 以下；tokens.css 明文指定那兩個
    // 只給「與鄰近資訊重複的裝飾性文字」。免責不屬於那一類，退回去就是合規文字讀不清。
    expect(contrast(tokenHex(token![1]), tokenHex('--tf-canvas'))).toBeGreaterThanOrEqual(4.5)
  })
})

describe('閱讀頁的窄版斷點三處同步', () => {
  // 1023 這個數字有三份各自獨立的副本，改了其中一份而漏掉另兩份是**靜默**的：
  // 骨架與實頁會在不同寬度切版，而摘錄跳轉的捲動補償會挑錯時機。
  it.each([
    ['ReportPage.module.css', reportPageCss],
    ['ReportSkeleton.module.css', reportSkeletonCss],
    ['ReportPage.tsx（跳轉後的捲動補償）', reportPageSource],
  ])('%s 用的是 1023px', (_label, source) => {
    expect(source).toContain('max-width: 1023px')
  })
})

describe('閱讀頁免責只有一個節點', () => {
  it('ReportPage.tsx 只引用一次 styles.disc', () => {
    // 寬窄兩版靠 grid-area 換位置，不是各放一份。真正的守門是 ReportPage.test.tsx 那三條
    // 「頁底免責恆存在」（getByText 撞到兩個節點會直接拋錯），這裡只是把規則寫在
    // 版面契約裡一起看得到，成本一行。
    expect([...reportPageSource.matchAll(/styles\.disc\b/g)]).toHaveLength(1)
  })
})
