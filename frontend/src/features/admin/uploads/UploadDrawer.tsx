import { Suspense, lazy, type ReactNode } from 'react'
import { Link } from 'react-router'
import { Button, Chip, Drawer, DrawerFootNote, DrawerSection, Status } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { ApiError } from '../../../lib/api'
import type { AdminUploadDetail, AdminUploadPreview } from '../../../lib/generated/adminApi'
import { ViewerBoundary } from '../../report/pdf/ViewerBoundary'
import { fmtDateTime } from '../auditLabels'
import { failureLabel, fmtBytes, isInFlight, isRetryableFailure, reviewErrorMessage, stateLabel } from '../uploadLabels'
import { useUpload, useUploadFile, useUploadPreview } from '../useAdminUploads'
import u from '../Uploads.module.css'
import { BUSY_HINT, EXPIRED_HINT, notRetryableHint, reviewOptions, stateTone } from './reviewRules'
import type { UploadReview } from './useUploadReview'
import { t } from '@/i18n'

// 懶載入：PDFium 的 WASM 體積可觀，只在真的要預覽時才下載（與閱讀頁的 PdfPane 同一支檢視器）。
const PdfViewer = lazy(() => import('../../report/pdf/PdfViewer'))

const PREVIEWABLE = new Set(['draft', 'published'])
const WIDTH = 520

const dash = (v: string | number | null | undefined) => (v == null || v === '' ? '—' : String(v))

function Row({ label, children }: { label: string; children: ReactNode }) {
  return <><dt>{label}</dt><dd>{children ?? '—'}</dd></>
}

// ── 各區塊 ──────────────────────────────────────────────────────────────

function FileInfo({ upload }: { upload: AdminUploadDetail }) {
  return (
    <DrawerSection title={t('admin.uploads.uploadDrawer.fileInfo')}>
      <dl className={c.kv}>
        <Row label={t('admin.uploads.uploadDrawer.size')}>{fmtBytes(upload.size_bytes)}</Row>
        <Row label={t('admin.uploads.uploadDrawer.uploadedBy')}>{dash(upload.uploaded_by ?? (upload.api_client ? t('admin.uploads.uploadDrawer.api', { api_client: upload.api_client }) : null))}</Row>
        <Row label={t('admin.uploads.uploadDrawer.uploadedAt')}>{fmtDateTime(upload.uploaded_at)}</Row>
        <Row label={t('admin.uploads.uploadDrawer.fileModifiedAt')}>{fmtDateTime(upload.client_mtime)}</Row>
        <Row label={t('admin.uploads.uploadDrawer.statusUpdated')}>{fmtDateTime(upload.state_changed_at)}</Row>
        <Row label="SHA-256"><span className={u.hash}>{upload.file_hash}</span></Row>
      </dl>
    </DrawerSection>
  )
}

function ScanInfo({ upload }: { upload: AdminUploadDetail }) {
  return (
    <DrawerSection title={t('admin.uploads.uploadDrawer.scanResult')}>
      <dl className={c.kv}>
        <Row label={t('admin.uploads.uploadDrawer.engineAndSignatures')}>{dash(upload.scan_engine)}</Row>
        <Row label={t('admin.uploads.uploadDrawer.scannedAt')}>{fmtDateTime(upload.scanned_at)}</Row>
        <Row label={t('admin.uploads.uploadDrawer.attempts')}>{upload.scan_attempts}</Row>
        <Row label={t('admin.uploads.uploadDrawer.virusName')}>{upload.scan_signature ? <span className={u.bad}>{upload.scan_signature}</span> : '—'}</Row>
        {upload.scan_last_error && <Row label={t('admin.uploads.uploadDrawer.lastError')}><span className={u.prose}>{upload.scan_last_error}</span></Row>}
      </dl>
      {upload.state === 'quarantined' && upload.scan_last_error && (
        <p className={u.hint}>{t('admin.uploads.uploadDrawer.whenTheScannerIsPaused')}</p>
      )}
    </DrawerSection>
  )
}

