import { useState } from 'react'
import type { TurnState } from '../lib/conversation'
import { deriveProcess } from '../lib/process'
import styles from './AskPage.module.css'

export function ProcessSteps({ turn }: { turn: TurnState }) {
  const [open, setOpen] = useState(false)
  const { steps, headLabel, headDone } = deriveProcess(turn)
  return (
    <div className={styles.process} data-testid="ask-process">
      <button
        type="button"
        className={styles.processHead}
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.procIco} aria-hidden="true">
          {headDone ? '✓' : <span className={styles.spin} />}
        </span>
        <span>{headLabel}</span>
      </button>
      {open && (
        <ol className={styles.processSteps}>
          {steps
            .filter((s) => !s.hidden)
            .map((s) => (
              <li key={s.key} data-step={s.key} data-state={s.state} className={styles.step}>
                <span className={styles.stepIco} aria-hidden="true">
                  {s.state === 'done' ? '✓' : s.state === 'active' ? <span className={styles.spin} /> : '○'}
                </span>
                <span>{s.label}</span>
              </li>
            ))}
        </ol>
      )}
    </div>
  )
}
