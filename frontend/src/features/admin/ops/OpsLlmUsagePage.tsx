import { useState } from 'react'
import { Card, PageHeader, Segmented, StatTile, Tabs, TrendBars, type SegOption } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { LlmUsageOnline, LlmUsageResponse, LlmUsageTotals } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { dailyAxis, dailyCalls, dayIn } from './data/usageSeries'
import { OpsQueryError } from './OpsShared'
import { useLlmUsage } from './useOps'
import styles from './data/Data.module.css'

const RANGES: readonly SegOption<string>[] = [
  { value: '7', label: '7 天' },
  { value: '30', label: '30 天' },
  { value: '90', label: '90 天' },
]

type View = 'task' | 'model' | 'day'
const VIEWS: readonly SegOption<View>[] = [
  { value: 'task', label: '依任務' },
  { value: 'model', label: '依模型' },
  { value: 'day', label: '依日期' },
]
const VIEW_KEY_LABEL: Record<View, string> = { task: '任務', model: '模型', day: '日期' }

const fmtInt = (n: number) => n.toLocaleString('zh-TW')

function avgMs(t: LlmUsageTotals): string {
  return t.calls ? `${(t.total_ms / t.calls / 1000).toFixed(1)} 秒` : '—'
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
            <th>{keyLabel}</th><th className={c.tdRight}>呼叫</th><th className={c.tdRight}>失敗</th>
            <th className={c.tdRight}>輸入（快取命中）</th><th className={c.tdRight}>輸入（未命中）</th>
            <th className={c.tdRight}>輸出</th><th className={c.tdRight}>推理</th><th className={c.tdRight}>平均耗時</th>
            {showCost && <th className={c.tdRight}>費用</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.key}>
              <td className={c.mono}>{r.key}</td>
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
function Totals({ t, costAvailable, prefix = '' }: { t: LlmUsageTotals; costAvailable: boolean; prefix?: string }) {
  const tiles: [string, string, string][] = [
    ['呼叫次數', fmtInt(t.calls), `失敗 ${fmtInt(t.failures)}・無用量 ${fmtInt(t.calls_without_tokens)}`],
    ['輸入 token', fmtInt(t.prompt_hit_tokens + t.prompt_miss_tokens), `快取命中 ${fmtInt(t.prompt_hit_tokens)}`],
    ['輸出 token', fmtInt(t.completion_tokens), `其中推理 ${fmtInt(t.reasoning_tokens)}`],
  ]
  if (costAvailable && t.cost != null) tiles.push(['費用', t.cost.toFixed(4), '行內帶 cost 欄的加總'])
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
        <Tabs label={`${prefix}用量明細`} tabs={VIEWS} value={view} onChange={setView} />
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
    <Card title="每日呼叫" hint={withOnline ? '批次＋線上' : '只有批次'} labelledBy="llm-daily-title"
      right={<span>單日最高 <span className={c.num}>{fmtInt(peak)}</span> 次</span>}>
      <div className={styles.chartBody}>
        <TrendBars bars={bars} label="每日呼叫次數" format={v => `${fmtInt(v)} 次`} highlightLast axis={dailyAxis(bars)} />
      </div>
    </Card>
  )
}

function BatchSection({ d }: { d: LlmUsageResponse }) {
  return (
    <section className={styles.section} aria-labelledby="llm-batch-title">
      <div className={styles.sectionHead}>
        <h2 id="llm-batch-title" className={styles.sectionTitle}>批次</h2>
        {d.source.exists && (
          <p className={c.meta}>
            <span>範圍 <b>{fmtDateTime(d.since)}</b> ～ <b>{fmtDateTime(d.until)}</b>（日期以台北時間切）</span>
            <span>範圍內 <b>{fmtInt(d.source.lines_in_range)}</b> 筆</span>
            {d.source.lines_invalid > 0 && <span>無法解析 <b>{fmtInt(d.source.lines_invalid)}</b> 行（已略過）</span>}
          </p>
        )}
      </div>
      {!d.source.exists ? (
        <p className={styles.idle}>還沒有用量紀錄（data/llm_usage.jsonl 不存在；批次第一次呼叫 LLM 時才會建立）。</p>
      ) : (
        <>
          {d.source.truncated && (
            <p className={c.banner} data-tone="warn">
              檔案超過讀取上限，只涵蓋 {fmtDateTime(d.source.earliest_ts)} 之後的紀錄。
            </p>
          )}
          {d.totals.calls === 0 ? (
            <p className={styles.idle}>這段期間沒有任何批次 LLM 呼叫。</p>
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
        <h2 id="llm-online-title" className={styles.sectionTitle}>線上（問答、忠實度抽查）</h2>
        {online?.available && (
          <p className={c.meta}>
            <span>{online.since_day} ～ {online.until_day}（台北日，頭尾兩天整天計入）</span>
            <span>歸因到 <b>{fmtInt(online.attributed_users)}</b> 位使用者</span>
            {online.unattributed_calls > 0 && <span>未歸因 <b>{fmtInt(online.unattributed_calls)}</b> 次</span>}
          </p>
        )}
      </div>
      {online == null ? (
        <p className={styles.idle}>後端沒有回傳線上用量。</p>
      ) : !online.available ? (
        <p className={c.banner} data-tone="warn" role="alert">{online.error ?? '線上用量暫時讀不到'}（批次用量不受影響）</p>
      ) : online.totals.calls === 0 ? (
        <p className={styles.idle}>這段期間沒有任何線上 LLM 呼叫。</p>
      ) : (
        <>
          <Totals t={online.totals} costAvailable={online.cost_available} prefix="線上" />
          <Breakdown d={online} prefix="線上・" />
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
          <h2 id="llm-total-title" className={styles.sectionTitle}>合計（批次＋線上）</h2>
          <Totals t={combined.totals} costAvailable={combined.cost_available} prefix="合計" />
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
 * 問答與忠實度抽查，Admin v2）兩個來源，依任務、模型、日期（台北時間）。費用真值看 DeepSeek 餘額差分，這裡是歸因。
 * 個人用量只在配額頁。
 */
export default function OpsLlmUsagePage() {
  const [days, setDays] = useState('30')
  const q = useLlmUsage(Number(days))
  return (
    <>
      <PageHeader
        summary="批次與線上 LLM 呼叫的 token 彙總；日期以台北時間切日。"
        help={(
          <>
            兩個來源：批次（<code>data/llm_usage.jsonl</code>，摘要、標題、摘錄、訊號、簡報、標註）與線上（問答、忠實度抽查，記在 DB 的台北日彙總）。
            只記 metadata、不記問題與答案。費用真值看 DeepSeek 餘額差分，這裡用來看哪個任務、哪個模型花了多少 token；個人用量在「配額」頁。
          </>
        )}
        actions={<Segmented label="範圍" options={RANGES} value={days} onChange={setDays} />}
      />
      {q.isPending ? <p className={c.empty}>載入中…</p> : q.isError ? (
        <OpsQueryError error={q.error} what="LLM 用量" />
      ) : <Usage d={q.data} />}
    </>
  )
}
