import { Pressable } from '../../components/primitives/Pressable'
import styles from './LoadMore.module.css'

interface Props { remaining: number; loading: boolean; onClick: () => void }

export function LoadMore({ remaining, loading, onClick }: Props) {
  return (
    <div className={styles.wrap}>
      <Pressable className={styles.btn} disabled={loading} onClick={onClick}>
        {loading ? '載入中…' : `載入更多（還有 ${remaining.toLocaleString()} 篇）`}
      </Pressable>
    </div>
  )
}
