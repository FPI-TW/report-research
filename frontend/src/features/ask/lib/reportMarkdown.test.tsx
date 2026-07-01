import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { renderReport } from './reportMarkdown'

const wrap = (markdown: string) =>
  render(<MantineProvider theme={theme}>{renderReport(markdown)}</MantineProvider>)

// jsdom 無真實 layout engine，ResizeObserver 又只是 no-op polyfill，Recharts 的
// ResponsiveContainer 永遠量到 0x0 而印出「width/height should be greater than 0」
// warning——這是已知的 jsdom 限制（非本模組邏輯問題，見 ReportChart.test.tsx 同作法），
// 過濾掉以保持測試輸出乾淨，其餘 console.warn 訊息仍原樣輸出。
let warnSpy: ReturnType<typeof vi.spyOn>
beforeEach(() => {
  const original = console.warn.bind(console)
  warnSpy = vi.spyOn(console, 'warn').mockImplementation((...args: unknown[]) => {
    if (typeof args[0] === 'string' && args[0].includes('should be greater than 0')) return
    original(...args)
  })
})
afterEach(() => {
  warnSpy.mockRestore()
})

describe('renderReport', () => {
  test('混合 md+kpi+chart → 輸出含文字 + report-kpi + report-chart', () => {
    const markdown = [
      '# 標題',
      '這是內文段落。',
      '```kpi',
      JSON.stringify({
        items: [{ label: '營收', value: '100億', change: '+5%', dir: 'up', source: null }],
      }),
      '```',
      '```chart',
      JSON.stringify({
        type: 'bar',
        title: '季營收',
        x: ['Q1', 'Q2'],
        series: [{ name: '營收', values: [100, 120] }],
        unit: null,
        source: null,
      }),
      '```',
      '結尾段落。',
    ].join('\n')

    wrap(markdown)

    expect(screen.getByText('標題')).toBeInTheDocument()
    expect(screen.getByText('這是內文段落。')).toBeInTheDocument()
    expect(screen.getByText('結尾段落。')).toBeInTheDocument()
    expect(screen.getByTestId('report-kpi')).toBeInTheDocument()
    expect(screen.getByTestId('report-chart')).toBeInTheDocument()
  })

  test('純 markdown → 文字存在，且無 report-kpi / report-chart', () => {
    wrap('# 標題\n\n純文字段落。')

    expect(screen.getByText('標題')).toBeInTheDocument()
    expect(screen.getByText('純文字段落。')).toBeInTheDocument()
    expect(screen.queryByTestId('report-kpi')).toBeNull()
    expect(screen.queryByTestId('report-chart')).toBeNull()
  })

  test('kpi-only 字串 → report-kpi 存在，無 report-chart', () => {
    const markdown = [
      '```kpi',
      JSON.stringify({
        items: [{ label: 'EPS', value: '5.2', change: null, dir: null, source: null }],
      }),
      '```',
    ].join('\n')

    wrap(markdown)

    expect(screen.getByTestId('report-kpi')).toBeInTheDocument()
    expect(screen.queryByTestId('report-chart')).toBeNull()
  })
})
