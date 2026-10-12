import { useState } from 'react'
import { Field, FormDialog, isElevationCancelled } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { QuotaUserRow } from '../../../lib/generated/adminApi'
import { KIND_LABELS, KINDS, LIMIT_MAX, REASON_MAX, fmtInt, limitText, messageOf } from './quotaLabels'
import { currentOf, draftOf, planChanges, type Draft, type QuotaChange } from './quotaPlan'
import type { QuotaKind, QuotaMode } from './useQuota'
import styles from './Quota.module.css'

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
    <FormDialog open title={`調整「${user.username}」的每日上限`} submitLabel="儲存" busy={busy} error={error}
      body="只影響這個帳號，台北時間每天 0 點重置。上限 0＝完全不能用。"
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
                min={0} max={LIMIT_MAX} step={1} aria-label={`${label}每日上限`}
                value={d.mode === 'limit' ? d.limit : ''}
                placeholder={d.mode === 'unlimited' ? '不限' : item ? `預設 ${fmtInt(item.default_limit)}` : '預設'}
                onChange={e => set(kind, { mode: 'limit', limit: e.target.value })} />
              <select className={`${c.select} ${styles.kindMode}`} aria-label={`${label}設定`} value={d.mode}
                onChange={e => set(kind, { mode: e.target.value as QuotaMode, limit: e.target.value === 'limit' ? d.limit : '' })}>
                <option value="limit">指定上限</option>
                <option value="default">程式預設</option>
                {(isSuper || cur.mode === 'unlimited') && <option value="unlimited">不限</option>}
              </select>
            </div>
            {item && (
              <div className={styles.kindNow}>
                目前：{item.mode === 'default' ? `程式預設 ${fmtInt(item.default_limit)}` : item.mode === 'unlimited' ? '不限' : `覆寫 ${limitText(item)}`}
                ・今天已用 {fmtInt(item.used)}
                {item.reason && <>・備註：{item.reason}</>}
              </div>
            )}
          </div>
        )
      })}
      <Field label="備註（選填，只有管理員看得到）" counter={`${note.length}／${REASON_MAX}`}>
        <input className={c.input} value={note} maxLength={REASON_MAX} placeholder="例如：研究週需求" autoComplete="off"
          onChange={e => { setNote(e.target.value); setError(null) }} />
      </Field>
    </FormDialog>
  )
}
