import { useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { stagesToSteps } from '../../lib/thinkingStages'
import type { Turn } from '../../lib/askReducer'
import styles from './ThinkingSteps.module.css'

export function ThinkingSteps({ turn }: { turn: Turn }) {
  const live = turn.phase === 'thinking' || turn.phase === 'streaming'
  const [open, setOpen] = useState(true)
  const rawSteps = stagesToSteps(turn.stages, turn.webUsed)
  const terminal = turn.phase === 'done' || turn.phase === 'notice' || turn.phase === 'error'
  const steps = terminal ? rawSteps.map(s => (s.state === 'active' ? { ...s, state: 'done' as const } : s)) : rawSteps
  const sec = Math.round((turn.thinkingMs ?? 0) / 1000)
  const label = live ? '思考中…' : `已思考 ${sec} 秒`
  const hasSteps = turn.stages.length > 0

  return (
    <div className={styles.card}>
      <button type="button" className={styles.head} onClick={() => setOpen(o => !o)}>
        <span>{label}</span>
        {hasSteps && <Icon name="chevronDown" size={15} className={open ? styles.chevOpen : styles.chev} />}
      </button>
      {hasSteps && open && (
        <div className={styles.steps}>
          {steps.map(s => (
            <div key={s.key} className={styles.step}>
              {s.state === 'done' && <Icon name="check" size={16} className={styles.done} />}
              {s.state === 'active' && <Icon name="spinner" size={16} className={styles.spin} />}
              {s.state === 'pending' && <span className={styles.dot} />}
              <span className={s.state === 'pending' ? styles.pendingText : undefined}>{s.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
