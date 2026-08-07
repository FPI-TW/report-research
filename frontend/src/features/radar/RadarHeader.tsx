import { Icon } from '../../components/primitives/Icon'
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
        {/* 標的名只在大標題捲掉之後補進來，而且是**接在左側叢集後面**——舊版把它做成
            `flex:1` 置中的獨立元素，於是代碼在最左、名字浮在正中間，同一個識別被拆成
            相距數百像素的兩塊。接在後面則是往右長，右邊的窗期切換一格都不會動。
            這個條件也順便解掉重複：沒捲時大標題已經在講「儒鴻 1476」，麵包屑不必再講一次。 */}
        <nav className={styles.crumb} aria-label="麵包屑">
          <Pressable type="button" className={styles.crumbBtn} onClick={onBack}>
            <Icon name="chevronDown" size={14} className={styles.crumbIcon} />
            券商觀點
          </Pressable>
          <span className={styles.sep} aria-hidden="true">/</span>
          {/* 市場用色票而不是再一層純文字：它同時是路徑的一層與這條列上唯一的顏色，
              而且與清單頁表格的市場徽章同一套語言，兩頁之間看得出是同一個東西。 */}
          <span className={styles.crumbBadge} style={marketVar(market)}>
            {marketDisplay || market}
          </span>
          {scrolled ? (
            <>
              <span className={styles.current} aria-current="page">{name || code}</span>
              {name ? <span className={styles.currentCode}>{code}</span> : null}
            </>
          ) : null}
        </nav>
        <WindowSegmented value={window} onChange={onWindowChange} />
      </div>

      <header className={styles.wrap}>
        {/* 市場色票不在這裡再放一顆：麵包屑那一顆就在正上方 40px 處，同一個標的的
            名稱、代碼、市場在同一屏出現兩次是版面在重複、不是在強調。 */}
        <div className={styles.titleRow}>
          <h1 className={styles.title}>{name || code}</h1>
          <span className={styles.code}>{code}</span>
        </div>

        {/* 切窗期／換標的時整頁內容被換掉，但畫面上沒有任何東西告訴螢幕閱讀器「資料變了」。
            這一行本來就會跟著改（家數、份數、資料截止日、窗期），把它宣告成 polite 的
            即時區域，等於用既有內容當更新通知，不必另外塞一段只給輔助技術聽的隱藏文字。 */}
        <p className={styles.meta} aria-live="polite">
          {brokers != null ? `${brokers} 家券商` : '—'}
          {reports != null ? ` · ${reports} 份可用研報` : ''}
          {asOf ? ` · 資料截至 ${fmtDate(asOf)}` : ''}
          {window ? ` · ${WINDOW_LABEL[window] ?? window}` : ''}
        </p>
      </header>
    </>
  )
}
