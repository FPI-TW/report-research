import { Fragment, useId, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Icon } from '../../components/primitives/Icon'
import { tfInstant, tfTransition } from '../../lib/motionTokens'
import type { BrokerSummary, Market, Window } from '../../lib/radarSchemas'
import { BrokerTimeline } from './BrokerTimeline'
import { DirectionTag } from './DirectionTag'
import {
  fmtDateOrNA, fmtEpsParts, fmtPriceOrNA, NOT_PROVIDED, RATING_BUCKET, RATING_DISPLAY,
} from './radarFormat'
import styles from './BrokerList.module.css'

/**
 * 受控展開（可選）。市場共識摘要的「查看券商觀點」要展開這裡的某一列，而
 * 「哪一列是展開的」只能有一個真相來源——內外各留一份會讓兩個入口互相覆蓋。
 * 不傳時退回元件自己的狀態，單獨使用 BrokerList 的呼叫端不受影響。
 *
 * 兩個欄位**綁成一組**而不是各自 optional：只傳 `openBroker` 而漏了 handler 的話，
 * 展開鈕會完全點不動、而且沒有任何錯誤——用型別把那個組合擋在編譯期。
 */
type OpenControl =
  | { openBroker: string | null, onOpenBrokerChange: (key: string | null) => void }
  | { openBroker?: undefined, onOpenBrokerChange?: undefined }

type Props = OpenControl & {
  brokers: BrokerSummary[]
  code: string
  market: Market
  window: Window
  onOpenReport: (reportId: string, fileName?: string | null) => void
}

