import { useId } from 'react'
import { ChevronRight } from 'lucide-react'
import { copyText } from '../../lib/clipboard'
import type { BrokerSummary, EpsGroup, Market, Window } from '../../lib/radarSchemas'
import { showToast } from '../../lib/toast'
import { BrokerHistory } from './BrokerHistory'
import { brokerKey, brokerName, epsBasisKey, epsGroupKey } from './brokerKeys'
import { toneOf } from './changeText'
import { brokerTableTsv } from './consensusExport'
import { epsFyLabel, fmtShortDate, fmtValue, RATING_DISPLAY } from './radarFormat'
import shared from './shared.module.css'
import styles from './BrokerTable.module.css'

interface Props {
  brokers: BrokerSummary[]
  code: string
  market: Market
  window: Window
  /** 目標價共識的主要幣別；其他幣別的價格在數字後面補幣別。 */
  targetCurrency: string | null
  /** EPS 共識的口徑；不同口徑的 EPS 在數字下方補年度。 */
  epsGroup: EpsGroup | null
  /** 展開哪家券商（受控：狀態在網址的 `broker`）。 */
  openBroker: string | null
  onOpenBrokerChange: (brokerKey: string | null) => void
  onOpenReport: (reportId: string, title?: string | null) => void
}

/**
 * 各券商最新觀點（交接包：可展開該券商歷程）。
 *
 * 每一列是一顆展開鈕（`aria-expanded`）。欄名只是視覺上的表頭，所以每一格的值前面都有
 * 只給讀屏的欄名，按鈕的可及名稱才讀得出「目標價 1,568」而不是一串沒有標籤的數字。
 */
export function BrokerTable({
  brokers, code, market, window, targetCurrency, epsGroup,
  openBroker, onOpenBrokerChange, onOpenReport,
}: Props) {
  const uid = useId()
  const rows = brokers.filter(b => brokerKey(b))
  const epsKey = epsGroup ? epsGroupKey(epsGroup) : null

  function copyTable() {
    copyText(brokerTableTsv(rows, targetCurrency, epsGroup)).then(
      () => showToast('已複製表格，可貼入試算表', { ok: true }),
      () => showToast('複製失敗，請再試一次'),
    )
  }

  return (
    <section className={styles.section} aria-labelledby={`${uid}-h`}>
      <div className={styles.headRow}>
        <h2 id={`${uid}-h`} className={styles.heading}>各券商最新觀點</h2>
        {rows.length ? (
          <button type="button" className={styles.copy} onClick={copyTable}>複製表格</button>
        ) : null}
      </div>

      {rows.length ? (
        <div className={`${shared.tile} ${styles.frame}`}>
          <div className={`${styles.grid} ${styles.colHead}`} aria-hidden="true">
            <span>券商</span>
            <span>評等</span>
            <span className={styles.right}>目標價</span>
            <span className={styles.right}>EPS</span>
            <span>近期變化</span>
            <span className={styles.right}>最新研報</span>
          </div>
          <ul className={styles.list}>
            {rows.map(b => {
              const key = brokerKey(b)
              const isOpen = openBroker === key
              const panelId = `${uid}-${key}`
              const currency = b.latest_target_currency?.trim() || null
              const offBasis = b.latest_eps_value != null && epsKey != null && epsBasisKey(b) !== epsKey
              const tone = toneOf(b.recent_change_direction)
              return (
                <li key={key} className={styles.item} data-open={isOpen || undefined}>
                  <button
                    type="button"
                    className={`${styles.grid} ${styles.row}`}
                    aria-expanded={isOpen}
                    aria-controls={panelId}
                    data-testid={`broker-row-${key}`}
                    data-broker-row={key}
                    onClick={() => onOpenBrokerChange(isOpen ? null : key)}
                  >
                    <span className={styles.broker}>
                      <ChevronRight size={13} strokeWidth={2} className={styles.chev} aria-hidden="true" />
                      <span className={styles.name}>{brokerName(b)}</span>
                      {b.stale ? <span className={styles.stale}>期間外</span> : null}
                    </span>
                    <span>
                      <span className={shared.srOnly}>{' 評等 '}</span>
                      <span
                        className={`${shared.badge} ${shared[b.latest_rating]}`}
                        title={b.latest_rating_raw || undefined}
                      >
                        {RATING_DISPLAY[b.latest_rating]}
                      </span>
                    </span>
                    <span className={`${styles.right} ${styles.value} ${shared.num}`}>
                      <span className={shared.srOnly}>{' 目標價 '}</span>
                      {b.latest_target_price != null ? (
                        <>
                          <span>{fmtValue(b.latest_target_price)}</span>
                          {currency && currency !== targetCurrency
                            ? <span className={styles.meta}>{currency}</span>
                            : null}
                        </>
                      ) : <span className={styles.na}><span aria-hidden="true">—</span><span className={shared.srOnly}>未提供</span></span>}
                    </span>
                    <span className={`${styles.right} ${styles.value} ${styles.eps} ${shared.num}`}>
                      <span className={shared.srOnly}>{' EPS '}</span>
                      {b.latest_eps_value != null ? (
                        <>
                          <span>{fmtValue(b.latest_eps_value)}</span>
                          {offBasis ? (
                            <span className={styles.meta}>{epsFyLabel(b.latest_eps_fy, b.latest_eps_period)}</span>
                          ) : null}
                        </>
                      ) : <span className={styles.na}><span aria-hidden="true">—</span><span className={shared.srOnly}>未提供</span></span>}
                    </span>
                    <span className={styles.change} data-tone={b.recent_change_label ? tone : 'flat'}>
                      <span className={shared.srOnly}>{' 近期變化 '}</span>
                      <span>{b.recent_change_label || '無變動'}</span>
                    </span>
                    <span className={`${styles.right} ${styles.date} ${shared.num}`}>
                      <span className={shared.srOnly}>{' 最新研報 '}</span>
                      <span>{fmtShortDate(b.latest_report_date)}</span>
                    </span>
                  </button>
                  {isOpen ? (
                    <div id={panelId}>
                      <BrokerHistory
                        code={code}
                        market={market}
                        broker={key}
                        window={window}
                        targetCurrency={targetCurrency}
                        onOpenReport={onOpenReport}
                      />
                    </div>
                  ) : null}
                </li>
              )
            })}
          </ul>
        </div>
      ) : (
        <p className={styles.empty}>還沒有具名券商的觀點</p>
      )}
    </section>
  )
}
