import { useLayoutEffect, type RefObject } from 'react'

/**
 * 浮層（對話框、抽屜、⌘K）關閉後把焦點還給開啟前的那個元素，鍵盤使用者才不必從頁首重新 Tab。
 *
 * 用 layout effect：要搶在 `useFocusTrap`（一般 effect）把焦點移進浮層之前記下原本的焦點。
 * 還原的條件：
 * - 原本的元素還在文件裡（列被刪掉、頁面換掉就不還原）；
 * - 焦點現在落在浮層裡，或已經掉回 `<body>`——關閉的同時焦點被移到別處（例如接著開的另一個浮層）就不搶。
 */
export function useRestoreFocus(ref: RefObject<HTMLElement | null>, open: boolean): void {
  useLayoutEffect(() => {
    if (!open) return
    const before = document.activeElement instanceof HTMLElement ? document.activeElement : null
    const overlay = ref.current
    return () => {
      if (!before || before === document.body || !before.isConnected) return
      const now = document.activeElement
      if (now && now !== document.body && !overlay?.contains(now)) return
      before.focus()
    }
  }, [ref, open])
}