function ProcessInfo({ upload }: { upload: AdminUploadDetail }) {
  const r = upload.report
  const flags: string[] = []
  if (r?.needs_review) flags.push(t('admin.uploads.uploadDrawer.extractionQualityNeedsReview'))
  if (r?.pages_failed?.length) flags.push(t('admin.uploads.uploadDrawer.extractionFailedOnPage', { pages_failed: r.pages_failed.join(t('admin.uploads.uploadDrawer.text')) }))
  return (
    <DrawerSection title={t('admin.uploads.uploadDrawer.processingResult')}>
      <dl className={c.kv}>
        <Row label={t('admin.uploads.uploadDrawer.processingRuns')}>{upload.process_attempts}</Row>
        <Row label={t('admin.uploads.uploadDrawer.processedAt')}>{fmtDateTime(upload.processed_at)}</Row>
        {upload.failure_kind && (
          <Row label={t('admin.uploads.uploadDrawer.failureReason')}>
            <span className={u.prose}>
              {failureLabel(upload.failure_kind)}{isRetryableFailure(upload.failure_kind) && t('admin.uploads.uploadDrawer.retryable')}
            </span>
          </Row>
        )}
        {upload.failure_detail && <Row label={t('admin.uploads.uploadDrawer.failureDetails')}><span className={u.prose}>{upload.failure_detail}</span></Row>}
        {r && (
          <>
            <Row label={t('admin.uploads.uploadDrawer.extractor')}>{[r.extractor, r.extraction_version].filter(Boolean).join(' ') || '—'}</Row>
            <Row label={t('admin.uploads.uploadDrawer.qualityScore')}>{r.quality_score != null ? r.quality_score.toFixed(2) : '—'}</Row>
            <Row label={t('admin.uploads.uploadDrawer.pages')}>{dash(r.page_count)}</Row>
            <Row label={t('admin.uploads.uploadDrawer.failedPages')}>{r.pages_failed?.length ?? 0}</Row>
            <Row label={t('admin.uploads.uploadDrawer.qualityFlags')}>
              {flags.length ? <span className={`${u.prose} ${u.bad}`}>{flags.join(t('admin.uploads.uploadDrawer.text2'))}</span> : <span className={u.prose}>{t('admin.uploads.uploadDrawer.none')}</span>}
            </Row>
            <Row label={t('admin.uploads.uploadDrawer.ingestedAt')}>{fmtDateTime(r.created_at)}</Row>
          </>
        )}
      </dl>
      {!r && !upload.failure_kind && <p className={u.hint}>{t('admin.uploads.uploadDrawer.noIngestedReportYetAvailable')}</p>}
    </DrawerSection>
  )
}

function Decision({ upload }: { upload: AdminUploadDetail }) {
  if (!upload.decided_at && !upload.decision_reason && !upload.purge_after && !upload.purged_at) return null
  return (
    <DrawerSection title={t('admin.uploads.uploadDrawer.reviewDecision')}>
      <dl className={c.kv}>
        <Row label={t('admin.uploads.uploadDrawer.decidedBy')}>{dash(upload.decided_by)}</Row>
        <Row label={t('admin.uploads.uploadDrawer.decidedAt')}>{fmtDateTime(upload.decided_at)}</Row>
        {upload.decision_reason && <Row label={t('admin.uploads.uploadDrawer.rejectionReason')}><span className={u.prose}>{upload.decision_reason}</span></Row>}
        {upload.purge_after && <Row label={t('admin.uploads.uploadDrawer.scheduledPurge')}>{fmtDateTime(upload.purge_after)}</Row>}
        {upload.purged_at && <Row label={t('admin.uploads.uploadDrawer.purged')}>{fmtDateTime(upload.purged_at)}</Row>}
      </dl>
    </DrawerSection>
  )
}

const PENDING = <span className={u.pending}>產生中</span>

function Tags({ tags }: { tags: AdminUploadPreview['tags'] }) {
  const targets = [...tags.stock_targets, ...tags.futures_targets]
  const company = [tags.company_name, tags.stock_code].filter(Boolean).join(' ')
  const chips = [
    tags.market ? t('admin.uploads.uploadDrawer.market', { market: tags.market }) : null,
    tags.source,
    tags.report_type,
    company || null,
    tags.report_date ? tags.report_date.slice(0, 10) : null,
  ].filter((x): x is string => Boolean(x))
  return (
    <>
      {chips.length > 0 && <div className={u.chips}>{chips.map(x => <Chip key={x}>{x}</Chip>)}</div>}
      <dl className={c.kv}>
        <Row label={t('admin.uploads.uploadDrawer.researchReport')}><span className={u.prose}>{tags.is_research ? t('admin.uploads.uploadDrawer.yes') : t('admin.uploads.uploadDrawer.no')}</span></Row>
        <Row label={t('admin.uploads.uploadDrawer.language')}>{dash(tags.language)}</Row>
        <Row label={t('admin.uploads.uploadDrawer.assetClass')}>{tags.instrument_types.length ? tags.instrument_types.join(t('admin.uploads.uploadDrawer.text')) : '—'}</Row>
        <Row label={t('admin.uploads.uploadDrawer.instruments')}>{targets.length ? targets.join(t('admin.uploads.uploadDrawer.text')) : '—'}</Row>
        <Row label={t('admin.uploads.uploadDrawer.confidence')}>{tags.confidence != null ? tags.confidence.toFixed(2) : '—'}</Row>
      </dl>
    </>
  )
}

