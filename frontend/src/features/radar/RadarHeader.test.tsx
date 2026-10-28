import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RadarHeader } from './RadarHeader'

function renderHeader(over: Partial<Parameters<typeof RadarHeader>[0]> = {}) {
  const props = {
    market: 'TW' as const,
    marketDisplay: '台股',
    code: '2330',
    name: '台積電',
    asOf: '2024-10-27',
    coverage: {
      state: 'ok' as const,
      brokers_total: 9,
      brokers_extracted: 8,
      brokers_in_consensus: 7,
      reports_available: 31,
      note: '',
    },
    window: '90' as const,
    onWindowChange: vi.fn(),
    onBack: vi.fn(),
    onBrowseReports: vi.fn(),
    ...over,
  }
  render(<RadarHeader {...props} />)
  return props
}

describe('RadarHeader', () => {
  it('標的名是 h1，旁邊是代號與市場', () => {
    renderHeader()
    expect(screen.getByRole('heading', { level: 1, name: '台積電' })).toBeInTheDocument()
    expect(screen.getByText('2330')).toBeInTheDocument()
    expect(screen.getByText('台股')).toBeInTheDocument()
  })

  it('說明列：券商數、納入共識家數、篇數與資料截止日，不用「·」', () => {
    renderHeader()
    const meta = screen.getByText((_, el) => el?.tagName === 'P' && (el.textContent ?? '').includes('家券商'))
    expect(meta).toHaveTextContent('9 家券商，7 家納入共識，31 篇研報，資料截至 2024-10-27')
    expect(meta.textContent).not.toContain('·')
    expect(meta).toHaveAttribute('aria-live', 'polite')
  })

  it('沒有名稱時標題退回代號，代號不重複印', () => {
    renderHeader({ name: null })
    expect(screen.getByRole('heading', { level: 1, name: '2330' })).toBeInTheDocument()
    expect(screen.getAllByText('2330')).toHaveLength(1)
  })

  it('返回、期間、相關研報都可操作', () => {
    const props = renderHeader()
    fireEvent.click(screen.getByRole('button', { name: '券商共識' }))
    expect(props.onBack).toHaveBeenCalled()
    const group = screen.getByRole('radiogroup', { name: '期間' })
    fireEvent.click(within(group).getByRole('radio', { name: '180 天' }))
    expect(props.onWindowChange).toHaveBeenCalledWith('180')
    fireEvent.click(screen.getByRole('button', { name: '相關研報' }))
    expect(props.onBrowseReports).toHaveBeenCalledWith()
  })

  it('partial 時在說明列下方附上資料註記', () => {
    renderHeader({
      coverage: {
        state: 'partial', brokers_total: 3, brokers_extracted: 1,
        brokers_in_consensus: 1, reports_available: 4, note: '部分研報仍在整理',
      },
    })
    expect(screen.getByText('部分研報仍在整理')).toBeInTheDocument()
  })
})
