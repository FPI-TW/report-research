import type { ViewMode } from '../../lib/searchFilters'
import styles from './ViewSwitch.module.css'

interface Props { view: ViewMode; onChange: (v: ViewMode) => void }

/** 檢視切換：位於工具列的分段控制（cards＝高密度列表、table＝表格） */
export function ViewSwitch({ view, onChange }: Props) {
  // 滑動 thumb 的目標段位（純 CSS transform 平移，無需量測、jsdom 靜態）
  const idx = view === 'table' ? 1 : 0
  return (
    <div className={styles.wrap} role="group" aria-label="檢視切換">
      <span className={styles.thumb} style={{ ['--idx' as string]: idx }} aria-hidden="true" />
      <button type="button" aria-label="列表檢視" aria-pressed={view === 'cards'}
        className={`${styles.btn} ${view === 'cards' ? styles.active : ''}`} onClick={() => onChange('cards')}>≣</button>
      <button type="button" aria-label="表格檢視" aria-pressed={view === 'table'}
        className={`${styles.btn} ${view === 'table' ? styles.active : ''}`} onClick={() => onChange('table')}>▦</button>
    </div>
  )
}
