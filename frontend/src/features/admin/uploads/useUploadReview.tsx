import { useState, type ReactNode } from 'react'
import { ConfirmDialog, toast } from '../../../components/console'
import type { AdminUpload } from '../../../lib/generated/adminApi'
import { graceRemaining, reviewErrorMessage, stateLabel } from '../uploadLabels'
import { useUploadAction, type UploadAction } from '../useAdminUploads'
import { RejectDialog } from './RejectDialog'

type ConfirmKind = Exclude<UploadAction, 'reject'>

function confirmText(kind: ConfirmKind, upload: AdminUpload, now: number): { title: string; body: string; label: string } {
  const name = upload.original_name
  switch (kind) {
    case 'publish':
      return {
        title: `發布「${name}」？`,
        body: '發布後所有人都能在檢索、問答與閱讀頁看到這份研報；之後要下架只能到「研報管理」隱藏，不能再退回。',
        label: '發布',
      }
    case 'unreject': {
      const grace = graceRemaining(upload.purge_after, now)
      return {
        title: `撤銷退回「${name}」？`,
        body: `撤銷後這份上傳回到退回前的狀態（由系統依處理進度判斷）${grace ? `；寬限期還剩 ${grace}` : ''}。`,
        label: '撤銷退回',
      }
    }
    case 'retry':
      return { title: `重試「${name}」？`, body: '系統會在下一輪重新抽字、標註與入庫；處理完成後成為草稿。', label: '重試' }
  }
}

function doneMessage(kind: UploadAction, name: string, result: AdminUpload): string {
  switch (kind) {
    case 'publish': return `已發布「${name}」`
    case 'reject': return `已退回「${name}」`
    // 撤銷後回到哪個狀態由後端依處理進度決定，照回應說。
    case 'unreject': return `「${name}」回到${stateLabel(result.state)}`
    case 'retry': return '已排入重試，等 worker 下一輪處理'
  }
}

export interface UploadReview {
  /** 要求執行一個審核動作：發布、撤銷退回、重試先跳確認框，退回先跳原因表單。 */
  request: (kind: UploadAction, upload: AdminUpload) => void
  busy: boolean
  /** 確認框與退回表單；放進頁面的 JSX。 */
  dialogs: ReactNode
}

/**
 * 審核動作（發布／退回／撤銷退回／重試）的確認、送出與結果提示；清單的列動作與詳情抽屜共用同一份。
 * 成功跳 toast；失敗時退回的錯誤留在表單裡，其他動作跳紅色 toast。409 代表畫面上的狀態已過時，
 * `useUploadAction` 不論成敗都會重抓清單與詳情。
 */
export function useUploadReview(now: number): UploadReview {
  const action = useUploadAction()
  const [confirm, setConfirm] = useState<{ kind: ConfirmKind; upload: AdminUpload } | null>(null)
  const [rejecting, setRejecting] = useState<AdminUpload | null>(null)
  const [rejectError, setRejectError] = useState<string | null>(null)

  const run = (kind: UploadAction, upload: AdminUpload, reason?: string) => {
    action.mutate({ action: kind, uploadId: upload.upload_id, reason }, {
      onSuccess: result => {
        if (kind === 'reject') { setRejecting(null); setRejectError(null) }
        toast(doneMessage(kind, upload.original_name, result))
      },
      onError: err => {
        if (kind === 'reject') setRejectError(reviewErrorMessage(err))
        else toast(reviewErrorMessage(err), { tone: 'bad' })
      },
    })
  }

  const request = (kind: UploadAction, upload: AdminUpload) => {
    if (kind === 'reject') { setRejectError(null); setRejecting(upload) } else setConfirm({ kind, upload })
  }

  const text = confirm ? confirmText(confirm.kind, confirm.upload, now) : null
  const dialogs = (
    <>
      <ConfirmDialog
        open={confirm != null}
        tone="safe"
        title={text?.title ?? ''}
        body={text?.body ?? ''}
        confirmLabel={text?.label ?? ''}
        onConfirm={() => { const c0 = confirm; setConfirm(null); if (c0) run(c0.kind, c0.upload) }}
        onCancel={() => setConfirm(null)}
      />
      <RejectDialog
        // key 綁對象：換一筆退回時原因欄重新開始。
        key={rejecting?.upload_id ?? 'none'}
        target={rejecting}
        busy={action.isPending}
        error={rejectError}
        onClose={() => { setRejecting(null); setRejectError(null) }}
        onSubmit={reason => { if (rejecting) run('reject', rejecting, reason) }}
      />
    </>
  )
  return { request, busy: action.isPending, dialogs }
}
