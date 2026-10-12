import type { ReactNode } from 'react'
import c from './Console.module.css'

/** 主控台頁面的骨架：自己捲動的容器＋28px 內距。維運各頁由 `OperationsLayout` 包好，不必再包一層。 */
export function ConsolePage({ children }: { children: ReactNode }) {
  return (
    <div className={c.page}>
      <div className={c.inner}>{children}</div>
    </div>
  )
}
