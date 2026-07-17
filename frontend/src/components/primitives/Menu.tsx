import type { ReactNode } from 'react'
import { Pressable } from './Pressable'
import styles from './Menu.module.css'

export function MenuItem({ children, onClick }: { children: ReactNode; onClick?: () => void }) {
  return (
    <Pressable role="menuitem" onClick={onClick} className={styles.item}>
      {children}
    </Pressable>
  )
}
