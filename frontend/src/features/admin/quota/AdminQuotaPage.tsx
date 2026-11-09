import { useState } from 'react'
import { Link } from 'react-router'
import {
  Card, Chip, ConsolePage, PageHeader, RowActions, Segmented, StatTile, toast, useElevate,
} from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import type { QuotaItem, QuotaOverview } from '../../../lib/generated/adminApi'
import { useHasScope, useMe } from '../../../lib/useMe'
import { roleLabel } from '../auditLabels'
import { RequireScope } from '../RequireScope'
import { KIND_LABELS, KINDS, fmtInt, limitText, messageOf } from './quotaLabels'
import { QuotaLimitDialog } from './QuotaLimitDialog'
import type { QuotaChange } from './quotaPlan'
import { useQuotaOverview, useQuotaStats, useSetQuotaOverride } from './useQuota'
import styles from './Quota.module.css'
import { t } from '@/i18n'

const STATS_RANGES = ['7', '14', '30'] as const
type StatsRange = (typeof STATS_RANGES)[number]
const SUPER_ONLY_HINT = '只有 super admin 能調整 super admin 的配額'

/**
 * 單格：今天用了幾次／上限。超出上限的次數另外標出：影子模式下那些請求照常放行（本來會擋），
 * 正式阻擋時是被擋下的次數；上傳一直是正式阻擋。
 */
function UsageCell({ item, shadow }: { item: QuotaItem; shadow: boolean }) {
  const full = item.limit != null && item.used >= item.limit
  return (
    <>
      <div className={styles.usage}>
        <span className={c.num} data-tone={item.over > 0 ? 'bad' : full ? 'warn' : undefined}>
          {t('admin.quota.adminQuotaPage.text', { used: fmtInt(item.used), item: limitText(item) })}
        </span>
        {item.mode !== 'default' && <Chip tone="gold">{item.mode === 'unlimited' ? t('admin.quota.adminQuotaPage.unlimited') : t('admin.quota.adminQuotaPage.override')}</Chip>}
      </div>
      {item.over > 0 && (
        <div className={styles.over}>
          {t('admin.quota.adminQuotaPage.overBy', { over: fmtInt(item.over) })}{shadow && item.kind !== 'upload' ? t('admin.quota.adminQuotaPage.allowedAsUsualInShadow') : t('admin.quota.adminQuotaPage.blocked')}{t('admin.quota.adminQuotaPage.text2')}
        </div>
      )}
    </>
  )
}

function EnforcementTiles({ data }: { data: QuotaOverview }) {
  const e = data.enforcement
  const shadow = e.mode === 'shadow'
  const over = data.over_today.ask + data.over_today.export
  const flagSource = e.flag_source === 'fallback' ? t('admin.quota.adminQuotaPage.couldnTReadTheFlag') : e.flag_source === 'db' ? t('admin.quota.adminQuotaPage.fromDbOverride') : t('admin.quota.adminQuotaPage.codeDefault')
  const flagSub = `${flagSource}${e.flag_scoped ? t('admin.quota.adminQuotaPage.appliesOnlyToSomeRoles') : ''}`
  return (
    <div className={c.tiles}>
      <StatTile label={t('admin.quota.adminQuotaPage.blockingMode')} value={shadow ? t('admin.quota.adminQuotaPage.shadowMode') : t('admin.quota.adminQuotaPage.enforced')}
        sub={shadow ? t('admin.quota.adminQuotaPage.allowedAsUsualOnlyRecorded') : t('admin.quota.adminQuotaPage.overQuotaRequestsGet429')} />
      <StatTile label={t('admin.quota.adminQuotaPage.environmentVariableQuotaEnforce')} value={e.env_ceiling ? t('admin.quota.adminQuotaPage.on') : t('admin.quota.adminQuotaPage.off')}
        sub={t('admin.quota.adminQuotaPage.capabilityCeilingWhenOffThe')} subTitle={t('admin.quota.adminQuotaPage.capabilityCeilingWhenOffThe')} />
      <StatTile label={t('admin.quota.adminQuotaPage.flagQuotaEnforce')} value={e.flag_enabled ? t('admin.quota.adminQuotaPage.on') : t('admin.quota.adminQuotaPage.off')}
        tone={e.flag_source === 'fallback' ? 'warn' : undefined} lamp={e.flag_source === 'fallback' ? 'warn' : undefined}
        sub={flagSub} subTitle={flagSub} />
      <StatTile label={t('admin.quota.adminQuotaPage.overTheLimitToday', { v: shadow ? t('admin.quota.adminQuotaPage.wouldHaveBlocked') : t('admin.quota.adminQuotaPage.blocked') })} value={fmtInt(over)} unit={t('admin.quota.adminQuotaPage.times')}
        sub={t('admin.quota.adminQuotaPage.qAExport', { ask: fmtInt(data.over_today.ask), export: fmtInt(data.over_today.export) })} />
      <StatTile label={t('admin.quota.adminQuotaPage.defaultDailyLimit')} value={fmtInt(data.defaults.ask)} unit={t('admin.quota.adminQuotaPage.qA')}
        sub={t('admin.quota.adminQuotaPage.exportUpload', { export: fmtInt(data.defaults.export), upload: fmtInt(data.defaults.upload) })} />
    </div>
  )
}

