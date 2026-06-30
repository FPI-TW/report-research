import React from 'react'
import { renderHook, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { useSearchParamsState } from './useSearchParamsState'
import { DEFAULT_FILTERS } from '../lib/filters'

const allow = { markets: ['TW', 'US'], instruments: ['equity', 'futures'], types: ['daily', 'weekly'] }

const wrap = (initial: string) =>
  ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
  )

test('由 URL 還原並可寫回', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/app/search?q=AI&market=TW'),
  })
  expect(result.current.filters.q).toBe('AI')
  expect(result.current.filters.market).toBe('TW')
  act(() => result.current.setFilters({ ...result.current.filters, market: '全部' }))
  expect(result.current.filters.market).toBe('全部')
})

test('無 URL 參數時回傳預設值', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/app/search'),
  })
  expect(result.current.filters.q).toBe('')
  expect(result.current.filters.market).toBe('全部')
  expect(result.current.filters.stock).toBe(false)
  expect(result.current.filters.futures).toBe(false)
})

test('不在 allowlist 的 market 退回全部', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/app/search?market=INVALID'),
  })
  expect(result.current.filters.market).toBe('全部')
})

test('setFilters 寫入多個欄位', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/app/search'),
  })
  act(() =>
    result.current.setFilters({ ...DEFAULT_FILTERS, q: 'semiconductor', market: 'US' }),
  )
  expect(result.current.filters.q).toBe('semiconductor')
  expect(result.current.filters.market).toBe('US')
})
