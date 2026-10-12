import { afterEach, describe, expect, it, vi } from 'vitest'
import { ADMIN_THEME_STORAGE_KEY, applyAdminThemeEarly, readStoredTheme, resolveTheme } from './adminTheme'

const stubSystem = (dark: boolean) => vi.stubGlobal('matchMedia', (q: string) => ({
  matches: dark && q.includes('dark'), media: q, addEventListener: () => {}, removeEventListener: () => {},
}))

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  window.localStorage.clear()
  delete document.documentElement.dataset.theme
})

describe('管理後台主題', () => {
  it('沒有存過選擇時跟系統', () => {
    stubSystem(true)
    expect(resolveTheme()).toBe('dark')
    stubSystem(false)
    expect(resolveTheme()).toBe('light')
  })

  it('存過的選擇優先於系統', () => {
    stubSystem(true)
    window.localStorage.setItem(ADMIN_THEME_STORAGE_KEY, 'light')
    expect(resolveTheme()).toBe('light')
  })

  it('localStorage 裡不是 dark／light 的值當成沒存過', () => {
    stubSystem(false)
    window.localStorage.setItem(ADMIN_THEME_STORAGE_KEY, 'sepia')
    expect(readStoredTheme()).toBeNull()
    expect(resolveTheme()).toBe('light')
  })

  it('localStorage 不能用時退回跟系統，不丟例外', () => {
    stubSystem(true)
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    expect(resolveTheme()).toBe('dark')
  })

  it('只有 /app/admin 底下才先寫主題；研報平台不碰', () => {
    stubSystem(true)
    applyAdminThemeEarly('/app/search')
    expect(document.documentElement.dataset.theme).toBeUndefined()
    applyAdminThemeEarly('/app/administrator')
    expect(document.documentElement.dataset.theme).toBeUndefined()
    applyAdminThemeEarly('/app/admin/operations/logs')
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
