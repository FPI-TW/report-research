import { useEffect, useRef, useState } from 'react'
import { marketLabel, marketTint } from '../lib/meta'
import type { Source } from '../lib/askSchemas'
import styles from './CitePreview.module.css'

interface Props {
  n: number
  source?: Source
  onCite: (n: number) => void
}

const SHOW_DELAY_MS = 150
const CARD_W = 300
const CARD_EST_H = 170
const GAP = 8

/**
 * 引註 [n] 膠囊＋hover／focus 即時來源預覽。
 * - 預覽卡為純展示（pointer-events: none、aria-hidden），互動仍走點擊 → 來源抽屜；
 *   可及名稱維持數字本身（不加 aria-label，與既有測試/讀屏行為一致）。
 * - fixed 定位避免被對話捲動容器裁切；貼近視窗下緣時翻到上方，捲動/縮放即關閉。
 */
export function CiteButton({ n, source, onCite }: Props) {
  const btnRef = useRef<HTMLButtonElement>(null)
  const timer = useRef<number | undefined>(undefined)
  const [pos, setPos] = useState<{ top: number; left: number; above: boolean } | null>(null)

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
    const above = r.bottom + GAP + CARD_EST_H > window.innerHeight
    const left = Math.max(8, Math.min(r.left - 16, window.innerWidth - CARD_W - 8))
    setPos({ top: above ? r.top - GAP : r.bottom + GAP, left, above })
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
    <button
      ref={btnRef}
      type="button"
      className="tf-cite"
      onClick={() => { cancel(); onCite(n) }}
      onMouseEnter={schedule}
      onMouseLeave={cancel}
      onFocus={schedule}
      onBlur={cancel}
      onKeyDown={e => { if (e.key === 'Escape') cancel() }}
    >
      {n}
      {pos && source && (
        <span
          className={styles.card}
          style={pos.above
            ? { top: pos.top, left: pos.left, transform: 'translateY(-100%)' }
            : { top: pos.top, left: pos.left }}
          aria-hidden="true"
        >
          <span className={styles.top}>
            <span className={styles.mkt} style={marketTint(source.market)}>{marketLabel(source.market)}</span>
            {source.report_date && <span className={styles.date}>{source.report_date}</span>}
          </span>
          <span className={styles.title}>{source.file_name}</span>
          <span className={styles.foot}>點擊編號查看引用來源 ›</span>
        </span>
      )}
    </button>
  )
}
