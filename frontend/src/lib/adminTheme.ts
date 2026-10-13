import { useCallback, useEffect, useLayoutEffect, useState } from 'react'

/**
 * 管理後台的深色／亮色主題。
 *
 * 只有管理後台有兩套主題：`data-theme` 在 `AdminShell` 掛著的期間寫到 `<html>`（彈窗 portal 到
 * body，寫在外殼節點上會漏掉它們），離開管理後台就拿掉，研報平台維持原本的亮色。色值在
 * `styles/tokens.css` 的 `[data-theme]` 兩組。
 *
 * 預設跟系統的 `prefers-color-scheme`（系統切換時跟著變）；使用者按過切換之後存 localStorage，
 * 之後以它為準。localStorage 讀寫失敗（隱私模式、被停用）時退回跟系統，不擋畫面。
 */
export type AdminTheme = 'dark' | 'light'

export const ADMIN_THEME_STORAGE_KEY = 'tf.admin.theme'
const DARK_QUERY = '(prefers-color-scheme: dark)'

export function readStoredTheme(): AdminTheme | null {
  try {
    const v = window.localStorage.getItem(ADMIN_THEME_STORAGE_KEY)
    return v === 'dark' || v === 'light' ? v : null
  } catch {
    return null
  }
}

export function systemTheme(): AdminTheme {
  return typeof window.matchMedia === 'function' && window.matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

export function resolveTheme(): AdminTheme {
  return readStoredTheme() ?? systemTheme()
}

/**
 * 進入點在 React 掛上之前呼叫：直接開 /app/admin/* 時先把主題寫上去，外殼的 chunk 還在下載的
 * 那一段才不會先閃一次亮底。
 */
export function applyAdminThemeEarly(pathname: string = window.location.pathname): void {
  if (/^\/app\/admin(\/|$)/.test(pathname)) document.documentElement.dataset.theme = resolveTheme()
}

export function useAdminTheme(): { theme: AdminTheme; setTheme: (t: AdminTheme) => void } {
  const [theme, setThemeState] = useState<AdminTheme>(resolveTheme)

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme
  }, [theme])
  // 離開管理後台：拿掉屬性，研報平台回到沒有主題的狀態。
  useLayoutEffect(() => () => { delete document.documentElement.dataset.theme }, [])

  // 沒有存過選擇時跟著系統走；存過就不理會系統的變化。
  useEffect(() => {
    if (typeof window.matchMedia !== 'function') return
    const mq = window.matchMedia(DARK_QUERY)
    const onChange = () => { if (readStoredTheme() === null) setThemeState(systemTheme()) }
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  const setTheme = useCallback((t: AdminTheme) => {
    try { window.localStorage.setItem(ADMIN_THEME_STORAGE_KEY, t) } catch { /* 存不了就只在這次生效 */ }
    setThemeState(t)
  }, [])

  return { theme, setTheme }
}
