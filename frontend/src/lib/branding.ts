import { useSyncExternalStore } from 'react'
import { z } from 'zod'
import { getLocale, isLocale, subscribeLocale, t, type Locale } from '@/i18n'

/**
 * 執行期品牌（`GET /api/branding`，免登入；後端 `web/routers/branding.py`，設定來源 `app/profile.py`）。
 *
 * 機構名、產品名、logo、favicon 與強調色都不寫死在前端：同一份 build 服務不同 profile。抓到之前與抓不到時一律用
 * 中性預設（`NEUTRAL_BRANDING`），**不阻斷頁面**——品牌是外觀，不是功能。
 *
 * 兩層（同 `useFeatures.ts`）：`loadBranding()` 在 `main.tsx` 啟動時抓一次、寫進 module 級 store 並套用到文件
 * （`document.title`、favicon、強調色 CSS 變數）；元件以 `useBranding()` 讀 store（useSyncExternalStore，不需要
 * QueryClient，測試以 `setBranding()` 模擬後端的答案）。
 *
 * 欄位一律 `optional()`／`nullish()`：前後端版本錯開時，少一個欄位不能讓整份解析失敗、退回中性預設。
 *
 * 名稱依介面語系挑（`[institution]` 的 `*_en`，沒給就用原名；profile 沒給名稱時是中性預設的譯名）：`Branding` 的三個
 * 名稱是 getter，讀的當下才依語系決定，切換語系不必重抓。`locale` 是 profile 的 `[locale]`（預設語系與開放的語系）。
 */
export const brandingResponseSchema = z.object({
  institution_name: z.string().optional(),
  product_name: z.string().optional(),
  short_name: z.string().optional(),
  institution_name_en: z.string().optional(),
  product_name_en: z.string().optional(),
  short_name_en: z.string().optional(),
  locale: z.object({
    default: z.string().optional(),
    available: z.array(z.string()).optional(),
  }).optional(),
  logo_light_url: z.string().nullish(),
  logo_dark_url: z.string().nullish(),
  favicon_url: z.string().nullish(),
  colors: z.object({
    light: z.record(z.string(), z.string()).optional(),
    dark: z.record(z.string(), z.string()).optional(),
  }).optional(),
})

export interface Branding {
  /** 機構名；中性預設是「本平台」（免責聲明「非○○之立場」讀起來仍通順）。 */
  institutionName: string
  productName: string
  shortName: string
  logoLightUrl: string | null
  logoDarkUrl: string | null
  faviconUrl: string | null
  colorsLight: Record<string, string>
  colorsDark: Record<string, string>
  /** profile 的預設語系；沒抓到（或不認得）是 null，由 `src/i18n` 退回 zh-Hant。 */
  defaultLocale: Locale | null
  /** profile 開放的語系；null＝全部。 */
  availableLocales: Locale[] | null
}

interface BrandNames { institutionName?: string; productName?: string; shortName?: string }

/** 依目前語系挑名稱：英文介面先用 `*_en`、再用原名；兩者都沒有時是中性預設的譯名。 */
function brandingWithNames(
  zh: BrandNames,
  en: BrandNames,
  rest: Omit<Branding, 'institutionName' | 'productName' | 'shortName'>,
): Branding {
  const pick = (k: keyof BrandNames): string | undefined => (getLocale() === 'en' ? en[k] || zh[k] : zh[k])
  return {
    get institutionName() { return pick('institutionName') || t('lib.branding.thisPlatform') },
    get productName() { return pick('productName') || t('lib.branding.researchIntelligence') },
    get shortName() { return pick('shortName') || pick('productName') || t('lib.branding.researchIntelligence') },
    ...rest,
  }
}

export const NEUTRAL_BRANDING: Branding = brandingWithNames({}, {}, {
  logoLightUrl: null,
  logoDarkUrl: null,
  faviconUrl: null,
  colorsLight: {},
  colorsDark: {},
  defaultLocale: null,
  availableLocales: null,
})

/**
 * profile 的強調色鍵 → 要覆寫的 CSS 變數（`styles/tokens.css` 的鎏金系列與管理後台的 `--ac-gold*`）。
 * 鍵與後端 `app/profile.py` 的 `ACCENT_TOKENS` 逐一相同（`tests/test_profile.py` 對帳）。
 */
export const ACCENT_CSS_VARS: Record<string, readonly string[]> = {
  accent_text: ['--tf-gold-text', '--ac-goldt'],
  accent_strong: ['--tf-gold-strong', '--ac-gold'],
  accent_hover: ['--tf-gold-hover'],
  accent_tint: ['--tf-gold-tint'],
  accent_line: ['--tf-gold-line', '--ac-gline'],
  accent_panel: ['--tf-gold-panel', '--ac-gsel'],
  on_accent: ['--tf-on-gold', '--ac-ongold'],
  accent_button: ['--ac-goldbtn'],
}

