import { useState } from 'react'
import { Card, Segmented } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { AuditEntry } from '../../../lib/adminSchemas'
import { actionLabel, actorLabel, auditSummary, fmtDateTime } from '../auditLabels'
import { KeysetPager } from './KeysetPager'
import s from './Security.module.css'
import { CATEGORY_LABELS } from './securityLabels'
import { type HighRiskCategory, useHighRisk } from './useSecurity'
import { t } from '@/i18n'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.security.securityTimeline.couldnTLoadTryAgain')
}

const DAYS = [7, 30, 90, 365] as const
const DAY_OPTIONS = DAYS.map(d => ({ value: String(d), label: t('admin.security.securityTimeline.days', { d }) }))

/** 高風險操作時間線（稽核紀錄裡屬於 security_ops.HIGH_RISK_ACTIONS 的列）。只顯示，不送告警。 */
export function HighRiskCard() {
  const [days, setDays] = useState<number>(30)
  const [category, setCategory] = useState<HighRiskCategory | ''>('')
  const [cursors, setCursors] = useState<(number | null)[]>([null])
  const q = useHighRisk({ days, category, beforeId: cursors[cursors.length - 1] })

  return (
    <Card title={t('admin.security.securityTimeline.highRiskActions')} labelledBy="sec-risk-title"
      right={<Segmented label={t('admin.security.securityTimeline.period')} options={DAY_OPTIONS} value={String(days)}
        onChange={v => { setDays(Number(v)); setCursors([null]) }} />}>
      <div className={s.filters}>
        <label className={s.inlineField}>
          {t('admin.security.securityTimeline.category')}
          <select className={c.select} value={category}
            onChange={e => { setCategory(e.target.value as HighRiskCategory | ''); setCursors([null]) }}>
            <option value="">{t('admin.security.securityTimeline.all')}</option>
            {Object.entries(CATEGORY_LABELS).map(([v, label]) => <option key={v} value={v}>{label}</option>)}
          </select>
        </label>
      </div>
      {q.isPending ? (
        <p className={c.empty}>{t('admin.security.securityTimeline.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.security.securityTimeline.couldnTLoadHighRisk', { error: messageOf(q.error) })}</p></div>
      ) : (
        <>
          {q.data.items.length === 0 ? (
            <p className={c.empty}>{t('admin.security.securityTimeline.noHighRiskActionsIn')}</p>
          ) : (
            <div className={c.tableWrap}>
              <table className={c.table}>
                <thead><tr><th>{t('admin.security.securityTimeline.time')}</th><th>{t('admin.security.securityTimeline.category')}</th><th>{t('admin.security.securityTimeline.actor')}</th><th>{t('admin.security.securityTimeline.action')}</th><th>{t('admin.security.securityTimeline.content')}</th></tr></thead>
                <tbody>
                  {q.data.items.map(e => {
                    // 產生的 client 把這幾個欄位標成 optional；補成稽核列的形狀才能共用標籤函式。
                    const entry: AuditEntry = {
                      ...e, actor_user_id: e.actor_user_id ?? null, actor_username: e.actor_username ?? null,
                      target_id: e.target_id ?? null,
                    }
                    return (
                      <tr key={e.id}>
                        <td className={c.num}>{fmtDateTime(e.created_at)}</td>
                        <td className={c.nowrap}>{CATEGORY_LABELS[e.category] ?? e.category}</td>
                        <td className={e.actor_username ? c.t1 : c.muted}>{actorLabel(entry)}</td>
                        <td className={c.nowrap}>{actionLabel(e.action)}</td>
                        <td className={s.wrap}>{auditSummary(entry)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {(q.data.items.length > 0 || cursors.length > 1) && (
            <KeysetPager page={cursors.length} hasOlder={q.data.next_before_id != null}
              onNewer={() => setCursors(cursors.slice(0, -1))}
              onOlder={() => { if (q.data.next_before_id != null) setCursors([...cursors, q.data.next_before_id]) }} />
          )}
        </>
      )}
    </Card>
  )
}
