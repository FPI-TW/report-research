import { useCallback, useState } from 'react'

// 改版後的側欄（256／52px）用新的鍵：舊鍵記的是舊版「圖示軌＋對話面板」的預設收合，
// 沿用會讓大多數人一進新版就是收合狀態。
const KEY = 'tf.sidebar.v2.collapsed'

/** 預設展開；使用者收合或展開過就記住，之後照他的選擇。 */
export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(KEY) === '1' } catch { return false }
  })
  const toggle = useCallback(() => {
    setCollapsed((c) => {
      const next = !c
      try { localStorage.setItem(KEY, next ? '1' : '0') } catch { /* ignore */ }
      return next
    })
  }, [])
  return { collapsed, toggle }
}
