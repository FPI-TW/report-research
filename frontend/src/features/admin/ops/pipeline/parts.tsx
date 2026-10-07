import type { ReactNode } from 'react'
import styles from './Pipeline.module.css'

export type Tone = 'ok' | 'run' | 'idle' | 'warn' | 'bad'

const TONE_CLASS: Record<Tone, string> = {
  ok: styles.toneOk,
  run: styles.toneRun,
  idle: styles.toneIdle,
  warn: styles.toneWarn,
  bad: styles.toneBad,
}

export function TonePill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`${styles.tone} ${TONE_CLASS[tone]}`}>{children}</span>
}

/** 進度條：寬度與 aria-valuenow 夾在 0–100（後端偶有超界或 NaN 時不撐破版面）。 */
export function Bar({ pct, label, gold = false }: { pct: number; label: string; gold?: boolean }) {
  const v = Number.isFinite(pct) ? Math.max(0, Math.min(100, pct)) : 0
  return (
    <div className={styles.bar} role="progressbar" aria-label={label} aria-valuenow={Math.round(v)}
      aria-valuemin={0} aria-valuemax={100}>
      <div className={`${styles.barFill} ${gold ? styles.barGold : ''}`} style={{ width: `${v.toFixed(1)}%` }} />
    </div>
  )
}

/** 卡片內的一列：左標籤、右內容。 */
export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={styles.row}>
      <div className={styles.rowLabel}>{label}</div>
      <div className={styles.rowBody}>{children}</div>
    </div>
  )
}

/** 數字大於 0 才標警示色（舊監控頁的判準：0 是常態，不該被染色）。 */
export function Count({ value, tone = 'warn' }: { value: number; tone?: 'warn' | 'bad' }) {
  const cls = value > 0 ? (tone === 'bad' ? styles.badNum : styles.warnNum) : ''
  return <span className={cls}>{value.toLocaleString('en-US')}</span>
}
