import { renderHook, render, screen, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { usePresence } from './usePresence'

function stubReduced(matches: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: /prefers-reduced-motion/.test(query) ? matches : false,
    media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }))
}

describe('usePresence', () => {
  beforeEach(() => {
    // rAF 同步執行，讓進場翻態確定性
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { cb(0); return 1 })
    vi.stubGlobal('cancelAnimationFrame', () => {})
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    vi.useRealTimers()
  })

  it('未曾開啟時不掛載', () => {
    stubReduced(false)
    const { result } = renderHook(() => usePresence(false))
    expect(result.current.isMounted).toBe(false)
    expect(result.current.state).toBe('closed')
  })

  it('開啟：同步掛載並於 rAF 後進場', () => {
    stubReduced(false)
    const { result } = renderHook(({ open }) => usePresence(open), { initialProps: { open: true } })
    expect(result.current.isMounted).toBe(true)
    expect(result.current.state).toBe('open')
  })

  it('關閉：離場期間仍掛載，時長後卸載', () => {
    stubReduced(false)
    const { result, rerender } = renderHook(
      ({ open }) => usePresence(open, { duration: 240 }),
      { initialProps: { open: true } },
    )
    expect(result.current.isMounted).toBe(true)
    vi.useFakeTimers()
    act(() => rerender({ open: false }))
    expect(result.current.state).toBe('closed')
    expect(result.current.isMounted).toBe(true)
    act(() => { vi.advanceTimersByTime(240) })
    expect(result.current.isMounted).toBe(false)
  })

  it('關閉時面板 DOM 節點身分保留（不卸載重建，離場過渡才有起始值）', () => {
    stubReduced(false)
    function Panel({ open }: { open: boolean }) {
      const { isMounted, state } = usePresence(open, { duration: 240 })
      if (!isMounted) return null
      return <div data-testid="panel" data-state={state} />
    }
    const { rerender } = render(<Panel open />)
    const before = screen.getByTestId('panel')
    vi.useFakeTimers()
    act(() => rerender(<Panel open={false} />))
    const after = screen.getByTestId('panel')
    expect(after).toBe(before) // 同一節點 → CSS 離場過渡可從 open 值過渡到 closed 值
    expect(after.getAttribute('data-state')).toBe('closed')
  })

  it('reduced-motion：關閉立即卸載', () => {
    stubReduced(true)
    const { result, rerender } = renderHook(
      ({ open }) => usePresence(open),
      { initialProps: { open: true } },
    )
    vi.useFakeTimers()
    act(() => rerender({ open: false }))
    act(() => { vi.advanceTimersByTime(0) })
    expect(result.current.isMounted).toBe(false)
  })
})
