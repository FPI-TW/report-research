import { useEffect, type RefObject } from 'react'

const FOCUSABLE =
  'a[href],button:not([disabled]),textarea,input,select,[tabindex]:not([tabindex="-1"])'

export function useFocusTrap(ref: RefObject<HTMLElement | null>, active: boolean): void {
  useEffect(() => {
    if (!active) return
    const el = ref.current
    if (!el) return
    // 關閉後把焦點還給打開它的元素（若它還在頁面上）；打開它的若是已卸載的選單項目就不處理。
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const nodes = () => Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE))
    nodes()[0]?.focus()
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const items = nodes()
      if (items.length === 0) return
      const first = items[0]
      const last = items[items.length - 1]
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault(); last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault(); first.focus()
      }
    }
    el.addEventListener('keydown', onKey)
    return () => {
      el.removeEventListener('keydown', onKey)
      if (opener?.isConnected && !el.contains(opener)) opener.focus()
    }
  }, [ref, active])
}
