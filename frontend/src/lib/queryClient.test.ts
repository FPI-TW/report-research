import { expect, test } from 'vitest'
import { createQueryClient, DEFAULT_STALE_TIME_MS } from './queryClient'

test('全站 query 預設 staleTime 不是出廠的 0', () => {
  const qc = createQueryClient()
  expect(qc.getDefaultOptions().queries?.staleTime).toBe(DEFAULT_STALE_TIME_MS)
  expect(DEFAULT_STALE_TIME_MS).toBeGreaterThan(0)
})

test('不動 retry 與 refetchOnWindowFocus：各 query 既有行為不因這份預設改變', () => {
  const q = createQueryClient().getDefaultOptions().queries
  expect(q?.retry).toBeUndefined()
  expect(q?.refetchOnWindowFocus).toBeUndefined()
})
