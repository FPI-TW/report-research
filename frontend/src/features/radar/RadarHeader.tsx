import { useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Popover } from '../../components/primitives/Popover'
import { Pressable } from '../../components/primitives/Pressable'
import type { Coverage, Window } from '../../lib/radarSchemas'
import { fmtDate, WINDOW_LABEL } from './radarFormat'
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
  const [infoOpen, setInfoOpen] = useState(false)
  const brokers = coverage?.brokers_in_consensus ?? coverage?.brokers_extracted
  const reports = coverage?.reports_available

  return (
    <header className={styles.wrap}>
      <div className={styles.left}>
        <nav className={styles.crumb} aria-label="麵包屑">
          <Pressable className={styles.crumbBtn} onClick={onBack}>標的</Pressable>
          <span className={styles.sep}>/</span>
          <span>{marketDisplay || market}</span>
          <span className={styles.sep}>/</span>
          <span>{code}</span>
        </nav>
        <div className={styles.titleRow}>
          <h1 className={styles.title}>{name || code}</h1>
          <span className={styles.code}>{code}</span>
          <span className={styles.badge}>{marketDisplay || market}</span>
        </div>
        <div className={styles.meta}>
          {brokers != null ? `${brokers} 家券商` : '—'}
          {reports != null ? ` · ${reports} 份可用研報` : ''}
          {asOf ? ` · 資料截至 ${fmtDate(asOf)}` : ''}
          {window ? ` · ${WINDOW_LABEL[window] ?? window}` : ''}
        </div>
        <div className={styles.blockTitle}>
          <h2 className={styles.blockH}>研報觀點變化雷達</h2>
          <span style={{ position: 'relative' }}>
            <Pressable
              className={styles.infoBtn}
              aria-label="說明"
              aria-expanded={infoOpen}
              onClick={() => setInfoOpen(v => !v)}
            >
              <Icon name="info" size={16} />
            </Pressable>
            <div className={styles.pop}>
              <Popover
                open={infoOpen}
                onClose={() => setInfoOpen(false)}
                role="dialog"
                ariaLabel="觀點雷達說明"
              >
                <p className={styles.popText}>
                  本區整理券商研報中的已擷取觀點與數值變化，非系統預測或投資建議。
                </p>
              </Popover>
            </div>
          </span>
        </div>
      </div>
      <div className={styles.right}>
        <WindowSegmented value={window} onChange={onWindowChange} />
      </div>
    </header>
  )
}
