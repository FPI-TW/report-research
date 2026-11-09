import { useState } from 'react'
import { Field, FormDialog } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { displayTitle } from '../../../lib/displayTitle'
import type { AdminReportItem } from '../../../lib/generated/adminApi'
import { useReportVisibility } from '../useAdminReports'
import { namesPreview } from '../useSelection'
import { REASON_MAX, messageOf } from './reportStatus'
import s from './Reports.module.css'
import { t } from '@/i18n'

/** 原因欄：必填、最多 500 字；輸入框放寬到兩倍，超過時由送出前的檢查說明，不是默默截掉。 */
function ReasonField({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <Field label={t('admin.reports.reportDialogs.reason')} counter={t('admin.reports.reportDialogs.text', { length: value.trim().length, REASON_MAX })}>
      <textarea className={c.textarea} rows={3} value={value} maxLength={REASON_MAX * 2} placeholder={placeholder}
        onChange={e => onChange(e.target.value)} />
    </Field>
  )
}

/** 回 null 表示可以送出，否則是要顯示的錯誤。 */
function checkReason(note: string): string | null {
  if (!note) return t('admin.reports.reportDialogs.aReasonIsRequiredTo')
  if (note.length > REASON_MAX) return t('admin.reports.reportDialogs.reasonCanBeAtMost', { REASON_MAX })
  return null
}

export function HideDialog({ report, onClose, onDone }: {
  report: AdminReportItem | null; onClose: () => void; onDone: (report: AdminReportItem) => void
}) {
  const visibility = useReportVisibility()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const close = () => { setReason(''); setError(null); onClose() }

  const submit = () => {
    if (!report) return
    const note = reason.trim()
    const problem = checkReason(note)
    setError(problem)
    if (problem) return
    visibility.mutate({ fileHash: report.file_hash, hidden: true, reason: note }, {
      onSuccess: () => { close(); onDone(report) },
      onError: err => setError(messageOf(err)),
    })
  }

  return (
    <FormDialog
      open={report != null}
      title={report ? t('admin.reports.reportDialogs.hide', { report: displayTitle(report) }) : ''}
      body={t('admin.reports.reportDialogs.onceHiddenTheReportDisappears')}
      submitLabel={visibility.isPending ? t('admin.reports.reportDialogs.hiding') : t('admin.reports.reportDialogs.hide2')}
      submitTone="danger"
      busy={visibility.isPending}
      error={error}
      onSubmit={submit}
      onClose={close}
    >
      <ReasonField value={reason} onChange={setReason} placeholder={t('admin.reports.reportDialogs.eGNotResearchIncorrect')} />
    </FormDialog>
  )
}

/** 批次隱藏：列出對象（最多 5 個名稱）並要求原因；原因規則與單筆相同，後端仍會再驗一次。 */
export function BulkHideDialog({ targets, onClose, onSubmit }: {
  targets: AdminReportItem[] | null; onClose: () => void; onSubmit: (reason: string) => Promise<void>
}) {
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const close = () => { setReason(''); setError(null); onClose() }
  const count = targets?.length ?? 0

  const submit = async () => {
    const note = reason.trim()
    const problem = checkReason(note)
    setError(problem)
    if (problem) return
    setBusy(true)
    try {
      await onSubmit(note)
      setReason('')
      onClose()
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <FormDialog
      open={targets != null}
      title={t('admin.reports.reportDialogs.hideReports', { count })}
      body={t('admin.reports.reportDialogs.theSameReasonAppliesTo')}
      submitLabel={busy ? t('admin.reports.reportDialogs.hiding') : t('admin.reports.reportDialogs.hideReports2', { count })}
      submitTone="danger"
      busy={busy}
      error={error}
      onSubmit={() => { void submit() }}
      onClose={close}
    >
      {targets && <p className={s.targets}>{namesPreview(targets.map(r => displayTitle(r)))}</p>}
      <ReasonField value={reason} onChange={setReason} />
    </FormDialog>
  )
}
