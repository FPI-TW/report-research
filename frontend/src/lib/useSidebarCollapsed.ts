import { useCallback, useState } from 'react'

const KEY = 'tf.sidebar.collapsed'

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
