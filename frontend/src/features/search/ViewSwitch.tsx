import { Pressable } from '../../components/primitives/Pressable'
import type { ViewMode } from '../../lib/searchFilters'
import styles from './ViewSwitch.module.css'

interface Props { view: ViewMode; onChange: (v: ViewMode) => void }

/** 檢視切換：位於工具列的分段控制（cards＝高密度列表、table＝表格） */
export function ViewSwitch({ view, onChange }: Props) {
  return (
    <div className={styles.wrap} role="group" aria-label="檢視切換">
      <Pressable aria-label="列表檢視" aria-pressed={view === 'cards'}
        className={`${styles.btn} ${view === 'cards' ? styles.active : ''}`} onClick={() => onChange('cards')}>≣</Pressable>
      <Pressable aria-label="表格檢視" aria-pressed={view === 'table'}
        className={`${styles.btn} ${view === 'table' ? styles.active : ''}`} onClick={() => onChange('table')}>▦</Pressable>
    </div>
  )
}
