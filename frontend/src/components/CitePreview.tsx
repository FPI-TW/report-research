import { useEffect, useRef, useState } from 'react'
import { marketLabel, marketTint } from '../lib/meta'
import { usePresence } from '../lib/usePresence'
import type { Source } from '../lib/askSchemas'
import styles from './CitePreview.module.css'

interface Props {
  n: number
  source?: Source
  onCite: (n: number) => void
}

interface Pos { top: number; left: number; above: boolean }

const SHOW_DELAY_MS = 150
const HIDE_MS = 150
const CARD_W = 300
const CARD_EST_H = 170
const GAP = 8

/**
 * 引註 [n] 膠囊＋hover／focus 即時來源預覽。
 * - 預覽卡為純展示（pointer-events: none、aria-hidden），互動仍走點擊 → 來源抽屜；
 *   可及名稱維持數字本身（不加 aria-label，與既有測試/讀屏行為一致）。
 * - fixed 定位避免被對話捲動容器裁切；貼近視窗下緣時翻到上方，捲動/縮放即關閉。
 * - 動效與定位解耦：外層只負責 fixed 定位（含上翻 translateY(-100%)），
 *   內層做進場／離場的縮放＋上浮＋淡入；離場經 usePresence 保留位置優雅收起。
 */
export function CiteButton({ n, source, onCite }: Props) {
  const btnRef = useRef<HTMLButtonElement>(null)
  const timer = useRef<number | undefined>(undefined)
  const [pos, setPos] = useState<Pos | null>(null)
  // 離場期間 pos 已為 null，沿用上次位置維持定位不跳動；於事件處理器寫入 state（不在 render 期讀寫 ref）
  const [lastPos, setLastPos] = useState<Pos | null>(null)
  const { isMounted, state } = usePresence(pos !== null, { duration: HIDE_MS })
  const shown = pos ?? lastPos

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
    const p = { top: above ? r.top - GAP : r.bottom + GAP, left, above }
    setLastPos(p)
    setPos(p)
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
      {isMounted && shown && source && (
        <span
          className={styles.card}
          style={shown.above
            ? { top: shown.top, left: shown.left, transform: 'translateY(-100%)' }
            : { top: shown.top, left: shown.left }}
          aria-hidden="true"
        >
          <span className={`${styles.inner} ${shown.above ? styles.above : ''}`} data-state={state}>
            <span className={styles.top}>
              <span className={styles.mkt} style={marketTint(source.market)}>{marketLabel(source.market)}</span>
              {source.report_date && <span className={styles.date}>{source.report_date}</span>}
            </span>
            <span className={styles.title}>{source.file_name}</span>
            <span className={styles.foot}>點擊編號查看引用來源 ›</span>
          </span>
        </span>
      )}
    </button>
  )
}
