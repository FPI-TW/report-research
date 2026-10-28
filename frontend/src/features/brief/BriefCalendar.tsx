import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import {
  WEEKDAY_HEADERS,
  addMonths,
  monthCells,
  monthIndex,
  monthOf,
  partsOf,
  type MonthRef,
} from './briefDates'
import styles from './BriefCalendar.module.css'

interface Props {
  /** 有簡報的日期（新→舊）。 */
  dates: readonly string[]
  /** 正在看的簡報日期。 */
  selected: string | null
  today: string
  onPick: (iso: string) => void
  className?: string
}

/**
 * 簡報月曆。月份跟著選取的日期走（用前一份／後一份切到別的月份時，月曆一起翻過去），
 * 也可以自己往前後翻；可翻的範圍是最舊一份簡報到「最新一份與今天較晚者」所在的月份。
 */
export function BriefCalendar({ dates, selected, today, onPick, className }: Props) {
  const anchor = selected ?? dates[0] ?? today
  // 「記住上一次的錨點」：錨點換了就把月份對回去，否則保留使用者自己翻到的月份。
  const [view, setView] = useState<{ anchor: string; month: MonthRef }>(() => ({ anchor, month: monthOf(anchor) }))
  if (view.anchor !== anchor) setView({ anchor, month: monthOf(anchor) })
  const month = view.anchor === anchor ? view.month : monthOf(anchor)

  const available = new Set(dates)
  const oldest = dates.length ? monthOf(dates[dates.length - 1]) : monthOf(anchor)
  const newestIdx = Math.max(monthIndex(dates.length ? monthOf(dates[0]) : monthOf(anchor)), monthIndex(monthOf(today)))
  const canPrev = monthIndex(month) > monthIndex(oldest)
  const canNext = monthIndex(month) < newestIdx
  const go = (delta: number) => setView({ anchor, month: addMonths(month, delta) })

  const { blanks, days } = monthCells(month)
  const label = `${month.y} 年 ${month.m} 月`

  return (
    <section className={[className, styles.calendar].filter(Boolean).join(' ')} aria-labelledby="brief-cal">
      <div className={styles.head}>
        <h2 id="brief-cal" className={styles.label} aria-live="polite">{label}</h2>
        <button type="button" className={styles.nav} aria-label="上個月" disabled={!canPrev} onClick={() => go(-1)}>
          <ChevronLeft size={14} strokeWidth={2} aria-hidden="true" />
        </button>
        <button type="button" className={styles.nav} aria-label="下個月" disabled={!canNext} onClick={() => go(1)}>
          <ChevronRight size={14} strokeWidth={2} aria-hidden="true" />
        </button>
      </div>
      <div className={`${styles.grid} ${styles.weekdays}`} aria-hidden="true">
        {WEEKDAY_HEADERS.map(w => <span key={w} className={styles.weekday}>{w}</span>)}
      </div>
      <div className={styles.grid} role="group" aria-label="簡報日期">
        {Array.from({ length: blanks }, (_, i) => <span key={`b${i}`} aria-hidden="true" />)}
        {days.map(({ iso, day }) => {
          const avail = available.has(iso)
          const isSel = iso === selected
          const isToday = iso === today
          const cls = [styles.day, avail && styles.avail, isToday && styles.today, isSel && styles.selected]
            .filter(Boolean).join(' ')
          const { m } = partsOf(iso)
          return (
            <button
              key={iso}
              type="button"
              className={cls}
              disabled={!avail}
              aria-pressed={avail ? isSel : undefined}
              aria-current={isToday ? 'date' : undefined}
              aria-label={`${m} 月 ${day} 日${avail ? '' : '，無簡報'}`}
              onClick={() => { if (avail && !isSel) onPick(iso) }}
            >
              {day}
            </button>
          )
        })}
      </div>
    </section>
  )
}
