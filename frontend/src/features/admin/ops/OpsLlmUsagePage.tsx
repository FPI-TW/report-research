import { useState } from 'react'
import type { LlmUsageResponse, LlmUsageTotals } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { OpsQueryError } from './OpsShared'
import { useLlmUsage } from './useOps'
import adminStyles from '../Admin.module.css'
import styles from './Ops.module.css'

const RANGES = [
  { days: 7, label: '最近 7 天' },
  { days: 30, label: '最近 30 天' },
  { days: 90, label: '最近 90 天' },
]

const fmtInt = (n: number) => n.toLocaleString('zh-TW')

function avgMs(t: LlmUsageTotals): string {
  return t.calls ? `${(t.total_ms / t.calls / 1000).toFixed(1)} 秒` : '—'
}

function UsageTable({ caption, keyLabel, rows, showCost }: {
  caption: string; keyLabel: string; rows: (LlmUsageTotals & { key: string })[]; showCost: boolean
}) {
  return (
    <div className={adminStyles.tableWrap}>
      <table className={adminStyles.table} aria-label={caption}>
        <thead>
          <tr>
            <th>{keyLabel}</th><th>呼叫</th><th>失敗</th><th>輸入（快取命中）</th><th>輸入（未命中）</th>
            <th>輸出</th><th>推理</th><th>平均耗時</th>{showCost && <th>費用</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map(r => (
            <tr key={r.key}>
              <td className={adminStyles.wrapCell}>{r.key}</td>
              <td className={adminStyles.num}>{fmtInt(r.calls)}</td>
              <td className={adminStyles.num}>{r.failures ? <b>{fmtInt(r.failures)}</b> : 0}</td>
              <td className={adminStyles.num}>{fmtInt(r.prompt_hit_tokens)}</td>
              <td className={adminStyles.num}>{fmtInt(r.prompt_miss_tokens)}</td>
              <td className={adminStyles.num}>{fmtInt(r.completion_tokens)}</td>
              <td className={adminStyles.num}>{fmtInt(r.reasoning_tokens)}</td>
              <td className={adminStyles.num}>{avgMs(r)}</td>
              {showCost && <td className={adminStyles.num}>{r.cost != null ? r.cost.toFixed(4) : '—'}</td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Totals({ d }: { d: LlmUsageResponse }) {
  const t = d.totals
  const cards: [string, string, string][] = [
    ['呼叫次數', fmtInt(t.calls), `失敗 ${fmtInt(t.failures)}・無用量 ${fmtInt(t.calls_without_tokens)}`],
    ['輸入 token', fmtInt(t.prompt_hit_tokens + t.prompt_miss_tokens), `快取命中 ${fmtInt(t.prompt_hit_tokens)}`],
    ['輸出 token', fmtInt(t.completion_tokens), `其中推理 ${fmtInt(t.reasoning_tokens)}`],
  ]
  if (d.cost_available && t.cost != null) cards.push(['費用', t.cost.toFixed(4), '行內帶 cost 欄的加總'])
  return (
    <div className={styles.tierGrid}>
      {cards.map(([name, value, hint]) => (
        <div key={name} className={styles.tierCard} aria-label={name}>
          <div className={styles.tierName}>{name}</div>
          <div className={styles.tierCount}>{value}</div>
          <div className={styles.tierHint}>{hint}</div>
        </div>
      ))}
    </div>
  )
}

/**
 * 批次 LLM 用量（`data/llm_usage.jsonl` 的彙總）：依任務、模型、日期（台北時間）。只含批次（摘要、標題、
 * 摘錄、訊號、簡報、標註…），線上問答不寫這份檔；費用真值看 DeepSeek 餘額差分，這裡是歸因。
 */
export default function OpsLlmUsagePage() {
  const [days, setDays] = useState(30)
  const q = useLlmUsage(days)
  return (
    <section className={adminStyles.card} aria-labelledby="llm-usage-title">
      <div className={adminStyles.cardHead}>
        <h2 id="llm-usage-title" className={adminStyles.ctitle}>LLM 用量</h2>
        <label className={adminStyles.field}>
          範圍
          <select value={days} onChange={e => setDays(Number(e.target.value))}>
            {RANGES.map(r => <option key={r.days} value={r.days}>{r.label}</option>)}
          </select>
        </label>
      </div>
      <p className={adminStyles.hint}>
        只含批次的 LLM 呼叫（線上問答不記在這份檔）。費用真值看 DeepSeek 餘額差分，這裡用來看哪個任務、哪個模型花了多少 token。
      </p>
      <div className={adminStyles.spacer} />
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what="LLM 用量" />
      ) : !q.data.source.exists ? (
        <p className={adminStyles.idle}>還沒有用量紀錄（data/llm_usage.jsonl 不存在；批次第一次呼叫 LLM 時才會建立）。</p>
      ) : (
        <>
          <p className={styles.meta}>
            <span>範圍 <b>{fmtDateTime(q.data.since)}</b> ～ <b>{fmtDateTime(q.data.until)}</b>（日期以台北時間切）</span>
            <span>範圍內 <b>{fmtInt(q.data.source.lines_in_range)}</b> 筆</span>
            {q.data.source.lines_invalid > 0 && <span>無法解析 <b>{fmtInt(q.data.source.lines_invalid)}</b> 行（已略過）</span>}
          </p>
          {q.data.source.truncated && (
            <p className={styles.warnNote}>
              檔案超過讀取上限，只涵蓋 {fmtDateTime(q.data.source.earliest_ts)} 之後的紀錄。
            </p>
          )}
          <div className={adminStyles.spacer} />
          {q.data.totals.calls === 0 ? (
            <p className={adminStyles.idle}>這段期間沒有任何批次 LLM 呼叫。</p>
          ) : (
            <>
              <Totals d={q.data} />
              <div className={adminStyles.spacer} />
              <h3 className={styles.groupTitle}>依任務</h3>
              <UsageTable caption="依任務" keyLabel="任務" showCost={q.data.cost_available}
                rows={q.data.by_task.map(r => ({ ...r, key: r.task }))} />
              <div className={adminStyles.spacer} />
              <h3 className={styles.groupTitle}>依模型</h3>
              <UsageTable caption="依模型" keyLabel="模型" showCost={q.data.cost_available}
                rows={q.data.by_model.map(r => ({ ...r, key: r.model }))} />
              <div className={adminStyles.spacer} />
              <h3 className={styles.groupTitle}>依日期（新→舊）</h3>
              <UsageTable caption="依日期" keyLabel="日期" showCost={q.data.cost_available}
                rows={[...q.data.by_day].reverse().map(r => ({ ...r, key: r.day }))} />
            </>
          )}
        </>
      )}
    </section>
  )
}
