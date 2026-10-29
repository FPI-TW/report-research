import { describe, expect, it } from 'vitest'
import tokensCss from '../../styles/tokens.css?raw'

const radarCssModules = import.meta.glob('./*.module.css', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>

const radarSources = import.meta.glob('./*.tsx', {
  eager: true,
  import: 'default',
  query: '?raw',
}) as Record<string, string>

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

function stripComments(css: string): string {
  return css.replace(/\/\*[\s\S]*?\*\//g, '')
}

/** TSX 去掉註解（含 JSX 裡包在大括號中的區塊註解），只看程式與文案。 */
function stripCodeComments(source: string): string {
  return source
    .replace(/\{\/\*[\s\S]*?\*\/\}/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '')
}

function tokenValue(name: string): string {
  const match = new RegExp(`${escapeRegExp(name)}:\\s*(#[0-9a-fA-F]{6})`).exec(tokensCss)
  expect(match, `${name} 應定義為六位 hex 色碼`).not.toBeNull()
  return match![1]
}

function relativeLuminance(hex: string): number {
  const channels = [1, 3, 5].map((offset) => {
    const encoded = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255
    return encoded <= 0.04045 ? encoded / 12.92 : ((encoded + 0.055) / 1.055) ** 2.4
  })
  return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
}

function contrastRatio(foreground: string, background: string): number {
  const lighter = Math.max(relativeLuminance(foreground), relativeLuminance(background))
  const darker = Math.min(relativeLuminance(foreground), relativeLuminance(background))
  return (lighter + 0.05) / (darker + 0.05)
}

describe('券商共識 token 存在性契約', () => {
  /*
   * 未定義的 CSS 自訂屬性是完全靜默的：var() 沒有 fallback 時該宣告在 computed-value 階段失效、
   * 退回 initial 值。瀏覽器不報錯，jsdom 也測不到，只有那一個屬性悄悄消失。
   */
  it('各 module 引用的 --tf-* 都在 tokens.css 有定義', () => {
    const defined = new Set(
      Array.from(tokensCss.matchAll(/(--tf-[a-z0-9-]+)\s*:/gi), (match) => match[1]),
    )
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

describe('券商共識文字對比契約', () => {
  it('AA muted token 在白底與畫布底都至少達 4.5:1', () => {
    const muted = tokenValue('--tf-text-muted-aa')
    expect(contrastRatio(muted, tokenValue('--tf-surface'))).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(muted, tokenValue('--tf-canvas'))).toBeGreaterThanOrEqual(4.5)
  })

  /*
   * 交接包的輔助色 #8e8e93（--tf-text-4）在白底只有 3.3:1，小字讀不清。本頁的次要文字一律用
   * --tf-text-muted-aa（#6b6b70），色階只差一階、讀得到。
   */
  it.each(['--tf-text-3', '--tf-text-4'])('不使用未達 AA 的 %s', (token) => {
    expect(Object.keys(radarCssModules).length).toBeGreaterThan(0)
    for (const [path, css] of Object.entries(radarCssModules)) {
      expect(css, path).not.toContain(`var(${token})`)
    }
  })
})

describe('券商共識動態契約', () => {
  /* 交接包：所有 hover 位移與動畫都要尊重 prefers-reduced-motion。 */
  it('有 animation 或 transition 的 module 都有 reduced-motion 區塊', () => {
    const missing: string[] = []
    for (const [path, raw] of Object.entries(radarCssModules)) {
      const css = stripComments(raw)
      if (!/\b(animation|transition)\s*:/.test(css)) continue
      if (!css.includes('@media (prefers-reduced-motion: reduce)')) missing.push(path)
    }
    expect(missing).toEqual([])
  })

  /* 交接包「實測抓到的坑」1：scrollIntoView 會連外層一起捲。 */
  it('不用 scrollIntoView', () => {
    for (const [path, source] of Object.entries(radarSources)) {
      expect(stripCodeComments(source), path).not.toMatch(/\.scrollIntoView\b/)
    }
  })
})

describe('券商共識字型契約', () => {
  /* 交接包「實測抓到的坑」4：數字換 monospace 會讓行高與基線和旁邊的字錯開；只有代號用 --tf-code。 */
  it('不用 --tf-mono；--tf-code 只出現在代號樣式並沿用父層行高', () => {
    for (const [path, raw] of Object.entries(radarCssModules)) {
      const css = stripComments(raw)
      expect(css, path).not.toContain('var(--tf-mono)')
      for (const match of css.matchAll(/([^{}]+)\{([^}]*var\(--tf-code\)[^}]*)\}/g)) {
        expect(match[1].trim(), path).toBe('.code')
        expect(match[2], path).toMatch(/line-height:\s*inherit/)
      }
    }
  })

  it('文案不用「·」當分隔符', () => {
    for (const [path, source] of Object.entries(radarSources)) {
      if (path.endsWith('.test.tsx')) continue
      expect(stripCodeComments(source), path).not.toContain('·')
    }
  })
})
