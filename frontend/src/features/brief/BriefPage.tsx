import { useCallback, useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link, useSearchParams } from 'react-router'
import { ApiError } from '../../lib/api'
import { renderAnswer } from '../../lib/askMarkdown'
import { getBriefByDate, getLatestBrief } from '../../lib/briefApi'
import type { BriefEnvelope, BriefReportRef } from '../../lib/briefSchemas'
import { displayTitle } from '../../lib/displayTitle'
import { marketLabel as marketLabelOf } from '../../lib/meta'
import styles from './BriefPage.module.css'

const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/

function formatDate(iso: string): string {
  if (!ISO_DATE.test(iso)) return iso
  const [y, m, d] = iso.split('-')
  return `${y} 年 ${Number(m)} 月 ${Number(d)} 日`
}

function marketLabel(code: string | null | undefined): string {
  return code ? marketLabelOf(code) : '未分類'
}

function SourceItem({ report }: { report: BriefReportRef }) {
  return (
    <li className={styles.srcItem}>
      <Link className={styles.srcLink} to={`/report/${report.file_hash}`}>
        {displayTitle(report)}
      </Link>
      <span className={styles.srcMeta}>
        {marketLabel(report.market)}
        {report.source_display ? `｜${report.source_display}` : ''}
        {report.report_date ? `｜${report.report_date}` : ''}
      </span>
    </li>
  )
}

export default function BriefPage() {
  const [params, setParams] = useSearchParams()
  const requested = params.get('date')
  const dateParam = requested && ISO_DATE.test(requested) ? requested : null

  const query = useQuery<BriefEnvelope>({
    queryKey: ['brief', dateParam ?? 'latest'],
    queryFn: () => (dateParam ? getBriefByDate(dateParam) : getLatestBrief()),
    // 簡報一天只換一次，重取沒有意義；失敗多半是 404（該日沒有簡報），重試只是拖延訊息。
    retry: false,
    staleTime: 5 * 60 * 1000,
  })

  const onPickDate = useCallback((value: string) => {
    setParams(prev => {
      const sp = new URLSearchParams(prev)
      if (value) sp.set('date', value)
      else sp.delete('date')
      return sp
    }, { replace: true })
  }, [setParams])

  const brief = query.data?.status === 'ready' ? query.data.brief ?? null : null
  const dates = query.data?.available_dates ?? []

  // renderAnswer 是問答那條路徑的 markdown 渲染器（標題／清單／表格／行內樣式都在裡面）。
  // 簡報沒有 [n] 引用，故 sourceCount 給 0——超出範圍的 [n] 會原樣當文字留著，不會變成
  // 點不動的膠囊。刻意重用而不是另寫一份：兩份渲染器遲早會在細節上分岔。
  const body = useMemo(
    () => (brief ? renderAnswer(brief.markdown, 0, () => {}) : null),
    [brief],
  )

  const notFound = query.error instanceof ApiError && query.error.status === 404

  return (
    <div className={styles.page}>
      <div className={styles.scroll}>
        <div className={styles.inner}>
          <header className={styles.head}>
            <div>
              <h1 className={styles.title}>每日簡報</h1>
              <p className={styles.lede}>
                把窗期內新進的研報與券商觀點變動整理成一份可快速讀完的摘要。
              </p>
            </div>
            {dates.length > 0 && (
              <label className={styles.pick}>
                <span className={styles.pickLabel}>日期</span>
                <select
                  className={styles.select}
                  value={brief?.brief_date ?? ''}
                  onChange={e => onPickDate(e.target.value)}
                >
                  {dates.map(d => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              </label>
            )}
          </header>

          {query.isPending && <p className={styles.state}>載入中…</p>}

          {notFound && (
            <p className={styles.state}>
              {dateParam ? `${formatDate(dateParam)} 沒有簡報。` : '目前沒有簡報。'}
            </p>
          )}

          {query.isError && !notFound && (
            <p className={styles.state}>簡報載入失敗，請稍後再試。</p>
          )}

          {query.data?.status === 'pending' && (
            <p className={styles.state}>
              今天的簡報還沒產生。批次每天上午彙整前一日入庫的研報，稍後再回來看。
            </p>
          )}

          {brief && (
            <article className={styles.body}>
              <div className={styles.meta}>
                <span className={styles.metaDate}>{formatDate(brief.brief_date)}</span>
                <span className={styles.metaStat}>
                  新進研報 {brief.report_count} 篇
                </span>
                <span className={styles.metaStat}>
                  觀點變動 {brief.signal_count} 筆
                </span>
              </div>

              <div className={styles.md}>{body}</div>

              {brief.reports.length > 0 && (
                <section className={styles.sources}>
                  <h2 className={styles.sourcesTitle}>本期來源研報</h2>
                  <ul className={styles.srcList}>
                    {brief.reports.map(r => (
                      <SourceItem key={r.report_id} report={r} />
                    ))}
                  </ul>
                  {brief.report_count > brief.reports.length && (
                    // 差額一定要說：prompt 有篇數上限，靜默只列一部分會讓讀者以為
                    // 那天只有這幾篇。
                    <p className={styles.srcMore}>
                      另有 {brief.report_count - brief.reports.length} 篇未列入本期彙整。
                    </p>
                  )}
                </section>
              )}
            </article>
          )}

          <p className={styles.disclaimer}>
            本頁彙整券商研報內容與已擷取的觀點變化，非系統預測或投資建議。
          </p>
        </div>
      </div>
    </div>
  )
}
