import { Pressable } from '../../components/primitives/Pressable'
import type { Coverage, Window } from '../../lib/radarSchemas'
import { useScrolled } from '../../lib/useScrolled'
import { fmtDate, marketVar, WINDOW_LABEL } from './radarFormat'
import { WindowSegmented } from './WindowSegmented'
import styles from './RadarHeader.module.css'

interface Props {
  market: string
  marketDisplay?: string | null
  code: string
  name?: string | null
  asOf?: string | null
  coverage?: Coverage | null
  window: Window
  onWindowChange: (w: Window) => void
  onBack: () => void
}

export function RadarHeader({
  market, marketDisplay, code, name, asOf, coverage, window, onWindowChange, onBack,
}: Props) {
  const brokers = coverage?.brokers_in_consensus ?? coverage?.brokers_extracted
  const reports = coverage?.reports_available
  const { scrolled, sentinelRef } = useScrolled()

  // 導覽列（麵包屑＋窗期）與大標題刻意拆成兩個元素：sticky 只在父容器的範圍內有效，
  // 包在同一個 <header> 裡的話，header 一捲出視窗導覽列就跟著鬆開。導覽列因此
  // 提到 RadarOverview 的根層（那一層跟整頁一樣高），大標題才留在 <header> 裡照常捲走
  // —— 這也正是 macOS／iOS 大標題的行為：標題捲掉，精簡導覽列留下。
  return (
    <>
      <span ref={sentinelRef} className={styles.sentinel} aria-hidden="true" />
      <div className={`${styles.top} ${scrolled ? styles.topStuck : ''}`}>
        <nav className={styles.crumb} aria-label="麵包屑">
          <Pressable type="button" className={styles.crumbBtn} onClick={onBack}>標的</Pressable>
          <span className={styles.sep}>/</span>
          <span>{marketDisplay || market}</span>
          <span className={styles.sep}>/</span>
          <span>{code}</span>
        </nav>
        {/* 大標題捲掉之後，把標的名收進導覽列——長券商列表捲到底時仍看得出在看哪一檔 */}
        {scrolled ? <span className={styles.stuckTitle}>{name || code}</span> : null}
        <WindowSegmented value={window} onChange={onWindowChange} />
      </div>

      <header className={styles.wrap}>
        <div className={styles.titleRow}>
          <h1 className={styles.title}>{name || code}</h1>
          <span className={styles.code}>{code}</span>
          <span className={styles.badge} style={marketVar(market)}>{marketDisplay || market}</span>
        </div>

        <p className={styles.meta}>
          {brokers != null ? `${brokers} 家券商` : '—'}
          {reports != null ? ` · ${reports} 份可用研報` : ''}
          {asOf ? ` · 資料截至 ${fmtDate(asOf)}` : ''}
          {window ? ` · ${WINDOW_LABEL[window] ?? window}` : ''}
        </p>
      </header>
    </>
  )
}
