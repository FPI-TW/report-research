import { useEffect } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Icon } from '../../components/primitives/Icon'
import { displayTitle } from '../../lib/displayTitle'
import { marketLabel, marketTint } from '../../lib/meta'
import { TF_DUR, TF_EASE_OUT, tfInstant } from '../../lib/motionTokens'
import type { AnswerView } from '../../lib/askReducer'
import styles from './SourcesDrawer.module.css'

function hostOf(url: string): string {
  try { return new URL(url).hostname } catch { return url }
}

interface Props {
  open: boolean
  view: AnswerView | null
  onClose: () => void
  onOpenReport: (reportId: string, fileName: string) => void
}

export function SourcesDrawer({ open, view, onClose, onOpenReport }: Props) {
  const reduced = useReducedMotion()
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  // 關閉時父層會將 view 設為 null；AnimatePresence 會保留離場當下的子樹快照，故滑出仍以先前內容播放。
  const total = view ? view.sources.length + view.extSources.length : 0

  return (
    <AnimatePresence>
      {open && view && (
        <motion.aside
          key="drawer"
          className={styles.panel}
          aria-label="引用來源"
          initial={{ x: '100%', opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: '100%', opacity: 0 }}
          transition={reduced ? tfInstant : { duration: TF_DUR.d3, ease: TF_EASE_OUT }}
        >
          <div className={styles.header}>
            <span className={styles.title}>引用來源</span>
            <span className={styles.count}>{total}</span>
            <span className={styles.headSpacer} />
            <button type="button" className={styles.close} onClick={onClose} aria-label="關閉"><Icon name="x" size={16} /></button>
          </div>
          <div className={`${styles.body} tf-scroll`}>
            {view.sources.length > 0 && <div className={styles.subhead}>研報 · {view.sources.length}</div>}
            <div className={styles.list}>
              {view.sources.map(s => (
                <button key={`s${s.n}`} type="button" className={styles.srcCard} onClick={() => onOpenReport(s.report_id, displayTitle(s))}>
                  <div className={styles.srcTop}>
                    <span className={styles.numGold}>{s.n}</span>
                    <span className={styles.mkt} style={marketTint(s.market)}>{marketLabel(s.market)}</span>
                    <span className={styles.srcSpacer} />
                    {s.report_date && <span className={styles.srcDate}>{s.report_date}</span>}
                  </div>
                  <div className={styles.srcTitle}>{displayTitle(s)}</div>
                </button>
              ))}
            </div>
            {view.extSources.length > 0 && (
              <>
                <div className={styles.subhead} style={{ marginTop: 14 }}>網路補充 · {view.extSources.length}</div>
                <div className={styles.list}>
                  {view.extSources.map((e, i) => (
                    <a key={`e${i}`} className={styles.extCard} href={e.url} target="_blank" rel="noopener noreferrer">
                      <span className={styles.numOrange}>{view.sources.length + i + 1}</span>
                      <div>
                        <div className={styles.extTitle}>{e.title}</div>
                        <div className={styles.extMeta}>網路 · {hostOf(e.url)}</div>
                      </div>
                    </a>
                  ))}
                </div>
              </>
            )}
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  )
}
