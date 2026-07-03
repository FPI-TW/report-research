import { marketColor, marketLabel, instrumentLabel } from '../../lib/meta'
import { highlight } from '../../lib/highlight'
import type { ReportRow } from '../../lib/schemas'
import type { SearchMode } from '../../lib/searchFilters'
import styles from './ResultCard.module.css'

interface Props {
  row: ReportRow
  mode: SearchMode
  isLatest: boolean
  terms: string[]
  onOpen: (id: string, fileName: string) => void
}

function truncate(s: string | null, n: number): string {
  if (!s) return ''
  return s.length > n ? s.slice(0, n) + '…' : s
}

export function ResultCard({ row, mode, isLatest, terms, onOpen }: Props) {
  const targets = [...(row.stock_targets ?? []), ...(row.futures_targets ?? [])]
  const pills = [...(row.instrument_types ?? []).map(instrumentLabel), ...targets]
  const date = (row.report_date ?? '').slice(0, 10)
  const open = () => onOpen(row.report_id, row.file_name)
  const pct = mode === 'search'
    ? Math.max(4, Math.min(100, Math.round((row.best_score ?? 0) * 100)))
    : 0
  const snippet = row.passages?.[0]?.content ?? ''

  return (
    <div
      className={styles.card}
      role="button"
      tabIndex={0}
      aria-label={row.file_name}
      onClick={open}
      onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open() } }}
    >
      <div className={styles.top}>
        <span className={styles.badge} style={{ background: marketColor(row.market ?? '') }}>
          {marketLabel(row.market ?? '')}
        </span>
        {isLatest && <span className={styles.latest}>最新</span>}
      </div>
      <div className={styles.title}>{row.file_name}</div>
      {pills.length > 0 && (
        <div className={styles.tags}>
          {pills.map((t, i) => <span key={i} className={styles.tag}>{t}</span>)}
        </div>
      )}
      {mode === 'search' ? (
        <>
          {snippet && (
            <div className={styles.snippet}>{highlight(snippet.slice(0, 300), terms)}{snippet.length > 300 ? '…' : ''}</div>
          )}
          <div className={styles.scoreRow}>
            <div className={styles.scoreTrack}>
              <div className={styles.scoreFill} style={{ width: `${pct}%` }} />
            </div>
            <span className={styles.scoreNum}>相關度 {pct}</span>
          </div>
        </>
      ) : (
        row.summary && <div className={styles.summary}>{truncate(row.summary, 88)}</div>
      )}
      <div className={styles.footer}>
        <span>{[row.source, date].filter(Boolean).join(' · ')}</span>
        <span className={styles.cta}>查看全文 ›</span>
      </div>
    </div>
  )
}