// 後端已驗過色值；這裡再擋一次，因為它會被原樣寫進 <style>（多一道防線，不信任網路另一端）。
const COLOR_RE = /^(#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(,\s*(0|1|0?\.\d{1,3})\s*)?\))$/

export function fromResponse(raw: z.infer<typeof brandingResponseSchema>): Branding {
  const zh: BrandNames = {
    institutionName: raw.institution_name?.trim(),
    productName: raw.product_name?.trim(),
    shortName: raw.short_name?.trim(),
  }
  const en: BrandNames = {
    institutionName: raw.institution_name_en?.trim(),
    productName: raw.product_name_en?.trim(),
    shortName: raw.short_name_en?.trim(),
  }
  const available = (raw.locale?.available ?? []).filter(isLocale)
  const def = raw.locale?.default
  return brandingWithNames(zh, en, {
    logoLightUrl: raw.logo_light_url ?? null,
    logoDarkUrl: raw.logo_dark_url ?? raw.logo_light_url ?? null,
    faviconUrl: raw.favicon_url ?? null,
    colorsLight: raw.colors?.light ?? {},
    colorsDark: raw.colors?.dark ?? {},
    defaultLocale: isLocale(def) ? def : null,
    availableLocales: available.length ? available : null,
  })
}

function declarations(colors: Record<string, string>): string {
  const out: string[] = []
  for (const [key, value] of Object.entries(colors)) {
    const vars = ACCENT_CSS_VARS[key]
    if (!vars || !COLOR_RE.test(value)) continue
    for (const v of vars) out.push(`${v}: ${value};`)
  }
  return out.join(' ')
}

/** 強調色覆寫的樣式表文字。亮色同時寫 `:root`（主平台沒有 data-theme），暗色只在管理後台的 data-theme 下生效。 */
export function accentStyleText(b: Pick<Branding, 'colorsLight' | 'colorsDark'>): string {
  const light = declarations(b.colorsLight)
  const dark = declarations(b.colorsDark)
  return [
    light && `:root, :root[data-theme='light'] { ${light} }`,
    dark && `:root[data-theme='dark'] { ${dark} }`,
  ].filter(Boolean).join('\n')
}

const STYLE_ID = 'tf-brand-colors'

/** 把品牌套到文件上：標題、favicon、強調色。樣式表掛在 <head> 最後，才蓋得過 tokens.css 同樣權重的宣告。 */
export function applyBrandingToDocument(b: Branding, doc: Document = document): void {
  doc.title = b.productName
  if (b.faviconUrl) {
    let link = doc.querySelector<HTMLLinkElement>('link[rel="icon"]')
    if (!link) {
      link = doc.createElement('link')
      link.rel = 'icon'
      doc.head.appendChild(link)
    }
    link.removeAttribute('type')
    link.href = b.faviconUrl
  }
  const css = accentStyleText(b)
  let style = doc.getElementById(STYLE_ID)
  if (!css) {
    style?.remove()
    return
  }
  if (!style) {
    style = doc.createElement('style')
    style.id = STYLE_ID
  }
  style.textContent = css
  doc.head.appendChild(style)
}

let current: Branding = NEUTRAL_BRANDING
const listeners = new Set<() => void>()

function subscribe(cb: () => void): () => void {
  listeners.add(cb)
  return () => listeners.delete(cb)
}

function getSnapshot(): Branding {
  return current
}

/** 寫入 store（`loadBranding` 抓到時呼叫；測試也用它，傳 null 還原成中性預設）。 */
export function setBranding(next: Branding | null): void {
  const value = next ?? NEUTRAL_BRANDING
  if (value === current) return
  current = value
  listeners.forEach((l) => l())
}

export function useBranding(): Branding {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot)
}

// 切換語系時頁面標題跟著換（名稱是 getter，重新套用一次即可）。
subscribeLocale(() => {
  try { document.title = current.productName } catch { /* ignore */ }
})

/** 抓 `/api/branding`、寫進 store 並套用到文件；失敗就留在中性預設（只記一筆警告，不拋）。 */
export async function loadBranding(): Promise<Branding> {
  try {
    const resp = await fetch('/api/branding', { credentials: 'same-origin', cache: 'no-store' })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const branding = fromResponse(brandingResponseSchema.parse(await resp.json()))
    setBranding(branding)
    applyBrandingToDocument(branding)
    return branding
  } catch (err) {
    console.warn('讀不到品牌設定，改用中性預設', err)
    return current
  }
}
