import { describe, expect, it } from 'vitest'
import tokensCss from '../../styles/tokens.css?raw'
import trendChartCss from '../../features/admin/ops/OpsTrendChart.module.css?raw'
import requireAdminCss from '../shell/RequireAdmin.module.css?raw'
import consoleCss from './Console.module.css?raw'
import overlayCss from './Overlay.module.css?raw'

/*
 * 主控台共用樣式的契約：按鈕對比、不寫死色碼、鍵盤焦點的指示。
 *
 * jsdom 不求值 CSS，所以這裡驗的是「原始碼有沒有寫出那個宣告」與「token 的色值算出來夠不夠」，
 * 不是瀏覽器實際畫出來的樣子；後者要開真的瀏覽器看（畫面檢查那一輪就是這樣量出不合格的）。
 */

const stripComments = (css: string) => css.replace(/\/\*[\s\S]*?\*\//g, '')

/** 取某個選擇器（逐字）的規則內容；同一個選擇器出現多次就全部接起來。 */
function rule(css: string, selector: string): string {
  const esc = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const found = [...stripComments(css).matchAll(new RegExp(`(?<=^|[},])\\s*${esc}\\s*(?:,[^{]*)?\\{([^}]*)\\}`, 'g'))]
  expect(found.length, `找不到 ${selector} 的規則`).toBeGreaterThan(0)
  return found.map(m => m[1]).join(';')
}

/** tokens.css 裡某個主題區塊的 `--ac-*` 色值（只收六位 hex）。 */
function themeTokens(theme: 'light' | 'dark'): Record<string, string> {
  const head = theme === 'light' ? ":root, :root[data-theme='light'] {" : ":root[data-theme='dark'] {"
  const css = stripComments(tokensCss)
  const start = css.indexOf(head)
  expect(start, `tokens.css 應有 ${theme} 主題區塊`).toBeGreaterThan(-1)
  const block = css.slice(start + head.length, css.indexOf('}', start))
  return Object.fromEntries([...block.matchAll(/(--ac-[a-z0-9-]+):\s*(#[0-9a-fA-F]{6})\s*;/g)].map(m => [m[1], m[2]]))
}

type Rgb = [number, number, number]
const rgb = (hex: string): Rgb => [1, 3, 5].map(o => Number.parseInt(hex.slice(o, o + 2), 16)) as Rgb
/** CSS `filter: brightness(k)`：sRGB 各通道乘 k、上限 255（整顆按鈕連字一起變亮）。 */
const brighten = (c: Rgb, k: number): Rgb => c.map(v => Math.min(255, v * k)) as Rgb
function luminance(c: Rgb): number {
  const [r, g, b] = c.map((v) => {
    const x = v / 255
    return x <= 0.04045 ? x / 12.92 : ((x + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}
function contrast(a: Rgb, b: Rgb): number {
  const [x, y] = [luminance(a), luminance(b)]
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05)
}

const HOVER_BRIGHTNESS = 1.06
const BUTTONS = [
  { name: '金色主要按鈕', selector: '.btnPrimary', bg: '--ac-goldbtn', fg: '--ac-ongold' },
  { name: '紅色確認按鈕', selector: '.btnDanger', bg: '--ac-badbtn', fg: '--ac-onbad' },
] as const

describe('實心按鈕的對比（WCAG AA，文字 ≥ 4.5:1）', () => {
  for (const b of BUTTONS) {
    it(`${b.name}用按鈕專用的 token，不直接用燈號的色`, () => {
      const decl = rule(consoleCss, b.selector)
      expect(decl).toContain(`background: var(${b.bg})`)
      expect(decl).toContain(`border-color: var(${b.bg})`)
      expect(decl).toContain(`color: var(${b.fg})`)
    })
    for (const theme of ['light', 'dark'] as const) {
      it(`${b.name}：${theme === 'light' ? '亮色' : '深色'}主題，平常與 hover 都過`, () => {
        const t = themeTokens(theme)
        expect(t[b.bg], `${b.bg} 應定義為六位 hex`).toBeDefined()
        expect(t[b.fg], `${b.fg} 應定義為六位 hex`).toBeDefined()
        const [bg, fg] = [rgb(t[b.bg]), rgb(t[b.fg])]
        expect(contrast(fg, bg)).toBeGreaterThanOrEqual(4.5)
        expect(contrast(brighten(fg, HOVER_BRIGHTNESS), brighten(bg, HOVER_BRIGHTNESS))).toBeGreaterThanOrEqual(4.5)
      })
    }
  }

  it('hover 的亮度就是上面算的那個值（改了要一起改）', () => {
    for (const sel of ['.btnPrimary:hover:not(:disabled)', '.btnDanger:hover:not(:disabled)']) {
      expect(rule(consoleCss, sel)).toContain(`filter: brightness(${HOVER_BRIGHTNESS})`)
    }
    expect(rule(requireAdminCss, '.action:hover')).toContain(`filter: brightness(${HOVER_BRIGHTNESS})`)
  })

  it('「請先開啟兩步驟驗證」的按鈕與金色主要按鈕同一組色', () => {
    const decl = rule(requireAdminCss, '.action')
    expect(decl).toContain('background: var(--ac-goldbtn)')
    expect(decl).toContain('color: var(--ac-ongold)')
  })

  it('燈號與指示條的金、紅沒有跟著改（按鈕是另外一組）', () => {
    expect(themeTokens('light')).toMatchObject({ '--ac-gold': '#ae7415', '--ac-bad': '#d6453a' })
    expect(themeTokens('dark')).toMatchObject({ '--ac-gold': '#d4a64a', '--ac-bad': '#f2655a' })
  })
})

describe('不寫死色碼', () => {
  const files = {
    'Console.module.css': consoleCss, 'Overlay.module.css': overlayCss,
    'RequireAdmin.module.css': requireAdminCss, 'OpsTrendChart.module.css': trendChartCss,
  }
  for (const [name, css] of Object.entries(files)) {
    it(`${name} 的色全部走 --ac-*`, () => {
      const body = stripComments(css)
      expect(body.match(/#[0-9a-fA-F]{3,8}\b/g) ?? []).toEqual([])
      expect(body.match(/\brgba?\(/g) ?? []).toEqual([])
      // 舊的 --tf-* 色不能再用；動畫時間（--tf-dur-*、--tf-ease-*）不是色，不在此限。
      expect((body.match(/--tf-[a-z0-9-]+/g) ?? []).filter(v => !/^--tf-(dur|ease)-/.test(v))).toEqual([])
    })
  }
})

describe('鍵盤焦點與輸入', () => {
  it('「更多」選單：鍵盤移到的項目保留底色，再加一圈內縮的金框（和滑鼠 hover 分得出來）', () => {
    const decl = rule(overlayCss, '.menuItem:focus-visible')
    expect(decl).toContain('background: var(--ac-menuh)')
    expect(decl).toContain('outline: 2px solid var(--ac-gold)')
    expect(decl).toContain('outline-offset: -2px')
    // 內縮的框要排在 `outline: none` 之後才會生效。
    expect(decl.lastIndexOf('outline: 2px solid')).toBeGreaterThan(decl.lastIndexOf('outline: none'))
    expect(rule(overlayCss, '.menuItem:hover:not(:disabled)')).not.toContain('outline: 2px')
  })

  it('驗證碼欄：字距只給輸入的數字，placeholder 不拉開', () => {
    expect(rule(overlayCss, '.codeInput')).toContain('letter-spacing: 0.12em')
    expect(rule(overlayCss, '.codeInput::placeholder')).toContain('letter-spacing: normal')
  })

  it('管理後台的焦點環是金色（外殼、內容區、浮層一致）；研報平台維持石墨色', () => {
    const css = stripComments(tokensCss)
    expect(css).toMatch(/:root\[data-theme\]\s*\{\s*--tf-focus-ring:\s*var\(--ac-gold\);\s*\}/)
    expect(rule(css, ':focus-visible')).toContain('outline: 2px solid var(--tf-focus-ring, var(--tf-graphite))')
    // 輸入框只換邊框色（設計原樣）：自己的 outline: none 不能被蓋掉，所以焦點環的規則不能比單一類名更具體。
    expect(rule(consoleCss, '.input')).toContain('outline: none')
    expect(css).not.toMatch(/[.\]\w]\s+:focus-visible\s*\{/)
  })
})

describe('資料庫趨勢圖的軸', () => {
  for (const sel of ['.yaxis', '.xaxis']) {
    it(`${sel}：等寬字、文字階 --ac-t5`, () => {
      const decl = rule(trendChartCss, sel)
      expect(decl).toContain('font-family: var(--ac-mono)')
      expect(decl).toContain('color: var(--ac-t5)')
    })
  }

  it('--ac-t5 對卡片底色在兩種主題都 ≥ 4.5:1', () => {
    for (const theme of ['light', 'dark'] as const) {
      const t = themeTokens(theme)
      expect(contrast(rgb(t['--ac-t5']), rgb(t['--ac-panel']))).toBeGreaterThanOrEqual(4.5)
    }
  })
})
