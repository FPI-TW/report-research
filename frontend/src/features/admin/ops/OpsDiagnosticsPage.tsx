import { useQuery } from '@tanstack/react-query'
import { Fragment, useState, type ReactNode } from 'react'
import { adminApi, type DiagnosticsResponse } from '../../../lib/generated/adminApi'
import { copyText } from '../../../lib/clipboard'
import { fmtDateTime } from '../auditLabels'
import { Button, Card as ConsoleCard, PageHeader, Status, toast, type LampTone } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { OpsQueryError } from './OpsShared'
import styles from './Diagnostics.module.css'

type Tone = 'ok' | 'warn' | 'fail' | 'idle'
const LAMP_TONE: Record<Tone, LampTone> = { ok: 'ok', warn: 'warn', fail: 'bad', idle: 'idle' }

const SCHEMA_LABELS: Record<DiagnosticsResponse['schema_info']['status'], [string, Tone]> = {
  ok: ['一致', 'ok'], behind: ['DB 落後', 'fail'], ahead: ['DB 超前', 'fail'], unversioned: ['未接管', 'warn'],
  ambiguous: ['不明確', 'fail'], error: ['查不到', 'fail'],
}
const STORAGE_LABELS: Record<string, [string, Tone]> = {
  ok: ['正常', 'ok'], degraded: ['異常', 'fail'], unknown: ['尚未探測', 'idle'], disabled: ['未啟用（local）', 'idle'],
}
const LLM_LABELS: Record<string, [string, Tone]> = {
  ok: ['正常', 'ok'], low: ['餘額偏低', 'warn'], exhausted: ['已用罄', 'fail'], auth_failed: ['金鑰失效', 'fail'],
  unreachable: ['連不上', 'fail'], indeterminate: ['判斷不出', 'fail'], unknown: ['尚未查詢', 'idle'],
  disabled: ['未使用', 'idle'],
}
const WARMUP_LABELS: Record<string, string> = {
  skipped: '已略過（SKIP_WARMUP=1）', absent: '沒有暖機工作', running: '暖機中', done: '已結束', failed: '失敗',
  cancelled: '已取消',
}
const DAILY_REASON: Record<string, string> = {
  missing: '還沒有狀態檔', too_large: '狀態檔過大，未讀取', invalid: '狀態檔格式不對', error: '讀取失敗',
}

function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  // 外面包一層：Status 是 inline-flex，接在一行文字前面時基線會落在燈號上，整塊往下掉
  return <span className={styles.pill}><Status tone={LAMP_TONE[tone]}>{children}</Status></span>
}

/** 等寬且可在任意字元斷行：commit、路徑、bundle 檔名。 */
function Mono({ children }: { children: ReactNode }) {
  return <span className={`${c.mono} ${styles.breakAll}`}>{children}</span>
}

function yesNo(v: boolean | null | undefined, yes = '是', no = '否'): string {
  if (v == null) return '—'
  return v ? yes : no
}

function fmtBytes(n: number | null | undefined): string {
  if (n == null) return '—'
  if (n >= 1024 ** 3) return `${(n / 1024 ** 3).toFixed(2)} GiB`
  return `${(n / 1024 ** 2).toFixed(0)} MiB`
}

function fmtDuration(s: number | null | undefined): string {
  if (s == null) return '—'
  if (s < 60) return `${Math.round(s)} 秒`
  if (s < 3600) return `${Math.round(s / 60)} 分鐘`
  if (s < 86_400) return `${(s / 3600).toFixed(1)} 小時`
  return `${(s / 86_400).toFixed(1)} 天`
}

function fmtMs(ms: number | null | undefined): string {
  return ms == null ? '—' : `${ms.toFixed(1)} ms`
}

function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 12) : '—'
}

