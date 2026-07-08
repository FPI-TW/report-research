import type { ReactNode } from 'react'
import styles from './MonthGroup.module.css'

interface Props { title: string; count: number; children: ReactNode }

export function MonthGroup({ title, count, children }: Props) {
  return (
    <section>
      <div className={styles.header}>
        <span className={styles.pill}>
          <span className={styles.title}>{title}</span>
          <span className={styles.dot}>·</span>
          <span className={styles.count}>{count} 篇</span>
        </span>
      </div>
      {children}
    </section>
  )
}
