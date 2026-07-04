import { useEffect } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { marketColor, marketLabel } from '../../lib/meta'
import type { Turn } from '../../lib/askReducer'
import styles from './SourcesDrawer.module.css'

function hostOf(url: string): string {
  try { return new URL(url).hostname } catch { return url }
}

interface Props {
  open: boolean
  turn: Turn | null
  onClose: () => void
  onOpenReport: (reportId: string, fileName: string) => void
}

export function SourcesDrawer({ open, turn, onClose, onOpenReport }: Props) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open || !turn) return null
  const total = turn.sources.length + turn.extSources.length

  return (
    <aside className={styles.panel} aria-label="引用來源">
      <div className={styles.header}>
        <span className={styles.title}>引用來源</span>
        <button type="button" className={styles.close} onClick={onClose} aria-label="關閉"><Icon name="x" size={17} /></button>
      </div>
      <div className={`${styles.body} tf-scroll`}>
        <div className={styles.subhead}>資料來源 · {total}</div>
        <div className={styles.list}>
          {turn.sources.map(s => (
            <button key={`s${s.n}`} type="button" className={styles.srcCard} onClick={() => onOpenReport(s.report_id, s.file_name)}>
              <span className={styles.numGold}>{s.n}</span>
              <div className={styles.srcMain}>
                <span className={styles.mkt} style={{ background: marketColor(s.market) }}>{marketLabel(s.market)}</span>
                <div className={styles.srcTitle}>{s.file_name}</div>
                {s.report_date && <div className={styles.srcMeta}>{s.report_date}</div>}
              </div>
            </button>
          ))}
          {turn.extSources.map((e, i) => (
            <a key={`e${i}`} className={styles.extCard} href={e.url} target="_blank" rel="noopener noreferrer">
              <span className={styles.numOrange}>{turn.sources.length + i + 1}</span>
              <div>
                <div className={styles.extTitle}>{e.title}</div>
                <div className={styles.extMeta}>網路 · {hostOf(e.url)}</div>
              </div>
            </a>
          ))}
        </div>
      </div>
    </aside>
  )
}
