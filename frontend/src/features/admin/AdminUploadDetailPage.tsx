import { Suspense, lazy, useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { ConfirmDialog } from '../../components/primitives/ConfirmDialog'
import { Modal } from '../../components/primitives/Modal'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { ApiError } from '../../lib/api'
import type { AdminUploadDetail, AdminUploadPreview } from '../../lib/generated/adminApi'
import { ViewerBoundary } from '../report/pdf/ViewerBoundary'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import {
  REJECTABLE_STATES, failureLabel, fmtBytes, graceRemaining, isInFlight, isRetryableFailure, reviewErrorMessage,
  stateLabel,
} from './uploadLabels'
import { useUpload, useUploadAction, useUploadFile, useUploadPreview, type UploadAction } from './useAdminUploads'
import styles from './Admin.module.css'
import u from './Uploads.module.css'

// 懶載入：PDFium 的 WASM 體積可觀，只在真的要預覽時才下載（與閱讀頁的 PdfPane 同一支檢視器）。
const PdfViewer = lazy(() => import('../report/pdf/PdfViewer'))

// 與後端 app/services/upload_review.py 的 REASON_MAX_CHARS 相同；這裡只是提早提示，後端仍會再驗一次。
const REASON_MAX = 500

function useNow(intervalMs = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), intervalMs)
    return () => clearInterval(t)
  }, [intervalMs])
  return now
}

const PREVIEWABLE = new Set(['draft', 'published'])

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <><dt>{label}</dt><dd>{children ?? '—'}</dd></>
}

const dash = (v: string | number | null | undefined) => (v == null || v === '' ? '—' : String(v))

// ── 動作與對話框 ────────────────────────────────────────────────────────

