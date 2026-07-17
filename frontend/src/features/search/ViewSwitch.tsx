import { Tabs, TabsList, TabsTrigger } from '../../components/animate-ui/components/animate/tabs'
import type { ViewMode } from '../../lib/searchFilters'
import styles from './ViewSwitch.module.css'

interface Props { view: ViewMode; onChange: (v: ViewMode) => void }

const OPTIONS: { v: ViewMode; label: string; aria: string }[] = [
  { v: 'cards', label: '≣', aria: '列表檢視' },
  { v: 'table', label: '▦', aria: '表格檢視' },
]

/**
 * 檢視切換：工具列分段控制（cards＝高密度列表、table＝表格）。
 * 改用 animate-ui Tabs：active 底板以 motion-highlight（layoutId 共享版面）於兩鍵間滑移，
 * 軌道/底板/文字配色由 tailwind.css 的 @theme token 對應到品牌 --tf-* 變數。
 */
export function ViewSwitch({ view, onChange }: Props) {
  return (
    <Tabs value={view} onValueChange={v => onChange(v as ViewMode)} className={styles.tabs}>
      <TabsList aria-label="檢視切換" className={styles.list}>
        {OPTIONS.map(o => (
          <TabsTrigger key={o.v} value={o.v} aria-label={o.aria} className={styles.trigger} whileTap={{ scale: 0.94 }}>
            {o.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  )
}
