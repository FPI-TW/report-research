import { renderHook } from '@testing-library/react'
import { useTween } from './useTween'

beforeEach(() => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: true })) as unknown as typeof matchMedia,
  )
})

test('reduced-motion 下直接回終值', () => {
  const { result } = renderHook(() => useTween(1234))
  expect(result.current).toBe(1234)
})

test('value 為 null 回 null', () => {
  const { result } = renderHook(() => useTween(null))
  expect(result.current).toBeNull()
})
