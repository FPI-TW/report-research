import { useCallback, useRef, useState } from 'react'
import { useReducedMotion } from 'motion/react'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import type { Market, Window } from '../../lib/radarSchemas'
import { BrokerTable } from './BrokerTable'
import { ConsensusCards } from './ConsensusCards'
import { primaryTargetGroup } from './consensusData'
import { RadarHeader } from './RadarHeader'
import { RadarSkeleton } from './RadarSkeleton'
import {
  RadarLoadError, RadarNotFound, RadarPendingExtraction, RadarWindowEmpty,
} from './RadarStates'
import { RecentChanges } from './RecentChanges'
import { TargetDotPlot } from './TargetDotPlot'
import { ThesisCards } from './ThesisCards'
import { isNotFound, useInstrumentRadar, useRadarEvents } from './useRadar'
import styles from './RadarOverview.module.css'

interface Props {
  market: Market
  code: string
  window: Window
  onWindowChange: (w: Window) => void
  onBack: () => void
  onBrowseReports: () => void
  /** 展開哪家券商的歷程（狀態在網址的 `broker`）。 */
  openBroker: string | null
  onOpenBrokerChange: (brokerKey: string | null) => void
}

/**
 * 券商共識詳情（交接包「券商共識」詳情）：頁首 → 三張摘要卡 → 各券商目標價點圖 →
 * 四向觀點 → 各券商最新觀點（可展開歷程）→ 近期變化。
 */
export function RadarOverview({
  market, code, window, onWindowChange, onBack, onBrowseReports, openBroker, onOpenBrokerChange,
}: Props) {
  const q = useInstrumentRadar(code, market, window)
  const data = q.data
  const reduced = useReducedMotion()
  const brokersRef = useRef<HTMLDivElement>(null)
  const [report, setReport] = useState<{ id: string; title?: string } | null>(null)

  // 「查看全部」綁在目前的期間上：換期間就回到收合，不會拿 90 天的展開狀態套到 30 天。
  const eventKey = `${market}:${code}:${window}`
  const [expandedEventKey, setExpandedEventKey] = useState<string | null>(null)
  const showAllEvents = expandedEventKey === eventKey
  const fullEvents = useRadarEvents(code, market, window, Boolean(showAllEvents && data?.recent_events_has_more))
  const shownEvents = showAllEvents && fullEvents.data ? fullEvents.data.items : (data?.recent_events ?? [])
  const retryEvents = fullEvents.isFetchNextPageError ? fullEvents.loadMore : fullEvents.refetch

  const openReport = useCallback((id: string, title?: string | null) => {
    setReport({ id, title: title ?? undefined })
  }, [])

  /**
   * 點圖上點一家券商：展開它的歷程、把那一列捲進畫面並移入焦點。
   * 只捲頁面自己的捲動容器（不用 scrollIntoView：它會連外層一起捲）；展開列在下一幀才出現。
   */
  const viewBroker = useCallback((key: string) => {
    onOpenBrokerChange(key)
    requestAnimationFrame(() => {
      const row = brokersRef.current?.querySelector<HTMLElement>(`[data-broker-row="${CSS.escape(key)}"]`)
      if (!row) return
      const scroller = row.closest<HTMLElement>('[data-radar-scroll]')
      if (scroller) {
        const top = row.getBoundingClientRect().top - scroller.getBoundingClientRect().top + scroller.scrollTop - 24
        scroller.scrollTo?.({ top, behavior: reduced ? 'auto' : 'smooth' })
      }
      row.focus({ preventScroll: true })
    })
  }, [onOpenBrokerChange, reduced])

  const notFound = q.isError && isNotFound(q.error)
  const group = primaryTargetGroup(data?.target_price)

  function body() {
    if (q.isLoading && !data) return <RadarSkeleton />
    if (notFound) return <RadarNotFound onBack={onBack} />
    if (q.isError || !data) return <RadarLoadError error={q.error} onRetry={() => void q.refetch()} />
    if (data.coverage.state === 'pending_extraction') {
      return <RadarPendingExtraction note={data.coverage.note} />
    }
    if (data.coverage.state === 'window_empty') {
      return (
        <RadarWindowEmpty
          note={data.coverage.note}
          onShowAll={window === 'all' ? undefined : () => onWindowChange('all')}
        />
      )
    }
    return (
      <>
        <ConsensusCards rating={data.rating} target={data.target_price} eps={data.eps?.primary} />
        {group ? (
          <TargetDotPlot brokers={data.brokers} group={group} activeKey={openBroker} onSelect={viewBroker} />
        ) : null}
        <ThesisCards thesis={data.thesis} />
        <div ref={brokersRef}>
          <BrokerTable
            brokers={data.brokers}
            code={code}
            market={market}
            window={window}
            targetCurrency={group?.currency ?? null}
            epsGroup={data.eps?.primary ?? null}
            openBroker={openBroker}
            onOpenBrokerChange={onOpenBrokerChange}
            onOpenReport={openReport}
          />
        </div>
        <RecentChanges
          events={shownEvents}
          total={fullEvents.data?.total ?? data.recent_events_total}
          showAll={showAllEvents}
          onToggleAll={() => setExpandedEventKey(showAllEvents ? null : eventKey)}
          hasMore={showAllEvents && fullEvents.hasMore}
          remaining={fullEvents.remaining}
          isLoadingMore={showAllEvents && fullEvents.isFetching}
          loadError={showAllEvents && fullEvents.isError}
          onLoadMore={() => void fullEvents.loadMore()}
          onRetryLoad={() => void retryEvents()}
          onOpenReport={openReport}
        />
        {data.notes.length ? (
          <ul className={styles.notes}>
            {data.notes.map((n, i) => <li key={i}>{n}</li>)}
          </ul>
        ) : null}
      </>
    )
  }

  return (
    <div className={styles.wrap}>
      <RadarHeader
        market={market}
        marketDisplay={data?.market_display}
        code={code}
        name={data?.instrument_name}
        asOf={data?.as_of}
        coverage={data?.coverage}
        window={window}
        onWindowChange={onWindowChange}
        onBack={onBack}
        onBrowseReports={onBrowseReports}
      />
      {body()}
      <ReportDetailModal
        reportId={report?.id ?? null}
        fileName={report?.title}
        onClose={() => setReport(null)}
      />
    </div>
  )
}
