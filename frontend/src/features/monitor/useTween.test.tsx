import { act, renderHook } from '@testing-library/react'
import { useTween } from './useTween'

beforeEach(() => {
  vi.stubGlobal(
    'matchMedia',
    vi.fn(() => ({ matches: true })) as unknown as typeof matchMedia,
  )
})

afterEach(() => vi.unstubAllGlobals())

test('reduced-motion 下直接回終值', () => {
  const { result } = renderHook(() => useTween(1234))
  expect(result.current).toBe(1234)
})

test('value 為 null 回 null', () => {
  const { result } = renderHook(() => useTween(null))
  expect(result.current).toBeNull()
})

test('normal-motion 下緩動由起點收斂到終值', () => {
  // 覆寫 beforeEach 的 reduced-motion 為 normal-motion，實際走 RAF easing 路徑
  vi.stubGlobal('matchMedia', vi.fn(() => ({ matches: false })) as unknown as typeof matchMedia)
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] })
  try {
    const { result, rerender } = renderHook(({ v }: { v: number | null }) => useTween(v), {
      initialProps: { v: 0 },
    })
    act(() => {
      vi.advanceTimersByTime(20) // flush 初始 RAF（start===value 直接定為 0）
    })
    expect(result.current).toBe(0)

    rerender({ v: 100 })
    act(() => {
      vi.advanceTimersByTime(300) // 動畫進行中：應在 0 與 100 之間
    })
    const mid = result.current as number
    expect(mid).toBeGreaterThan(0)
    expect(mid).toBeLessThan(100)

    act(() => {
      vi.advanceTimersByTime(1000) // 超過 600ms easing → 收斂到終值
    })
    expect(result.current).toBe(100)
  } finally {
    vi.useRealTimers()
  }
})
