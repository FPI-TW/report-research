import tokensCss from '../../styles/tokens.css?raw'
import brokerTimelineCss from './BrokerTimeline.module.css?raw'
import instrumentPickerCss from './InstrumentPicker.module.css?raw'
import keyFiguresCss from './KeyFigures.module.css?raw'
import radarHeaderCss from './RadarHeader.module.css?raw'
import radarPageCss from './RadarPage.module.css?raw'
import radarSkeletonCss from './RadarSkeleton.module.css?raw'
import radarSkeletonSource from './RadarSkeleton.tsx?raw'
import radarStatesCss from './RadarStates.module.css?raw'
import recentChangesCss from './RecentChanges.module.css?raw'
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

function blockAfter(source: string, marker: RegExp, label: string): string {
  const match = marker.exec(source)
  expect(match, `${label} 應存在`).not.toBeNull()

  const openBrace = source.indexOf('{', match!.index)
  expect(openBrace, `${label} 應有區塊`).toBeGreaterThanOrEqual(0)

  let depth = 0
  for (let index = openBrace; index < source.length; index += 1) {
    if (source[index] === '{') depth += 1
    if (source[index] === '}') depth -= 1
    if (depth === 0) return source.slice(openBrace + 1, index)
  }

  throw new Error(`${label} 缺少結尾大括號`)
}

function mediaBlock(source: string, maxWidth: number): string {
  return blockAfter(
    source,
    new RegExp(`@media\\s*\\(max-width:\\s*${maxWidth}px\\)`),
    `@media (max-width: ${maxWidth}px)`,
  )
}

function ruleBlock(source: string, selector: string): string {
  return blockAfter(
    source,
    new RegExp(`${escapeRegExp(selector)}\\s*\\{`),
    selector,
  )
}

function expectDeclaration(
  source: string,
  selector: string,
  property: string,
  value: string,
): void {
  const body = ruleBlock(source, selector).replace(/\s+/g, ' ')
  expect(body).toContain(`${property}: ${value};`)
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
  it('KPI 維持兩欄且第三張跨滿整列', () => {
    const mobile = mediaBlock(keyFiguresCss, 860)

    expectDeclaration(mobile, '.grid', 'grid-template-columns', 'repeat(2, minmax(0, 1fr))')
    expectDeclaration(mobile, '.fig:nth-child(3)', 'grid-column', '1 / -1')
  })

  it('KPI 大字在 390px 的雙欄內縮放且不溢出', () => {
    const mobile = mediaBlock(keyFiguresCss, 560)

    expectDeclaration(mobile, '.fig', 'min-width', '0')
    expectDeclaration(mobile, '.fig', 'padding', '20px 14px 22px')
    expectDeclaration(mobile, '.val', 'font-size', 'clamp(26px, 8vw, 32px)')
    expectDeclaration(mobile, '.val', 'white-space', 'nowrap')
  })

  it('論點羅盤在 390px 維持 2x2，僅 340px 以下退為單欄', () => {
    const tablet = mediaBlock(thesisCompassCss, 1080)
    const narrow = mediaBlock(thesisCompassCss, 340)

    expectDeclaration(tablet, '.grid', 'grid-template-columns', 'repeat(2, minmax(0, 1fr))')
    expectDeclaration(narrow, '.grid', 'grid-template-columns', '1fr')
    expect(thesisCompassCss).not.toMatch(/@media\s*\(max-width:\s*(?:3[5-9]\d|[4-9]\d\d)px\)[\s\S]*?\.grid\s*\{\s*grid-template-columns:\s*1fr/)
  })

  it('骨架以三張 KPI 對齊兩欄加跨欄版型', () => {
    const mobile = mediaBlock(radarSkeletonCss, 860)

    expect(radarSkeletonSource).toMatch(
      /className=\{styles\.kpi\}[\s\S]*?Array\.from\(\{\s*length:\s*3\s*\}/,
    )
    expectDeclaration(mobile, '.kpi', 'grid-template-columns', 'repeat(2, minmax(0, 1fr))')
    expectDeclaration(mobile, '.kpi > .card:nth-child(3)', 'grid-column', '1 / -1')
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
})

describe('Radar 觸控目標契約', () => {
  it.each([
    ['窗期按鈕', windowSegmentedCss, '.btn'],
    ['市場篩選', instrumentPickerCss, '.chip'],
    ['錯誤重試', instrumentPickerCss, '.retry'],
    ['麵包屑返回', radarHeaderCss, '.crumbBtn'],
    ['查看全部', radarPageCss, '.sectionMeta'],
    ['事件報告連結', recentChangesCss, '.link'],
    ['歷史收合', brokerTimelineCss, '.collapse'],
    ['歷史報告連結', brokerTimelineCss, '.link'],
    ['狀態主要／次要操作', radarStatesCss, '.btn, .btnSecondary'],
  ])('%s 至少 44px 高', (_label, css, selector) => {
    expectDeclaration(css, selector, 'min-height', '44px')
  })
})

describe('Radar muted text 對比契約', () => {
  it('AA muted token 在白底與紙色底都至少達 4.5:1', () => {
    const muted = tokenValue('--tf-text-muted-aa')

    expect(contrastRatio(muted, tokenValue('--tf-surface'))).toBeGreaterThanOrEqual(4.5)
    expect(contrastRatio(muted, tokenValue('--tf-canvas'))).toBeGreaterThanOrEqual(4.5)
  })

  it('Radar 不再使用未達 AA 的舊 muted token', () => {
    expect(Object.keys(radarCssModules).length).toBeGreaterThan(0)
    for (const [path, css] of Object.entries(radarCssModules)) {
      expect(css, path).not.toContain('var(--tf-text-3)')
    }
  })
})
