import { useQuery } from '@tanstack/react-query'
import { Fragment, useState, type ReactNode } from 'react'
import { adminApi, type DiagnosticsResponse } from '../../../lib/generated/adminApi'
import { copyText } from '../../../lib/clipboard'
import { fmtDateTime } from '../auditLabels'
import { Button, Card as ConsoleCard, PageHeader, Status, toast, type LampTone } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { OpsQueryError } from './OpsShared'
import styles from './Diagnostics.module.css'
import { t } from '@/i18n'

type Tone = 'ok' | 'warn' | 'fail' | 'idle'
const LAMP_TONE: Record<Tone, LampTone> = { ok: 'ok', warn: 'warn', fail: 'bad', idle: 'idle' }

const SCHEMA_LABELS: Record<DiagnosticsResponse['schema_info']['status'], [string, Tone]> = {
  get ok() { return [t('admin.ops.opsDiagnosticsPage.inSync'), 'ok'] as [string, Tone] }, get behind() { return [t('admin.ops.opsDiagnosticsPage.dbBehind'), 'fail'] as [string, Tone] }, get ahead() { return [t('admin.ops.opsDiagnosticsPage.dbAhead'), 'fail'] as [string, Tone] }, get unversioned() { return [t('admin.ops.opsDiagnosticsPage.notManaged'), 'warn'] as [string, Tone] },
  get ambiguous() { return [t('admin.ops.opsDiagnosticsPage.ambiguous'), 'fail'] as [string, Tone] }, get error() { return [t('admin.ops.opsDiagnosticsPage.notFound'), 'fail'] as [string, Tone] },
}
const STORAGE_LABELS: Record<string, [string, Tone]> = {
  get ok() { return [t('admin.ops.opsDiagnosticsPage.ok'), 'ok'] as [string, Tone] }, get degraded() { return [t('admin.ops.opsDiagnosticsPage.error'), 'fail'] as [string, Tone] }, get unknown() { return [t('admin.ops.opsDiagnosticsPage.notProbedYet'), 'idle'] as [string, Tone] }, get disabled() { return [t('admin.ops.opsDiagnosticsPage.disabledLocal'), 'idle'] as [string, Tone] },
}
const LLM_LABELS: Record<string, [string, Tone]> = {
  get ok() { return [t('admin.ops.opsDiagnosticsPage.ok'), 'ok'] as [string, Tone] }, get exhausted() { return [t('admin.ops.opsDiagnosticsPage.quotaExhausted'), 'fail'] as [string, Tone] }, get auth_failed() { return [t('admin.ops.opsDiagnosticsPage.invalidKey'), 'fail'] as [string, Tone] },
  get unreachable() { return [t('admin.ops.opsDiagnosticsPage.unreachable'), 'fail'] as [string, Tone] }, get indeterminate() { return [t('admin.ops.opsDiagnosticsPage.undetermined'), 'fail'] as [string, Tone] }, get unknown() { return [t('admin.ops.opsDiagnosticsPage.notCheckedYet'), 'idle'] as [string, Tone] },
  get disabled() { return [t('admin.ops.opsDiagnosticsPage.notUsed'), 'idle'] as [string, Tone] },
}
const WARMUP_LABELS: Record<string, string> = {
  get skipped() { return t('admin.ops.opsDiagnosticsPage.skippedSkipWarmup1') }, get absent() { return t('admin.ops.opsDiagnosticsPage.noWarmupWork') }, get running() { return t('admin.ops.opsDiagnosticsPage.warmingUp') }, get done() { return t('admin.ops.opsDiagnosticsPage.finished') }, get failed() { return t('admin.ops.opsDiagnosticsPage.failed') },
  get cancelled() { return t('admin.ops.opsDiagnosticsPage.canceled') },
}
const DAILY_REASON: Record<string, string> = {
  get missing() { return t('admin.ops.opsDiagnosticsPage.noStatusFileYet') }, get too_large() { return t('admin.ops.opsDiagnosticsPage.statusFileTooLargeNot') }, get invalid() { return t('admin.ops.opsDiagnosticsPage.statusFileMalformed') }, get error() { return t('admin.ops.opsDiagnosticsPage.readFailed') },
}

