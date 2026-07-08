import { renderHook, act } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useTween } from './useTween'

function stubReduced(matches: boolean) {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: /prefers-reduced-motion/.test(query) ? matches : false,
    media: query, onchange: null,
    addEventListener: () => {}, removeEventListener: () => {},
    addListener: () => {}, removeListener: () => {}, dispatchEvent: () => false,
  }))
}

// 可控 rAF：手動逐幀推進，避免 fake-timers 與 React 排程衝突
let rafCbs: Array<(t: number) => void> = []
let now = 0
function installRaf() {
  rafCbs = []
  now = 0
  vi.stubGlobal('requestAnimationFrame', (cb: (t: number) => void) => { rafCbs.push(cb); return rafCbs.length })
  vi.stubGlobal('cancelAnimationFrame', () => {})
}
function flushFrame(dt: number) {
  now += dt
  const cbs = rafCbs
  rafCbs = []
  act(() => { cbs.forEach(cb => cb(now)) })
}

afterEach(() => { vi.unstubAllGlobals() })

describe('useTween', () => {
  it('首次渲染直接顯示真值（不從 0 數起）', () => {
    // 全域預設 reduced=true → 跳值
    const { result } = renderHook(() => useTween(1000, { decimals: 0 }))
    expect(result.current).toBe(1000)
  })

  it('reduced-motion：改變 target 立即跳值', () => {
    const { result, rerender } = renderHook(({ v }) => useTween(v, { decimals: 0 }), { initialProps: { v: 100 } })
    expect(result.current).toBe(100)
    act(() => rerender({ v: 500 }))
    expect(result.current).toBe(500)
  })

  describe('非 reduced 動畫路徑', () => {
    beforeEach(() => { stubReduced(false); installRaf() })

    it('改變 target 會補間並最終抵達', () => {
      const { result, rerender } = renderHook(({ v }) => useTween(v, { duration: 300, decimals: 2 }), { initialProps: { v: 0 } })
      expect(result.current).toBe(0)
      act(() => rerender({ v: 100 }))
      flushFrame(16)
      flushFrame(150)
      expect(result.current).toBeGreaterThan(0)
      expect(result.current).toBeLessThan(100)
      flushFrame(300)
      expect(result.current).toBe(100)
    })

    it('中途 retarget 不跳、最終抵達新值', () => {
      const { result, rerender } = renderHook(({ v }) => useTween(v, { duration: 300, decimals: 2 }), { initialProps: { v: 0 } })
      act(() => rerender({ v: 100 }))
      flushFrame(16)
      flushFrame(150)
      const mid = result.current
      expect(mid).toBeGreaterThan(0)
      expect(mid).toBeLessThan(100)
      act(() => rerender({ v: 200 }))
      flushFrame(16)
      flushFrame(400)
      expect(result.current).toBe(200)
    })
  })
})
