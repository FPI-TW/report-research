import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { ArrowRight, ArrowUpRight, X } from 'lucide-react'
import { marketLabel, marketTint } from '../../lib/meta'
import type { AnswerView } from '../../lib/askReducer'
import { sourceItems } from './sourceItems'
import styles from './SourcesDrawer.module.css'

// 捲到卡片時與抽屜上下緣留的距離。
const EDGE = 8

interface Props {
  open: boolean
  /** 關閉後仍保留上一份內容，滑出動畫期間畫面不會先變空。 */
  view: AnswerView | null
  /** 要捲進視野並閃一下金框的那一則（null＝只開抽屜）。 */
  active: number | null
  /** 每次點引用都 +1：同一則再點一次也要重新捲動與閃框。 */
  nonce: number
  onClose: () => void
  /** 「在閱讀頁開啟」：換到閱讀頁網址並導過去（失敗由呼叫端提示）。 */
  onOpenReport: (reportId: string) => Promise<void>
}

/**
 * 引用來源抽屜（交接包「引用來源抽屜」）：右側 372px 浮層，常駐 DOM、以 transform 滑入滑出；
 * 關閉時 inert，鍵盤與輔助技術都碰不到。
 *
 * 捲到指定卡片**只改抽屜捲動區的 scrollTop**，不用 scrollIntoView：後者會連外層一起捲，
 * 抽屜還在畫面外滑入時，整個版面會被往左拉（交接包坑 1）。
 */
export function SourcesDrawer({ open, view, active, nonce, onClose, onOpenReport }: Props) {
  const bodyRef = useRef<HTMLDivElement>(null)
  const [pending, setPending] = useState<number | null>(null)

  // Esc 由最上層往下關：搜尋、設定等彈窗（Radix）處理掉的 Esc 會 preventDefault，這裡就不再關抽屜。
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape' && !e.defaultPrevented) onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  useLayoutEffect(() => {
    if (!open || active == null) return
    const box = bodyRef.current
    const card = box?.querySelector<HTMLElement>(`[data-n="${active}"]`)
    if (!box || !card) return
    const cr = card.getBoundingClientRect()
    const br = box.getBoundingClientRect()
    let dy = 0
    if (cr.top < br.top + EDGE) dy = cr.top - br.top - EDGE
    else if (cr.bottom > br.bottom - EDGE) dy = Math.min(cr.bottom - br.bottom + EDGE, cr.top - br.top - EDGE)
    if (!dy) return
    const top = box.scrollTop + dy
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (typeof box.scrollTo === 'function') box.scrollTo({ top, behavior: reduce ? 'auto' : 'smooth' })
    else box.scrollTop = top
  }, [open, active, nonce, view])

  const items = view ? sourceItems(view) : []
  const flash = nonce % 2 ? styles.flashA : styles.flashB

  const openReport = (n: number, reportId: string) => {
    if (pending != null) return
    setPending(n)
    void onOpenReport(reportId).finally(() => setPending(null))
  }

  return (
    <aside
      className={`${styles.panel} ${open ? styles.panelOn : ''}`}
      aria-label="引用來源"
      aria-hidden={!open}
      inert={!open}
    >
      <div className={styles.header}>
        <h2 className={styles.title}>引用來源</h2>
        <span className={styles.count}>{items.length}</span>
        <span className={styles.spacer} />
        <button type="button" className={styles.close} onClick={onClose} aria-label="關閉">
          <X size={15} strokeWidth={1.8} aria-hidden />
        </button>
      </div>
      <div ref={bodyRef} className={`${styles.body} tf-scroll`}>
        {items.map((s, i) => (
          <article
            key={`${s.kind}${s.n}`}
            data-n={s.n}
            className={`${styles.card} ${s.n === active ? flash : ''}`}
            style={{ animationDelay: s.n === active ? undefined : `${Math.min(i, 6) * 0.04}s` }}
          >
            <div className={styles.top}>
              <span className={styles.num}>{s.n}</span>
              {s.kind === 'report'
                ? <span className={styles.mkt} style={marketTint(s.market)}>{marketLabel(s.market)}</span>
                : <span className={`${styles.mkt} ${styles.webTag}`}>網路</span>}
              {s.kind === 'web' && <span className={styles.host}>{s.host}</span>}
              <span className={styles.spacer} />
              {s.kind === 'report' && s.date && <span className={styles.date}>{s.date}</span>}
            </div>
            <h3 className={styles.cardTitle}>{s.title}</h3>
            {s.kind === 'report' ? (
              <button
                type="button"
                className={styles.lk}
                onClick={() => openReport(s.n, s.reportId)}
                aria-busy={pending === s.n || undefined}
                disabled={pending != null && pending !== s.n}
              >
                在閱讀頁開啟<ArrowRight size={13} strokeWidth={2} aria-hidden />
              </button>
            ) : (
              <a className={styles.lk} href={s.url} target="_blank" rel="noopener noreferrer">
                開啟網頁<ArrowUpRight size={13} strokeWidth={2} aria-hidden />
              </a>
            )}
          </article>
        ))}
      </div>
    </aside>
  )
}
