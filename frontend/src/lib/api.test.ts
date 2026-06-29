import { afterEach, expect, test, vi } from 'vitest'
import { z } from 'zod'
import { ApiError, getJSON } from './api'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

test('getJSON 解析並回傳型別化資料', async () => {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response(JSON.stringify({ a: 1 }), { status: 200 })),
  )
  const out = await getJSON('/api/x', z.object({ a: z.number() }))
  expect(out.a).toBe(1)
})

test('getJSON 遇 401 觸發導向登入並拋 ApiError', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { pathname: '/app/monitor', search: '', assign } as unknown as Location)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })))
  await expect(getJSON('/api/x', z.object({ a: z.number() }))).rejects.toBeInstanceOf(ApiError)
  expect(assign).toHaveBeenCalledWith('/login?next=%2Fapp%2Fmonitor')
})
