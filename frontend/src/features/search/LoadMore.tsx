import atoms from './atoms.module.css'
import styles from './LoadMore.module.css'

interface Props { remaining: number; loading: boolean; onClick: () => void }

export function LoadMore({ remaining, loading, onClick }: Props) {
  return (
    <div className={styles.wrap}>
      <button type="button" className={atoms.btn} disabled={loading} onClick={onClick}>
        {loading ? '載入中…' : <>載入更多（還有 <span className={atoms.num}>{remaining.toLocaleString()}</span> 篇）</>}
      </button>
    </div>
  )
}
