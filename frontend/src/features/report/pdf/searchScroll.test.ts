import { describe, expect, it, vi } from 'vitest'
import { scrollToSearchResult } from './searchScroll'

const hit = { pageIndex: 11, rects: [{ origin: { x: 72, y: 430 } }] }

function scrollStub() {
  return { scrollToPage: vi.fn() }
}

describe('scrollToSearchResult', () => {
  // goToResult 只換 activeResultIndex、不捲動，所以捲動一定要自己來。
  it('未旋轉的頁：帶頁碼、命中座標與 alignY', () => {
    const scroll = scrollStub()
    scrollToSearchResult(scroll, 0, hit)
    expect(scroll.scrollToPage).toHaveBeenCalledWith({
      pageNumber: 12,
      pageCoordinates: { x: 72, y: 430 },
      alignY: 25,
      behavior: 'smooth',
    })
  })

  // 內建旋轉頁的 pageCoordinates 會被 plugin-scroll 再套一次旋轉（rects 已在正規化
  // 座標系），兩次相加會把座標送到頁面外。寧可只捲到頁首，也不要捲到錯的地方。
  it('內建旋轉的頁：只給頁碼，不給座標', () => {
    const scroll = scrollStub()
    scrollToSearchResult(scroll, 1, hit)
    expect(scroll.scrollToPage).toHaveBeenCalledWith({ pageNumber: 12, behavior: 'smooth' })
  })

  it('沒有 rect 時退回只給頁碼（錨不到不是錯誤）', () => {
    const scroll = scrollStub()
    scrollToSearchResult(scroll, 0, { pageIndex: 3, rects: [] })
    expect(scroll.scrollToPage).toHaveBeenCalledWith({ pageNumber: 4, behavior: 'smooth' })
  })

  it('reduce motion 時不做平滑捲動', () => {
    const scroll = scrollStub()
    scrollToSearchResult(scroll, 0, hit, true)
    expect(scroll.scrollToPage.mock.calls[0][0].behavior).toBe('auto')
  })

  it('沒有 scroll 能力或沒有命中時不呼叫也不拋', () => {
    const scroll = scrollStub()
    expect(() => scrollToSearchResult(null, 0, hit)).not.toThrow()
    scrollToSearchResult(scroll, 0, undefined)
    expect(scroll.scrollToPage).not.toHaveBeenCalled()
  })

  it('pageRotation 為 undefined 視同未旋轉', () => {
    const scroll = scrollStub()
    scrollToSearchResult(scroll, undefined, hit)
    expect(scroll.scrollToPage.mock.calls[0][0].pageCoordinates).toEqual({ x: 72, y: 430 })
  })
})
