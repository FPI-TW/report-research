import { Pressable } from '../../components/primitives/Pressable'
import type { Coverage, Window } from '../../lib/radarSchemas'
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

  return (
    <header className={styles.wrap}>
      <div className={styles.top}>
        <nav className={styles.crumb} aria-label="麵包屑">
          <Pressable type="button" className={styles.crumbBtn} onClick={onBack}>標的</Pressable>
          <span className={styles.sep}>/</span>
          <span>{marketDisplay || market}</span>
          <span className={styles.sep}>/</span>
          <span>{code}</span>
        </nav>
        <WindowSegmented value={window} onChange={onWindowChange} />
      </div>

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
  )
}
