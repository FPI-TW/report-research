import type { EmptyStateCopy } from '../../lib/resultsMeta'
import styles from './EmptyState.module.css'

interface Props { copy: EmptyStateCopy; onClear: () => void; onBrowseAll: () => void }

export function EmptyState({ copy, onClear, onBrowseAll }: Props) {
  return (
    <div className={styles.state}>
      <div className={styles.title}>{copy.title}</div>
      <div className={styles.hint}>{copy.hint}</div>
      <div className={styles.actions}>
        {copy.showClear && <button type="button" className={styles.btn} onClick={onClear}>清除篩選再試</button>}
        {copy.showBrowseAll && <button type="button" className={styles.btn} onClick={onBrowseAll}>瀏覽全部報告</button>}
      </div>
    </div>
  )
}
