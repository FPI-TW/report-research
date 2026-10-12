import { useState } from 'react'
import { Field, FormDialog } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { displayTitle } from '../../../lib/displayTitle'
import type { AdminReportItem } from '../../../lib/generated/adminApi'
import { useReportVisibility } from '../useAdminReports'
import { namesPreview } from '../useSelection'
import { REASON_MAX, messageOf } from './reportStatus'
import s from './Reports.module.css'

/** 原因欄：必填、最多 500 字；輸入框放寬到兩倍，超過時由送出前的檢查說明，不是默默截掉。 */
function ReasonField({ value, onChange, placeholder }: { value: string; onChange: (v: string) => void; placeholder?: string }) {
  return (
    <Field label="原因" counter={`${value.trim().length}／${REASON_MAX}`}>
      <textarea className={c.textarea} rows={3} value={value} maxLength={REASON_MAX * 2} placeholder={placeholder}
        onChange={e => onChange(e.target.value)} />
    </Field>
  )
}

/** 回 null 表示可以送出，否則是要顯示的錯誤。 */
function checkReason(note: string): string | null {
  if (!note) return '隱藏研報必須填寫原因'
  if (note.length > REASON_MAX) return `原因最多 ${REASON_MAX} 字`
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
      title={report ? `隱藏「${displayTitle(report)}」` : ''}
      body="隱藏後檢索、問答、閱讀頁、雷達、簡報與原檔都看不到這份研報；批次照常處理，恢復後立即生效。原因只有管理員看得到。"
      submitLabel={visibility.isPending ? '隱藏中…' : '隱藏'}
      submitTone="danger"
      busy={visibility.isPending}
      error={error}
      onSubmit={submit}
      onClose={close}
    >
      <ReasonField value={reason} onChange={setReason} placeholder="例如：非研究檔、內容有誤、重複上傳" />
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
      title={`隱藏 ${count} 篇研報`}
      body="同一個原因套用到每一篇，每篇各留一筆操作紀錄；上傳草稿不能隱藏，會逐筆略過。"
      submitLabel={busy ? '隱藏中…' : `隱藏 ${count} 篇`}
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