function ContentPreview({ uploadId }: { uploadId: string }) {
  const preview = useUploadPreview(uploadId, true)
  if (preview.isPending) return <DrawerSection title={t('admin.uploads.uploadDrawer.content')}><p className={u.note}>{t('admin.uploads.uploadDrawer.loadingContentPreview')}</p></DrawerSection>
  if (preview.isError) {
    return (
      <DrawerSection title={t('admin.uploads.uploadDrawer.content')}>
        <p className={c.error} role="alert">{t('admin.uploads.uploadDrawer.couldnTLoadContentPreview', { error: reviewErrorMessage(preview.error) })}</p>
      </DrawerSection>
    )
  }
  const p = preview.data
  return (
    <>
      <DrawerSection title={t('admin.uploads.uploadDrawer.tags')}><Tags tags={p.tags} /></DrawerSection>
      <DrawerSection title={t('admin.uploads.uploadDrawer.content')}>
        <p className={u.contentTitle}>{p.title_state === 'pending' ? PENDING : dash(p.title)}</p>
        {p.title_original && p.title_original !== p.title && <p className={u.contentSub}>{t('admin.uploads.uploadDrawer.originalTitle', { title_original: p.title_original })}</p>}
        <p className={u.summaryText}>{p.summary_state === 'pending' ? PENDING : dash(p.summary)}</p>
      </DrawerSection>
      <DrawerSection title={t('admin.uploads.uploadDrawer.takeaways')}>
        {p.takeaways_state === 'pending' ? (
          <p className={u.note}>{PENDING}</p>
        ) : p.takeaways.length === 0 ? (
          <p className={u.note}>{t('admin.uploads.uploadDrawer.noTakeawaysToShow')}</p>
        ) : (
          <ol className={u.takeaways}>
            {p.takeaways.map(item => (
              <li key={item.ordinal}>
                {item.claim}
                {item.quote && <span className={u.quote}>{item.quote}</span>}
              </li>
            ))}
          </ol>
        )}
      </DrawerSection>
      <DrawerSection title={t('admin.uploads.uploadDrawer.canonicalText')}>
        {p.text_state === 'missing' || !p.text ? (
          <p className={u.note}>{t('admin.uploads.uploadDrawer.noTextExtracted')}</p>
        ) : (
          <details className={u.textBox}>
            <summary>{t('admin.uploads.uploadDrawer.showFullTextCharacters', { text_chars: p.text_chars.toLocaleString() })}{p.text_truncated ? t('admin.uploads.uploadDrawer.onlyTheBeginningIsShown') : ''}{t('admin.uploads.uploadDrawer.text3')}</summary>
            <pre className={u.text}>{p.text}</pre>
          </details>
        )}
      </DrawerSection>
    </>
  )
}

function PdfPreview({ uploadId, title }: { uploadId: string; title: string }) {
  const file = useUploadFile(uploadId, true)
  let body: ReactNode
  if (file.isPending) {
    body = <p className={u.note}>{t('admin.uploads.uploadDrawer.fetchingOriginalFile')}</p>
  } else if (file.isError) {
    const err = file.error
    if (err instanceof ApiError && err.status === 404 && err.code !== 'upload_report_missing') {
      body = (
        <>
          <p className={u.note} role="status">{t('admin.uploads.uploadDrawer.originalFilePreviewIsnT')}</p>
          {err.message && !/^HTTP \d+$/.test(err.message) && <p className={u.hint}>{err.message}</p>}
        </>
      )
    } else {
      body = <p className={c.error} role="alert">{t('admin.uploads.uploadDrawer.couldnTGetTheOriginal', { err: reviewErrorMessage(err) })}</p>
    }
  } else {
    const { url, file_name } = file.data
    body = (
      <>
        <div className={u.pdfBar}>
          <a className={c.link} href={url} target="_blank" rel="noopener noreferrer">{t('admin.uploads.uploadDrawer.openInNewTab')}</a>
          <a className={c.link} href={url} download={file_name}>{t('admin.uploads.uploadDrawer.download')}</a>
        </div>
        <div className={u.pdfStage}>
          {/* key 綁網址：presign 換新時邊界的 failed 狀態不沾黏（同 PdfPane 的理由）。 */}
          <ViewerBoundary key={url} fallback={<iframe className={u.pdfFrame} src={url} title={title} />}>
            <Suspense fallback={<p className={u.note} role="status">{t('admin.uploads.uploadDrawer.startingPdfEngine')}</p>}>
              <PdfViewer url={url} title={title} />
            </Suspense>
          </ViewerBoundary>
        </div>
      </>
    )
  }
  return <DrawerSection title={t('admin.uploads.uploadDrawer.pdfPreview')}>{body}</DrawerSection>
}