function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <Status tone={LAMP_TONE[tone]}>{children}</Status>
}

/** 等寬且可在任意字元斷行：commit、路徑、bundle 檔名。 */
function Mono({ children }: { children: ReactNode }) {
  return <span className={`${c.mono} ${styles.breakAll}`}>{children}</span>
}

function yesNo(v: boolean | null | undefined, yes = t('admin.ops.opsDiagnosticsPage.yes'), no = t('admin.ops.opsDiagnosticsPage.no')): string {
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
  if (s < 60) return t('admin.ops.opsDiagnosticsPage.s', { s: Math.round(s) })
  if (s < 3600) return t('admin.ops.opsDiagnosticsPage.min', { round: Math.round(s / 60) })
  if (s < 86_400) return t('admin.ops.opsDiagnosticsPage.h', { v: (s / 3600).toFixed(1) })
  return t('admin.ops.opsDiagnosticsPage.days', { v: (s / 86_400).toFixed(1) })
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
  return <p className={c.error} role="alert">{t('admin.ops.opsDiagnosticsPage.thisSectionFailedToLoad', { error })}</p>
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
      () => { setFailed(false); toast(t('admin.ops.opsDiagnosticsPage.copiedJsonNoSecrets')) },
      () => { setFailed(true); toast(t('admin.ops.opsDiagnosticsPage.couldnTCopyAutomaticallyCopy'), { tone: 'bad' }) },
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
    [t('admin.ops.opsDiagnosticsPage.database'), <Pill tone={d.checks.db.ok ? 'ok' : 'fail'}>{d.checks.db.ok ? t('admin.ops.opsDiagnosticsPage.reachable') : t('admin.ops.opsDiagnosticsPage.unreachable')}</Pill>,
      d.checks.db.ok ? fmtMs(d.checks.db.latency_ms) : (d.checks.db.error ?? '')],
    [t('admin.ops.opsDiagnosticsPage.schemaVersion'), <Pill tone={schemaTone}>{schemaLabel}</Pill>,
      t('admin.ops.opsDiagnosticsPage.dbCode', { v: d.schema_info.db_revisions.join(',') || '—', v2: d.schema_info.code_heads.join(',') || '—' })],
    [t('admin.ops.opsDiagnosticsPage.objectStorage'), <Pill tone={storageTone}>{storageLabel}</Pill>, d.config.object_storage_mode ?? ''],
    ['LLM', <Pill tone={llmTone}>{llmLabel}</Pill>, d.checks.llm.key_configured ? t('admin.ops.opsDiagnosticsPage.keySet') : t('admin.ops.opsDiagnosticsPage.keyNotSet')],
    [t('admin.ops.opsDiagnosticsPage.opsAgent'), <Pill tone={d.checks.ops_agent.ok ? 'ok' : 'warn'}>{d.checks.ops_agent.ok ? t('admin.ops.opsDiagnosticsPage.reachable') : t('admin.ops.opsDiagnosticsPage.unreachable')}</Pill>,
      d.checks.ops_agent.ok ? t('admin.ops.opsDiagnosticsPage.services', { v: d.checks.ops_agent.services ?? '—' }) : (d.checks.ops_agent.error ?? '')],
    [t('admin.ops.opsDiagnosticsPage.codeVersion'), git?.restart_pending
      ? <Pill tone="warn">{t('admin.ops.opsDiagnosticsPage.restartPending')}</Pill>
      : <Pill tone={git?.available ? 'ok' : 'idle'}>{git?.available ? t('admin.ops.opsDiagnosticsPage.matchesDisk') : t('admin.ops.opsDiagnosticsPage.unavailable')}</Pill>,
      shortSha(git?.commit_at_start)],
  ]
  return (
    <div className={c.tiles} role="list" aria-label={t('admin.ops.opsDiagnosticsPage.diagnosticSummary')}>
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

const BUILD_SOURCE_LABELS: Record<string, string> = {
  get image() { return t('admin.ops.opsDiagnosticsPage.containerImageWrittenAtBuild') },
  get host() { return t('admin.ops.opsDiagnosticsPage.hostMakeBuildInfo') },
  get git() { return t('admin.ops.opsDiagnosticsPage.commitFromGitOnly') },
}

function treeLabel(clean: boolean | null | undefined): string {
  if (clean == null) return t('admin.ops.opsDiagnosticsPage.unknown')
  return clean ? t('admin.ops.opsDiagnosticsPage.clean') : t('admin.ops.opsDiagnosticsPage.uncommittedChanges')
}

function buildRows(build: DiagnosticsResponse['versions']['build']): [string, ReactNode][] {
  if (!build?.available) return [[t('admin.ops.opsDiagnosticsPage.buildInfo'), t('admin.ops.opsDiagnosticsPage.unavailableNoBuildInfoFile')]]
  return [
    ['commit', <Mono>{build.commit ?? t('admin.ops.opsDiagnosticsPage.notProvidedAtBuild')}</Mono>],
    [t('admin.ops.opsDiagnosticsPage.version'), build.version ?? '—'],
    [t('admin.ops.opsDiagnosticsPage.workTreeAtBuild'), treeLabel(build.tree_clean)],
    [t('admin.ops.opsDiagnosticsPage.source'), BUILD_SOURCE_LABELS[build.source ?? ''] ?? '—'],
    ...(build.built_at ? [[t('admin.ops.opsDiagnosticsPage.writtenAt'), fmtDateTime(build.built_at)] as [string, ReactNode]] : []),
  ]
}

function VersionsCard({ d }: { d: DiagnosticsResponse }) {
  const v = d.versions
  const git = v.git
  const fe = v.frontend
  return (
    <Card id="diag-versions" title={t('admin.ops.opsDiagnosticsPage.version')}>
      <SectionError error={v.error} />
      {v.build?.stale_file && (
        <Warn>{t('admin.ops.opsDiagnosticsPage.theBuildInfoFileAnd')}</Warn>
      )}
      <Sub>{t('admin.ops.opsDiagnosticsPage.build')}</Sub>
      <KV label={t('admin.ops.opsDiagnosticsPage.buildInfo')} rows={buildRows(v.build)} />
      {git?.restart_pending && (
        <Warn>
          {t('admin.ops.opsDiagnosticsPage.theCodeOnDiskDiffers', { commit_on_disk: shortSha(git.commit_on_disk), commit_at_start: shortSha(git.commit_at_start) })}
        </Warn>
      )}
      <Sub>{t('admin.ops.opsDiagnosticsPage.code')}</Sub>
      <KV label={t('admin.ops.opsDiagnosticsPage.codeVersion')} rows={git?.available ? [
        [t('admin.ops.opsDiagnosticsPage.commitAtStart'), <Mono>{git.commit_at_start ?? '—'}</Mono>],
        [t('admin.ops.opsDiagnosticsPage.branchAtStart'), git.branch_at_start ?? t('admin.ops.opsDiagnosticsPage.detached')],
        [t('admin.ops.opsDiagnosticsPage.commitOnDisk'), <Mono>{git.commit_on_disk ?? '—'}</Mono>],
      ] : [['commit', t('admin.ops.opsDiagnosticsPage.unavailable2', { v: git?.reason ?? t('admin.ops.opsDiagnosticsPage.unknown2') })]]} />
      <Sub>{t('admin.ops.opsDiagnosticsPage.frontend')}</Sub>
      <KV label={t('admin.ops.opsDiagnosticsPage.frontendBuild')} rows={fe?.available ? [
        [t('admin.ops.opsDiagnosticsPage.buildTime'), fmtDateTime(fe.built_at)],
        [t('admin.ops.opsDiagnosticsPage.entryBundle'), <Mono>{(fe.entry_assets ?? []).join(t('admin.ops.opsDiagnosticsPage.text')) || '—'}</Mono>],
      ] : [['build', t('admin.ops.opsDiagnosticsPage.frontendDistIndexHtmlNot')]]} />
      <Sub>{t('admin.ops.opsDiagnosticsPage.runtimeAndPackages')}</Sub>
      <KV label={t('admin.ops.opsDiagnosticsPage.packageVersions')} rows={[
        ['Python', v.python ?? '—'],
        [t('admin.ops.opsDiagnosticsPage.platform'), v.platform ?? '—'],
        ...Object.entries(v.packages ?? {}).map(([name, ver]): [string, ReactNode] => [name, ver == null ? t('admin.ops.opsDiagnosticsPage.notInstalled') : String(ver)]),
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
      <KV label={t('admin.ops.opsDiagnosticsPage.schemaVersion2')} rows={[
        ['DB revision', s.db_revisions.join(t('admin.ops.opsDiagnosticsPage.text')) || '—'],
        [t('admin.ops.opsDiagnosticsPage.codeHead'), s.code_heads.join(t('admin.ops.opsDiagnosticsPage.text')) || '—'],
        ...(s.pending.length ? [[t('admin.ops.opsDiagnosticsPage.notApplied'), s.pending.join(t('admin.ops.opsDiagnosticsPage.text'))] as [string, ReactNode]] : []),
      ]} />
      <Sub>{t('admin.ops.opsDiagnosticsPage.dailyCheckReportMarkSchema')}</Sub>
      {dc.available ? (
        <>
          <KV label={t('admin.ops.opsDiagnosticsPage.dailyCheck')} rows={[
            [t('admin.ops.opsDiagnosticsPage.lastRun'), `${fmtDateTime(dc.checked_at)}${dc.age_hours != null ? t('admin.ops.opsDiagnosticsPage.hAgo', { age_hours: dc.age_hours.toFixed(1) }) : ''}`],
            [t('admin.ops.opsDiagnosticsPage.mode'), dc.mode ?? '—'],
            [t('admin.ops.opsDiagnosticsPage.exitCode'), dc.exit_code == null ? '—' : `${dc.exit_code}${dc.alert ? t('admin.ops.opsDiagnosticsPage.alerts') : ''}`],
            [t('admin.ops.opsDiagnosticsPage.versionDrift'), t('admin.ops.opsDiagnosticsPage.text2', { v: dc.version_status ?? '—', v2: dc.drift_status ?? '—', v3: dc.drift_count != null ? t('admin.ops.opsDiagnosticsPage.items', { drift_count: dc.drift_count }) : '' })],
            [t('admin.ops.opsDiagnosticsPage.issues'), dc.problems?.length ? dc.problems.join(t('admin.ops.opsDiagnosticsPage.text')) : t('admin.ops.opsDiagnosticsPage.none')],
            [t('admin.ops.opsDiagnosticsPage.description'), dc.message ?? '—'],
          ]} />
          {dc.stale && <Warn>{t('admin.ops.opsDiagnosticsPage.theStatusFileHasnT')}</Warn>}
        </>
      ) : (
        <p className={c.muted}>{DAILY_REASON[dc.unavailable_reason ?? ''] ?? t('admin.ops.opsDiagnosticsPage.unreadable')}{t('admin.ops.opsDiagnosticsPage.text3')}</p>
      )}
    </Card>
  )
}

function ChecksCard({ d }: { d: DiagnosticsResponse }) {
  const ck = d.checks
  const [storageLabel, storageTone] = STORAGE_LABELS[ck.storage.state] ?? [ck.storage.state, 'warn']
  const [llmLabel, llmTone] = LLM_LABELS[ck.llm.state] ?? [ck.llm.state, 'warn']
  return (
    <Card id="diag-checks" title={t('admin.ops.opsDiagnosticsPage.connectivity')}>
      <p className={c.meta}>{t('admin.ops.opsDiagnosticsPage.r2AndLlmShowOnly')}</p>
      <KV label={t('admin.ops.opsDiagnosticsPage.connectivity')} rows={[
        [t('admin.ops.opsDiagnosticsPage.database'), <><Pill tone={ck.db.ok ? 'ok' : 'fail'}>{ck.db.ok ? t('admin.ops.opsDiagnosticsPage.ok') : t('admin.ops.opsDiagnosticsPage.failed')}</Pill>{' '}
          {ck.db.ok ? t('admin.ops.opsDiagnosticsPage.select1Postgresql', { latency_ms: fmtMs(ck.db.latency_ms), v: ck.db.server_version ?? '—' }) : ck.db.error}</>],
        [t('admin.ops.opsDiagnosticsPage.objectStorage'), <><Pill tone={storageTone}>{storageLabel}</Pill>{' '}
          {ck.storage.last_probe_age_s != null ? t('admin.ops.opsDiagnosticsPage.lastProbedAgo', { last_probe_age_s: fmtDuration(ck.storage.last_probe_age_s) }) : ''}
          {ck.storage.consecutive_failures ? t('admin.ops.opsDiagnosticsPage.consecutiveFailures', { consecutive_failures: ck.storage.consecutive_failures }) : ''}
          {ck.storage.error ? t('admin.ops.opsDiagnosticsPage.text4', { error: ck.storage.error }) : ''}</>],
        ['LLM', <><Pill tone={llmTone}>{llmLabel}</Pill>{' '}
          {ck.llm.last_check_age_s != null ? t('admin.ops.opsDiagnosticsPage.lastCheckedAgo', { last_check_age_s: fmtDuration(ck.llm.last_check_age_s) }) : ''}
          {ck.llm.quota_latched ? t('admin.ops.opsDiagnosticsPage.thisProcessHasHitQuota') : ''}
          {!ck.llm.ask_uses_http ? t('admin.ops.opsDiagnosticsPage.theMainAnswerModelIsn') : ''}
          {ck.llm.error ? t('admin.ops.opsDiagnosticsPage.text4', { error: ck.llm.error }) : ''}</>],
        [t('admin.ops.opsDiagnosticsPage.opsAgent'), <><Pill tone={ck.ops_agent.ok ? 'ok' : 'warn'}>{ck.ops_agent.ok ? t('admin.ops.opsDiagnosticsPage.ok') : t('admin.ops.opsDiagnosticsPage.unreachable')}</Pill>{' '}
          {ck.ops_agent.ok ? t('admin.ops.opsDiagnosticsPage.services2', { latency_ms: fmtMs(ck.ops_agent.latency_ms), v: ck.ops_agent.services ?? '—' }) : ck.ops_agent.error}</>],
      ]} />
    </Card>
  )
}

function RuntimeCard({ d }: { d: DiagnosticsResponse }) {
  const r = d.runtime
  const m = d.models
  const p = d.db_pool
  return (
    <Card id="diag-runtime" title={t('admin.ops.opsDiagnosticsPage.runtime')}>
      <SectionError error={r.error} />
      <KV label={t('admin.ops.opsDiagnosticsPage.process')} rows={[
        [t('admin.ops.opsDiagnosticsPage.host'), r.hostname ?? '—'],
        ['PID', r.pid ?? '—'],
        [t('admin.ops.opsDiagnosticsPage.startedAt'), t('admin.ops.opsDiagnosticsPage.up', { started_at: fmtDateTime(r.started_at), uptime_s: fmtDuration(r.uptime_s) })],
        [t('admin.ops.opsDiagnosticsPage.memoryRss'), t('admin.ops.opsDiagnosticsPage.peak', { rss_bytes: fmtBytes(r.rss_bytes), peak_rss_bytes: fmtBytes(r.peak_rss_bytes) })],
        [t('admin.ops.opsDiagnosticsPage.threads'), r.threads ?? '—'],
        [t('admin.ops.opsDiagnosticsPage.timeZone'), r.timezone ? `${r.timezone.name} ${r.timezone.utc_offset}${r.timezone.tz_env ? t('admin.ops.opsDiagnosticsPage.tz', { tz_env: r.timezone.tz_env }) : ''}` : '—'],
        ['Python', <Mono>{r.python_executable ?? '—'}</Mono>],
      ]} />
      <Sub>{t('admin.ops.opsDiagnosticsPage.models')}</Sub>
      <SectionError error={m.error} />
      <KV label={t('admin.ops.opsDiagnosticsPage.models')} rows={[
        [t('admin.ops.opsDiagnosticsPage.embeddingModel'), t('admin.ops.opsDiagnosticsPage.text5', { v: m.embed_model ?? '—', v2: m.embed_loaded ? t('admin.ops.opsDiagnosticsPage.loaded') : t('admin.ops.opsDiagnosticsPage.notLoadedYet') })],
        ['rerank', m.rerank_load_failed ? t('admin.ops.opsDiagnosticsPage.loadFailedCircuitOpen') : m.rerank_loaded ? t('admin.ops.opsDiagnosticsPage.loaded') : t('admin.ops.opsDiagnosticsPage.notLoadedYet')],
        [t('admin.ops.opsDiagnosticsPage.warmup'), `${WARMUP_LABELS[m.warmup ?? ''] ?? '—'}${m.warmup_error ? t('admin.ops.opsDiagnosticsPage.text6', { warmup_error: m.warmup_error }) : ''}`],
      ]} />
      <Sub>{t('admin.ops.opsDiagnosticsPage.dbConnectionPool')}</Sub>
      <SectionError error={p.error} />
      <KV label={t('admin.ops.opsDiagnosticsPage.connectionPool')} rows={[
        [t('admin.ops.opsDiagnosticsPage.inUse'), t('admin.ops.opsDiagnosticsPage.max', { v: p.checked_out ?? '—', v2: p.size != null && p.max_overflow != null ? p.size + p.max_overflow : '—' })],
        [t('admin.ops.opsDiagnosticsPage.open'), t('admin.ops.opsDiagnosticsPage.resident', { v: p.open_connections ?? '—', v2: p.size ?? '—' })],
      ]} />
      <Sub>{t('admin.ops.opsDiagnosticsPage.concurrencyGates')}</Sub>
      {d.gates_error && <p className={c.error} role="alert">{t('admin.ops.opsDiagnosticsPage.readFailed2', { gates_error: d.gates_error })}</p>}
      <KV label={t('admin.ops.opsDiagnosticsPage.concurrencyGates')} rows={d.gates.length ? d.gates.map((g): [string, ReactNode] => [
        g.name, t('admin.ops.opsDiagnosticsPage.inUseWaitingMax', { v: g.in_use ?? '—', capacity: g.capacity, waiting: g.waiting, v2: g.max_queue || t('admin.ops.opsDiagnosticsPage.unlimited') }),
      ]) : [['—', t('admin.ops.opsDiagnosticsPage.noGates')]]} />
    </Card>
  )
}

function ConfigCard({ d }: { d: DiagnosticsResponse }) {
  const cfg = d.config
  return (
    <Card id="diag-config" title={t('admin.ops.opsDiagnosticsPage.settingsAllowlist')}>
      <SectionError error={cfg.error} />
      <KV label={t('admin.ops.opsDiagnosticsPage.settings')} rows={[
        [t('admin.ops.opsDiagnosticsPage.database'), <Mono>{cfg.db_target ?? '—'}</Mono>],
        [t('admin.ops.opsDiagnosticsPage.objectStorage'), cfg.object_storage_mode ?? '—'],
        [t('admin.ops.opsDiagnosticsPage.llmProvider'), cfg.llm_provider ?? '—'],
        [t('admin.ops.opsDiagnosticsPage.extractor'), cfg.extractor ?? '—'],
        [t('admin.ops.opsDiagnosticsPage.logLevel'), cfg.log_level ?? '—'],
        [t('admin.ops.opsDiagnosticsPage.opsAgent'), <Mono>{`${cfg.ops_agent_environment || t('admin.ops.opsDiagnosticsPage.disabled')} ${cfg.ops_agent_socket ?? ''}`}</Mono>],
      ]} />
      <Sub>{t('admin.ops.opsDiagnosticsPage.modelsForOnlineTasks')}</Sub>
      <KV label={t('admin.ops.opsDiagnosticsPage.models')} rows={Object.entries(cfg.models ?? {}).map(([task, model]): [string, ReactNode] => [task, String(model)])} />
      {cfg.flags && (
        <>
          <Sub>{t('admin.ops.opsDiagnosticsPage.featureFlags')}</Sub>
          <KV label={t('admin.ops.opsDiagnosticsPage.featureFlags')} rows={Object.entries(cfg.flags).map(([k, v]): [string, ReactNode] => [k, yesNo(v, t('admin.ops.opsDiagnosticsPage.on'), t('admin.ops.opsDiagnosticsPage.off'))])} />
        </>
      )}
      {cfg.secrets_present && (
        <>
          <Sub>{t('admin.ops.opsDiagnosticsPage.secretsSetOrNotOnly')}</Sub>
          <KV label={t('admin.ops.opsDiagnosticsPage.secrets')} rows={Object.entries(cfg.secrets_present).map(([k, v]): [string, ReactNode] => [k, yesNo(v, t('admin.ops.opsDiagnosticsPage.set'), t('admin.ops.opsDiagnosticsPage.notSet'))])} />
        </>
      )}
      {cfg.limits && (
        <>
          <Sub>{t('admin.ops.opsDiagnosticsPage.limitsAndTimeouts')}</Sub>
          <KV label={t('admin.ops.opsDiagnosticsPage.limits')} rows={Object.entries(cfg.limits).map(([k, v]): [string, ReactNode] => [k, String(v)])} />
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
      summary={t('admin.ops.opsDiagnosticsPage.versionSchemaRuntimeSettingsAnd')}
      help={(
        <>
          {t('admin.ops.opsDiagnosticsPage.codeVersionComparesTheCommit')}
        </>
      )}
      actions={q.data && (
        <>
          <Button onClick={() => q.refetch()} disabled={q.isFetching}>
            {q.isFetching ? t('admin.ops.opsDiagnosticsPage.refreshing') : t('admin.ops.opsDiagnosticsPage.refresh')}
          </Button>
          <Button variant="primary" onClick={bundle.copy}>{t('admin.ops.opsDiagnosticsPage.copyDiagnostics')}</Button>
        </>
      )}
    />
  )
  if (q.isPending) return <>{header}<p className={c.empty}>{t('admin.ops.opsDiagnosticsPage.loading')}</p></>
  if (q.isError) return <>{header}<OpsQueryError error={q.error} what={t('admin.ops.opsDiagnosticsPage.diagnostics')} /></>
  const d = q.data
  return (
    <>
      {header}
      <p className={c.meta}><span>{t('admin.ops.opsDiagnosticsPage.generatedAt')}{' '}<b>{fmtDateTime(d.generated_at)}</b>{t('admin.ops.opsDiagnosticsPage.serverCacheS', { cache_ttl_s: d.cache_ttl_s })}</span></p>
      <Summary d={d} />
      {bundle.failed && (
        <textarea
          className={styles.manual}
          readOnly
          value={bundle.text}
          aria-label={t('admin.ops.opsDiagnosticsPage.diagnosticsJson')}
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
