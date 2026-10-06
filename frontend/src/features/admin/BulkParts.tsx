import { useEffect, useRef } from 'react'
import type { BulkOutcome } from './useSelection'
import styles from './Admin.module.css'

/** 表頭的「全選本頁」：全部已選＝勾、部分已選＝半勾（indeterminate）、沒有可選的列＝停用。 */
export function SelectAllBox({ ids, selected, onChange, label = '全選本頁' }: {
  ids: readonly string[]; selected: ReadonlySet<string>; onChange: (on: boolean) => void; label?: string
}) {
  const ref = useRef<HTMLInputElement>(null)
  const count = ids.filter(id => selected.has(id)).length
  const all = ids.length > 0 && count === ids.length
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = count > 0 && !all
  }, [count, all])
  return (
    <input ref={ref} type="checkbox" className={styles.rowCheck} aria-label={label} checked={all}
      disabled={ids.length === 0} onChange={e => onChange(e.target.checked)} />
  )
}

/** 送出批次後的逐筆結果：成功幾筆、未變更幾筆、略過幾筆與各自的原因。 */
export function BulkResultPanel({ outcome, onClose }: { outcome: BulkOutcome | null; onClose: () => void }) {
  if (!outcome) return null
  const { title, ok, unchanged = 0, skipped } = outcome
  const parts = [`成功 ${ok} 筆`]
  if (unchanged) parts.push(`未變更 ${unchanged} 筆（已是目標狀態）`)
  parts.push(`略過 ${skipped.length} 筆`)
  return (
    <section className={styles.bulkResult} aria-label="批次結果" role="status">
      <div className={styles.bulkResultHead}>
        <strong>{title}：{parts.join('、')}</strong>
        <button type="button" className={styles.action} onClick={onClose}>關閉</button>
      </div>
      {skipped.length > 0 && (
        <ul className={styles.bulkSkipped}>
          {skipped.map((s, i) => <li key={i}><span className={styles.bulkName}>{s.name}</span>：{s.detail}</li>)}
        </ul>
      )}
    </section>
  )
}
