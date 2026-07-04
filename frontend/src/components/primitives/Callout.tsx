import type { ReactNode } from 'react'
import { Icon } from './Icon'
import styles from './Callout.module.css'

interface Props {
  variant: 'error' | 'warning'
  children: ReactNode
  action?: { label: string; onClick: () => void }
}

export function Callout({ variant, children, action }: Props) {
  return (
    <div className={`${styles.box} ${styles[variant]}`} role="alert">
      <Icon name={variant === 'error' ? 'alertCircle' : 'alertTriangle'} size={18} className={styles.icon} />
      <div className={styles.content}>
        <div className={styles.text}>{children}</div>
        {action && (
          <button type="button" className={styles.action} onClick={action.onClick}>{action.label}</button>
        )}
      </div>
    </div>
  )
}
