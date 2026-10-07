import { useState } from 'react'
import type { LlmUsageOnline, LlmUsageTotals } from '../../../lib/generated/adminApi'
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

function Totals({ t, costAvailable, prefix = '' }: { t: LlmUsageTotals; costAvailable: boolean; prefix?: string }) {
  const cards: [string, string, string][] = [
    ['呼叫次數', fmtInt(t.calls), `失敗 ${fmtInt(t.failures)}・無用量 ${fmtInt(t.calls_without_tokens)}`],
    ['輸入 token', fmtInt(t.prompt_hit_tokens + t.prompt_miss_tokens), `快取命中 ${fmtInt(t.prompt_hit_tokens)}`],
    ['輸出 token', fmtInt(t.completion_tokens), `其中推理 ${fmtInt(t.reasoning_tokens)}`],
  ]
  if (costAvailable && t.cost != null) cards.push(['費用', t.cost.toFixed(4), '行內帶 cost 欄的加總'])
  return (
    <div className={styles.tierGrid}>
      {cards.map(([name, value, hint]) => (
        <div key={name} className={styles.tierCard} aria-label={`${prefix}${name}`}>
          <div className={styles.tierName}>{name}</div>
          <div className={styles.tierCount}>{value}</div>
          <div className={styles.tierHint}>{hint}</div>
        </div>
      ))}
    </div>
  )
}

/** 依任務／模型／日期三張表；prefix 讓批次與線上兩段的表格名稱不重複。 */
function Breakdown({ d, prefix = '' }: {
  d: Pick<LlmUsageOnline, 'by_task' | 'by_model' | 'by_day' | 'cost_available'>; prefix?: string
}) {
  return (
    <>
      <h3 className={styles.groupTitle}>依任務</h3>
      <UsageTable caption={`${prefix}依任務`} keyLabel="任務" showCost={d.cost_available}
        rows={d.by_task.map(r => ({ ...r, key: r.task }))} />
      <div className={adminStyles.spacer} />
      <h3 className={styles.groupTitle}>依模型</h3>
      <UsageTable caption={`${prefix}依模型`} keyLabel="模型" showCost={d.cost_available}
        rows={d.by_model.map(r => ({ ...r, key: r.model }))} />
      <div className={adminStyles.spacer} />
      <h3 className={styles.groupTitle}>依日期（新→舊）</h3>
      <UsageTable caption={`${prefix}依日期`} keyLabel="日期" showCost={d.cost_available}
        rows={[...d.by_day].reverse().map(r => ({ ...r, key: r.day }))} />
    </>
  )
}

/** 線上（web 行程的問答、忠實度抽查…）：research.llm_usage_daily 的台北日彙總，只有 metadata、不出個人維度。 */
function OnlineSection({ online }: { online: LlmUsageOnline | null | undefined }) {
  return (
    <section aria-labelledby="llm-online-title">
      <h3 id="llm-online-title" className={styles.groupTitle}>線上（問答、忠實度抽查）</h3>
      {online == null ? (
        <p className={adminStyles.idle}>後端沒有回傳線上用量。</p>
      ) : !online.available ? (
        <p className={styles.warnNote} role="alert">{online.error ?? '線上用量暫時讀不到'}（批次用量不受影響）</p>
      ) : (
        <>
          <p className={styles.meta}>
            <span>{online.since_day} ～ {online.until_day}（台北日，頭尾兩天整天計入）</span>
            <span>歸因到 <b>{fmtInt(online.attributed_users)}</b> 位使用者</span>
            {online.unattributed_calls > 0 && <span>未歸因 <b>{fmtInt(online.unattributed_calls)}</b> 次</span>}
          </p>
          <div className={adminStyles.spacer} />
          {online.totals.calls === 0 ? (
            <p className={adminStyles.idle}>這段期間沒有任何線上 LLM 呼叫。</p>
          ) : (
            <>
              <Totals t={online.totals} costAvailable={online.cost_available} prefix="線上" />
              <div className={adminStyles.spacer} />
              <Breakdown d={online} prefix="線上・" />
            </>
          )}
        </>
      )}
    </section>
  )
}

/**
 * LLM 用量：批次（`data/llm_usage.jsonl`，摘要、標題、摘錄、訊號、簡報、標註…）與線上（`research.llm_usage_daily`，
 * 問答與忠實度抽查，Admin v2）兩個來源，依任務、模型、日期（台北時間）。費用真值看 DeepSeek 餘額差分，這裡是歸因。
 * 個人用量只在配額頁。
 */
export default function OpsLlmUsagePage() {
  const [days, setDays] = useState(30)
  const q = useLlmUsage(days)
  const combined = q.data?.combined
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
        批次（jsonl）與線上（問答、忠實度抽查，記在 DB）兩個來源，只記 metadata、不記問題與答案。費用真值看 DeepSeek 餘額差分，這裡用來看哪個任務、哪個模型花了多少 token；個人用量在「配額」頁。
      </p>
      <div className={adminStyles.spacer} />
      {q.isPending ? (
        <p className={adminStyles.idle}>載入中…</p>
      ) : q.isError ? (
        <OpsQueryError error={q.error} what="LLM 用量" />
      ) : (
        <>
          {combined && q.data.online?.available && combined.totals.calls > 0 && (
            <>
              <h3 className={styles.groupTitle}>合計（批次＋線上）</h3>
              <Totals t={combined.totals} costAvailable={combined.cost_available} prefix="合計" />
              <div className={adminStyles.spacer} />
            </>
          )}
          <h3 className={styles.groupTitle}>批次</h3>
          {!q.data.source.exists ? (
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
                  <Totals t={q.data.totals} costAvailable={q.data.cost_available} />
                  <div className={adminStyles.spacer} />
                  <Breakdown d={q.data} />
                </>
              )}
            </>
          )}
          <div className={adminStyles.spacer} />
          <OnlineSection online={q.data.online} />
        </>
      )}
    </section>
  )
}
