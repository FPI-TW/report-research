import { afterEach, expect, test, vi } from 'vitest'
import { z } from 'zod'
import { ApiError, errorDetail, requestJSON } from './api'

afterEach(() => vi.unstubAllGlobals())

test('errorDetail：字串原樣、422 陣列取 msg、403 補一句、其餘退回狀態碼', () => {
  expect(errorDetail({ detail: '至少要保留一位啟用中的管理員' }, 409)).toBe('至少要保留一位啟用中的管理員')
  expect(errorDetail({ detail: [{ msg: 'field required' }, { msg: 'too short' }] }, 422)).toBe('field required；too short')
  expect(errorDetail({}, 403)).toBe('需要管理員權限')
  expect(errorDetail(null, 502)).toBe('HTTP 502')
})

test('requestJSON：非 2xx 把 detail 放進 ApiError，保留狀態碼', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: '帳號「alice」已存在' }), { status: 409 })))
  const err = await requestJSON('/api/admin/users', z.object({})).catch(e => e)
  expect(err).toBeInstanceOf(ApiError)
  expect((err as ApiError).status).toBe(409)
  expect((err as ApiError).message).toBe('帳號「alice」已存在')
})

test('requestJSON：回應不是 JSON 時只說狀態碼', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response('<html>bad gateway</html>', { status: 502 })))
  const err = await requestJSON('/api/admin/users', z.object({})).catch(e => e)
  expect((err as ApiError).message).toBe('HTTP 502')
})
