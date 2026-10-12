import { describe, expect, it } from 'vitest'
import { buildUpdate, exportFilename, exportOverrideSummary, overrideSummary } from './flagText'

describe('overrideSummary', () => {
  it('沒有覆寫時說明預設', () => {
    expect(overrideSummary(null, true)).toBe('沒有覆寫（預設開啟）')
    expect(overrideSummary(undefined, false)).toBe('沒有覆寫（預設關閉）')
  })
  it('全站與限定', () => {
    expect(overrideSummary({ enabled: false }, true)).toBe('全站關閉')
    expect(overrideSummary({ enabled: true, allow_roles: null, allow_users: null }, false)).toBe('全站開啟')
    expect(overrideSummary({ enabled: true, allow_roles: ['admin', 'user'] }, false)).toBe('限定開啟：管理員、一般使用者')
    expect(overrideSummary({ enabled: true, allow_users: [{ id: 'abcdef0123', username: null }] }, false))
      .toBe('限定開啟：（已刪除的帳號 abcdef01）')
  })
})

describe('exportOverrideSummary', () => {
  it('匯入預覽的前後值', () => {
    expect(exportOverrideSummary(null)).toBe('沒有覆寫')
    expect(exportOverrideSummary({ enabled: true, allow_users: ['alice'] })).toBe('限定開啟：使用者 alice')
  })
})

describe('buildUpdate', () => {
  it('全站開／關不帶作用域，註記去空白、空字串為 null', () => {
    expect(buildUpdate('on', ['admin'], ['u1'], '  ')).toEqual({ enabled: true, allow_roles: null, allow_users: null, note: null })
    expect(buildUpdate('off', [], [], ' 降級 ')).toEqual({ enabled: false, allow_roles: null, allow_users: null, note: '降級' })
  })
  it('限定：沒勾任何維度回 null；只勾一邊時另一邊是 null（不是空清單）', () => {
    expect(buildUpdate('scoped', [], [], '')).toBeNull()
    expect(buildUpdate('scoped', ['admin'], [], '')).toEqual({ enabled: true, allow_roles: ['admin'], allow_users: null, note: null })
  })
})

describe('exportFilename', () => {
  it('台北日期與環境', () => {
    expect(exportFilename('staging', new Date('2026-10-07T17:30:00Z'))).toBe('report-mark-flags-staging-20261008.json')
    expect(exportFilename(null, new Date('2026-10-07T01:00:00Z'))).toBe('report-mark-flags-unknown-20261007.json')
  })
})
