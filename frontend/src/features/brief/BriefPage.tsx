import { useCallback, useState, type CSSProperties } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { useSearchParams } from 'react-router'
import { CalendarDays, CircleAlert } from 'lucide-react'
import { ApiError } from '../../lib/api'
import { getBriefByDate, getBriefDates, getLatestBrief } from '../../lib/briefApi'
import type { BriefEnvelope } from '../../lib/briefSchemas'
import { BriefCalendar } from './BriefCalendar'
import { BriefMain } from './BriefMain'
import { BriefSources } from './BriefSources'
import { ISO_DATE, dayTitle, mergeDates, neighbours, todayIso } from './briefDates'
import styles from './BriefPage.module.css'

const STALE_MS = 5 * 60 * 1000

/**
 * 每日簡報（交接包「每日簡報」）。選取的日期放在網址 `?date=`（沒有就看最新一份），
 * 左欄是這份簡報的內容，右欄是月曆與本期來源研報。
 *
 * 刻意拿掉的：簡報本文的「評等與目標價」段（使用者要求，見 briefContent.ts）與複製按鈕。
 */
export default function BriefPage() {
  const [params, setParams] = useSearchParams()
  const requested = params.get('date')
  const dateParam = requested && ISO_DATE.test(requested) ? requested : null
  const [today] = useState(() => todayIso())

  const query = useQuery<BriefEnvelope>({
    queryKey: ['brief', dateParam ?? 'latest'],
    queryFn: () => (dateParam ? getBriefByDate(dateParam) : getLatestBrief()),
    // 簡報一天只換一次，重取沒有意義；失敗多半是 404（該日沒有簡報），重試只是拖延訊息。
    retry: false,
    staleTime: STALE_MS,
    // 切換日期時先留著上一份，新的一到左欄整欄重播進場（不閃骨架）。
    placeholderData: keepPreviousData,
  })
  // 月曆要看得到比 available_dates（30 筆）更早的日期；失敗就退回 available_dates。
  const datesQuery = useQuery({
    queryKey: ['brief', 'dates'],
    queryFn: () => getBriefDates(),
    retry: false,
    staleTime: STALE_MS,
  })

  const go = useCallback((iso: string) => {
    setParams(prev => {
      const sp = new URLSearchParams(prev)
      sp.set('date', iso)
      return sp
    }, { replace: true })
  }, [setParams])

  const envelope = query.data
  const brief = envelope?.status === 'ready' ? envelope.brief ?? null : null
  const dates = mergeDates(datesQuery.data, envelope?.available_dates)
  const notFound = query.error instanceof ApiError && query.error.status === 404

  // 初次出現的那一份走 stagger 進場；之後每換一份，左欄整欄以 0.5s 重播（交接包「每日簡報」）。
  const [motion, setMotion] = useState<{ last: string | null; swapped: boolean }>({ last: null, swapped: false })
  const shownDate = brief?.brief_date ?? null
  if (shownDate && shownDate !== motion.last) setMotion({ last: shownDate, swapped: motion.last !== null })

  let body
  if (query.isPending || (query.isError && !notFound && query.isFetching)) {
    body = <BriefSkeleton />
  } else if (notFound && dateParam) {
    body = <NotGenerated date={dateParam} today={today} previous={dates.find(d => d < dateParam) ?? null} onGo={go} />
  } else if (query.isError) {
    body = <LoadFailed error={query.error} onRetry={() => { void query.refetch() }} />
  } else if (!brief) {
    // status=pending：一份簡報都還沒產生過（每天早上都可能出現的正常狀態，不是錯誤）。
    body = <NotGenerated date={today} today={today} previous={dates[0] ?? null} onGo={go} />
  } else {
    const { older, newer } = neighbours(dates, brief.brief_date)
    body = (
      <div className={styles.grid}>
        <div
          key={brief.brief_date}
          className={`${styles.col} ${motion.swapped ? styles.swap : styles.stagger}`}
          aria-busy={query.isPlaceholderData || undefined}
        >
          <BriefMain brief={brief} older={older} newer={newer} onGo={go} />
        </div>
        <aside className={`${styles.rail} ${styles.stagger}`} aria-label="日期與來源">
          <BriefCalendar
            className={styles.tile}
            dates={dates}
            selected={dateParam ?? brief.brief_date}
            today={today}
            onPick={go}
          />
          {brief.reports.length > 0 && (
            <BriefSources key={brief.brief_date} className={styles.tile} reports={brief.reports} reportCount={brief.report_count} />
          )}
          <p className={styles.disclaimer}>內容由 AI 彙整自券商研報，可能有誤，非投資建議。</p>
        </aside>
      </div>
    )
  }

  return (
    <div className={styles.page}>
      <div className={styles.scroll}>
        <div className={styles.pad}>
          <h1 className={styles.title}>每日簡報</h1>
          {body}
        </div>
      </div>
    </div>
  )
}

