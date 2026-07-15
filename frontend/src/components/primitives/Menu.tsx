import type { ReactNode } from 'react'
import styles from './Menu.module.css'

export function MenuItem({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <button type="button" role="menuitem" onClick={onClick} className={styles.item}>
      {children}
    </button>
  )
}
