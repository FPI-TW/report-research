import React from 'react'
import { test, expect, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { useSearchParamsState } from './useSearchParamsState'
import { DEFAULT_FILTERS } from '../lib/filters'
import type { Allowlists } from '../lib/filters'

const allow: Allowlists = {
  markets: ['TW', 'US'],
  instruments: ['equity', 'futures'],
  types: ['daily', 'weekly'],
}

const wrap = (initial: string) =>
  ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter initialEntries={[initial]}>{children}</MemoryRouter>
  )

beforeEach(() => localStorage.clear())

// ── 既有 filters 測試 ──────────────────────────────────────────────────────────

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

// ── viewState 測試 ──────────────────────────────────────────────────────────────

test('預設 viewState 為 group/month', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/search'),
  })
  expect(result.current.viewState).toEqual({ view: 'group', group: 'month' })
})

test('URL view=table 還原', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/search?view=table'),
  })
  expect(result.current.viewState.view).toBe('table')
})

test('setViewState 保留既有 filters（q）', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/search?q=ai'),
  })
  act(() => result.current.setViewState({ view: 'table', group: 'month' }))
  expect(result.current.filters.q).toBe('ai')
  expect(result.current.viewState.view).toBe('table')
})

test('setFilters 保留既有 view', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/search?view=table'),
  })
  act(() => result.current.setFilters({ ...result.current.filters, q: 'x' }))
  expect(result.current.viewState.view).toBe('table')
})

test('setViewState 寫 localStorage rm_view', () => {
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/search'),
  })
  act(() => result.current.setViewState({ view: 'table', group: 'month' }))
  expect(localStorage.getItem('rm_view')).toBe('table')
})

test('URL 無 view 時用 localStorage', () => {
  localStorage.setItem('rm_view', 'table')
  const { result } = renderHook(() => useSearchParamsState(allow), {
    wrapper: wrap('/search'),
  })
  expect(result.current.viewState.view).toBe('table')
})