export function BrokerList({
  brokers, code, market, window, onOpenReport, openBroker, onOpenBrokerChange,
}: Props) {
  const [uncontrolledOpen, setUncontrolledOpen] = useState<string | null>(null)
  const open = openBroker !== undefined ? openBroker : uncontrolledOpen
  // 面板 id 的前綴。桌機表格與手機卡片是兩份各自渲染的 DOM，若同一頁出現兩個
  // BrokerList（或 id 只由 broker key 組成），aria-controls 會指到別人的面板。
  const uid = useId()
  const reduced = useReducedMotion()
  const transition = reduced ? tfInstant : tfTransition
  const attributedBrokers = brokers.filter(broker => Boolean(broker.broker?.trim()))

  function toggle(key: string) {
    const next = open === key ? null : key
    if (openBroker !== undefined) onOpenBrokerChange?.(next)
    else setUncontrolledOpen(next)
  }

  if (!attributedBrokers.length) {
    return <div className={styles.dash}>尚無券商清單</div>
  }

  /** 展開面板的高度過渡。收合也要有，所以外層必須是 AnimatePresence 而非條件渲染。 */
  const panelMotion = {
    initial: { height: 0, opacity: 0 },
    animate: { height: 'auto' as const, opacity: 1 },
    exit: { height: 0, opacity: 0 },
    transition,
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
              const panelId = `${uid}-table-${key}`
              const eps = fmtEpsParts(
                b.latest_eps_value,
                b.latest_eps_currency,
                b.latest_eps_fy,
                b.latest_eps_period,
                b.latest_eps_unit,
              )
              return (
                <Fragment key={key}>
                  {/* 整列**不可點**。這一列唯一的展開／收合控制項是右端那顆 <button>：
                      多一個入口就多一份狀態要同步，而 <tr> 既不可聚焦、也沒有 aria-expanded，
                      鍵盤與輔助技術使用者拿到的仍是那顆鈕——兩者不會是同一個「控制項」。
                      hover 底色留著，它是掃視表格時的追列輔助，不是「整列可點」的承諾。 */}
                  <tr className={`${styles.row} ${isOpen ? styles.rowOpen : ''}`}>
                    <td>
                      <div className={styles.broker}>
                        <span>{b.broker_display || b.broker || NOT_PROVIDED}</span>
                        {b.stale ? <span className={styles.stale}>窗外最新</span> : null}
                      </div>
                    </td>
                    <td>
                      {/* 顏色掛在 <span> 而不是 <td>：.table td 自帶 color，特異度 (0,1,1)
                          會贏過 (0,1,0) 的 .bull/.bear，掛在儲存格上等於整組語意色靜默失效
                          （本 repo 已經有 .mvNet b 那個一模一樣的案底）。 */}
                      <span className={`${styles.rating} ${styles[RATING_BUCKET[b.latest_rating]]}`}>
                        {b.latest_rating_raw || RATING_DISPLAY[b.latest_rating]}
                      </span>
                    </td>
                    <td className={styles.num}>
                      {b.latest_target_price != null
                        ? fmtPriceOrNA(b.latest_target_price, b.latest_target_currency)
                        : <span className={styles.na}>{NOT_PROVIDED}</span>}
                    </td>
                    {/* 主值與比較群組標籤拆成兩級：整格同字級時「US$5」與「FY27E」一樣重，
                        而後者是群組註記、不是要一眼讀的數字。
                        兩段串起來仍逐字等於 fmtEps（fmtEpsParts 的契約）。 */}
                    <td className={`${styles.num} ${styles.epsCell}`} data-testid={`broker-eps-${key}-desktop`}>
                      {b.latest_eps_value != null ? (
                        <>
                          {eps.value}
                          <span className={styles.epsMeta}>{eps.meta}</span>
                        </>
                      ) : (
                        <span className={styles.na}>{NOT_PROVIDED}</span>
                      )}
                    </td>
                    <td>
                      {/* 後端已把變化組成一句話（「上調評等：中立 → 買進」／「目標價上修 6.3%」），
                          DirectionTag 再補上方向圖示——文字與圖示各自成立，不靠顏色表意。 */}
                      {b.recent_change_label ? (
                        <DirectionTag
                          direction={b.recent_change_direction}
                          label={b.recent_change_label}
                        />
                      ) : (
                        <span className={styles.na}>{NOT_PROVIDED}</span>
                      )}
                    </td>
                    <td className={styles.dateCell}>{fmtDateOrNA(b.latest_report_date)}</td>
                    <td>
                      <button
                        type="button"
                        className={styles.expandButton}
                        aria-expanded={isOpen}
                        aria-controls={panelId}
                        aria-label={`${isOpen ? '收合' : '展開'} ${b.broker_display || key} 歷程`}
                        data-testid={`broker-row-${key}`}
                        onClick={() => toggle(key)}
                      >
                        <motion.span
                          style={{ display: 'inline-flex' }}
                          animate={{ rotate: isOpen ? 180 : 0 }}
                          transition={transition}
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
                  {/* 高度過渡做在 <td> 內層的 div 上，不是 <tr> 上：display: table-row
                      的高度動畫在各家瀏覽器都不可靠。<tr> 只負責留在 DOM 裡撐到退場結束。 */}
                  <AnimatePresence initial={false}>
                    {isOpen ? (
                      <motion.tr key="panel">
                        <td colSpan={7} className={styles.panelCell}>
                          <motion.div id={panelId} className={styles.panelInner} {...panelMotion}>
                            <BrokerTimeline
                              code={code}
                              market={market}
                              broker={key}
                              brokerDisplay={b.broker_display}
                              window={window}
                              expanded
                              onOpenReport={onOpenReport}
                            />
                          </motion.div>
                        </td>
                      </motion.tr>
                    ) : null}
                  </AnimatePresence>
                </Fragment>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* ≤900px 的卡片版。六欄表格在手機上會被壓成逐字直排（中文的 min-content 是一個字），
          所以這裡不是「同一張表縮小」，而是把每一欄改成看得懂的標籤／值配對。 */}
      <div className={styles.cards}>
        {attributedBrokers.map(b => {
          const key = b.broker!.trim()
          const isOpen = open === key
          const panelId = `${uid}-card-${key}`
          const eps = fmtEpsParts(
            b.latest_eps_value,
            b.latest_eps_currency,
            b.latest_eps_fy,
            b.latest_eps_period,
            b.latest_eps_unit,
          )
          return (
            <div key={key} className={styles.card}>
              {/* 資料**不進 <button>**。button 在無障礙樹裡是葉節點，子樹只會被壓成
                  可及名稱，而 ≤900px 時桌機那份有語意的 <td> 是 display: none、整份離開
                  無障礙樹——把目標價／EPS／報告日期包在鈕裡，等於窄視窗使用者的券商清單
                  只剩一串「大和 · 買進，按鈕，已收合」。
                  同理刻意不加 aria-label：它會連券商名與評等一起蓋掉，只留「展開…歷程」。
                  按下去會發生什麼由 role ＋ aria-expanded 交代，這與同 feature 的
                  BrokerTimeline HistoryItem 是同一個決定。
                  刻意不補 data-testid：桌機那份已經有 broker-row-${key}，兩份同 id 會讓
                  getByTestId 拋「找到多個元素」。 */}
              <button
                type="button"
                className={styles.cardHead}
                aria-expanded={isOpen}
                aria-controls={panelId}
                onClick={() => toggle(key)}
              >
                <span className={styles.cardName}>
                  {b.broker_display || b.broker || NOT_PROVIDED}
                  {' · '}
                  <span className={styles[RATING_BUCKET[b.latest_rating]]}>
                    {b.latest_rating_raw || RATING_DISPLAY[b.latest_rating]}
                  </span>
                  {/* 桌機版有、手機版原本沒有：stale 代表這家券商在所選窗期內沒有訊號，
                      顯示的是窗外最近一筆。窗期正是這頁的語意核心，少了它，同一家券商
                      在寬窄兩種螢幕上講的是不同的話——這不是排版退化，是資料語意被靜默刪掉。 */}
                  {b.stale ? <span className={styles.stale}>窗外最新</span> : null}
                </span>
                <motion.span
                  style={{ display: 'inline-flex' }}
                  animate={{ rotate: isOpen ? 180 : 0 }}
                  transition={transition}
                >
                  <Icon
                    name="chevronDown"
                    size={18}
                    className={`${styles.expand} ${isOpen ? styles.expandOpen : ''}`}
                  />
                </motion.span>
              </button>
              {/* 每一格各帶 data-testid：標籤文字（目標價／報告日期）與桌機的 <th> 逐字
                  相同，而 jsdom 不求值 media query ⇒ 兩份 DOM 同時存在，全域 getByText
                  會被表頭滿足，那種斷言刪掉手機這一格也不會紅。 */}
              <dl className={styles.cardFields}>
                <div className={styles.cardField} data-testid={`broker-change-${key}-mobile`}>
                  <dt>最近變化</dt>
                  <dd>
                    {b.recent_change_label ? (
                      <DirectionTag
                        direction={b.recent_change_direction}
                        label={b.recent_change_label}
                      />
                    ) : (
                      <span className={styles.na}>{NOT_PROVIDED}</span>
                    )}
                  </dd>
                </div>
                <div className={styles.cardField} data-testid={`broker-target-${key}-mobile`}>
                  <dt>目標價</dt>
                  <dd>{fmtPriceOrNA(b.latest_target_price, b.latest_target_currency)}</dd>
                </div>
                <div className={styles.cardField} data-testid={`broker-eps-${key}-mobile`}>
                  <dt>EPS</dt>
                  <dd>
                    {b.latest_eps_value != null ? (
                      <>
                        {eps.value}
                        <span className={styles.epsMeta}>{eps.meta}</span>
                      </>
                    ) : NOT_PROVIDED}
                  </dd>
                </div>
                <div className={styles.cardField} data-testid={`broker-date-${key}-mobile`}>
                  <dt>報告日期</dt>
                  <dd>{fmtDateOrNA(b.latest_report_date)}</dd>
                </div>
              </dl>
              <AnimatePresence initial={false}>
                {isOpen ? (
                  <motion.div
                    key="panel"
                    id={panelId}
                    className={styles.panelInner}
                    {...panelMotion}
                  >
                    <BrokerTimeline
                      code={code}
                      market={market}
                      broker={key}
                      brokerDisplay={b.broker_display}
                      window={window}
                      expanded
                      onOpenReport={onOpenReport}
                    />
                  </motion.div>
                ) : null}
              </AnimatePresence>
            </div>
          )
        })}
      </div>
    </>
  )
}