function BriefSkeleton() {
  const skel = (width: number | string, height: number, extra?: CSSProperties) => (
    <span className={styles.skel} style={{ width, height, ...extra }} />
  )
  return (
    <div className={`${styles.grid} ${styles.fadeIn}`} aria-busy="true">
      <span className={styles.srOnly} role="status">簡報載入中</span>
      <div className={styles.col} aria-hidden="true">
        <div className={`${styles.tile} ${styles.skelOverview}`}>
          {skel('42%', 38)}
          <span className={styles.skelStats}>{skel(64, 40)}{skel(64, 40)}{skel(64, 40)}</span>
          {skel('100%', 8, { marginTop: 6 })}
        </div>
        <div className={`${styles.tile} ${styles.skelText}`}>
          {skel('18%', 16)}
          {skel('92%', 20, { marginTop: 8 })}
          {skel('70%', 20)}
          {skel('96%', 13, { marginTop: 14 })}
          {skel('84%', 13)}
          {skel('90%', 13)}
        </div>
      </div>
      <div className={`${styles.tile} ${styles.skelRail}`} aria-hidden="true">
        {skel('46%', 14)}
        {skel('100%', 196, { borderRadius: 10 })}
      </div>
    </div>
  )
}

function NotGenerated({ date, today, previous, onGo }: {
  date: string
  today: string
  previous: string | null
  onGo: (iso: string) => void
}) {
  // 今天以後的日期是「還沒產生」；過去的日期沒有簡報是那段時間沒有產生，不說「尚未」。
  const title = date >= today ? `${dayTitle(date)}的簡報尚未產生` : `${dayTitle(date)}沒有簡報`
  return (
    <div className={`${styles.tile} ${styles.stateBox} ${styles.fadeIn}`}>
      <span className={styles.stateIc} aria-hidden="true"><CalendarDays size={18} strokeWidth={1.8} /></span>
      <h2 className={styles.stateT}>{title}</h2>
      <p className={styles.stateD}>每天上午彙整前一日入庫的研報。</p>
      {previous && previous !== date && (
        <div className={styles.stateA}>
          <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={() => onGo(previous)}>
            看前一份簡報
          </button>
        </div>
      )}
    </div>
  )
}

function LoadFailed({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  // 拿得到 HTTP 狀態碼就是伺服器有回應，不說「連線逾時」。
  const detail = error instanceof ApiError ? '請稍後再試。' : '連線逾時，請稍後再試。'
  return (
    <div className={`${styles.tile} ${styles.stateBox} ${styles.fadeIn}`} role="alert">
      <span className={styles.stateIc} aria-hidden="true"><CircleAlert size={18} strokeWidth={1.8} /></span>
      <h2 className={styles.stateT}>簡報載入失敗</h2>
      <p className={styles.stateD}>{detail}</p>
      <div className={styles.stateA}>
        <button type="button" className={styles.btn} onClick={onRetry}>重試</button>
      </div>
    </div>
  )
}