/** 鍵值清單；值可以是任意節點。 */
function KV({ rows, label }: { rows: [string, ReactNode][]; label: string }) {
  return (
    <dl className={`${c.kv} ${styles.kv}`} aria-label={label}>
      {rows.map(([k, v]) => (
        <Fragment key={k}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

function SectionError({ error }: { error: string | null | undefined }) {
  if (!error) return null
  return <p className={c.error} role="alert">這一段讀取失敗：{error}</p>
}

function Card({ id, title, right, children }: { id: string; title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <ConsoleCard title={title} labelledBy={id} right={right}>
      <div className={styles.body}>{children}</div>
    </ConsoleCard>
  )
}

function Sub({ children }: { children: ReactNode }) {
  return <h3 className={styles.sub}>{children}</h3>
}

function Warn({ children }: { children: ReactNode }) {
  return <p className={c.banner} data-tone="warn">{children}</p>
}

function useDiagnostics() {
  return useQuery<DiagnosticsResponse>({
    queryKey: ['admin', 'ops', 'diagnostics'],
    queryFn: () => adminApi.getDiagnostics(),
    retry: false,
  })
}

/**
 * 「複製診斷包」：整份回應（後端已去除祕密）排版成 JSON。區網 HTTP 沒有 navigator.clipboard，走 copyText。
 * 結果用 toast 說；自動複製失敗時頁面上另外給一個可以手動複製的文字框（`failed`）。
 */
function useCopyBundle(data: DiagnosticsResponse | undefined) {
  const [failed, setFailed] = useState(false)
  const text = data ? JSON.stringify(data, null, 2) : ''
  const copy = () => {
    copyText(text).then(
      () => { setFailed(false); toast('已複製（JSON，不含祕密）') },
      () => { setFailed(true); toast('無法自動複製，請從下方手動複製', { tone: 'bad' }) },
    )
  }
  return { copy, failed, text }
}

function Summary({ d }: { d: DiagnosticsResponse }) {
  const [schemaLabel, schemaTone] = SCHEMA_LABELS[d.schema_info.status]
  const [storageLabel, storageTone] = STORAGE_LABELS[d.checks.storage.state] ?? [d.checks.storage.state, 'warn']
  const llmState = d.checks.llm.state
  const [llmLabel, llmTone] = LLM_LABELS[llmState] ?? [llmState, 'warn']
  const git = d.versions.git
  const items: [string, ReactNode, string][] = [
    ['資料庫', <Pill tone={d.checks.db.ok ? 'ok' : 'fail'}>{d.checks.db.ok ? '連得上' : '連不上'}</Pill>,
      d.checks.db.ok ? fmtMs(d.checks.db.latency_ms) : (d.checks.db.error ?? '')],
    ['Schema 版本', <Pill tone={schemaTone}>{schemaLabel}</Pill>,
      `DB ${d.schema_info.db_revisions.join(',') || '—'}／程式 ${d.schema_info.code_heads.join(',') || '—'}`],
    ['物件儲存', <Pill tone={storageTone}>{storageLabel}</Pill>, d.config.object_storage_mode ?? ''],
    ['DeepSeek', <Pill tone={llmTone}>{llmLabel}</Pill>, d.checks.llm.key_configured ? '已設金鑰' : '未設金鑰'],
    ['維運代理', <Pill tone={d.checks.ops_agent.ok ? 'ok' : 'warn'}>{d.checks.ops_agent.ok ? '連得上' : '連不上'}</Pill>,
      d.checks.ops_agent.ok ? `${d.checks.ops_agent.services ?? '—'} 個服務` : (d.checks.ops_agent.error ?? '')],
    ['程式版本', git?.restart_pending
      ? <Pill tone="warn">待重啟</Pill>
      : <Pill tone={git?.available ? 'ok' : 'idle'}>{git?.available ? '與磁碟一致' : '無法取得'}</Pill>,
      shortSha(git?.commit_at_start)],
  ]
  return (
    <div className={c.tiles} role="list" aria-label="診斷摘要">
      {items.map(([label, pill, detail]) => (
        <div key={label} className={c.tile} role="listitem">
          <span className={c.tileLabel}>{label}</span>
          <span>{pill}</span>
          {detail && <span className={styles.summaryDetail}>{detail}</span>}
        </div>
      ))}
    </div>
  )
}

function VersionsCard({ d }: { d: DiagnosticsResponse }) {
  const v = d.versions
  const git = v.git
  const fe = v.frontend
  return (
    <Card id="diag-versions" title="版本">
      <SectionError error={v.error} />
      {git?.restart_pending && (
        <Warn>
          磁碟上的程式（{shortSha(git.commit_on_disk)}）與這個行程啟動時（{shortSha(git.commit_at_start)}）不同：
          程式已更新但 web 還沒重啟。
        </Warn>
      )}
      <Sub>程式</Sub>
      <KV label="程式版本" rows={git?.available ? [
        ['啟動時 commit', <Mono>{git.commit_at_start ?? '—'}</Mono>],
        ['啟動時分支', git.branch_at_start ?? '（detached）'],
        ['磁碟上 commit', <Mono>{git.commit_on_disk ?? '—'}</Mono>],
      ] : [['commit', `無法取得（${git?.reason ?? '未知'}）`]]} />
      <Sub>前端</Sub>
      <KV label="前端 build" rows={fe?.available ? [
        ['build 時間', fmtDateTime(fe.built_at)],
        ['入口 bundle', <Mono>{(fe.entry_assets ?? []).join('、') || '—'}</Mono>],
      ] : [['build', '找不到 frontend/dist/index.html（要先 make build-web）']]} />
      <Sub>執行環境與套件</Sub>
      <KV label="套件版本" rows={[
        ['Python', v.python ?? '—'],
        ['平台', v.platform ?? '—'],
        ...Object.entries(v.packages ?? {}).map(([name, ver]): [string, ReactNode] => [name, ver == null ? '未安裝' : String(ver)]),
      ]} />
    </Card>
  )
}

function SchemaCard({ d }: { d: DiagnosticsResponse }) {
  const s = d.schema_info
  const dc = s.daily_check
  const [label, tone] = SCHEMA_LABELS[s.status]
  return (
    <Card id="diag-schema" title="Schema" right={<Pill tone={tone}>{label}</Pill>}>
      <SectionError error={s.error} />
      <KV label="schema 版本" rows={[
        ['DB revision', s.db_revisions.join('、') || '—'],
        ['程式 head', s.code_heads.join('、') || '—'],
        ...(s.pending.length ? [['尚未套用', s.pending.join('、')] as [string, ReactNode]] : []),
      ]} />
      <Sub>每日檢查（report-mark-schema-check）</Sub>
      {dc.available ? (
        <>
          <KV label="每日檢查" rows={[
            ['最後一次', `${fmtDateTime(dc.checked_at)}${dc.age_hours != null ? `（${dc.age_hours.toFixed(1)} 小時前）` : ''}`],
            ['模式', dc.mode ?? '—'],
            ['退出碼', dc.exit_code == null ? '—' : `${dc.exit_code}${dc.alert ? '（會告警）' : ''}`],
            ['版本／drift', `${dc.version_status ?? '—'}／${dc.drift_status ?? '—'}${dc.drift_count != null ? `（${dc.drift_count} 項）` : ''}`],
            ['問題', dc.problems?.length ? dc.problems.join('、') : '無'],
            ['說明', dc.message ?? '—'],
          ]} />
          {dc.stale && <Warn>狀態檔超過 36 小時沒更新：確認 timer 有在跑、或狀態檔寫得進去。</Warn>}
        </>
      ) : (
        <p className={c.muted}>{DAILY_REASON[dc.unavailable_reason ?? ''] ?? '無法讀取'}。</p>
      )}
    </Card>
  )
}

function ChecksCard({ d }: { d: DiagnosticsResponse }) {
  const ck = d.checks
  const [storageLabel, storageTone] = STORAGE_LABELS[ck.storage.state] ?? [ck.storage.state, 'warn']
  const [llmLabel, llmTone] = LLM_LABELS[ck.llm.state] ?? [ck.llm.state, 'warn']
  return (
    <Card id="diag-checks" title="連通性">
      <p className={styles.meta}>R2 與 DeepSeek 只讀健康檢查上一次的結論，診斷頁不會另外呼叫（計費）。</p>
      <KV label="連通性" rows={[
        ['資料庫', <><Pill tone={ck.db.ok ? 'ok' : 'fail'}>{ck.db.ok ? '正常' : '失敗'}</Pill>{' '}
          {ck.db.ok ? `SELECT 1 ${fmtMs(ck.db.latency_ms)}；PostgreSQL ${ck.db.server_version ?? '—'}` : ck.db.error}</>],
        ['物件儲存', <><Pill tone={storageTone}>{storageLabel}</Pill>{' '}
          {ck.storage.last_probe_age_s != null ? `上次探測 ${fmtDuration(ck.storage.last_probe_age_s)}前` : ''}
          {ck.storage.consecutive_failures ? `；連續失敗 ${ck.storage.consecutive_failures} 次` : ''}
          {ck.storage.error ? `（${ck.storage.error}）` : ''}</>],
        ['DeepSeek', <><Pill tone={llmTone}>{llmLabel}</Pill>{' '}
          {ck.llm.last_check_age_s != null ? `上次查詢 ${fmtDuration(ck.llm.last_check_age_s)}前` : ''}
          {ck.llm.quota_latched ? '；本行程收過 402' : ''}
          {!ck.llm.ask_uses_http ? '；問答主答未走 DeepSeek' : ''}
          {ck.llm.error ? `（${ck.llm.error}）` : ''}</>],
        ['維運代理', <><Pill tone={ck.ops_agent.ok ? 'ok' : 'warn'}>{ck.ops_agent.ok ? '正常' : '連不上'}</Pill>{' '}
          {ck.ops_agent.ok ? `${fmtMs(ck.ops_agent.latency_ms)}；${ck.ops_agent.services ?? '—'} 個服務` : ck.ops_agent.error}</>],
      ]} />
    </Card>
  )
}

function RuntimeCard({ d }: { d: DiagnosticsResponse }) {
  const r = d.runtime
  const m = d.models
  const p = d.db_pool
  return (
    <Card id="diag-runtime" title="執行環境">
      <SectionError error={r.error} />
      <KV label="行程" rows={[
        ['主機', r.hostname ?? '—'],
        ['PID', r.pid ?? '—'],
        ['啟動時間', `${fmtDateTime(r.started_at)}（已執行 ${fmtDuration(r.uptime_s)}）`],
        ['記憶體 RSS', `${fmtBytes(r.rss_bytes)}（峰值 ${fmtBytes(r.peak_rss_bytes)}）`],
        ['執行緒', r.threads ?? '—'],
        ['時區', r.timezone ? `${r.timezone.name} ${r.timezone.utc_offset}${r.timezone.tz_env ? `（TZ=${r.timezone.tz_env}）` : ''}` : '—'],
        ['Python', <Mono>{r.python_executable ?? '—'}</Mono>],
      ]} />
      <Sub>模型</Sub>
      <SectionError error={m.error} />
      <KV label="模型" rows={[
        ['嵌入模型', `${m.embed_model ?? '—'}：${m.embed_loaded ? '已載入' : '尚未載入'}`],
        ['rerank', m.rerank_load_failed ? '載入失敗（已熔斷）' : m.rerank_loaded ? '已載入' : '尚未載入'],
        ['暖機', `${WARMUP_LABELS[m.warmup ?? ''] ?? '—'}${m.warmup_error ? `（${m.warmup_error}）` : ''}`],
      ]} />
      <Sub>DB 連線池</Sub>
      <SectionError error={p.error} />
      <KV label="連線池" rows={[
        ['使用中', `${p.checked_out ?? '—'}／上限 ${p.size != null && p.max_overflow != null ? p.size + p.max_overflow : '—'}`],
        ['已開啟', `${p.open_connections ?? '—'}（常駐 ${p.size ?? '—'}）`],
      ]} />
      <Sub>併發閘</Sub>
      {d.gates_error && <p className={c.error} role="alert">讀取失敗：{d.gates_error}</p>}
      <KV label="併發閘" rows={d.gates.length ? d.gates.map((g): [string, ReactNode] => [
        g.name, `使用中 ${g.in_use ?? '—'}／${g.capacity}；排隊 ${g.waiting}（上限 ${g.max_queue || '不限'}）`,
      ]) : [['—', '沒有閘門']]} />
    </Card>
  )
}

function ConfigCard({ d }: { d: DiagnosticsResponse }) {
  const cfg = d.config
  return (
    <Card id="diag-config" title="設定（白名單）">
      <SectionError error={cfg.error} />
      <KV label="設定" rows={[
        ['資料庫', <Mono>{cfg.db_target ?? '—'}</Mono>],
        ['物件儲存', cfg.object_storage_mode ?? '—'],
        ['LLM 供應商', cfg.llm_provider ?? '—'],
        ['抽取器', cfg.extractor ?? '—'],
        ['日誌等級', cfg.log_level ?? '—'],
        ['維運代理', <Mono>{`${cfg.ops_agent_environment || '（停用）'} ${cfg.ops_agent_socket ?? ''}`}</Mono>],
      ]} />
      <Sub>線上任務的模型</Sub>
      <KV label="模型" rows={Object.entries(cfg.models ?? {}).map(([task, model]): [string, ReactNode] => [task, String(model)])} />
      {cfg.flags && (
        <>
          <Sub>功能旗標</Sub>
          <KV label="功能旗標" rows={Object.entries(cfg.flags).map(([k, v]): [string, ReactNode] => [k, yesNo(v, '開', '關')])} />
        </>
      )}
      {cfg.secrets_present && (
        <>
          <Sub>祕密（只顯示有沒有設）</Sub>
          <KV label="祕密" rows={Object.entries(cfg.secrets_present).map(([k, v]): [string, ReactNode] => [k, yesNo(v, '已設', '未設')])} />
        </>
      )}
      {cfg.limits && (
        <>
          <Sub>上限與逾時</Sub>
          <KV label="上限" rows={Object.entries(cfg.limits).map(([k, v]): [string, ReactNode] => [k, String(v)])} />
        </>
      )}
    </Card>
  )
}

/**
 * 診斷：這個 web 行程此刻的版本、schema、執行環境、白名單設定與便宜的連通性檢查（後端快取 30 秒）。
 * 「複製診斷包」複製整份 JSON（後端已去除祕密）貼給維運。
 */
export default function OpsDiagnosticsPage() {
  const q = useDiagnostics()
  const bundle = useCopyBundle(q.data)
  const header = (
    <PageHeader
      summary="這個 web 行程目前的版本、schema、執行環境、設定與連通性。"
      help={(
        <>
          程式版本比對行程啟動時與磁碟上的 commit，兩者不同表示程式已更新、等著重新啟動；前端讀 frontend/dist 的建置時間；
          schema 比對 DB 的 alembic 版本與程式的 head，並附每日檢查最後一次的結果。設定只列白名單裡的項目，祕密只顯示有沒有設。
          R2 與 DeepSeek 只讀健康檢查上一次的結論，不會另外呼叫。「複製診斷包」複製整份 JSON（不含祕密），可以直接貼給維運。
        </>
      )}
      actions={q.data && (
        <>
          <Button onClick={() => q.refetch()} disabled={q.isFetching}>
            {q.isFetching ? '重新整理中…' : '重新整理'}
          </Button>
          <Button variant="primary" onClick={bundle.copy}>複製診斷包</Button>
        </>
      )}
    />
  )
  if (q.isPending) return <>{header}<p className={c.empty}>載入中…</p></>
  if (q.isError) return <>{header}<OpsQueryError error={q.error} what="診斷" /></>
  const d = q.data
  return (
    <>
      {header}
      <p className={styles.meta}>產生時間 <b>{fmtDateTime(d.generated_at)}</b>（伺服器快取 {d.cache_ttl_s} 秒）</p>
      <Summary d={d} />
      {bundle.failed && (
        <textarea
          className={styles.manual}
          readOnly
          value={bundle.text}
          aria-label="診斷包 JSON"
          onFocus={e => e.currentTarget.select()}
        />
      )}
      <div className={styles.grid}>
        <ChecksCard d={d} />
        <SchemaCard d={d} />
        <VersionsCard d={d} />
        <RuntimeCard d={d} />
        <ConfigCard d={d} />
      </div>
    </>
  )
}
