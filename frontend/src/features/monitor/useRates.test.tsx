import { afterEach, expect, test, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { useRates } from './useRates'
import type { Progress } from './progressSchema'

function mk(reports: number, tagDone: number): Progress {
  return {
    ts: '', db: { reports, chunks: reports * 10, markets: [] },
    summary: { done: 0, total: 10, remaining: 10, pct: 0 },
    tagging: { done: tagDone, total: 100, fail: 100 - tagDone, pct: tagDone },
    ingest: null,
    pipelines: { web: true, ingest: false, tag: true, summaries: false },
    orchestrator: null,
  }
}

afterEach(() => vi.restoreAllMocks())

test('首筆 → 全 null（記 baseline）', () => {
  const { result } = renderHook(({ p }) => useRates(p), { initialProps: { p: mk(10, 5) as Progress | undefined } })
  expect(result.current).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})

test('第二筆且經過 >8s → 算開頁至今速率', () => {
  let t = 1_000_000
  vi.spyOn(Date, 'now').mockImplementation(() => t)
  const { result, rerender } = renderHook(({ p }) => useRates(p), { initialProps: { p: mk(0, 0) as Progress | undefined } })
  expect(result.current.tpm).toBeNull()
  t += 60_000 // +60s
  rerender({ p: mk(60, 60) })
  expect(result.current.rpm).toBeCloseTo(60)
  expect(result.current.tpm).toBeCloseTo(60)
})

test('progress undefined → 全 null', () => {
  const { result } = renderHook(() => useRates(undefined))
  expect(result.current).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})
