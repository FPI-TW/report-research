import { useCallback, useRef, useState } from 'react'
import { useReducedMotion } from 'motion/react'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import { Pressable } from '../../components/primitives/Pressable'
import type { Market, Window } from '../../lib/radarSchemas'
import { BrokerList } from './BrokerList'
import { ConsensusSummary } from './ConsensusSummary'
import { CoverageStrip } from './CoverageStrip'
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
  /**
   * 展開哪家券商的歷程。給了就是受控（RadarPage 把它放在網址的 `broker`，
   * 「你看一下某券商對這一檔的歷程」才貼得出連結）；沒給則自己記，行為與先前相同。
   */
  openBroker?: string | null
  onOpenBrokerChange?: (brokerKey: string | null) => void
}

export function RadarOverview({
  market, code, window, onWindowChange, onBack, onBrowseReports,
  openBroker: controlledBroker, onOpenBrokerChange,
}: Props) {
  const q = useInstrumentRadar(code, market, window)
  const eventQueryKey = `${market}:${code}:${window}`
  const [expandedEventKey, setExpandedEventKey] = useState<string | null>(null)
  const showAllEvents = expandedEventKey === eventQueryKey
  const [reportId, setReportId] = useState<string | null>(null)
  const [reportName, setReportName] = useState<string | undefined>()
  // 券商歷程的展開狀態提到這一層：市場共識摘要的「查看券商觀點」與券商列自己的
  // 展開鈕指的是同一個面板，兩份狀態會互相覆蓋。
  const [localBroker, setLocalBroker] = useState<string | null>(null)
  const brokerControlled = controlledBroker !== undefined
  const openBroker = brokerControlled ? controlledBroker : localBroker
  const setOpenBroker = useCallback((key: string | null) => {
    if (brokerControlled) onOpenBrokerChange?.(key)
    else setLocalBroker(key)
  }, [brokerControlled, onOpenBrokerChange])
  const brokerSectionRef = useRef<HTMLElement>(null)
  const reduced = useReducedMotion()

  function openReport(id: string, fileName?: string | null) {
    setReportId(id)
    setReportName(fileName ?? undefined)
  }

  const viewBroker = useCallback((brokerKey: string) => {
    setOpenBroker(brokerKey)
    // jsdom 沒有 scrollIntoView，舊版 Safari 也不吃 options 物件——兩者都不該讓選取失效。
    brokerSectionRef.current?.scrollIntoView?.({
      behavior: reduced ? 'auto' : 'smooth',
      block: 'start',
    })
    // 只捲動不移焦點的話，鍵盤使用者的焦點還留在上面那顆「查看券商觀點」，
    // 再按 Tab 會走進中間所有的點——等於這個動作對他們什麼都沒做。
    // 展開列是同一次 render 才出現，所以要等一幀。
    requestAnimationFrame(() => {
      brokerSectionRef.current
        ?.querySelector<HTMLElement>(`[data-testid="broker-row-${CSS.escape(brokerKey)}"]`)
        ?.focus()
    })
  }, [reduced, setOpenBroker])

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

            <CoverageStrip coverage={data.coverage} />

            <section className={pageStyles.section}>
              <h2 className={pageStyles.sectionTitle}>市場共識摘要</h2>
              <ConsensusSummary
                rating={data.rating}
                targetCurrencyHint={data.target_price?.primary_currency}
                brokers={data.brokers}
                window={window}
                onViewBroker={viewBroker}
              />
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

            <section className={pageStyles.section} ref={brokerSectionRef}>
              <h2 className={pageStyles.sectionTitle}>各券商最新觀點</h2>
              <BrokerList
                brokers={data.brokers}
                code={code}
                market={market}
                window={window}
                openBroker={openBroker}
                onOpenBrokerChange={setOpenBroker}
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
