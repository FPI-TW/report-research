import { act, renderHook } from '@testing-library/react'
import { useMonitorRate } from './useMonitorRate'
import { NULL_RATE, type RateSample } from './rate'

test('sample 為 null 時維持 NULL_RATE（effect 不觸發）', () => {
  const { result } = renderHook(() => useMonitorRate(null))
  expect(result.current).toEqual(NULL_RATE)
})

test('首次 sample 建基線回 NULL_RATE；dt≥8s 後算出開頁平均速率', () => {
  const nowSpy = vi.spyOn(performance, 'now').mockReturnValue(0)
  try {
    const s1: RateSample = { reports: 100, chunks: 1000, sumDone: 10, tagDone: 50 }
    const { result, rerender } = renderHook(
      ({ s }: { s: RateSample | null }) => useMonitorRate(s),
      { initialProps: { s: s1 } },
    )
    // 首次：建 base、仍為 NULL_RATE
    expect(result.current).toEqual(NULL_RATE)

    // 10 秒後換新 sample 參照
    nowSpy.mockReturnValue(10_000)
    const s2: RateSample = { reports: 160, chunks: 1600, sumDone: 20, tagDone: 110 }
    act(() => rerender({ s: s2 }))

    // dt=10s：rpm=(160-100)/10*60=360；cps=(1600-1000)/10=60；spm=(20-10)/10*60=60；tpm=(110-50)/10*60=360
    expect(result.current.rpm).toBeCloseTo(360)
    expect(result.current.cps).toBeCloseTo(60)
    expect(result.current.spm).toBeCloseTo(60)
    expect(result.current.tpm).toBeCloseTo(360)
  } finally {
    nowSpy.mockRestore()
  }
})

test('dt<8s 時沿用上次速率（穩定不抖動）', () => {
  const nowSpy = vi.spyOn(performance, 'now').mockReturnValue(0)
  try {
    const { result, rerender } = renderHook(
      ({ s }: { s: RateSample | null }) => useMonitorRate(s),
      { initialProps: { s: { reports: 0, chunks: 0, sumDone: 0, tagDone: 0 } as RateSample } },
    )
    nowSpy.mockReturnValue(3_000) // 僅 3 秒
    act(() => rerender({ s: { reports: 30, chunks: 30, sumDone: 3, tagDone: 3 } }))
    // dt<8 → 仍回上次值（NULL_RATE）
    expect(result.current).toEqual(NULL_RATE)
  } finally {
    nowSpy.mockRestore()
  }
})

test('sumDone/tagDone 為 null 時對應速率為 null', () => {
  const nowSpy = vi.spyOn(performance, 'now').mockReturnValue(0)
  try {
    const { result, rerender } = renderHook(
      ({ s }: { s: RateSample | null }) => useMonitorRate(s),
      { initialProps: { s: { reports: 0, chunks: 0, sumDone: null, tagDone: null } as RateSample } },
    )
    nowSpy.mockReturnValue(10_000)
    act(() => rerender({ s: { reports: 60, chunks: 100, sumDone: null, tagDone: null } }))
    expect(result.current.rpm).toBeCloseTo(360)
    expect(result.current.cps).toBeCloseTo(10)
    expect(result.current.spm).toBeNull()
    expect(result.current.tpm).toBeNull()
  } finally {
    nowSpy.mockRestore()
  }
})
