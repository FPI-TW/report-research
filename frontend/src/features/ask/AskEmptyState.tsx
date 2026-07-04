import { Composer } from './Composer'
import styles from './AskEmptyState.module.css'

interface Props { value: string; onChange: (v: string) => void; onSubmit: (q: string) => void }

export function AskEmptyState({ value, onChange, onSubmit }: Props) {
  return (
    <div className={styles.wrap}>
      <div className={styles.glyph}>廷</div>
      <div className={styles.title}>向廷豐智能體提問</div>
      <div className={styles.sub}>以自然語言詢問研究主題，回答將附上券商研報的引用來源。</div>
      <Composer value={value} onChange={onChange} onSubmit={onSubmit} variant="center" />
    </div>
  )
}
