import { useId, useState } from 'react'
import { Link } from 'react-router'
import { ChevronDown } from 'lucide-react'
import type { BriefReportRef } from '../../lib/briefSchemas'
import { displayTitle } from '../../lib/displayTitle'
import { marketColor } from '../../lib/meta'
import { OTHER_COLOR } from './briefContent'
import { monthDay } from './briefDates'
import styles from './BriefSources.module.css'

// 收合時列幾篇（設計稿）。
const COLLAPSED = 6

interface Props {
  reports: readonly BriefReportRef[]
  /** 窗期內的實際篇數，可能大於 reports（prompt 有上限）。 */
  reportCount: number
  className?: string
}

export function BriefSources({ reports, reportCount, className }: Props) {
  const [open, setOpen] = useState(false)
  const listId = useId()
  const hidden = Math.max(0, reports.length - COLLAPSED)
  const shown = open ? reports : reports.slice(0, COLLAPSED)
  // 差額一定要說：prompt 有篇數上限，靜默只列一部分會讓讀者以為那天只有這幾篇。
  const unlisted = Math.max(0, reportCount - reports.length)

  return (
    <section className={[className, styles.sources].filter(Boolean).join(' ')} aria-labelledby="brief-src">
      <div className={styles.head}>
        <h2 id="brief-src" className={styles.label}>本期來源研報</h2>
        <span className={styles.count}>{reportCount}</span>
      </div>
      <ul id={listId} className={styles.list}>
        {shown.map(r => (
          <li key={r.report_id}>
            <Link className={styles.item} to={`/report/${r.file_hash}`}>
              <span
                className={styles.dot}
                aria-hidden="true"
                style={{ background: r.market ? marketColor(r.market) : OTHER_COLOR }}
              />
              <span className={styles.body}>
                <span className={styles.title}>{displayTitle(r)}</span>
                <span className={styles.meta}>
                  {(r.source_display ?? r.source) && <span>{r.source_display ?? r.source}</span>}
                  {r.report_date && <span className={styles.date}>{monthDay(r.report_date)}</span>}
                </span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          className={styles.more}
          aria-expanded={open}
          aria-controls={listId}
          onClick={() => setOpen(o => !o)}
        >
          {open ? '收合' : `其餘 ${hidden} 篇`}
          <ChevronDown
            size={13}
            strokeWidth={1.8}
            aria-hidden="true"
            className={open ? `${styles.chev} ${styles.chevUp}` : styles.chev}
          />
        </button>
      )}
      {unlisted > 0 && (open || hidden === 0) && (
        <p className={styles.note}>另有 {unlisted} 篇未列入本期彙整。</p>
      )}
    </section>
  )
}
