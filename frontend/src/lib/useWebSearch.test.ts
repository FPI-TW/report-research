import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useWebSearch, setWebSearch } from './useWebSearch'

describe('useWebSearch store', () => {
  beforeEach(() => { localStorage.clear(); setWebSearch(false) })
  afterEach(() => { setWebSearch(false); localStorage.clear() })

  it('預設關閉', () => {
    const { result } = renderHook(() => useWebSearch())
    expect(result.current).toBe(false)
  })

  it('setWebSearch 更新訂閱者並寫入 localStorage', () => {
    const { result } = renderHook(() => useWebSearch())
    act(() => setWebSearch(true))
    expect(result.current).toBe(true)
    expect(localStorage.getItem('tf.webSearch')).toBe('1')
  })

  it('關掉時寫入 0 而非移除鍵——「明確關閉」與「沒設定過」都是 false，但可查', () => {
    renderHook(() => useWebSearch())
    act(() => setWebSearch(true))
    act(() => setWebSearch(false))
    expect(localStorage.getItem('tf.webSearch')).toBe('0')
  })

  // 中央（空狀態）與底部兩個 Composer 會同時掛載；各持一份副本時的症狀是
  // 「按了亮起來、送出去的請求卻沒開」，沒有任何錯誤訊息。
  it('多個訂閱者共享單一來源', () => {
    const a = renderHook(() => useWebSearch())
    const b = renderHook(() => useWebSearch())
    act(() => setWebSearch(true))
    expect(a.result.current).toBe(true)
    expect(b.result.current).toBe(true)
  })

  it('模組初始化時讀取已持久化的開啟狀態', async () => {
    localStorage.setItem('tf.webSearch', '1')
    vi.resetModules()
    const mod = await import('./useWebSearch')
    const { result } = renderHook(() => mod.useWebSearch())
    expect(result.current).toBe(true)
    mod.setWebSearch(false)  // 還原此獨立模組實例
  })

  it('localStorage 讀取失敗時 fail-open 到關閉', async () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    vi.resetModules()
    const mod = await import('./useWebSearch')
    const { result } = renderHook(() => mod.useWebSearch())
    expect(result.current).toBe(false)
    spy.mockRestore()
  })
})
