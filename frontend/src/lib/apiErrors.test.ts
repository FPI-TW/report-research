import { afterEach, describe, expect, it, test, vi } from 'vitest'
import { z } from 'zod'
import { ApiError, errorDetail, requestJSON } from './api'
import { setLocale } from '../i18n'

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

test('requestJSON：統一錯誤格式的 code 放進 ApiError.code，detail 照舊當訊息', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(
    JSON.stringify({ detail: '這項操作需要重新驗證密碼', code: 'elevation_required', request_id: 'abc' }), { status: 403 },
  )))
  const err = await requestJSON('/api/admin/users/x/privileges', z.object({})).catch(e => e)
  expect((err as ApiError).message).toBe('這項操作需要重新驗證密碼')
  expect((err as ApiError).code).toBe('elevation_required')
})

test('requestJSON：沒有 code 的舊格式錯誤，ApiError.code 是 undefined', async () => {
  vi.stubGlobal('fetch', vi.fn(async () => new Response(JSON.stringify({ detail: 'x' }), { status: 400 })))
  const err = await requestJSON('/api/admin/users', z.object({})).catch(e => e)
  expect((err as ApiError).code).toBeUndefined()
})

describe('錯誤訊息依介面語系', () => {
  afterEach(() => setLocale('zh-Hant'))

  it('中文介面照舊顯示後端的 detail', () => {
    expect(errorDetail({ detail: '至少要保留一位啟用中的管理員', code: 'conflict' }, 409)).toBe('至少要保留一位啟用中的管理員')
  })

  it('英文介面依 code 顯示英文；未登記的 code 顯示原 detail', () => {
    setLocale('en')
    expect(errorDetail({ detail: '這項操作需要重新驗證密碼', code: 'elevation_required' }, 403))
      .toBe('This action requires re-verifying the password.')
    expect(errorDetail({ detail: '某個新錯誤', code: 'brand_new_code' }, 400)).toBe('某個新錯誤')
  })

  it('422 的驗證錯誤（pydantic 的英文 msg）兩種語系都照原文', () => {
    setLocale('en')
    expect(errorDetail({ detail: [{ msg: 'Field required' }], code: 'validation_error' }, 422)).toBe('Field required')
  })

  it('detail 是空的：用字典裡的訊息', () => {
    expect(errorDetail({ detail: '', code: 'rate_limited' }, 429)).toBe('嘗試次數過多，請稍後再試')
  })
})
