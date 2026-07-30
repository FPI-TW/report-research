import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 「內容是否已經捲到這條線之下」。
 *
 * 用途是讓吸頂的 chrome（頁首／工具列）在內容真的捲到它底下之後才實體化成玻璃，
 * 呼應 macOS 視窗標題列的行為：控制項在被需要之前是隱形的。
 *
 * 為何不是純 CSS：`position: sticky` 沒有「已吸頂」的選擇器，而 `animation-timeline:
 * scroll()` 的 Safari 支援還不足以當唯一依據，故用 sentinel + IntersectionObserver。
 *
 * 用法是把 `sentinelRef` 掛在捲動容器內、目標 chrome 之前的一個零高度元素上。
 * 採 callback ref 而非物件 ref，因為 sentinel 常在條件渲染下才掛載——物件 ref
 * 在 effect 執行時可能還是 null，觀察就悄悄沒接上。
 *
 * 環境沒有 IntersectionObserver（jsdom）時恆回 false：chrome 維持靜止態，
 * 不破版也不需要每個測試各自 mock 一次。
 */
export function useScrolled(): {
  scrolled: boolean
  sentinelRef: (node: HTMLElement | null) => void
} {
  const [scrolled, setScrolled] = useState(false)
  const observerRef = useRef<IntersectionObserver | null>(null)

  const sentinelRef = useCallback((node: HTMLElement | null) => {
    observerRef.current?.disconnect()
    observerRef.current = null

    if (!node || typeof IntersectionObserver === 'undefined') {
      setScrolled(false)
      return
    }

    const observer = new IntersectionObserver(
      ([entry]) => setScrolled(!entry.isIntersecting),
      { threshold: 0 },
    )
    observer.observe(node)
    observerRef.current = observer
  }, [])

  useEffect(() => () => observerRef.current?.disconnect(), [])

  return { scrolled, sentinelRef }
}
