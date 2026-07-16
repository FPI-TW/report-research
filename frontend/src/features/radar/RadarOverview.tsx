import { useState } from 'react'
import { ReportDetailModal } from '../../components/ReportDetailModal'
import { Pressable } from '../../components/primitives/Pressable'
import type { Window } from '../../lib/radarSchemas'
import { BrokerList } from './BrokerList'
import { ConsensusSnapshot } from './ConsensusSnapshot'
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
import { isNotFound, useInstrumentRadar } from './useRadar'

interface Props {
  market: string
  code: string
  window: Window
  onWindowChange: (w: Window) => void
  onBack: () => void
}

export function RadarOverview({ market, code, window, onWindowChange, onBack }: Props) {
  const q = useInstrumentRadar(code, market, window)
  const [showAllEvents, setShowAllEvents] = useState(false)
  const [reportId, setReportId] = useState<string | null>(null)
  const [reportName, setReportName] = useState<string | undefined>()

  function openReport(id: string, fileName?: string | null) {
    setReportId(id)
    setReportName(fileName ?? undefined)
  }

  const data = q.data
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
          <RadarPendingExtraction note={data.coverage.note} />
        ) : data.coverage.state === 'window_empty' ? (
          <RadarWindowEmpty note={data.coverage.note} />
        ) : (
          <>
            <ConsensusSnapshot
              rating={data.rating}
              target={data.target_price}
              eps={data.eps}
              coverage={data.coverage}
              window={window}
            />

            <section className={pageStyles.section}>
              <h2 className={pageStyles.sectionTitle}>四向觀點</h2>
              <ThesisCompass thesis={data.thesis} />
            </section>

            <section className={pageStyles.section}>
              <h2 className={pageStyles.sectionTitle}>
                近期關鍵變化
                {data.recent_events_total > 3 ? (
                  <Pressable
                    tapScale={0.97}
                    className={pageStyles.sectionMeta}
                    style={{
                      appearance: 'none', border: 0, background: 'none', cursor: 'pointer',
                    }}
                    onClick={() => setShowAllEvents(v => !v)}
                  >
                    {showAllEvents ? '收合' : `查看全部 ${data.recent_events_total} 項`}
                  </Pressable>
                ) : null}
              </h2>
              <RecentChanges
                events={data.recent_events}
                total={data.recent_events_total}
                showAll={showAllEvents}
                onToggleAll={() => setShowAllEvents(v => !v)}
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
              <ul className={pageStyles.muted} style={{ marginTop: 16, paddingLeft: 18 }}>
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
