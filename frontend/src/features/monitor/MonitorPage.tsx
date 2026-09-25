import styles from './MonitorPage.module.css'
import { useProgress } from './useProgress'
import { useClock } from './useClock'
import { useRates } from './useRates'
import { rateText, ingestRateText, fmtInt } from './rate'
import { KpiGrid } from './KpiGrid'
import { ProgressPanel } from './ProgressPanel'
import { IngestPanel } from './IngestPanel'
import { PipelineStatus } from './PipelineStatus'
import { DERIVED_LABEL, PIPELINE_ROWS } from './pipelineMeta'
import { MarketDistribution } from './MarketDistribution'
import { BrokerDistribution } from './BrokerDistribution'
import { MonitorSkeleton } from './MonitorSkeleton'
import { FaithfulnessPanel } from './FaithfulnessPanel'
import { ExtractionPanel } from './ExtractionPanel'
import { ReviewQueuePanel } from './ReviewQueuePanel'
import { ScheduleHealthPanel } from './ScheduleHealthPanel'
import { Pulse } from '../../components/primitives/motionLoops'
import { useScrolled } from '../../lib/useScrolled'

export default function MonitorPage() {
  const q = useProgress()
  const clock = useClock()
  const rates = useRates(q.data, q.dataUpdatedAt)
  const p = q.data
  const live = !q.isError
  // 頁首吸頂後才實體化成玻璃：LIVE 狀態與時鐘要一直看得見
  const { scrolled, sentinelRef } = useScrolled()

  // 分子與分母都走 PIPELINE_ROWS，避免加一列管線卻漏改這裡（分母原本硬編為 4）。
  const alive = p ? PIPELINE_ROWS.filter(r => p.pipelines[r.key]).length : 0

  return (
    <div className={styles.page}>
      <div className={styles.scroll}>
        <span ref={sentinelRef} className={styles.sentinel} aria-hidden="true" />
        <div className={`${styles.headerBar} ${scrolled ? styles.headerStuck : ''}`}>
          <div className={styles.headerInner}>
            <div className={styles.header}>
              <div>
                <h2 className={styles.title}>研報導入監控</h2>
                <div className={styles.sub}>
                  {p ? `${fmtInt(p.db.reports)} 篇已導入 · ${alive}/${PIPELINE_ROWS.length} 條管線執行中` : '連線中…'}
                </div>
              </div>
              <div className={styles.headRight}>
                <span className={`${styles.live} ${live ? '' : styles.stale}`}>
                  {live ? (
                    <Pulse className={styles.liveDot} min={0.35} duration={1.6} />
                  ) : (
                    <span className={`${styles.liveDot} ${styles.staleDot}`} />
                  )}
                  {live ? 'LIVE' : '重連中'}
                </span>
                <span className={styles.clock}>{clock}</span>
              </div>
            </div>
          </div>
        </div>

        <div className={styles.inner}>
          {p ? (
            <>
              <KpiGrid progress={p} />
              <div className={styles.panelGrid}>
                <ProgressPanel
                  title="語意標註"
                  data={p.tagging}
                  rateLine={rateText(p.tagging?.fail ?? 0, rates.tpm, '標註')}
                  idleText="目前無執行中的標註"
                />
                <IngestPanel progress={p} rateLine={ingestRateText(rates.rpm, rates.cps)} />
              </div>
              <div className={styles.panelGrid}>
                <ProgressPanel
                  title="摘要生成"
                  data={p.summary}
                  rateLine={rateText(p.summary.remaining, rates.spm, '摘要')}
                  idleText="—"
                />
                <PipelineStatus pipelines={p.pipelines} />
              </div>
              {/*
                派生資產新鮮度。後端從 P4 起就在回 takeaway/signal，但 progressSchema
                沒宣告這兩個鍵，zod 靜默剝除 → 資料一路送到前端卻從未進 DOM。
                批次停跑（實測 takeaway 停 8 天、signal 停 12 天）的症狀是閱讀頁
                優雅降級、少一個區塊，沒有人會回報，所以只能靠這裡看。
                全表覆蓋率不是訊號（兩者都刻意只跑子集），latest 有沒有前進才是。
              */}
              <div className={styles.panelGrid}>
                <ProgressPanel
                  title={`${DERIVED_LABEL.takeaways}（近 30 天）`}
                  data={p.takeaway ?? null}
                  rateLine={p.takeaway?.latest ? `最後產出 ${p.takeaway.latest}` : '尚無產出'}
                  idleText="此版後端未提供摘錄統計"
                />
                <ProgressPanel
                  title={`${DERIVED_LABEL.signals}（近 30 天）`}
                  data={p.signal ?? null}
                  rateLine={p.signal?.latest ? `最後產出 ${p.signal.latest}` : '尚無產出'}
                  idleText="此版後端未提供訊號統計"
                />
              </div>
              {/*
                排程健康擺在忠實度旁邊，是因為兩張卡都是「只寫不看的東西第一次有出口」：
                前者是 unit_failures.log（零程式消費端，2026-07-28 寫了 10 筆沒人知道）
                與生產入庫路徑 sync_run_*.log（runtime 區塊原本只認全量腳本的 log）。
              */}
              <div className={styles.panelGrid}>
                <ScheduleHealthPanel sync={p.sync} failures={p.unit_failures} />
                <FaithfulnessPanel evaluation={p.evaluation} />
              </div>
              {/*
                抽取品質與回填進度（E1）。回填要跑十幾個晚上，沒有這張卡只能靠 SQL 看；
                extraction_log 的落點分佈是「1,466 筆檔案為什麼不在語料庫」第一次有答案的地方。
              */}
              <div className={styles.panelGrid}>
                <ExtractionPanel extraction={p.extraction} />
                {/* 上面兩張卡（忠實度、抽取品質）只有筆數；這張列出是哪幾筆，外加倒讚。
                    自己取數、不吃 /api/progress 的輪詢（理由見 useReviewQueue）。 */}
                <ReviewQueuePanel scale={p.evaluation?.qa ?? null} />
              </div>
              <MarketDistribution markets={p.db.markets} />
              {/* 券商分佈接在市場分佈之後：兩張卡是同一種東西（語料的組成），
                  分母都是 db.reports。券商列數多得多（實測 31），所以放後面。 */}
              <BrokerDistribution sources={p.db.sources} />
              <div className={styles.footer}>資料每 5 秒自動更新 · 廷豐智能研報導入管線</div>
            </>
          ) : q.isError ? (
            <div className={styles.footer}>連線失敗，重試中…</div>
          ) : (
            <MonitorSkeleton />
          )}
        </div>
      </div>
    </div>
  )
}