function StatsCard() {
  const [range, setRange] = useState<StatsRange>('14')
  const q = useQuotaStats(Number(range))
  return (
    <Card title={t('admin.quota.adminQuotaPage.perUserDailyUsageDistribution')} labelledBy="quota-stats-title"
      right={<Segmented label={t('admin.quota.adminQuotaPage.range')} value={range} onChange={setRange} options={STATS_RANGES.map(d => ({ value: d, label: t('admin.quota.adminQuotaPage.days', { d }) }))} />}>
      {q.isPending ? (
        <p className={c.empty}>{t('admin.quota.adminQuotaPage.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.quota.adminQuotaPage.couldnTLoadUsageDistribution', { error: messageOf(q.error) })}</p></div>
      ) : (
        <>
          <div className={c.tableWrap}>
            <table className={c.table} aria-label={t('admin.quota.adminQuotaPage.usageDistribution')}>
              <thead>
                <tr>
                  <th>{t('admin.quota.adminQuotaPage.category')}</th><th className={c.tdRight}>{t('admin.quota.adminQuotaPage.defaultLimit')}</th><th className={c.tdRight}>P50</th><th className={c.tdRight}>P95</th>
                  <th className={c.tdRight}>{t('admin.quota.adminQuotaPage.max')}</th><th className={c.tdRight}>{t('admin.quota.adminQuotaPage.userDays')}</th><th className={c.tdRight}>{t('admin.quota.adminQuotaPage.users')}</th>
                  <th className={c.tdRight}>{t('admin.quota.adminQuotaPage.overQuotaUserDays')}</th><th className={c.tdRight}>{t('admin.quota.adminQuotaPage.overQuotaAttempts')}</th>
                </tr>
              </thead>
              <tbody>
                {q.data.kinds.map(k => (
                  <tr key={k.kind}>
                    <td className={c.t1}>{KIND_LABELS[k.kind]}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtInt(k.default_limit)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{k.p50 ?? '—'}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{k.p95 ?? '—'}</td>
                    <td className={`${c.num} ${c.tdRight} ${styles.stat}`} data-tone={k.max != null && k.max > k.default_limit ? 'warn' : undefined}>
                      {k.max ?? '—'}
                    </td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtInt(k.user_days)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{fmtInt(k.users)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{k.kind === 'upload' ? '—' : fmtInt(k.over_user_days)}</td>
                    <td className={`${c.num} ${c.tdRight}`}>{k.kind === 'upload' ? '—' : fmtInt(k.over_events)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={`${c.cardFoot} ${styles.foot}`}>
            <span>{t('admin.quota.adminQuotaPage.taipeiTime', { since_day: q.data.since_day, until_day: q.data.until_day })}</span>
            <span>{t('admin.quota.adminQuotaPage.demandCountedCallsAttemptsOver')}</span>
          </div>
        </>
      )}
    </Card>
  )
}

function UsersCard({ data }: { data: QuotaOverview }) {
  const me = useMe()
  const save = useSetQuotaOverride()
  const { elevated, dialog } = useElevate()
  const [editingId, setEditingId] = useState<string | null>(null)
  const isSuper = Boolean(me.data?.is_super)
  const shadow = data.enforcement.mode === 'shadow'
  // 表單比對的是清單裡最新的那一列（存了一部分之後重抓，剩下的才是「還沒改」的）。
  const editing = data.users.find(u => u.user_id === editingId) ?? null

  /** 調整配額要已提升：`useElevate` 先確認身分再送；一個類別一筆 PUT，依序送出。 */
  const onSave = async (changes: QuotaChange[]) => {
    if (!editing) return
    const { user_id: userId, username } = editing
    const results = await elevated(t('admin.quota.adminQuotaPage.adjustQuota'), async () => {
      const out = []
      for (const { kind, ...body } of changes) out.push(await save.mutateAsync({ userId, kind, ...body }))
      return out
    })
    toast(results.some(r => r.changed) ? t('admin.quota.adminQuotaPage.adjustedTheLimitFor', { username }) : t('admin.quota.adminQuotaPage.theLimitForIsUnchanged', { username }))
    setEditingId(null)
  }

  return (
    <Card title={t('admin.quota.adminQuotaPage.usagePerUserToday')} labelledBy="quota-users-title" right={<span className={c.mono}>{t('admin.quota.adminQuotaPage.taipeiTime2', { day: data.day })}</span>}>
      {data.users.length === 0 ? (
        <p className={c.empty}>{t('admin.quota.adminQuotaPage.noAccountsYet')}</p>
      ) : (
        <div className={c.tableWrap}>
          <table className={c.table} aria-label={t('admin.quota.adminQuotaPage.usagePerUser')}>
            <thead>
              <tr><th>{t('admin.quota.adminQuotaPage.account')}</th><th>{t('admin.quota.adminQuotaPage.qA')}</th><th>{t('admin.quota.adminQuotaPage.export')}</th><th>{t('admin.quota.adminQuotaPage.upload')}</th><th>{t('admin.quota.adminQuotaPage.onlineLlm')}</th><th className={`${c.tdActions} ${c.stickyRight}`}>{t('admin.quota.adminQuotaPage.actions')}</th></tr>
            </thead>
            <tbody>
              {data.users.map(u => {
                const locked = u.is_super && !isSuper
                return (
                  <tr key={u.user_id}>
                    <td>
                      <div className={styles.account}>
                        <span className={`${c.truncate} ${styles.username}`} title={u.username}>{u.username}</span>
                        {u.is_super && <Chip tone="gold">super</Chip>}
                        {!u.enabled && <Chip tone="idle">{t('admin.quota.adminQuotaPage.disabled')}</Chip>}
                      </div>
                      <div className={c.sub}>{roleLabel(u.role)}</div>
                    </td>
                    {KINDS.map(k => {
                      const item = u.items.find(i => i.kind === k)
                      return <td key={k}>{item ? <UsageCell item={item} shadow={shadow} /> : <span className={c.muted}>—</span>}</td>
                    })}
                    <td>
                      <span className={c.num}>{t('admin.quota.adminQuotaPage.calls', { calls: fmtInt(u.llm.calls) })}</span>
                      <div className={`${c.sub} ${c.num}`}>{fmtInt(u.llm.prompt_tokens + u.llm.completion_tokens)} token</div>
                    </td>
                    <td className={`${c.tdActions} ${c.stickyRight}`}>
                      <RowActions primary={{
                        label: t('admin.quota.adminQuotaPage.adjustLimit'), onClick: () => setEditingId(u.user_id),
                        disabled: locked, hint: locked ? SUPER_ONLY_HINT : undefined,
                      }} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
      <div className={`${c.cardFoot} ${styles.foot}`}>
        <span>{t('admin.quota.adminQuotaPage.countsOnlyNoTopics')}</span>
        <span>{t('admin.quota.adminQuotaPage.onlineLlmIsTodayS')}</span>
      </div>
      {editing && (
        <QuotaLimitDialog key={editing.user_id} user={editing} isSuper={isSuper}
          onClose={() => setEditingId(null)} onSave={onSave} />
      )}
      {dialog}
    </Card>
  )
}

function QuotaContent() {
  const q = useQuotaOverview()
  const canSeeFlags = useHasScope('ops.read')
  const mode = q.data?.enforcement.mode
  const summary = mode === 'shadow'
    ? t('admin.quota.adminQuotaPage.perUserDailyLimitsFor')
    : mode === 'enforce'
      ? t('admin.quota.adminQuotaPage.perUserDailyLimitsFor2')
      : t('admin.quota.adminQuotaPage.perUserDailyLimitsFor3')
  return (
    <ConsolePage>
      <PageHeader summary={summary}
        actions={canSeeFlags && <Link className={c.btn} to="/admin/flags">{t('admin.quota.adminQuotaPage.goToFeatureFlags')}</Link>}
        help={<>
          {t('admin.quota.adminQuotaPage.dailyQuotasFollowTaipeiCalendar')}{' '}<code>QUOTA_ENFORCE</code>{t('admin.quota.adminQuotaPage.capabilityCeilingAndTheFeature')}{' '}<code>quota.enforce</code>{' '}{t('admin.quota.adminQuotaPage.areOnIsTheQuota')}
        </>} />
      {q.isPending ? (
        <p className={c.empty}>{t('admin.quota.adminQuotaPage.loading')}</p>
      ) : q.isError ? (
        <p className={c.error} role="alert">{t('admin.quota.adminQuotaPage.couldnTLoadQuotas', { error: messageOf(q.error) })}</p>
      ) : (
        <>
          <EnforcementTiles data={q.data} />
          <UsersCard data={q.data} />
        </>
      )}
      <StatsCard />
    </ConsolePage>
  )
}

/** 配額（/admin/quota）。後端 /api/admin/quota*（accounts.manage）。 */
export default function AdminQuotaPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="accounts.manage" title={t('admin.quota.adminQuotaPage.quotas')}>
        <QuotaContent />
      </RequireScope>
    </RequireAdmin>
  )
}
