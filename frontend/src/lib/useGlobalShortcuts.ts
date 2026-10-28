import { useEffect } from 'react'

function isTyping(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false
  return t.isContentEditable || t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.tagName === 'SELECT'
}

/**
 * 全站快捷鍵（交接包「互動」）：⌘／Ctrl K 開關搜尋、⌘／Ctrl Shift O 新對話、在研報庫按 / 聚焦搜尋框。
 * Esc 由各彈窗自己處理。輸入法組字中一律不處理；打字中不攔截單鍵快捷鍵。
 */
export function useGlobalShortcuts({ toggleSearch, newChat, pathname }: {
  toggleSearch: () => void
  newChat: () => void
  pathname: string
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing) return
      const mod = e.metaKey || e.ctrlKey
      const k = e.key.toLowerCase()
      if (mod && !e.shiftKey && !e.altKey && k === 'k') { e.preventDefault(); toggleSearch(); return }
      if (mod && e.shiftKey && !e.altKey && k === 'o') { e.preventDefault(); newChat(); return }
      if (!mod && !e.altKey && e.key === '/' && pathname === '/library' && !isTyping(e.target)) {
        const input = document.querySelector<HTMLInputElement>('input[aria-label="搜尋研報"]')
        if (input) { e.preventDefault(); input.focus() }
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [toggleSearch, newChat, pathname])
}
