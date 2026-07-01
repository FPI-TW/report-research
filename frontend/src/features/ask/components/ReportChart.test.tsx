import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { ReportChart } from './ReportChart'
import type { ChartBlock } from '../schemas'

const wrap = (block: ChartBlock) =>
  render(
    <MantineProvider theme={theme}>
      <ReportChart block={block} />
    </MantineProvider>,
  )

// jsdom 無真實 layout engine，ResizeObserver 又只是 no-op polyfill，Recharts
// 的 ResponsiveContainer 永遠量到 0x0 而印出「width/height should be greater
// than 0」warning——這是已知的 jsdom 限制（非本元件邏輯問題），過濾掉以保持
// 測試輸出乾淨，其餘 console.warn 訊息仍原樣輸出。
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

describe('ReportChart', () => {
  test('type="bar" → 渲染 report-chart，data-charttype="bar"', () => {
    wrap({
      type: 'bar',
      title: '營收',
      x: ['Q1', 'Q2'],
      series: [{ name: '營收', values: [100, 120] }],
      unit: null,
      source: null,
    })
    const el = screen.getByTestId('report-chart')
    expect(el).toHaveAttribute('data-charttype', 'bar')
  })

  test('type="line" → 渲染 report-chart，data-charttype="line"', () => {
    wrap({
      type: 'line',
      title: '股價走勢',
      x: ['1月', '2月'],
      series: [{ name: '股價', values: [50, 55] }],
      unit: null,
      source: null,
    })
    const el = screen.getByTestId('report-chart')
    expect(el).toHaveAttribute('data-charttype', 'line')
  })

  test('type="pie" 全正值 → 渲染 report-chart，data-charttype="pie"', () => {
    wrap({
      type: 'pie',
      title: '市占率',
      x: ['A', 'B', 'C'],
      series: [{ name: '占比', values: [40, 35, 25] }],
      unit: '%',
      source: null,
    })
    const el = screen.getByTestId('report-chart')
    expect(el).toHaveAttribute('data-charttype', 'pie')
  })

  test('type="pie" 含負值 → 拒繪（回傳 null，report-chart 不存在）', () => {
    wrap({
      type: 'pie',
      title: '市占率',
      x: ['A', 'B'],
      series: [{ name: '占比', values: [40, -10] }],
      unit: '%',
      source: null,
    })
    expect(screen.queryByTestId('report-chart')).toBeNull()
  })

  test('series 為空 → 回傳 null', () => {
    wrap({
      type: 'bar',
      title: '營收',
      x: ['Q1', 'Q2'],
      series: [],
      unit: null,
      source: null,
    })
    expect(screen.queryByTestId('report-chart')).toBeNull()
  })

  test('bar/line 的 x 為空 → 回傳 null', () => {
    wrap({
      type: 'bar',
      title: '營收',
      x: [],
      series: [{ name: '營收', values: [] }],
      unit: null,
      source: null,
    })
    expect(screen.queryByTestId('report-chart')).toBeNull()
  })

  test('有 title → 顯示標題文字', () => {
    wrap({
      type: 'bar',
      title: '季營收比較',
      x: ['Q1', 'Q2'],
      series: [{ name: '營收', values: [100, 120] }],
      unit: null,
      source: null,
    })
    expect(screen.getByText('季營收比較')).toBeInTheDocument()
  })

  test('有 unit/source → 顯示標註文字', () => {
    wrap({
      type: 'bar',
      title: '營收',
      x: ['Q1', 'Q2'],
      series: [{ name: '營收', values: [100, 120] }],
      unit: '億元',
      source: '公司財報',
    })
    expect(screen.getByText(/億元/)).toBeInTheDocument()
    expect(screen.getByText(/公司財報/)).toBeInTheDocument()
  })
})
