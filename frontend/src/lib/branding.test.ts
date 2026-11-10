import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  ACCENT_CSS_VARS,
  NEUTRAL_BRANDING,
  accentStyleText,
  applyBrandingToDocument,
  brandingResponseSchema,
  fromResponse,
  loadBranding,
  setBranding,
} from './branding'

const ACME = {
  institution_name: '艾克米',
  product_name: '艾克米研究平台',
  short_name: '艾克米研報',
  logo_light_url: '/api/branding/assets/logo.png?v=1',
  logo_dark_url: '/api/branding/assets/logo-dark.png?v=1',
  favicon_url: '/api/branding/assets/favicon.svg?v=1',
  colors: { light: { accent_strong: '#1f5fbf' }, dark: { accent_strong: 'rgba(120, 170, 255, 0.9)' } },
}

afterEach(() => {
  vi.unstubAllGlobals()
  setBranding(null)
  document.getElementById('tf-brand-colors')?.remove()
  document.querySelectorAll('link[rel="icon"]').forEach(el => el.remove())
})

describe('品牌回應的解析', () => {
  it('完整回應', () => {
    const b = fromResponse(brandingResponseSchema.parse(ACME))
    expect(b).toMatchObject({ institutionName: '艾克米', productName: '艾克米研究平台', shortName: '艾克米研報' })
    expect(b.logoDarkUrl).toBe(ACME.logo_dark_url)
  })

  it('缺欄位不失敗，退回中性預設；暗色 logo 沒設就沿用亮色', () => {
    const b = fromResponse(brandingResponseSchema.parse({ logo_light_url: '/x.png', future_field: 1 }))
    expect(b.productName).toBe(NEUTRAL_BRANDING.productName)
    expect(b.institutionName).toBe(NEUTRAL_BRANDING.institutionName)
    expect(b.logoDarkUrl).toBe('/x.png')
    expect(b.faviconUrl).toBeNull()
  })
})

describe('強調色', () => {
  it('亮色寫 :root、暗色寫 data-theme=dark；一個鍵對到 tokens.css 與管理後台兩組變數', () => {
    const css = accentStyleText({ colorsLight: { accent_strong: '#1f5fbf' }, colorsDark: { accent_text: '#abcdef' } })
    expect(css).toContain(":root, :root[data-theme='light'] { --tf-gold-strong: #1f5fbf; --ac-gold: #1f5fbf; }")
    expect(css).toContain(":root[data-theme='dark'] { --tf-gold-text: #abcdef; --ac-goldt: #abcdef; }")
  })

  it('未知鍵與不合法的色值一律丟掉（它會被原樣寫進 <style>）', () => {
    const css = accentStyleText({
      colorsLight: { primary: '#000000', accent_text: 'red; } body { display: none', accent_line: 'url(x)' },
      colorsDark: {},
    })
    expect(css).toBe('')
  })

  it('console_* 只蓋管理後台的變數，而且不論 profile 的鍵序都排在 accent_line／accent_panel 之後', () => {
    const css = accentStyleText({
      colorsLight: { console_accent_line: '#222222', console_accent_panel: '#444444', accent_panel: '#333333', accent_line: '#111111' },
      colorsDark: {},
    })
    expect(css).toBe(":root, :root[data-theme='light'] { --tf-gold-line: #111111; --ac-gline: #111111; "
      + '--tf-gold-panel: #333333; --ac-gsel: #333333; --ac-gline: #222222; --ac-gsel: #444444; }')
  })

  it('Fluid 的強調色只由亮色的 accent_text 覆寫，深色主題不動它', () => {
    const css = accentStyleText({ colorsLight: { accent_text: '#123456' }, colorsDark: { accent_text: '#abcdef' } })
    expect(css).toBe(":root, :root[data-theme='light'] { --tf-gold-text: #123456; --ac-goldt: #123456; "
      + "--tf-fluid-accent: #123456; }\n:root[data-theme='dark'] { --tf-gold-text: #abcdef; --ac-goldt: #abcdef; }")
  })

  it('每個鍵都有對應的 CSS 變數', () => {
    for (const vars of Object.values(ACCENT_CSS_VARS)) expect(vars.length).toBeGreaterThan(0)
  })
})

describe('套用到文件', () => {
  it('標題、favicon 與強調色樣式表', () => {
    const link = document.createElement('link')
    link.rel = 'icon'
    link.type = 'image/svg+xml'
    link.href = '/favicon.svg'
    document.head.appendChild(link)
    applyBrandingToDocument(fromResponse(ACME))
    expect(document.title).toBe('艾克米研究平台')
    expect(document.querySelector<HTMLLinkElement>('link[rel="icon"]')!.getAttribute('href')).toBe(ACME.favicon_url)
    const style = document.getElementById('tf-brand-colors')!
    expect(style.textContent).toContain('--tf-gold-strong: #1f5fbf')
    expect(document.head.lastElementChild).toBe(style)  // 排在最後才蓋得過 tokens.css
  })

  it('沒有色票時不留樣式表', () => {
    applyBrandingToDocument(fromResponse(ACME))
    applyBrandingToDocument(NEUTRAL_BRANDING)
    expect(document.getElementById('tf-brand-colors')).toBeNull()
  })
})

describe('loadBranding', () => {
  it('抓到就寫進 store 並套用', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify(ACME), { status: 200 })))
    const b = await loadBranding()
    expect(b.productName).toBe('艾克米研究平台')
    expect(document.title).toBe('艾克米研究平台')
  })

  it('抓不到時留在中性預設，不拋', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('x', { status: 503 })))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    expect(await loadBranding()).toBe(NEUTRAL_BRANDING)
    expect(warn).toHaveBeenCalled()
    warn.mockRestore()
  })
})
