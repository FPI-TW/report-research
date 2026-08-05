import { Fragment, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { Icon } from '../../components/primitives/Icon'
import { tfInstant, tfTransition } from '../../lib/motionTokens'
import type { BrokerSummary, Market, Window } from '../../lib/radarSchemas'
import { BrokerTimeline } from './BrokerTimeline'
import { DirectionTag } from './DirectionTag'
import { fmtDate, fmtEps, fmtPrice, RATING_DISPLAY } from './radarFormat'
import styles from './BrokerList.module.css'

interface Props {
  brokers: BrokerSummary[]
  code: string
  market: Market
  window: Window
  onOpenReport: (reportId: string, fileName?: string | null) => void
}

export function BrokerList({ brokers, code, market, window, onOpenReport }: Props) {
  const [open, setOpen] = useState<string | null>(null)
  const reduced = useReducedMotion()
  const attributedBrokers = brokers.filter(broker => Boolean(broker.broker?.trim()))

  function toggle(key: string) {
    setOpen(prev => (prev === key ? null : key))
  }

  if (!attributedBrokers.length) {
    return <div className={styles.dash}>尚無券商清單</div>
  }

  return (
    <>
      <div className={styles.tableWrap}>
        {/* scope 是必要的而非加固：資料列之間插了 <tr><td colSpan={7}> 的展開面板，
            瀏覽器在這種結構下對「thead 的 th 是欄標頭」的推斷並不可靠。
            最後一欄拿掉 aria-label——按鈕自己已帶完整名稱（「展開 大和 歷程」），
            欄名再叫「展開」會讓儲存格導覽唸成「展開，展開 大和 歷程」。 */}
        <table className={styles.table} aria-label="各券商最新觀點">
          <thead>
            <tr>
              <th scope="col">券商</th>
              <th scope="col">最新評等</th>
              <th scope="col" className={styles.num}>目標價</th>
              <th scope="col" className={styles.num}>EPS</th>
              <th scope="col">最近變化</th>
              <th scope="col">報告日期</th>
              <th scope="col" />
            </tr>
          </thead>
          <tbody>
            {attributedBrokers.map(b => {
              const key = b.broker!.trim()
              const isOpen = open === key
              return (
                <Fragment key={key}>
                  <tr
                    className={`${styles.row} ${isOpen ? styles.rowOpen : ''}`}
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
                    <td className={`${styles.num} ${styles.epsCell}`} data-testid={`broker-eps-${key}-desktop`}>
                      {b.latest_eps_value != null
                        ? fmtEps(
                            b.latest_eps_value,
                            b.latest_eps_currency,
                            b.latest_eps_fy,
                            b.latest_eps_period,
                            b.latest_eps_unit,
                          )
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
                      <button
                        type="button"
                        className={styles.expandButton}
                        aria-expanded={isOpen}
                        aria-label={`${isOpen ? '收合' : '展開'} ${b.broker_display || key} 歷程`}
                        data-testid={`broker-row-${key}`}
                        onClick={() => toggle(key)}
                      >
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
                      </button>
                    </td>
                  </tr>
                  {isOpen ? (
                    <tr>
                      <td colSpan={7} className={styles.panelCell}>
                        <BrokerTimeline
                          code={code}
                          market={market}
                          broker={key}
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
        {attributedBrokers.map(b => {
          const key = b.broker!.trim()
          const isOpen = open === key
          return (
            <div key={key} className={styles.card}>
              {/* aria-label 覆蓋掉子樹串接：沒有它，這顆鈕的可及名稱會變成
                  「大和 · 買進 目標價 NT$2,444 EPS — 2026/07/11 目標價上修 6.3%」一長串，
                  完全沒有一個字說明按下去會展開歷程（aria-expanded 只唸得出「已收合」）。
                  桌機分支本來就有正確標籤，這裡是漏改。
                  刻意不補 data-testid：桌機那份已經有 broker-row-${key}，兩份同 id 會讓
                  getByTestId 拋「找到多個元素」。 */}
              <button
                type="button"
                className={styles.cardHead}
                aria-expanded={isOpen}
                aria-label={`${isOpen ? '收合' : '展開'} ${b.broker_display || key} 歷程`}
                onClick={() => toggle(key)}
              >
                <div className={styles.cardMain}>
                  <div className={styles.cardName}>
                    {b.broker_display || b.broker || '—'}
                    {' · '}
                    {b.latest_rating_raw || RATING_DISPLAY[b.latest_rating]}
                  </div>
                  <div className={styles.cardMeta}>
                    {/* 桌機版有、手機版原本沒有：stale 代表這家券商在所選窗期內沒有訊號，
                        顯示的是窗外最近一筆。窗期正是這頁的語意核心，少了它，同一家券商
                        在寬窄兩種螢幕上講的是不同的話——這不是排版退化，是資料語意被靜默刪掉。
                        放進 .cardMeta（flex-wrap）而非 .cardName 之後，才不會多佔一行。 */}
                    {b.stale ? <span className={styles.stale}>窗外最新</span> : null}
                    <span>
                      目標價{' '}
                      {b.latest_target_price != null
                        ? fmtPrice(b.latest_target_price, b.latest_target_currency)
                        : '—'}
                    </span>
                    <span data-testid={`broker-eps-${key}-mobile`}>
                      EPS{' '}
                      {fmtEps(
                        b.latest_eps_value,
                        b.latest_eps_currency,
                        b.latest_eps_fy,
                        b.latest_eps_period,
                        b.latest_eps_unit,
                      )}
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
              {isOpen ? (
                <BrokerTimeline
                  code={code}
                  market={market}
                  broker={key}
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
