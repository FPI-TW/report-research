import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useWebSearch, setWebSearch, useWebSearchPaused, noticeDisplayText, TIME_SENSITIVE_WEB_HINTS } from './useWebSearch'
import { setFeatures } from './useFeatures'

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

// 網搜暫停＝功能旗標 ask.web_search 沒開（/api/features）。正式環境 ASK_ENABLE_WEB=0 → 後端回 false → 暫停，
// 與先前寫死的常數相同；還沒抓到或抓失敗也是暫停（寧可少開一個外網功能）。
describe('useWebSearchPaused', () => {
  afterEach(() => setFeatures(null))
  it('還沒抓到 /api/features：暫停', () => {
    setFeatures(null)
    expect(renderHook(() => useWebSearchPaused()).result.current).toBe(true)
  })
  it('跟著旗標走，store 更新時重新渲染', () => {
    setFeatures({ 'ask.web_search': false })
    const { result } = renderHook(() => useWebSearchPaused())
    expect(result.current).toBe(true)
    act(() => setFeatures({ 'ask.web_search': true }))
    expect(result.current).toBe(false)
    act(() => setFeatures({ 'qa.agentic': true }))  // 沒有這個鍵＝關
    expect(result.current).toBe(true)
  })
})

// 後端附不附「可開網搜」只看伺服器總閘；前端暫停時開關不在畫面上，顯示時要剝掉那一句。
describe('noticeDisplayText', () => {
  const [zhHint, enHint] = TIME_SENSITIVE_WEB_HINTS
  it('暫停中：時效婉拒剝掉提示（中、英）', () => {
    expect(noticeDisplayText('無法驗證最新數字。' + zhHint, 'time_sensitive', true)).toBe('無法驗證最新數字。')
    expect(noticeDisplayText('Cannot verify.' + enHint, 'time_sensitive', true)).toBe('Cannot verify.')
  })
  it('未暫停：原樣顯示', () => {
    expect(noticeDisplayText('無法驗證。' + zhHint, 'time_sensitive', false)).toBe('無法驗證。' + zhHint)
  })
  it('沒有提示的婉拒、離題婉拒：原樣顯示', () => {
    expect(noticeDisplayText('無法驗證最新數字。', 'time_sensitive', true)).toBe('無法驗證最新數字。')
    expect(noticeDisplayText('離題。' + zhHint, 'off_topic', true)).toBe('離題。' + zhHint)
  })
})
