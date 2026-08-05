import { useState } from 'react'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import { Pressable } from '../../components/primitives/Pressable'
import type { Market, Window } from '../../lib/radarSchemas'
import { BrokerList } from './BrokerList'
import { ConsensusSnapshot } from './ConsensusSnapshot'
import { KeyFigures } from './KeyFigures'
import { RadarHeader } from './RadarHeader'
import pageStyles from './RadarPage.module.css'
import { RadarSkeleton } from './RadarSkeleton'
import {
  RadarLoadError,
  RadarNotFound,
  RadarPendingExtraction,
  RadarWindowEmpty,
} from './RadarStates'
import { RecentChanges } from './RecentChanges'
import { ThesisCompass } from './ThesisCompass'
import { isNotFound, useInstrumentRadar, useRadarEvents } from './useRadar'

interface Props {
  market: Market
  code: string
  window: Window
  onWindowChange: (w: Window) => void
  onBack: () => void
  onBrowseReports: () => void
}

export function RadarOverview({
  market, code, window, onWindowChange, onBack, onBrowseReports,
}: Props) {
  const q = useInstrumentRadar(code, market, window)
  const eventQueryKey = `${market}:${code}:${window}`
  const [expandedEventKey, setExpandedEventKey] = useState<string | null>(null)
  const showAllEvents = expandedEventKey === eventQueryKey
  const [reportId, setReportId] = useState<string | null>(null)
  const [reportName, setReportName] = useState<string | undefined>()

  function openReport(id: string, fileName?: string | null) {
    setReportId(id)
    setReportName(fileName ?? undefined)
  }

  const data = q.data
  const fullEvents = useRadarEvents(
    code,
    market,
    window,
    Boolean(showAllEvents && data?.recent_events_has_more),
  )
  const shownEvents = showAllEvents && fullEvents.data
    ? fullEvents.data.items
    : (data?.recent_events ?? [])
  const shownEventsTotal = fullEvents.data?.total ?? data?.recent_events_total ?? 0
  const eventLoadError = showAllEvents && fullEvents.isError
  const retryEvents = fullEvents.isFetchNextPageError
    ? fullEvents.loadMore
    : fullEvents.refetch

  function toggleAllEvents() {
    setExpandedEventKey(showAllEvents ? null : eventQueryKey)
  }
  const showSkeleton = q.isLoading && !data
  const notFound = q.isError && isNotFound(q.error)
  const loadError = q.isError && !notFound

  return (
    <div>
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
      />

      {showSkeleton ? <RadarSkeleton /> : null}

      {notFound ? <RadarNotFound onBack={onBack} /> : null}

      {loadError ? <RadarLoadError onRetry={() => q.refetch()} /> : null}

      {data && !notFound && !loadError ? (
        data.coverage.state === 'pending_extraction' ? (
          <RadarPendingExtraction note={data.coverage.note} onBrowseReports={onBrowseReports} />
        ) : data.coverage.state === 'window_empty' ? (
          <RadarWindowEmpty note={data.coverage.note} />
        ) : (
          <>
            {data.coverage.state === 'partial' ? (
              <aside
                className={pageStyles.coverageNotice}
                role="status"
                aria-label="部分資料"
              >
                <strong>部分資料</strong>
                <span>
                  {data.coverage.note || '部分研報仍在整理，以下僅顯示目前已擷取內容。'}
                </span>
              </aside>
            ) : null}

            <KeyFigures target={data.target_price} eps={data.eps} coverage={data.coverage} />

            <section className={pageStyles.section}>
              <h2 className={pageStyles.sectionTitle}>券商共識</h2>
              <ConsensusSnapshot rating={data.rating} window={window} />
            </section>

            <section className={pageStyles.section}>
              <h2 className={pageStyles.sectionTitle}>四向觀點</h2>
              <ThesisCompass thesis={data.thesis} />
            </section>

            <section className={pageStyles.section}>
              {/* 按鈕是 h2 的**兄弟**而非子節點：巢狀在標題裡會把「查看全部 20 項」
                  串進標題的可及名稱，而且展開後名稱還會變成「近期關鍵變化 收合」。 */}
              <div className={pageStyles.sectionHead}>
                <h2 className={pageStyles.sectionTitle}>近期關鍵變化</h2>
                {data.recent_events_total > 3 ? (
                  <Pressable
                    tapScale={0.97}
                    className={pageStyles.sectionMeta}
                    style={{
                      appearance: 'none', border: 0, background: 'none', cursor: 'pointer',
                    }}
                    onClick={toggleAllEvents}
                    aria-expanded={showAllEvents}
                    aria-controls="radar-recent-events"
                  >
                    {showAllEvents ? '收合' : `查看全部 ${data.recent_events_total} 項`}
                  </Pressable>
                ) : null}
              </div>
              <RecentChanges
                events={shownEvents}
                total={shownEventsTotal}
                showAll={showAllEvents}
                onToggleAll={toggleAllEvents}
                hasMore={showAllEvents && fullEvents.hasMore}
                remaining={fullEvents.remaining}
                isLoadingMore={showAllEvents && fullEvents.isFetching}
                loadError={eventLoadError}
                onLoadMore={() => void fullEvents.loadMore()}
                onRetryLoad={() => void retryEvents()}
                onOpenReport={openReport}
              />
            </section>

            <section className={pageStyles.section}>
              <h2 className={pageStyles.sectionTitle}>各券商最新觀點</h2>
              <BrokerList
                brokers={data.brokers}
                code={code}
                market={market}
                window={window}
                onOpenReport={openReport}
              />
            </section>

            {data.notes.length > 0 ? (
              <ul className={`${pageStyles.muted} ${pageStyles.notes}`}>
                {data.notes.map((n, i) => <li key={i}>{n}</li>)}
              </ul>
            ) : null}
          </>
        )
      ) : null}

      <ReportDetailModal
        reportId={reportId}
        fileName={reportName}
        onClose={() => setReportId(null)}
      />
    </div>
  )
}