function RejectDialog({ open, name, busy, error, onClose, onSubmit }: {
  open: boolean; name: string; busy: boolean; error: string | null; onClose: () => void; onSubmit: (reason: string) => void
}) {
  const [reason, setReason] = useState('')
  const note = reason.trim()
  const tooLong = note.length > REASON_MAX
  const close = () => { setReason(''); onClose() }
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!note || tooLong) return
    onSubmit(note)
  }
  return (
    <Modal open={open} onClose={close} title={`退回「${name}」`}>
      <form className={styles.dialogForm} onSubmit={submit} noValidate>
        <p className={styles.hint}>
          退回後這份上傳不會發布；寬限期內可以撤銷，過期後系統會清除檔案與草稿內容。原因只有管理員看得到，操作紀錄只記字數。
        </p>
        <label className={styles.field}>原因（必填，最多 {REASON_MAX} 字）
          <textarea value={reason} onChange={e => setReason(e.target.value)} rows={3} maxLength={REASON_MAX * 2} />
        </label>
        <span className={tooLong ? u.queueError : styles.muted}>{note.length}／{REASON_MAX} 字{tooLong ? '，超過上限' : ''}</span>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.action} onClick={close}>取消</button>
          <button type="submit" className={styles.primary} disabled={busy || !note || tooLong}>
            {busy ? '退回中…' : '退回'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

const DONE_MESSAGE: Record<UploadAction, string> = {
  publish: '已發布，所有使用者現在都看得到這份研報。',
  reject: '已退回。寬限期內可以撤銷。',
  unreject: '已撤銷退回。',
  retry: '已排入重新處理，等系統下一輪處理。',
}

function Actions({ upload, name, now, onNotice }: {
  upload: AdminUploadDetail; name: string; now: number; onNotice: (msg: string, isError?: boolean) => void
}) {
  const action = useUploadAction()
  const [confirm, setConfirm] = useState<Exclude<UploadAction, 'reject'> | null>(null)
  const [rejecting, setRejecting] = useState(false)
  const [rejectError, setRejectError] = useState<string | null>(null)
  const { state } = upload
  const busy = action.isPending
  const grace = state === 'rejected' && !upload.purged_at ? graceRemaining(upload.purge_after, now) : null

  const run = (kind: UploadAction, reason?: string) => {
    action.mutate({ action: kind, uploadId: upload.upload_id, reason }, {
      onSuccess: () => {
        if (kind === 'reject') { setRejecting(false); setRejectError(null) }
        onNotice(DONE_MESSAGE[kind])
      },
      onError: err => {
        if (kind === 'reject') setRejectError(reviewErrorMessage(err))
        else onNotice(reviewErrorMessage(err), true)
      },
    })
  }

  const buttons: ReactNode[] = []
  const notes: string[] = []
  if (state === 'draft') {
    buttons.push(<button key="publish" type="button" className={styles.primary} disabled={busy} onClick={() => setConfirm('publish')}>發布</button>)
  }
  if ((REJECTABLE_STATES as readonly string[]).includes(state)) {
    buttons.push(
      <button key="reject" type="button" className={`${styles.action} ${styles.danger}`} disabled={busy}
        onClick={() => { setRejectError(null); setRejecting(true) }}>退回</button>,
    )
  } else if (state === 'scanning' || state === 'processing') {
    buttons.push(<button key="reject" type="button" className={`${styles.action} ${styles.danger}`} disabled>退回</button>)
    notes.push('系統正在掃描或處理這份檔案，完成後才能退回。')
  }
  if (state === 'failed') {
    const retryable = isRetryableFailure(upload.failure_kind)
    buttons.push(
      <button key="retry" type="button" className={styles.action} disabled={busy || !retryable}
        onClick={() => setConfirm('retry')}>重試</button>,
    )
    if (!retryable) notes.push(`「${failureLabel(upload.failure_kind)}」重試也不會成功，不能重試；可以退回。`)
  }
  if (state === 'rejected') {
    if (upload.purged_at) {
      notes.push(`已於 ${fmtDateTime(upload.purged_at)} 清除，無法撤銷。`)
    } else {
      buttons.push(
        <button key="unreject" type="button" className={styles.action} disabled={busy || !grace}
          onClick={() => setConfirm('unreject')}>撤銷退回</button>,
      )
      notes.push(grace ? `寬限期還剩 ${grace}，期間內可以撤銷退回。` : '寬限期已過，等待系統清除，無法撤銷。')
    }
  }
  if (state === 'published') notes.push('已發布的研報不能退回；如需下架，請到「研報管理」隱藏。')
  if (state === 'infected' || state === 'blocked') notes.push('被攔截的檔案留在隔離區，不提供下載或預覽，也沒有可執行的動作。')
  if (state === 'duplicate') notes.push('語料庫已有同一份檔案，這筆上傳不會再處理。')

  const confirmText: Record<Exclude<UploadAction, 'reject'>, { title: string; body: string; label: string }> = {
    publish: {
      title: `發布「${name}」？`,
      body: '發布後所有使用者立刻能在檢索、問答與閱讀頁看到這份研報。之後如需下架只能在「研報管理」隱藏，不能再退回。',
      label: '發布',
    },
    unreject: {
      title: `撤銷退回「${name}」？`,
      body: `撤銷後這份上傳回到退回前的狀態（由系統依處理進度判斷）${grace ? `；寬限期還剩 ${grace}` : ''}。`,
      label: '撤銷退回',
    },
    retry: {
      title: `重試「${name}」？`,
      body: '系統會在下一輪重新抽字、標註與入庫；處理完成後成為草稿。',
      label: '重試',
    },
  }

  if (buttons.length === 0 && notes.length === 0) return null
  return (
    <section className={styles.card} aria-labelledby="upload-actions-title">
      <h2 id="upload-actions-title" className={styles.ctitle}>審核動作</h2>
      <div className={u.actionsBar}>
        {buttons}
        {notes.map(n => <p key={n} className={u.actionNote}>{n}</p>)}
        {state === 'published' && <Link to="/admin/reports" className={u.back}>前往研報管理</Link>}
      </div>
      <ConfirmDialog
        open={confirm != null}
        title={confirm ? confirmText[confirm].title : ''}
        body={confirm ? confirmText[confirm].body : ''}
        confirmLabel={confirm ? confirmText[confirm].label : ''}
        onConfirm={() => { const kind = confirm; setConfirm(null); if (kind) run(kind) }}
        onCancel={() => setConfirm(null)}
      />
      <RejectDialog open={rejecting} name={name} busy={busy} error={rejectError}
        onClose={() => { setRejecting(false); setRejectError(null) }} onSubmit={reason => run('reject', reason)} />
    </section>
  )
}

// ── 各區塊 ──────────────────────────────────────────────────────────────

function FileInfo({ upload }: { upload: AdminUploadDetail }) {
  return (
    <section className={styles.card} aria-labelledby="upload-file-title">
      <h2 id="upload-file-title" className={styles.ctitle}>檔案資訊</h2>
      <dl className={u.dl}>
        <Field label="原始檔名">{upload.original_name}</Field>
        <Field label="大小">{fmtBytes(upload.size_bytes)}</Field>
        <Field label="SHA-256"><span className={u.mono}>{upload.file_hash}</span></Field>
        <Field label="上傳者">{dash(upload.uploaded_by)}</Field>
        <Field label="上傳時間">{fmtDateTime(upload.uploaded_at)}</Field>
        <Field label="檔案修改時間">{fmtDateTime(upload.client_mtime)}</Field>
        <Field label="狀態">{stateLabel(upload.state)}</Field>
        <Field label="狀態更新">{fmtDateTime(upload.state_changed_at)}</Field>
      </dl>
    </section>
  )
}

function ScanInfo({ upload }: { upload: AdminUploadDetail }) {
  return (
    <section className={styles.card} aria-labelledby="upload-scan-title">
      <h2 id="upload-scan-title" className={styles.ctitle}>掃描結果</h2>
      <dl className={u.dl}>
        <Field label="引擎與病毒碼">{dash(upload.scan_engine)}</Field>
        <Field label="掃描時間">{fmtDateTime(upload.scanned_at)}</Field>
        <Field label="嘗試次數">{upload.scan_attempts}</Field>
        <Field label="病毒名">
          {upload.scan_signature ? <span className={u.danger}>{upload.scan_signature}</span> : '—'}
        </Field>
        {upload.scan_last_error && <Field label="最後錯誤"><span className={u.pre}>{upload.scan_last_error}</span></Field>}
      </dl>
      {upload.state === 'quarantined' && upload.scan_last_error && (
        <p className={styles.hint}>掃描服務暫停或逾時時，檔案會保留在隔離區，恢復後自動重掃，不會放行未掃描的檔案。</p>
      )}
    </section>
  )
}

function ProcessInfo({ upload }: { upload: AdminUploadDetail }) {
  const r = upload.report
  const flags: string[] = []
  if (r?.needs_review) flags.push('抽取品質需要複核')
  if (r?.pages_failed?.length) flags.push(`第 ${r.pages_failed.join('、')} 頁抽取失敗`)
  return (
    <section className={styles.card} aria-labelledby="upload-process-title">
      <h2 id="upload-process-title" className={styles.ctitle}>處理結果</h2>
      <dl className={u.dl}>
        <Field label="處理次數">{upload.process_attempts}</Field>
        <Field label="處理完成">{fmtDateTime(upload.processed_at)}</Field>
        {upload.failure_kind && (
          <Field label="失敗原因">
            {failureLabel(upload.failure_kind)}
            {isRetryableFailure(upload.failure_kind) && <span className={styles.muted}>（可重試）</span>}
          </Field>
        )}
        {upload.failure_detail && <Field label="失敗細節"><span className={u.pre}>{upload.failure_detail}</span></Field>}
        {r && (
          <>
            <Field label="抽取器">{[r.extractor, r.extraction_version].filter(Boolean).join(' ') || '—'}</Field>
            <Field label="品質分數">{r.quality_score != null ? r.quality_score.toFixed(2) : '—'}</Field>
            <Field label="頁數">{dash(r.page_count)}</Field>
            <Field label="品質旗標">{flags.length ? <span className={u.danger}>{flags.join('；')}</span> : '無'}</Field>
            <Field label="入庫時間">{fmtDateTime(r.created_at)}</Field>
          </>
        )}
      </dl>
      {!r && !upload.failure_kind && <p className={styles.hint}>還沒有入庫的研報（掃描與處理完成後才會有）。</p>}
    </section>
  )
}

function Decision({ upload }: { upload: AdminUploadDetail }) {
  if (!upload.decided_at && !upload.decision_reason && !upload.purge_after && !upload.purged_at) return null
  return (
    <section className={styles.card} aria-labelledby="upload-decision-title">
      <h2 id="upload-decision-title" className={styles.ctitle}>審核決策</h2>
      <dl className={u.dl}>
        <Field label="決策者">{dash(upload.decided_by)}</Field>
        <Field label="決策時間">{fmtDateTime(upload.decided_at)}</Field>
        {upload.decision_reason && <Field label="退回原因"><span className={u.pre}>{upload.decision_reason}</span></Field>}
        {upload.purge_after && <Field label="預定清除">{fmtDateTime(upload.purge_after)}</Field>}
        {upload.purged_at && <Field label="已清除">{fmtDateTime(upload.purged_at)}</Field>}
      </dl>
    </section>
  )
}

const PENDING = <span className={u.pending}>產生中</span>

function Tags({ tags }: { tags: AdminUploadPreview['tags'] }) {
  const targets = [...tags.stock_targets, ...tags.futures_targets]
  return (
    <dl className={u.dl}>
      <Field label="研究報告">{tags.is_research ? '是' : '否'}</Field>
      <Field label="市場">{dash(tags.market)}</Field>
      <Field label="券商">{dash(tags.source)}</Field>
      <Field label="報告日">{tags.report_date ? tags.report_date.slice(0, 10) : '—'}</Field>
      <Field label="類型">{dash(tags.report_type)}</Field>
      <Field label="語言">{dash(tags.language)}</Field>
      <Field label="公司">{[tags.stock_code, tags.company_name].filter(Boolean).join(' ') || '—'}</Field>
      <Field label="商品類別">{tags.instrument_types.length ? tags.instrument_types.join('、') : '—'}</Field>
      <Field label="標的">{targets.length ? targets.join('、') : '—'}</Field>
      <Field label="信心">{tags.confidence != null ? tags.confidence.toFixed(2) : '—'}</Field>
    </dl>
  )
}

function ContentPreview({ uploadId }: { uploadId: string }) {
  const preview = useUploadPreview(uploadId, true)
  if (preview.isPending) return <section className={styles.card}><p className={styles.idle}>內容預覽載入中…</p></section>
  if (preview.isError) {
    return (
      <section className={styles.card}>
        <p className={styles.error} role="alert">內容預覽載入失敗：{reviewErrorMessage(preview.error)}</p>
      </section>
    )
  }
  const p = preview.data
  return (
    <>
      <section className={styles.card} aria-labelledby="upload-content-title">
        <h2 id="upload-content-title" className={styles.ctitle}>內容</h2>
        <dl className={u.dl}>
          <Field label="標題">{p.title_state === 'pending' ? PENDING : p.title}</Field>
          {p.title_original && p.title_original !== p.title && <Field label="原始標題">{p.title_original}</Field>}
          <Field label="摘要">{p.summary_state === 'pending' ? PENDING : <span className={u.pre}>{p.summary}</span>}</Field>
        </dl>
      </section>
      <section className={styles.card} aria-labelledby="upload-tags-title">
        <h2 id="upload-tags-title" className={styles.ctitle}>標籤</h2>
        <Tags tags={p.tags} />
      </section>
      <section className={styles.card} aria-labelledby="upload-takeaways-title">
        <h2 id="upload-takeaways-title" className={styles.ctitle}>摘錄</h2>
        {p.takeaways_state === 'pending' ? (
          <p className={styles.idle}>{PENDING}</p>
        ) : p.takeaways.length === 0 ? (
          <p className={styles.idle}>沒有可展示的摘錄</p>
        ) : (
          <ol className={u.takeaways}>
            {p.takeaways.map(t => (
              <li key={t.ordinal}>
                {t.claim}
                {t.quote && <span className={u.quote}>{t.quote}</span>}
              </li>
            ))}
          </ol>
        )}
      </section>
      <section className={styles.card} aria-labelledby="upload-text-title">
        <h2 id="upload-text-title" className={styles.ctitle}>正典文字</h2>
        {p.text_state === 'missing' || !p.text ? (
          <p className={styles.idle}>沒有抽取到文字</p>
        ) : (
          <details className={u.textBox}>
            <summary>展開全文（{p.text_chars.toLocaleString()} 字{p.text_truncated ? '，只顯示前段' : ''}）</summary>
            <pre className={u.text}>{p.text}</pre>
          </details>
        )}
      </section>
    </>
  )
}

function PdfPreview({ uploadId, title }: { uploadId: string; title: string }) {
  const file = useUploadFile(uploadId, true)
  let body: ReactNode
  if (file.isPending) {
    body = <p className={styles.idle}>取得原檔中…</p>
  } else if (file.isError) {
    const err = file.error
    if (err instanceof ApiError && err.status === 404 && err.code !== 'upload_report_missing') {
      body = (
        <>
          <p className={styles.idle} role="status">此環境無法預覽原檔</p>
          {err.message && !/^HTTP \d+$/.test(err.message) && <p className={styles.hint}>{err.message}</p>}
        </>
      )
    } else {
      body = <p className={styles.error} role="alert">原檔無法取得：{reviewErrorMessage(err)}</p>
    }
  } else {
    const { url, file_name } = file.data
    body = (
      <>
        <div className={u.pdfBar}>
          <a href={url} target="_blank" rel="noopener noreferrer">在新分頁開啟</a>
          <a href={url} download={file_name}>下載</a>
        </div>
        <div className={u.pdfStage}>
          {/* key 綁網址：presign 換新時邊界的 failed 狀態不沾黏（同 PdfPane 的理由）。 */}
          <ViewerBoundary key={url} fallback={<iframe className={u.pdfFrame} src={url} title={title} />}>
            <Suspense fallback={<p className={styles.idle} role="status">正在啟動 PDF 引擎…</p>}>
              <PdfViewer url={url} title={title} />
            </Suspense>
          </ViewerBoundary>
        </div>
      </>
    )
  }
  return (
    <section className={styles.card} aria-labelledby="upload-pdf-title">
      <h2 id="upload-pdf-title" className={styles.ctitle}>PDF 預覽</h2>
      {body}
    </section>
  )
}

// ── 頁面 ────────────────────────────────────────────────────────────────

function UploadDetail({ uploadId }: { uploadId: string }) {
  const detail = useUpload(uploadId)
  const now = useNow()
  const [notice, setNotice] = useState<{ msg: string; isError: boolean } | null>(null)
  const onNotice = (msg: string, isError = false) => setNotice({ msg, isError })

  const back = <Link to="/admin/uploads" className={u.back}>← 回到上傳紀錄</Link>
  if (detail.isPending) return <>{back}<p className={styles.idle}>載入中…</p></>
  if (detail.isError) {
    const gone = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <>
        {back}
        <p className={styles.error} role="alert">
          {gone ? '找不到這筆上傳紀錄（可能已被清除）。' : `載入失敗：${reviewErrorMessage(detail.error)}`}
        </p>
      </>
    )
  }
  const upload = detail.data
  const name = upload.report?.title?.trim() || upload.original_name
  const previewable = PREVIEWABLE.has(upload.state)
  const off = ['failed', 'infected', 'blocked', 'rejected'].includes(upload.state)
  return (
    <>
      {back}
      <header className={styles.header}>
        <div className={u.titleRow}>
          <h1 className={styles.title}>{name}</h1>
          <span className={`${styles.badge} ${off ? styles.badgeOff : ''}`}>{stateLabel(upload.state)}</span>
        </div>
        <p className={styles.sub}>
          {upload.original_name}
          {isInFlight(upload.state) && '・處理中，畫面每 5 秒自動更新'}
        </p>
      </header>
      {notice && (
        <p className={notice.isError ? styles.error : styles.ok} role={notice.isError ? 'alert' : 'status'}>{notice.msg}</p>
      )}
      <Actions upload={upload} name={name} now={now} onNotice={onNotice} />
      <div className={u.grid}>
        <FileInfo upload={upload} />
        <ScanInfo upload={upload} />
        <ProcessInfo upload={upload} />
        <Decision upload={upload} />
      </div>
      {previewable ? (
        <>
          <ContentPreview uploadId={uploadId} />
          <PdfPreview uploadId={uploadId} title={name} />
        </>
      ) : (
        <p className={styles.hint}>內容與原檔預覽只在處理完成（草稿）或已發布時提供；隔離區與被攔截的檔案永遠不提供下載。</p>
      )}
    </>
  )
}

function DetailRoute() {
  const { uploadId = '' } = useParams()
  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <UploadDetail key={uploadId} uploadId={uploadId} />
      </div>
    </div>
  )
}

export default function AdminUploadDetailPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="reports.manage" title="研報管理"><DetailRoute /></RequireScope>
    </RequireAdmin>
  )
}
