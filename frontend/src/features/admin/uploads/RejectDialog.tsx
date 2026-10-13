import { useState } from 'react'
import { Field, FormDialog } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import type { AdminUpload } from '../../../lib/generated/adminApi'

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
      title={target ? `退回「${target.original_name}」` : ''}
      body="退回後檔案在寬限期內保留，可以撤銷；期滿由上傳 worker 清除。原因只有管理員看得到，操作紀錄只記字數。"
      submitLabel={busy ? '退回中…' : '退回'}
      submitTone="danger"
      submitDisabled={!note || tooLong}
      busy={busy}
      error={error}
      onSubmit={() => onSubmit(note)}
      onClose={close}
    >
      <Field label="原因" counter={`${note.length}／${REASON_MAX}${tooLong ? '，超過上限' : ''}`}>
        <textarea className={c.textarea} rows={3} value={reason} maxLength={REASON_MAX * 2}
          placeholder="例如：不是研究報告、內容不完整" onChange={e => setReason(e.target.value)} />
      </Field>
    </FormDialog>
  )
}
