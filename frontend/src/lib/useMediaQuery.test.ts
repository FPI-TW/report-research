import { renderHook } from '@testing-library/react'
import { afterEach, expect, test, vi } from 'vitest'
import { useMediaQuery } from './useMediaQuery'

afterEach(() => vi.unstubAllGlobals())

test('回傳 matchMedia 的 matches', () => {
  vi.stubGlobal('matchMedia', (q: string) => ({
    matches: true, media: q, addEventListener: () => {}, removeEventListener: () => {},
  }))
  const { result } = renderHook(() => useMediaQuery('(max-width: 767px)'))
  expect(result.current).toBe(true)
})
