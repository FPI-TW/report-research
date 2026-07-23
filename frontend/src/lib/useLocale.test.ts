import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useLocale, setLocale } from './useLocale'

describe('useLocale store', () => {
  beforeEach(() => { localStorage.clear(); setLocale('zh-Hant') })
  afterEach(() => { setLocale('zh-Hant'); localStorage.clear() })

  it('預設 zh-Hant', () => {
    const { result } = renderHook(() => useLocale())
    expect(result.current).toBe('zh-Hant')
  })

  it('setLocale 更新訂閱者並寫入 localStorage', () => {
    const { result } = renderHook(() => useLocale())
    act(() => setLocale('en'))
    expect(result.current).toBe('en')
    expect(localStorage.getItem('tf.locale')).toBe('en')
  })

  it('切回 zh-Hant', () => {
    const { result } = renderHook(() => useLocale())
    act(() => setLocale('en'))
    act(() => setLocale('zh-Hant'))
    expect(result.current).toBe('zh-Hant')
    expect(localStorage.getItem('tf.locale')).toBe('zh-Hant')
  })

  it('多個訂閱者共享單一來源', () => {
    const a = renderHook(() => useLocale())
    const b = renderHook(() => useLocale())
    act(() => setLocale('en'))
    expect(a.result.current).toBe('en')
    expect(b.result.current).toBe('en')
  })

  it('模組初始化時讀取已持久化的 en', async () => {
    localStorage.setItem('tf.locale', 'en')
    vi.resetModules()
    const mod = await import('./useLocale')
    const { result } = renderHook(() => mod.useLocale())
    expect(result.current).toBe('en')
    mod.setLocale('zh-Hant')  // 還原此獨立模組實例
  })
})
