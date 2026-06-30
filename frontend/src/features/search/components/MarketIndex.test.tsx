import { test, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { MarketIndex } from './MarketIndex'

test('用 markets 全語料 count 顯示、點擊回傳 market', () => {
  const onPick = vi.fn()
  render(
    <MarketIndex
      markets={[
        { market: 'TW', count: 1003 },
        { market: 'US', count: 50 },
      ]}
      onPickMarket={onPick}
    />,
  )
  expect(screen.getByText('1,003')).toBeInTheDocument()
  fireEvent.click(screen.getByText('台股'))
  expect(onPick).toHaveBeenCalledWith('TW')
})

test('空 market 字串被過濾掉', () => {
  render(
    <MarketIndex
      markets={[
        { market: '', count: 5 },
        { market: 'TW', count: 10 },
      ]}
      onPickMarket={() => {}}
    />,
  )
  const items = screen.getAllByTestId('market-index-item')
  // Only TW should appear, empty string filtered out
  expect(items).toHaveLength(1)
  expect(items[0].getAttribute('data-market')).toBe('TW')
})
