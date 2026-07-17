import { Pressable } from '../../components/primitives/Pressable'
import type { EmptyStateCopy } from '../../lib/resultsMeta'
import styles from './EmptyState.module.css'

interface Props { copy: EmptyStateCopy; onClear: () => void; onBrowseAll: () => void }

export function EmptyState({ copy, onClear, onBrowseAll }: Props) {
  return (
    <div className={styles.state}>
      <div className={styles.title}>{copy.title}</div>
      <div className={styles.hint}>{copy.hint}</div>
      <div className={styles.actions}>
        {copy.showClear && <Pressable className={styles.btn} onClick={onClear}>清除篩選再試</Pressable>}
        {copy.showBrowseAll && <Pressable className={styles.btn} onClick={onBrowseAll}>瀏覽全部報告</Pressable>}
      </div>
    </div>
  )
}
