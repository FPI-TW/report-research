import { useState } from 'react'
import { Card, PageHeader, Segmented, StatTile, Tabs, TrendBars, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { LlmUsageOnline, LlmUsageResponse, LlmUsageTotals } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { dailyAxis, dailyCalls, dayIn } from './data/usageSeries'
import { OpsQueryError } from './OpsShared'
import { useLlmUsage } from './useOps'
import styles from './data/Data.module.css'
import { t } from '@/i18n'

const RANGES: readonly SegOption<string>[] = [
  { value: '7', get label() { return t('admin.ops.opsLlmUsagePage.7Days') } },
  { value: '30', get label() { return t('admin.ops.opsLlmUsagePage.30Days') } },
  { value: '90', get label() { return t('admin.ops.opsLlmUsagePage.90Days') } },
]

type View = 'task' | 'model' | 'day'
const VIEWS: readonly SegOption<View>[] = [
  { value: 'task', get label() { return t('admin.ops.opsLlmUsagePage.byTask') } },
  { value: 'model', get label() { return t('admin.ops.opsLlmUsagePage.byModel') } },
  { value: 'day', get label() { return t('admin.ops.opsLlmUsagePage.byDate') } },
]
const VIEW_KEY_LABEL: Record<View, string> = { get task() { return t('admin.ops.opsLlmUsagePage.task') }, get model() { return t('admin.ops.opsLlmUsagePage.model') }, get day() { return t('admin.ops.opsLlmUsagePage.date') } }

const fmtInt = (n: number) => n.toLocaleString('zh-TW')

function avgMs(item: LlmUsageTotals): string {
  return item.calls ? t('admin.ops.opsLlmUsagePage.s', { v: (item.total_ms / item.calls / 1000).toFixed(1) }) : '—'
}

function UsageTable({ caption, keyLabel, rows, showCost }: {
  caption: string; keyLabel: string; rows: (LlmUsageTotals & { key: string })[]; showCost: boolean
}) {
  const R = `${c.num} ${c.tdRight}`
  return (
    <div className={c.tableWrap}>
      <table className={c.table} aria-label={caption}>
        <thead>
          <tr>
            <th>{keyLabel}</th><th className={c.tdRight}>{t('admin.ops.opsLlmUsagePage.calls')}</th><th className={c.tdRight}>{t('admin.ops.opsLlmUsagePage.failures')}</th>
            <th className={c.tdRight}>{t('admin.ops.opsLlmUsagePage.inputCacheHit')}</th><th className={c.tdRight}>{t('admin.ops.opsLlmUsagePage.inputCacheMiss')}</th>
            <th className={c.tdRight}>{t('admin.ops.opsLlmUsagePage.output')}</th><th className={c.tdRight}>{t('admin.ops.opsLlmUsagePage.reasoning')}</th><th className={c.tdRight}>{t('admin.ops.opsLlmUsagePage.avgDuration')}</th>
            {showCost && <th className={c.tdRight}>{t('admin.ops.opsLlmUsagePage.cost')}</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.key}>
              <td className={c.mono}><span className={`${c.truncate} ${styles.nameCol}`} title={r.key}>{r.key}</span></td>
              <td className={R}>{fmtInt(r.calls)}</td>
              <td className={R}>{r.failures ? <span className={styles.failNum}>{fmtInt(r.failures)}</span> : 0}</td>
              <td className={R}>{fmtInt(r.prompt_hit_tokens)}</td>
              <td className={R}>{fmtInt(r.prompt_miss_tokens)}</td>
              <td className={R}>{fmtInt(r.completion_tokens)}</td>
              <td className={R}>{fmtInt(r.reasoning_tokens)}</td>
              <td className={R}>{avgMs(r)}</td>
              {showCost && <td className={R}>{r.cost != null ? r.cost.toFixed(4) : '—'}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** 數字磚。`prefix` 讓合計、批次、線上三組磚的無障礙名稱不重複（畫面上的標籤不帶）。 */
function Totals({ t: item, costAvailable, prefix = '' }: { t: LlmUsageTotals; costAvailable: boolean; prefix?: string }) {
  const tiles: [string, string, string][] = [
    [t('admin.ops.opsLlmUsagePage.calls2'), fmtInt(item.calls), t('admin.ops.opsLlmUsagePage.failedWithoutUsage', { failures: fmtInt(item.failures), calls_without_tokens: fmtInt(item.calls_without_tokens) })],
    [t('admin.ops.opsLlmUsagePage.inputTokens'), fmtInt(item.prompt_hit_tokens + item.prompt_miss_tokens), t('admin.ops.opsLlmUsagePage.cacheHits', { prompt_hit_tokens: fmtInt(item.prompt_hit_tokens) })],
    [t('admin.ops.opsLlmUsagePage.outputTokens'), fmtInt(item.completion_tokens), t('admin.ops.opsLlmUsagePage.reasoning2', { reasoning_tokens: fmtInt(item.reasoning_tokens) })],
  ]
  if (costAvailable && item.cost != null) tiles.push([t('admin.ops.opsLlmUsagePage.cost'), item.cost.toFixed(4), t('admin.ops.opsLlmUsagePage.sumOfLinesCarryingA')])
  return (
    <div className={`${c.tiles} ${styles.wideTiles}`}>
      {tiles.map(([name, value, hint]) => (
        <StatTile key={name} name={`${prefix}${name}`} label={name} value={value} sub={hint} subTitle={hint} />
      ))}
    </div>
  )
}

/** 依任務／模型／日期三張表，用分頁籤切；prefix 讓批次與線上兩段的表格名稱不重複。 */
function Breakdown({ d, prefix = '' }: {
  d: Pick<LlmUsageOnline, 'by_task' | 'by_model' | 'by_day' | 'cost_available'>; prefix?: string
}) {
  const [view, setView] = useState<View>('task')
  const rows = view === 'task' ? d.by_task.map(r => ({ ...r, key: r.task }))
    : view === 'model' ? d.by_model.map(r => ({ ...r, key: r.model }))
      : [...d.by_day].reverse().map(r => ({ ...r, key: r.day }))  // 新→舊
  const label = VIEWS.find(v => v.value === view)!.label as string
  return (
    <Card>
      <div className={styles.tabs}>
        <Tabs label={t('admin.ops.opsLlmUsagePage.usageBreakdown', { prefix })} tabs={VIEWS} value={view} onChange={setView} />
      </div>
      <UsageTable caption={`${prefix}${label}`} keyLabel={VIEW_KEY_LABEL[view]} showCost={d.cost_available} rows={rows} />
    </Card>
  )
}

/**
 * 每日呼叫的長條圖。線上讀得到時畫「批次＋線上」的合計，否則只畫批次；最後一根（今天，還沒過完）用金色。
 * 批次用量檔超過讀取上限時，沒讀到的那幾天留空而不是畫成 0。
 */
function DailyCard({ d }: { d: LlmUsageResponse }) {
  const withOnline = Boolean(d.combined && d.online?.available)
  const byDay = withOnline ? d.combined!.by_day : d.by_day
  const sinceDay = dayIn(d.since, d.timezone)
  const untilDay = dayIn(d.until, d.timezone)
  if (!sinceDay || !untilDay) return null
  const knownFrom = d.source.truncated && d.source.earliest_ts ? dayIn(d.source.earliest_ts, d.timezone) : null
  const bars = dailyCalls(byDay, sinceDay, untilDay, knownFrom)
  if (!bars.some(b => (b.value ?? 0) > 0)) return null
  const peak = Math.max(...bars.map(b => b.value ?? 0))
  return (
    <Card title={t('admin.ops.opsLlmUsagePage.dailyCalls')} hint={withOnline ? t('admin.ops.opsLlmUsagePage.batchOnline') : t('admin.ops.opsLlmUsagePage.batchOnly')} labelledBy="llm-daily-title"
      right={<span>{t('admin.ops.opsLlmUsagePage.dailyMax')}{' '}<span className={c.num}>{fmtInt(peak)}</span>{' '}{t('admin.ops.opsLlmUsagePage.calls3')}</span>}>
      <div className={styles.chartBody}>
        <TrendBars bars={bars} label={t('admin.ops.opsLlmUsagePage.dailyCallCount')} format={v => t('admin.ops.opsLlmUsagePage.calls4', { v: fmtInt(v) })} highlightLast axis={dailyAxis(bars)} />
      </div>
    </Card>
  )
}

function BatchSection({ d }: { d: LlmUsageResponse }) {
  return (
    <section className={styles.section} aria-labelledby="llm-batch-title">
      <div className={styles.sectionHead}>
        <h2 id="llm-batch-title" className={styles.sectionTitle}>{t('admin.ops.opsLlmUsagePage.batch')}</h2>
        {d.source.exists && (
          <p className={c.meta}>
            <span>{t('admin.ops.opsLlmUsagePage.range')}{' '}<b>{fmtDateTime(d.since)}</b>{' '}{t('admin.ops.opsLlmUsagePage.text')}{' '}<b>{fmtDateTime(d.until)}</b>{t('admin.ops.opsLlmUsagePage.daysCutInTaipeiTime')}</span>
            <span>{t('admin.ops.opsLlmUsagePage.inRange')}{' '}<b>{fmtInt(d.source.lines_in_range)}</b>{' '}{t('admin.ops.opsLlmUsagePage.lines')}</span>
            {d.source.lines_invalid > 0 && <span>{t('admin.ops.opsLlmUsagePage.unparseable')}{' '}<b>{fmtInt(d.source.lines_invalid)}</b>{' '}{t('admin.ops.opsLlmUsagePage.linesSkipped')}</span>}
          </p>
        )}
      </div>
      {!d.source.exists ? (
        <p className={styles.idle}>{t('admin.ops.opsLlmUsagePage.noUsageRecordsYetData')}</p>
      ) : (
        <>
          {d.source.truncated && (
            <p className={c.banner} data-tone="warn">
              {t('admin.ops.opsLlmUsagePage.theFileExceedsTheRead', { earliest_ts: fmtDateTime(d.source.earliest_ts) })}
            </p>
          )}
          {d.totals.calls === 0 ? (
            <p className={styles.idle}>{t('admin.ops.opsLlmUsagePage.noBatchLlmCallsIn')}</p>
          ) : (
            <>
              <Totals t={d.totals} costAvailable={d.cost_available} />
              <Breakdown d={d} />
            </>
          )}
        </>
      )}
    </section>
  )
}

/** 線上（web 行程的問答、忠實度抽查…）：research.llm_usage_daily 的台北日彙總，只有 metadata、不出個人維度。 */
function OnlineSection({ online }: { online: LlmUsageOnline | null | undefined }) {
  return (
    <section className={styles.section} aria-labelledby="llm-online-title">
      <div className={styles.sectionHead}>
        <h2 id="llm-online-title" className={styles.sectionTitle}>{t('admin.ops.opsLlmUsagePage.onlineQAFaithfulnessSpot')}</h2>
        {online?.available && (
          <p className={c.meta}>
            <span>{t('admin.ops.opsLlmUsagePage.taipeiDaysFirstAndLast', { since_day: online.since_day, until_day: online.until_day })}</span>
            <span>{t('admin.ops.opsLlmUsagePage.attributedTo')}{' '}<b>{fmtInt(online.attributed_users)}</b>{' '}{t('admin.ops.opsLlmUsagePage.users')}</span>
            {online.unattributed_calls > 0 && <span>{t('admin.ops.opsLlmUsagePage.unattributed')}{' '}<b>{fmtInt(online.unattributed_calls)}</b>{' '}{t('admin.ops.opsLlmUsagePage.calls3')}</span>}
          </p>
        )}
      </div>
      {online == null ? (
        <p className={styles.idle}>{t('admin.ops.opsLlmUsagePage.theBackendReturnedNoOnline')}</p>
      ) : !online.available ? (
        <p className={c.banner} data-tone="warn" role="alert">{online.error ?? t('admin.ops.opsLlmUsagePage.onlineUsageTemporarilyUnavailable')}{t('admin.ops.opsLlmUsagePage.batchUsageUnaffected')}</p>
      ) : online.totals.calls === 0 ? (
        <p className={styles.idle}>{t('admin.ops.opsLlmUsagePage.noOnlineLlmCallsIn')}</p>
      ) : (
        <>
          <Totals t={online.totals} costAvailable={online.cost_available} prefix={t('admin.ops.opsLlmUsagePage.online')} />
          <Breakdown d={online} prefix={t('admin.ops.opsLlmUsagePage.online2')} />
        </>
      )}
    </section>
  )
}

function Usage({ d }: { d: LlmUsageResponse }) {
  const combined = d.combined
  return (
    <>
      {combined && d.online?.available && combined.totals.calls > 0 && (
        <section className={styles.section} aria-labelledby="llm-total-title">
          <h2 id="llm-total-title" className={styles.sectionTitle}>{t('admin.ops.opsLlmUsagePage.totalBatchOnline')}</h2>
          <Totals t={combined.totals} costAvailable={combined.cost_available} prefix={t('admin.ops.opsLlmUsagePage.total')} />
        </section>
      )}
      <DailyCard d={d} />
      <BatchSection d={d} />
      <OnlineSection online={d.online} />
    </>
  )
}

/**
 * LLM 用量：批次（`data/llm_usage.jsonl`，摘要、標題、摘錄、訊號、簡報、標註…）與線上（`research.llm_usage_daily`，
 * 問答與忠實度抽查，Admin v2）兩個來源，依任務、模型、日期（台北時間）。費用真值看供應商後台，這裡是歸因。
 * 個人用量只在配額頁。
 */
export default function OpsLlmUsagePage() {
  const [days, setDays] = useState('30')
  const q = useLlmUsage(Number(days))
  return (
    <>
      <PageHeader
        summary={t('admin.ops.opsLlmUsagePage.tokenTotalsOfBatchAnd')}
        help={(
          <>
            {t('admin.ops.opsLlmUsagePage.twoSourcesBatch')}<code>data/llm_usage.jsonl</code>{t('admin.ops.opsLlmUsagePage.summariesTitlesTakeawaysSignalsBriefs')}
          </>
        )}
        actions={<Segmented label={t('admin.ops.opsLlmUsagePage.range')} options={RANGES} value={days} onChange={setDays} />}
      />
      {q.isPending ? <p className={c.empty}>{t('admin.ops.opsLlmUsagePage.loading')}</p> : q.isError ? (
        <OpsQueryError error={q.error} what={t('admin.ops.opsLlmUsagePage.llmUsage')} />
      ) : <Usage d={q.data} />}
    </>
  )
}
