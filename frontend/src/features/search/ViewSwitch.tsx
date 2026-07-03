import type { ViewMode } from '../../lib/searchFilters'
import styles from './ViewSwitch.module.css'

interface Props { view: ViewMode; onChange: (v: ViewMode) => void }

export function ViewSwitch({ view, onChange }: Props) {
  return (
    <div className={styles.wrap} role="group" aria-label="檢視切換">
      <button type="button" aria-label="卡片檢視" aria-pressed={view === 'cards'}
        className={`${styles.btn} ${view === 'cards' ? styles.active : ''}`} onClick={() => onChange('cards')}>▦</button>
      <button type="button" aria-label="表格檢視" aria-pressed={view === 'table'}
        className={`${styles.btn} ${view === 'table' ? styles.active : ''}`} onClick={() => onChange('table')}>≣</button>
    </div>
  )
}
