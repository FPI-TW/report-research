import { expect, test } from 'vitest'
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

test('首筆 → 全 null（記 baseline）', () => {
  const { result } = renderHook(({ p, now }) => useRates(p, now), {
    initialProps: { p: mk(10, 5) as Progress | undefined, now: 1000 },
  })
  expect(result.current).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})

test('第二筆且經過 >8s → 算開頁至今速率', () => {
  const { result, rerender } = renderHook(({ p, now }) => useRates(p, now), {
    initialProps: { p: mk(0, 0) as Progress | undefined, now: 1_000_000 },
  })
  expect(result.current.tpm).toBeNull()
  rerender({ p: mk(60, 60), now: 1_060_000 }) // +60s
  expect(result.current.rpm).toBeCloseTo(60)
  expect(result.current.tpm).toBeCloseTo(60)
})

test('now 每秒微增但 <8s 仍暖機（首筆後短間隔不抖動）', () => {
  const { result, rerender } = renderHook(({ p, now }) => useRates(p, now), {
    initialProps: { p: mk(0, 0) as Progress | undefined, now: 0 },
  })
  rerender({ p: mk(0, 0), now: 3000 }) // +3s, same data
  expect(result.current).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})

test('progress undefined → 全 null', () => {
  const { result } = renderHook(() => useRates(undefined, 1000))
  expect(result.current).toEqual({ rpm: null, cps: null, spm: null, tpm: null })
})

test('tagging null → tpm null（rpm 仍計算）', () => {
  const base = { ...mk(0, 0), tagging: null } as Progress
  const later = { ...mk(60, 0), tagging: null } as Progress
  const { result, rerender } = renderHook(({ p, now }) => useRates(p, now), {
    initialProps: { p: base as Progress | undefined, now: 0 },
  })
  rerender({ p: later, now: 60_000 }) // +60s
  expect(result.current.tpm).toBeNull()
  expect(result.current.rpm).toBeCloseTo(60)
})
