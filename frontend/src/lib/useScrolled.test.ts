import { act, renderHook } from '@testing-library/react'
import { useScrolled } from './useScrolled'

type Cb = (entries: Array<{ isIntersecting: boolean }>) => void

/** 記錄下建構時的 callback，讓測試能手動觸發交會事件。 */
function installObserver() {
  const state = {
    callbacks: [] as Cb[],
    observed: [] as Element[],
    disconnects: 0,
  }
  class FakeObserver {
    constructor(cb: Cb) {
      state.callbacks.push(cb)
    }
    observe(node: Element) {
      state.observed.push(node)
    }
    disconnect() {
      state.disconnects += 1
    }
    unobserve() {}
    takeRecords() {
      return []
    }
  }
  vi.stubGlobal('IntersectionObserver', FakeObserver)
  return state
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('useScrolled', () => {
  it('sentinel 離開視窗時轉為已捲動，回到視窗時復原', () => {
    const state = installObserver()
    const { result } = renderHook(() => useScrolled())

    act(() => result.current.sentinelRef(document.createElement('div')))
    expect(result.current.scrolled).toBe(false)
    expect(state.observed).toHaveLength(1)

    act(() => state.callbacks[0]([{ isIntersecting: false }]))
    expect(result.current.scrolled).toBe(true)

    act(() => state.callbacks[0]([{ isIntersecting: true }]))
    expect(result.current.scrolled).toBe(false)
  })

  it('sentinel 換掉時先斷開舊觀察，不留下殘存的觀察者', () => {
    const state = installObserver()
    const { result } = renderHook(() => useScrolled())

    act(() => result.current.sentinelRef(document.createElement('div')))
    act(() => result.current.sentinelRef(document.createElement('div')))

    expect(state.disconnects).toBeGreaterThanOrEqual(1)
    expect(state.observed).toHaveLength(2)
  })

  it('環境沒有 IntersectionObserver 時恆為靜止態，不拋錯', () => {
    vi.stubGlobal('IntersectionObserver', undefined)
    const { result } = renderHook(() => useScrolled())

    act(() => result.current.sentinelRef(document.createElement('div')))

    expect(result.current.scrolled).toBe(false)
  })
})
