import { ChevronLeft } from 'lucide-react'
import { marketLabel } from '../../lib/meta'
import type { Coverage, Market, Window } from '../../lib/radarSchemas'
import { fmtShortDate, WINDOW_OPTIONS } from './radarFormat'
import { Segmented } from './Segmented'
import shared from './shared.module.css'
import styles from './RadarHeader.module.css'

interface Props {
  market: Market
  marketDisplay?: string | null
  code: string
  name?: string | null
  asOf?: string | null
  coverage?: Coverage | null
  window: Window
  onWindowChange: (w: Window) => void
  onBack: () => void
  onBrowseReports: () => void
}

/**
 * 詳情頁首：返回、標的名 28px、代號、市場、券商與篇數；右側期間分段與「相關研報」。
 *
 * 期間切換時整頁內容換掉，但畫面上沒有東西告訴讀屏「資料變了」；說明列本來就會跟著改
 * （家數、篇數、資料截止日），宣告成 polite 即時區域，就用既有內容當更新通知。
 */
export function RadarHeader({
  market, marketDisplay, code, name, asOf, coverage, window, onWindowChange, onBack, onBrowseReports,
}: Props) {
  const partialNote = coverage?.state === 'partial' ? coverage.note : ''
  return (
    <header className={styles.header}>
      <button type="button" className={styles.back} onClick={onBack}>
        <ChevronLeft size={14} strokeWidth={1.8} aria-hidden="true" />
        券商共識
      </button>

      <div className={styles.row}>
        <div className={styles.ident}>
          <div className={styles.titleLine}>
            <h1 className={styles.title}>{name || code}</h1>
            {name ? <span className={`${shared.code} ${styles.code}`}>{code}</span> : null}
            <span className={styles.mkt} style={{ color: `var(--mkt-${market}, var(--mkt-fallback))` }}>
              {marketDisplay || marketLabel(market)}
            </span>
          </div>
          <p className={styles.meta} aria-live="polite">
            {coverage ? (
              <>
                <b className={shared.num}>{coverage.brokers_total}</b> 家券商，
                <b className={shared.num}>{coverage.brokers_in_consensus}</b> 家納入共識，
                <b className={shared.num}>{coverage.reports_available}</b> 篇研報
                {asOf ? <>，<span className={styles.nowrap}>資料截至 <span className={shared.num}>{fmtShortDate(asOf)}</span></span></> : null}
              </>
            ) : null}
          </p>
          {partialNote ? <p className={styles.note}>{partialNote}</p> : null}
        </div>

        <Segmented
          label="期間"
          options={WINDOW_OPTIONS}
          value={window}
          onChange={onWindowChange}
          itemWidth={64}
        />
        <button type="button" className={shared.btn} onClick={() => onBrowseReports()}>相關研報</button>
      </div>
    </header>
  )
}
