import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { displayTitle } from '../../lib/displayTitle'
import { marketLabel, marketTint } from '../../lib/meta'
import type { Source } from '../../lib/askSchemas'
import styles from './CiteChip.module.css'

// 滑過 0.15 秒後才浮出（交接包「回答」）：掃過一串引用時不會一路閃卡片。
const SHOW_DELAY_MS = 150
const CARD_W = 300
const GAP = 10
// 上方要留得下卡片才往上開（預估高度；實際依標題行數 120–170px）。
const ROOM_ABOVE = 180

interface Props {
  n: number
  source?: Source
  onCite: (n: number) => void
  /** 串流中新出現的膠囊播一次彈出；歷史紀錄與收尾後的重畫不播。 */
  pop: boolean
}

interface Pos { left: number; top?: number; bottom?: number }

/**
 * 引用編號膠囊＋來源預覽卡。點擊交給來源抽屜；預覽卡純展示（不攔滑鼠、aria-hidden），
 * 可及名稱維持編號本身。卡片 portal 到 body、fixed 定位：回答在捲動容器裡，絕對定位會被裁掉。
 * 位置以 left／top（或 bottom）算好再給，不靠 translateX(-50%) 置中——進場的 transform 會蓋掉它
 * （交接包坑 2）。捲動或縮放就收起，免得卡片停在已經捲走的膠囊旁邊。
 */
export function CiteChip({ n, source, onCite, pop }: Props) {
  const btnRef = useRef<HTMLButtonElement>(null)
  const timer = useRef<number | undefined>(undefined)
  const [pos, setPos] = useState<Pos | null>(null)

  useEffect(() => () => window.clearTimeout(timer.current), [])
  useEffect(() => {
    if (!pos) return
    const close = () => setPos(null)
    window.addEventListener('scroll', close, true)
    window.addEventListener('resize', close)
    return () => {
      window.removeEventListener('scroll', close, true)
      window.removeEventListener('resize', close)
    }
  }, [pos])

  const show = () => {
    const el = btnRef.current
    if (!el || !source) return
    const r = el.getBoundingClientRect()
    const left = Math.max(8, Math.min(r.left + r.width / 2 - CARD_W / 2, window.innerWidth - CARD_W - 8))
    setPos(r.top > ROOM_ABOVE
      ? { left, bottom: window.innerHeight - r.top + GAP }
      : { left, top: r.bottom + GAP })
  }
  const schedule = () => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(show, SHOW_DELAY_MS)
  }
  const cancel = () => {
    window.clearTimeout(timer.current)
    setPos(null)
  }

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        className={`${styles.cite} ${pop ? styles.pop : ''}`}
        onClick={() => { cancel(); onCite(n) }}
        onMouseEnter={schedule}
        onMouseLeave={cancel}
        onFocus={schedule}
        onBlur={cancel}
        onKeyDown={e => { if (e.key === 'Escape') cancel() }}
      >
        {n}
      </button>
      {pos && source && createPortal(
        <span
          className={`${styles.card} ${pos.bottom != null ? styles.above : styles.below}`}
          style={{ left: pos.left, top: pos.top, bottom: pos.bottom }}
          aria-hidden="true"
        >
          <span className={styles.top}>
            <span className={styles.mkt} style={marketTint(source.market)}>{marketLabel(source.market)}</span>
            <span className={styles.spacer} />
            {source.report_date && <span className={styles.date}>{source.report_date}</span>}
          </span>
          <span className={styles.title}>{displayTitle(source)}</span>
        </span>,
        document.body,
      )}
    </>
  )
}
