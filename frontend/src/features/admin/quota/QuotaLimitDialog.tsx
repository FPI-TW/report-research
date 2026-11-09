import { useState } from 'react'
import { Field, FormDialog, isElevationCancelled } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { QuotaUserRow } from '../../../lib/generated/adminApi'
import { KIND_LABELS, KINDS, LIMIT_MAX, REASON_MAX, fmtInt, limitText, messageOf } from './quotaLabels'
import { currentOf, draftOf, planChanges, type Draft, type QuotaChange } from './quotaPlan'
import type { QuotaKind, QuotaMode } from './useQuota'
import styles from './Quota.module.css'
import { t } from '@/i18n'

/**
 * 「調整上限」表單：問答／匯出／上傳各一列（上限＋設定），加一個備註。
 * 輸入數字就是指定上限；「程式預設」清掉覆寫；「不限」只有 super admin 選得到。
 * 呼叫端以 `key={user.user_id}` 掛載，初值直接取自該帳號（不在 effect 裡同步 state）。
 */
export function QuotaLimitDialog({ user, isSuper, onClose, onSave }: {
  user: QuotaUserRow
  isSuper: boolean
  onClose: () => void
  /** 送出有變的類別；失敗就丟出例外（訊息顯示在表單裡），成功後由呼叫端關閉。 */
  onSave: (changes: QuotaChange[]) => Promise<void>
}) {
  const [draft, setDraft] = useState<Draft>(() => draftOf(user))
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const plan = planChanges(user, draft, note)
  const set = (kind: QuotaKind, next: Draft[QuotaKind]) => { setDraft(cur => ({ ...cur, [kind]: next })); setError(null) }

  const submit = async () => {
    if (plan.error) { setError(plan.error); return }
    setError(null)
    setBusy(true)
    try {
      await onSave(plan.changes)
    } catch (err) {
      if (!isElevationCancelled(err)) setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog open title={t('admin.quota.quotaLimitDialog.adjustDailyLimitsFor', { username: user.username })} submitLabel={t('admin.quota.quotaLimitDialog.save')} busy={busy} error={error}
      body={t('admin.quota.quotaLimitDialog.affectsOnlyThisAccountResets')}
      submitDisabled={!plan.error && plan.changes.length === 0}
      onSubmit={() => { void submit() }} onClose={onClose}>
      {KINDS.map(kind => {
        const label = KIND_LABELS[kind]
        const item = user.items.find(i => i.kind === kind)
        const cur = currentOf(user, kind)
        const d = draft[kind]
        return (
          <div key={kind} className={styles.kind} role="group" aria-label={label}>
            <div className={styles.kindRow}>
              <span className={styles.kindLabel}>{label}</span>
              <input className={`${c.input} ${c.inputMono} ${styles.kindLimit}`} type="number" inputMode="numeric"
                min={0} max={LIMIT_MAX} step={1} aria-label={t('admin.quota.quotaLimitDialog.dailyLimit', { label })}
                value={d.mode === 'limit' ? d.limit : ''}
                placeholder={d.mode === 'unlimited' ? t('admin.quota.quotaLimitDialog.unlimited') : item ? t('admin.quota.quotaLimitDialog.default', { default_limit: fmtInt(item.default_limit) }) : t('admin.quota.quotaLimitDialog.default2')}
                onChange={e => set(kind, { mode: 'limit', limit: e.target.value })} />
              <select className={`${c.select} ${styles.kindMode}`} aria-label={t('admin.quota.quotaLimitDialog.setting', { label })} value={d.mode}
                onChange={e => set(kind, { mode: e.target.value as QuotaMode, limit: e.target.value === 'limit' ? d.limit : '' })}>
                <option value="limit">{t('admin.quota.quotaLimitDialog.customLimit')}</option>
                <option value="default">{t('admin.quota.quotaLimitDialog.codeDefault')}</option>
                {(isSuper || cur.mode === 'unlimited') && <option value="unlimited">{t('admin.quota.quotaLimitDialog.unlimited')}</option>}
              </select>
            </div>
            {item && (
              <div className={styles.kindNow}>
                {t('admin.quota.quotaLimitDialog.current')}{item.mode === 'default' ? t('admin.quota.quotaLimitDialog.codeDefault2', { default_limit: fmtInt(item.default_limit) }) : item.mode === 'unlimited' ? t('admin.quota.quotaLimitDialog.unlimited') : t('admin.quota.quotaLimitDialog.override', { item: limitText(item) })}
                {t('admin.quota.quotaLimitDialog.usedToday', { used: fmtInt(item.used) })}
                {item.reason && <>{t('admin.quota.quotaLimitDialog.note', { reason: item.reason })}</>}
              </div>
            )}
          </div>
        )
      })}
      <Field label={t('admin.quota.quotaLimitDialog.noteOptionalVisibleToAdmins')} counter={t('admin.quota.quotaLimitDialog.text', { length: note.length, REASON_MAX })}>
        <input className={c.input} value={note} maxLength={REASON_MAX} placeholder={t('admin.quota.quotaLimitDialog.eGResearchWeekDemand')} autoComplete="off"
          onChange={e => { setNote(e.target.value); setError(null) }} />
      </Field>
    </FormDialog>
  )
}
