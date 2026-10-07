import { useCallback, useState } from 'react'

const KEY = 'tf.sidebar.collapsed'

/** 預設收合（只留圖示軌）；使用者展開或收合過就記住，之後照他的選擇。 */
export function useSidebarCollapsed() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { return localStorage.getItem(KEY) !== '0' } catch { return true }
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
