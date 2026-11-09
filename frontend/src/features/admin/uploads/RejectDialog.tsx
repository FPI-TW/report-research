import { useState } from 'react'
import { Field, FormDialog } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { AdminUpload } from '../../../lib/generated/adminApi'
import { t } from '@/i18n'

// 與後端 app/services/upload_review.py 的 REASON_MAX_CHARS 相同；這裡只是提早提示，後端仍會再驗一次。
const REASON_MAX = 500

/** 退回的原因表單：必填、最多 500 字；送出鈕在空白或超過上限時停用。 */
export function RejectDialog({ target, busy, error, onClose, onSubmit }: {
  target: AdminUpload | null; busy: boolean; error: string | null; onClose: () => void; onSubmit: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  const note = reason.trim()
  const tooLong = note.length > REASON_MAX
  const close = () => { setReason(''); onClose() }
  return (
    <FormDialog
      open={target != null}
      title={target ? t('admin.uploads.rejectDialog.reject', { original_name: target.original_name }) : ''}
      body={t('admin.uploads.rejectDialog.afterRejectionTheFileIs')}
      submitLabel={busy ? t('admin.uploads.rejectDialog.rejecting') : t('admin.uploads.rejectDialog.reject2')}
      submitTone="danger"
      submitDisabled={!note || tooLong}
      busy={busy}
      error={error}
      onSubmit={() => onSubmit(note)}
      onClose={close}
    >
      <Field label={t('admin.uploads.rejectDialog.reason')} counter={t('admin.uploads.rejectDialog.text', { length: note.length, REASON_MAX, v: tooLong ? t('admin.uploads.rejectDialog.overTheLimit') : '' })}>
        <textarea className={c.textarea} rows={3} value={reason} maxLength={REASON_MAX * 2}
          placeholder={t('admin.uploads.rejectDialog.eGNotAResearch')} onChange={e => setReason(e.target.value)} />
      </Field>
    </FormDialog>
  )
}
