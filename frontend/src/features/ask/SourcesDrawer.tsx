import { useEffect } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { marketLabel, marketTint } from '../../lib/meta'
import { usePresence } from '../../lib/usePresence'
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
  const { isMounted, state } = usePresence(open, { duration: 240 })
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!isMounted || !view) return null
  const total = view.sources.length + view.extSources.length

  return (
    <aside className={styles.panel} data-state={state} aria-label="引用來源">
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
            <button key={`s${s.n}`} type="button" className={styles.srcCard} onClick={() => onOpenReport(s.report_id, s.file_name)}>
              <div className={styles.srcTop}>
                <span className={styles.numGold}>{s.n}</span>
                <span className={styles.mkt} style={marketTint(s.market)}>{marketLabel(s.market)}</span>
                <span className={styles.srcSpacer} />
                {s.report_date && <span className={styles.srcDate}>{s.report_date}</span>}
              </div>
              <div className={styles.srcTitle}>{s.file_name}</div>
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
    </aside>
  )
}
