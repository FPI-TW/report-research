import { Fragment, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { Icon } from '../../components/primitives/Icon'
import { tfInstant, tfTransition } from '../../lib/motionTokens'
import type { BrokerSummary, Window } from '../../lib/radarSchemas'
import { BrokerTimeline } from './BrokerTimeline'
import { DirectionTag } from './DirectionTag'
import { fmtDate, fmtPrice, RATING_DISPLAY } from './radarFormat'
import styles from './BrokerList.module.css'

interface Props {
  brokers: BrokerSummary[]
  code: string
  market: string
  window: Window
  onOpenReport: (reportId: string, fileName?: string | null) => void
}

export function BrokerList({ brokers, code, market, window, onOpenReport }: Props) {
  const [open, setOpen] = useState<string | null>(null)
  const reduced = useReducedMotion()

  function toggle(key: string) {
    setOpen(prev => (prev === key ? null : key))
  }

  if (!brokers.length) {
    return <div className={styles.dash}>尚無券商清單</div>
  }

  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>券商</th>
              <th>最新評等</th>
              <th className={styles.num}>目標價</th>
              <th className={styles.num}>EPS</th>
              <th>最近變化</th>
              <th>報告日期</th>
              <th aria-label="展開" />
            </tr>
          </thead>
          <tbody>
            {brokers.map(b => {
              const key = b.broker || b.broker_display || b.latest_report_date
              const isOpen = open === key
              return (
                <Fragment key={key}>
                  <tr
                    className={`${styles.row} ${isOpen ? styles.rowOpen : ''}`}
                    tabIndex={0}
                    role="button"
                    aria-expanded={isOpen}
                    aria-label={`展開 ${b.broker_display || b.broker || '券商'} 歷程`}
                    data-testid={`broker-row-${b.broker || key}`}
                    onClick={() => toggle(key)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        toggle(key)
                      }
                    }}
                  >
                    <td>
                      <div className={styles.broker}>
                        <span>{b.broker_display || b.broker || '—'}</span>
                        {b.stale ? <span className={styles.stale}>窗外最新</span> : null}
                      </div>
                    </td>
                    <td>{b.latest_rating_raw || RATING_DISPLAY[b.latest_rating]}</td>
                    <td className={styles.num}>
                      {b.latest_target_price != null
                        ? fmtPrice(b.latest_target_price, b.latest_target_currency)
                        : <span className={styles.dash}>—</span>}
                    </td>
                    <td className={styles.num}>
                      {b.latest_eps_value != null
                        ? `${fmtPrice(b.latest_eps_value, b.latest_target_currency)}${b.latest_eps_fy ? ` FY${b.latest_eps_fy}` : ''}`
                        : <span className={styles.dash}>—</span>}
                    </td>
                    <td>
                      {b.recent_change_label ? (
                        <DirectionTag
                          direction={b.recent_change_direction}
                          label={b.recent_change_label}
                        />
                      ) : (
                        <span className={styles.dash}>—</span>
                      )}
                    </td>
                    <td>{fmtDate(b.latest_report_date)}</td>
                    <td>
                      <motion.span
                        style={{ display: 'inline-flex' }}
                        animate={{ rotate: isOpen ? 180 : 0 }}
                        transition={reduced ? tfInstant : tfTransition}
                      >
                        <Icon
                          name="chevronDown"
                          size={16}
                          className={`${styles.expand} ${isOpen ? styles.expandOpen : ''}`}
                        />
                      </motion.span>
                    </td>
                  </tr>
                  {isOpen && b.broker ? (
                    <tr>
                      <td colSpan={7} className={styles.panelCell}>
                        <BrokerTimeline
                          code={code}
                          market={market}
                          broker={b.broker}
                          brokerDisplay={b.broker_display}
                          window={window}
                          expanded
                          onCollapse={() => setOpen(null)}
                          onOpenReport={onOpenReport}
                        />
                      </td>
                    </tr>
                  ) : null}
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      <div className={styles.cards}>
        {brokers.map(b => {
          const key = b.broker || b.broker_display || b.latest_report_date
          const isOpen = open === key
          return (
            <div key={key} className={styles.card}>
              <button
                type="button"
                className={styles.cardHead}
                aria-expanded={isOpen}
                onClick={() => toggle(key)}
              >
                <div className={styles.cardMain}>
                  <div className={styles.cardName}>
                    {b.broker_display || b.broker || '—'}
                    {' · '}
                    {b.latest_rating_raw || RATING_DISPLAY[b.latest_rating]}
                  </div>
                  <div className={styles.cardMeta}>
                    <span>
                      目標價{' '}
                      {b.latest_target_price != null
                        ? fmtPrice(b.latest_target_price, b.latest_target_currency)
                        : '—'}
                    </span>
                    <span>
                      EPS{' '}
                      {b.latest_eps_value != null ? fmtPrice(b.latest_eps_value, null) : '—'}
                    </span>
                    <span>{fmtDate(b.latest_report_date)}</span>
                  </div>
                  {b.recent_change_label ? (
                    <div className={styles.cardChange}>
                      <DirectionTag
                        direction={b.recent_change_direction}
                        label={b.recent_change_label}
                      />
                    </div>
                  ) : null}
                </div>
                <motion.span
                  style={{ display: 'inline-flex' }}
                  animate={{ rotate: isOpen ? 180 : 0 }}
                  transition={reduced ? tfInstant : tfTransition}
                >
                  <Icon
                    name="chevronDown"
                    size={18}
                    className={`${styles.expand} ${isOpen ? styles.expandOpen : ''}`}
                  />
                </motion.span>
              </button>
              {isOpen && b.broker ? (
                <BrokerTimeline
                  code={code}
                  market={market}
                  broker={b.broker}
                  brokerDisplay={b.broker_display}
                  window={window}
                  expanded
                  onCollapse={() => setOpen(null)}
                  onOpenReport={onOpenReport}
                />
              ) : null}
            </div>
          )
        })}
      </div>
    </>
  )
}
