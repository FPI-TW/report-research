import { displayTitle } from '../../lib/displayTitle'
import type { Market, Window } from '../../lib/radarSchemas'
import { historyNote } from './changeText'
import { fmtShortDate, fmtValue, RATING_DISPLAY } from './radarFormat'
import { useBrokerHistory } from './useRadar'
import shared from './shared.module.css'
import styles from './BrokerHistory.module.css'

interface Props {
  code: string
  market: Market
  broker: string
  window: Window
  /** 主要幣別；不同幣別的目標價在數字後面補幣別。 */
  targetCurrency: string | null
  onOpenReport: (reportId: string, title?: string | null) => void
}

/**
 * 某家券商對這檔標的的歷程（展開「各券商最新觀點」的一列）：日期、評等、目標價、這份比前一份改了什麼。
 * 最新一筆是金色圓點，其餘灰點；期間外的報告淡一階。每一列都能開啟那份研報。
 */
export function BrokerHistory({ code, market, broker, window, targetCurrency, onOpenReport }: Props) {
  const q = useBrokerHistory(code, broker, market, window, true)

  if (q.isLoading) {
    return (
      <div className={styles.loading} aria-busy="true" data-testid="broker-history-loading">
        {Array.from({ length: 3 }, (_, i) => (
          <span key={i} className={shared.skel} style={{ width: `${88 - i * 14}%`, height: 12 }} />
        ))}
      </div>
    )
  }
  if (q.isError || !q.data) {
    return (
      <div className={styles.error} role="alert">
        歷程載入失敗
        <button type="button" className={styles.retry} onClick={() => void q.refetch()}>重試</button>
      </div>
    )
  }

  const { snapshots, diffs } = q.data
  if (!snapshots.length) return <p className={styles.empty}>沒有歷程資料</p>

  return (
    <ol className={styles.list}>
      {snapshots.map((s, i) => {
        const currency = s.target_currency?.trim() || null
        return (
          <li key={s.report_id} className={styles.item} data-out={!s.in_window || undefined}>
            <button
              type="button"
              className={styles.itemBtn}
              onClick={() => onOpenReport(s.report_link.report_id, displayTitle(s.report_link))}
            >
              <span className={styles.dot} data-latest={i === 0 || undefined} aria-hidden="true" />
              <span className={`${styles.date} ${shared.num}`}>{fmtShortDate(s.report_date)}</span>
              <span><span className={shared.srOnly}>{' 評等 '}</span><span>{RATING_DISPLAY[s.rating]}</span></span>
              <span className={shared.num}>
                <span className={shared.srOnly}>{' 目標價 '}</span>
                <span>{s.target_price != null ? fmtValue(s.target_price) : '—'}</span>
                {s.target_price != null && currency && currency !== targetCurrency
                  ? <span className={styles.ccy}>{currency}</span>
                  : null}
              </span>
              <span className={styles.note}><span className={shared.srOnly}>{' 備註 '}</span><span>{historyNote(diffs[i])}</span></span>
            </button>
          </li>
        )
      })}
    </ol>
  )
}
