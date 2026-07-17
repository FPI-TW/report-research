import type { ReactNode } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { revealVariantsFor, revealTransition } from '../../lib/motionTokens'
import { Icon } from './Icon'
import { Pressable } from './Pressable'
import styles from './Callout.module.css'

interface Props {
  variant: 'error' | 'warning'
  children: ReactNode
  action?: { label: string; onClick: () => void }
}

export function Callout({ variant, children, action }: Props) {
  const reduced = useReducedMotion()
  return (
    <motion.div
      className={`${styles.box} ${styles[variant]}`}
      role="alert"
      variants={revealVariantsFor('scale')}
      initial="hidden"
      animate="visible"
      transition={revealTransition(reduced)}
    >
      <Icon name={variant === 'error' ? 'alertCircle' : 'alertTriangle'} size={18} className={styles.icon} />
      <div className={styles.content}>
        <div className={styles.text}>{children}</div>
        {action && (
          <Pressable className={styles.action} onClick={action.onClick}>{action.label}</Pressable>
        )}
      </div>
    </motion.div>
  )
}
