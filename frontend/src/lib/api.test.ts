import { z } from 'zod'
import { afterEach, expect, test, vi } from 'vitest'
import { ApiError, getJSON } from './api'

afterEach(() => vi.unstubAllGlobals())

const schema = z.object({ ok: z.boolean() })

test('200 回 parsed 物件', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ ok: true }), { status: 200 })))
  await expect(getJSON('/x', schema)).resolves.toEqual({ ok: true })
})

test('401 導向登入並丟 ApiError', async () => {
  const assign = vi.fn()
  vi.stubGlobal('location', { pathname: '/app/search', search: '', assign } as unknown as Location)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 401 })))
  await expect(getJSON('/x', schema)).rejects.toBeInstanceOf(ApiError)
  expect(assign).toHaveBeenCalledWith('/login?next=' + encodeURIComponent('/app/search'))
})

test('500 丟 ApiError 帶 status', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 500 })))
  await expect(getJSON('/x', schema)).rejects.toMatchObject({ status: 500 })
})
