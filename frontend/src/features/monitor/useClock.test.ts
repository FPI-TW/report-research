import { afterEach, expect, test, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useClock } from './useClock'

afterEach(() => vi.useRealTimers())

test('回傳 HH:MM:SS 並每秒更新', () => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date(2026, 6, 5, 9, 8, 7))
  const { result } = renderHook(() => useClock())
  expect(result.current).toBe('09:08:07')
  act(() => {
    vi.advanceTimersByTime(1000)
  })
  expect(result.current).toBe('09:08:08')
})
