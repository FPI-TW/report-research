import { describe, expect, test } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MantineProvider } from '@mantine/core'
import { theme } from '../../../theme'
import { KpiCards } from './KpiCards'
import type { KpiBlock } from '../schemas'

const wrap = (block: KpiBlock) =>
  render(
    <MantineProvider theme={theme}>
      <KpiCards block={block} />
    </MantineProvider>,
  )

describe('KpiCards', () => {
  test('dir="up" → change 標記 data-dir="up"（綠色系）', () => {
    wrap({ items: [{ label: '營收', value: '100億', change: '+5%', dir: 'up', source: null }] })
    const change = screen.getByTestId('report-kpi-change')
    expect(change).toHaveAttribute('data-dir', 'up')
    expect(change).toHaveTextContent('+5%')
  })

  test('dir="down" → change 標記 data-dir="down"（紅色系）', () => {
    wrap({ items: [{ label: '毛利率', value: '30%', change: '-2%', dir: 'down', source: null }] })
    const change = screen.getByTestId('report-kpi-change')
    expect(change).toHaveAttribute('data-dir', 'down')
    expect(change).toHaveTextContent('-2%')
  })

  test('無 dir → 中性（data-dir=""）', () => {
    wrap({ items: [{ label: 'EPS', value: '5.2', change: '持平', dir: null, source: null }] })
    const change = screen.getByTestId('report-kpi-change')
    expect(change).toHaveAttribute('data-dir', '')
  })

  test('無 change → 不渲染 change 元素', () => {
    wrap({ items: [{ label: 'EPS', value: '5.2', change: null, dir: null, source: null }] })
    expect(screen.queryByTestId('report-kpi-change')).toBeNull()
  })

  test('有 source → 顯示來源徽章', () => {
    wrap({ items: [{ label: '營收', value: '100億', change: null, dir: null, source: '公司財報' }] })
    expect(screen.getByTestId('report-kpi-source')).toHaveTextContent('公司財報')
  })

  test('無 source → 不顯示來源徽章', () => {
    wrap({ items: [{ label: '營收', value: '100億', change: null, dir: null, source: null }] })
    expect(screen.queryByTestId('report-kpi-source')).toBeNull()
  })

  test('多筆 items → 每筆一張卡片，皆有 report-kpi-item testid', () => {
    wrap({
      items: [
        { label: '營收', value: '100億', change: '+5%', dir: 'up', source: null },
        { label: '毛利率', value: '30%', change: '-2%', dir: 'down', source: null },
      ],
    })
    expect(screen.getAllByTestId('report-kpi-item')).toHaveLength(2)
  })

  test('空 items → 不渲染 report-kpi 容器（回傳 null）', () => {
    wrap({ items: [] })
    expect(screen.queryByTestId('report-kpi')).toBeNull()
    expect(screen.queryByTestId('report-kpi-item')).toBeNull()
  })
})