// ── 抽屜 ────────────────────────────────────────────────────────────────

interface Props {
  /** URL 上的 `?upload=`；null＝關著。 */
  uploadId: string | null
  onClose: () => void
  review: UploadReview
  /** 現在時間（毫秒）：算退回寬限期還剩多久。 */
  now: number
}

/** 一筆上傳的詳情抽屜：檔案、掃描、處理、決策、內容預覽與原檔，底部是審核動作。 */
export function UploadDrawer({ uploadId, onClose, review, now }: Props) {
  if (!uploadId) return null
  return <Detail key={uploadId} uploadId={uploadId} onClose={onClose} review={review} now={now} />
}

function Detail({ uploadId, onClose, review, now }: Props & { uploadId: string }) {
  const detail = useUpload(uploadId)
  if (detail.isPending) {
    return (
      <Drawer open onClose={onClose} title={t('admin.uploads.uploadDrawer.uploadDetails')} width={WIDTH}>
        <DrawerSection><p className={u.note}>{t('admin.uploads.uploadDrawer.loading')}</p></DrawerSection>
      </Drawer>
    )
  }
  if (detail.isError) {
    const gone = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <Drawer open onClose={onClose} title={t('admin.uploads.uploadDrawer.uploadDetails')} width={WIDTH}>
        <DrawerSection>
          <p className={c.error} role="alert">
            {gone ? t('admin.uploads.uploadDrawer.uploadRecordNotFoundIt') : t('admin.uploads.uploadDrawer.couldnTLoad', { error: reviewErrorMessage(detail.error) })}
          </p>
        </DrawerSection>
      </Drawer>
    )
  }
  const upload = detail.data
  const { state } = upload
  const title = upload.report?.title?.trim()
  const opts = reviewOptions(upload, now)
  const busy = review.busy

  const buttons: ReactNode[] = []
  if (opts.reject) {
    buttons.push(
      <Button key="reject" variant="dangerText" disabled={busy || opts.reject === 'busy'}
        title={opts.reject === 'busy' ? BUSY_HINT : undefined} onClick={() => review.request('reject', upload)}>{t('admin.uploads.uploadDrawer.reject')}</Button>,
    )
  }
  if (opts.retry) {
    buttons.push(
      <Button key="retry" disabled={busy || opts.retry !== 'ok'}
        title={opts.retry !== 'ok' ? notRetryableHint(upload.failure_kind) : undefined}
        onClick={() => review.request('retry', upload)}>{t('admin.uploads.uploadDrawer.retry')}</Button>,
    )
  }
  if (opts.unreject && opts.unreject !== 'purged') {
    buttons.push(
      <Button key="unreject" disabled={busy || opts.unreject !== 'ok'}
        title={opts.unreject !== 'ok' ? EXPIRED_HINT : undefined}
        onClick={() => review.request('unreject', upload)}>{t('admin.uploads.uploadDrawer.undoRejection')}</Button>,
    )
  }
  if (state === 'published') buttons.push(<Link key="reports" to="/admin/reports" className={c.btn}>{t('admin.uploads.uploadDrawer.goToReports')}</Link>)
  if (opts.publish) {
    buttons.push(
      <Button key="publish" variant="primary" disabled={busy} onClick={() => review.request('publish', upload)}>{t('admin.uploads.uploadDrawer.publish')}</Button>,
    )
  }
  const footer = buttons.length > 0 || opts.note ? (
    <div className={u.foot} role="group" aria-label={t('admin.uploads.uploadDrawer.reviewActions')}>
      {opts.note && <DrawerFootNote>{opts.note}</DrawerFootNote>}
      {buttons}
    </div>
  ) : undefined

  const sub = [title, isInFlight(state) ? t('admin.uploads.uploadDrawer.processingTheViewRefreshesEvery') : null].filter(Boolean).join('・')
  return (
    <Drawer open onClose={onClose} title={upload.original_name} monoTitle width={WIDTH}
      status={<Status tone={stateTone(state)}>{stateLabel(state)}</Status>} subtitle={sub || undefined} footer={footer}>
      <FileInfo upload={upload} />
      <ScanInfo upload={upload} />
      <ProcessInfo upload={upload} />
      <Decision upload={upload} />
      {PREVIEWABLE.has(state) ? (
        <>
          <ContentPreview uploadId={uploadId} />
          <PdfPreview uploadId={uploadId} title={title || upload.original_name} />
        </>
      ) : (
        <DrawerSection>
          <p className={u.note}>{t('admin.uploads.uploadDrawer.contentAndOriginalFilePreviews')}</p>
        </DrawerSection>
      )}
    </Drawer>
  )
}
