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
    <DrawerSection title="檔案資訊">
      <dl className={c.kv}>
        <Row label="大小">{fmtBytes(upload.size_bytes)}</Row>
        <Row label="上傳者">{dash(upload.uploaded_by)}</Row>
        <Row label="上傳時間">{fmtDateTime(upload.uploaded_at)}</Row>
        <Row label="檔案修改時間">{fmtDateTime(upload.client_mtime)}</Row>
        <Row label="狀態更新">{fmtDateTime(upload.state_changed_at)}</Row>
        <Row label="SHA-256"><span className={u.hash}>{upload.file_hash}</span></Row>
      </dl>
    </DrawerSection>
  )
}

function ScanInfo({ upload }: { upload: AdminUploadDetail }) {
  return (
    <DrawerSection title="掃描結果">
      <dl className={c.kv}>
        <Row label="引擎與病毒碼">{dash(upload.scan_engine)}</Row>
        <Row label="掃描時間">{fmtDateTime(upload.scanned_at)}</Row>
        <Row label="嘗試次數">{upload.scan_attempts}</Row>
        <Row label="病毒名">{upload.scan_signature ? <span className={u.bad}>{upload.scan_signature}</span> : '—'}</Row>
        {upload.scan_last_error && <Row label="最後錯誤"><span className={u.prose}>{upload.scan_last_error}</span></Row>}
      </dl>
      {upload.state === 'quarantined' && upload.scan_last_error && (
        <p className={u.hint}>掃描服務暫停或逾時時，檔案會保留在隔離區，恢復後自動重掃，不會放行未掃描的檔案。</p>
      )}
    </DrawerSection>
  )
}

function ProcessInfo({ upload }: { upload: AdminUploadDetail }) {
  const r = upload.report
  const flags: string[] = []
  if (r?.needs_review) flags.push('抽取品質需要複核')
  if (r?.pages_failed?.length) flags.push(`第 ${r.pages_failed.join('、')} 頁抽取失敗`)
  return (
    <DrawerSection title="處理結果">
      <dl className={c.kv}>
        <Row label="處理次數">{upload.process_attempts}</Row>
        <Row label="處理完成">{fmtDateTime(upload.processed_at)}</Row>
        {upload.failure_kind && (
          <Row label="失敗原因">
            <span className={u.prose}>
              {failureLabel(upload.failure_kind)}{isRetryableFailure(upload.failure_kind) && '（可重試）'}
            </span>
          </Row>
        )}
        {upload.failure_detail && <Row label="失敗細節"><span className={u.prose}>{upload.failure_detail}</span></Row>}
        {r && (
          <>
            <Row label="抽取器">{[r.extractor, r.extraction_version].filter(Boolean).join(' ') || '—'}</Row>
            <Row label="品質分數">{r.quality_score != null ? r.quality_score.toFixed(2) : '—'}</Row>
            <Row label="頁數">{dash(r.page_count)}</Row>
            <Row label="失敗頁">{r.pages_failed?.length ?? 0}</Row>
            <Row label="品質旗標">
              {flags.length ? <span className={`${u.prose} ${u.bad}`}>{flags.join('；')}</span> : <span className={u.prose}>無</span>}
            </Row>
            <Row label="入庫時間">{fmtDateTime(r.created_at)}</Row>
          </>
        )}
      </dl>
      {!r && !upload.failure_kind && <p className={u.hint}>還沒有入庫的研報（掃描與處理完成後才會有）。</p>}
    </DrawerSection>
  )
}

function Decision({ upload }: { upload: AdminUploadDetail }) {
  if (!upload.decided_at && !upload.decision_reason && !upload.purge_after && !upload.purged_at) return null
  return (
    <DrawerSection title="審核決策">
      <dl className={c.kv}>
        <Row label="決策者">{dash(upload.decided_by)}</Row>
        <Row label="決策時間">{fmtDateTime(upload.decided_at)}</Row>
        {upload.decision_reason && <Row label="退回原因"><span className={u.prose}>{upload.decision_reason}</span></Row>}
        {upload.purge_after && <Row label="預定清除">{fmtDateTime(upload.purge_after)}</Row>}
        {upload.purged_at && <Row label="已清除">{fmtDateTime(upload.purged_at)}</Row>}
      </dl>
    </DrawerSection>
  )
}

const PENDING = <span className={u.pending}>產生中</span>

function Tags({ tags }: { tags: AdminUploadPreview['tags'] }) {
  const targets = [...tags.stock_targets, ...tags.futures_targets]
  const company = [tags.company_name, tags.stock_code].filter(Boolean).join(' ')
  const chips = [
    tags.market ? `市場 ${tags.market}` : null,
    tags.source,
    tags.report_type,
    company || null,
    tags.report_date ? tags.report_date.slice(0, 10) : null,
  ].filter((x): x is string => Boolean(x))
  return (
    <>
      {chips.length > 0 && <div className={u.chips}>{chips.map(x => <Chip key={x}>{x}</Chip>)}</div>}
      <dl className={c.kv}>
        <Row label="研究報告"><span className={u.prose}>{tags.is_research ? '是' : '否'}</span></Row>
        <Row label="語言">{dash(tags.language)}</Row>
        <Row label="商品類別">{tags.instrument_types.length ? tags.instrument_types.join('、') : '—'}</Row>
        <Row label="標的">{targets.length ? targets.join('、') : '—'}</Row>
        <Row label="信心">{tags.confidence != null ? tags.confidence.toFixed(2) : '—'}</Row>
      </dl>
    </>
  )
}

function ContentPreview({ uploadId }: { uploadId: string }) {
  const preview = useUploadPreview(uploadId, true)
  if (preview.isPending) return <DrawerSection title="內容"><p className={u.note}>內容預覽載入中…</p></DrawerSection>
  if (preview.isError) {
    return (
      <DrawerSection title="內容">
        <p className={c.error} role="alert">內容預覽載入失敗：{reviewErrorMessage(preview.error)}</p>
      </DrawerSection>
    )
  }
  const p = preview.data
  return (
    <>
      <DrawerSection title="標籤"><Tags tags={p.tags} /></DrawerSection>
      <DrawerSection title="內容">
        <p className={u.contentTitle}>{p.title_state === 'pending' ? PENDING : dash(p.title)}</p>
        {p.title_original && p.title_original !== p.title && <p className={u.contentSub}>原始標題：{p.title_original}</p>}
        <p className={u.summaryText}>{p.summary_state === 'pending' ? PENDING : dash(p.summary)}</p>
      </DrawerSection>
      <DrawerSection title="摘錄">
        {p.takeaways_state === 'pending' ? (
          <p className={u.note}>{PENDING}</p>
        ) : p.takeaways.length === 0 ? (
          <p className={u.note}>沒有可展示的摘錄</p>
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
      </DrawerSection>
      <DrawerSection title="正典文字">
        {p.text_state === 'missing' || !p.text ? (
          <p className={u.note}>沒有抽取到文字</p>
        ) : (
          <details className={u.textBox}>
            <summary>展開全文（{p.text_chars.toLocaleString()} 字{p.text_truncated ? '，只顯示前段' : ''}）</summary>
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
    body = <p className={u.note}>取得原檔中…</p>
  } else if (file.isError) {
    const err = file.error
    if (err instanceof ApiError && err.status === 404 && err.code !== 'upload_report_missing') {
      body = (
        <>
          <p className={u.note} role="status">此環境無法預覽原檔</p>
          {err.message && !/^HTTP \d+$/.test(err.message) && <p className={u.hint}>{err.message}</p>}
        </>
      )
    } else {
      body = <p className={c.error} role="alert">原檔無法取得：{reviewErrorMessage(err)}</p>
    }
  } else {
    const { url, file_name } = file.data
    body = (
      <>
        <div className={u.pdfBar}>
          <a className={c.link} href={url} target="_blank" rel="noopener noreferrer">在新分頁開啟</a>
          <a className={c.link} href={url} download={file_name}>下載</a>
        </div>
        <div className={u.pdfStage}>
          {/* key 綁網址：presign 換新時邊界的 failed 狀態不沾黏（同 PdfPane 的理由）。 */}
          <ViewerBoundary key={url} fallback={<iframe className={u.pdfFrame} src={url} title={title} />}>
            <Suspense fallback={<p className={u.note} role="status">正在啟動 PDF 引擎…</p>}>
              <PdfViewer url={url} title={title} />
            </Suspense>
          </ViewerBoundary>
        </div>
      </>
    )
  }
  return <DrawerSection title="PDF 預覽">{body}</DrawerSection>
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
      <Drawer open onClose={onClose} title="上傳詳情" width={WIDTH}>
        <DrawerSection><p className={u.note}>載入中…</p></DrawerSection>
      </Drawer>
    )
  }
  if (detail.isError) {
    const gone = detail.error instanceof ApiError && detail.error.status === 404
    return (
      <Drawer open onClose={onClose} title="上傳詳情" width={WIDTH}>
        <DrawerSection>
          <p className={c.error} role="alert">
            {gone ? '找不到這筆上傳紀錄（可能已被清除）。' : `載入失敗：${reviewErrorMessage(detail.error)}`}
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
        title={opts.reject === 'busy' ? BUSY_HINT : undefined} onClick={() => review.request('reject', upload)}>退回</Button>,
    )
  }
  if (opts.retry) {
    buttons.push(
      <Button key="retry" disabled={busy || opts.retry !== 'ok'}
        title={opts.retry !== 'ok' ? notRetryableHint(upload.failure_kind) : undefined}
        onClick={() => review.request('retry', upload)}>重試</Button>,
    )
  }
  if (opts.unreject && opts.unreject !== 'purged') {
    buttons.push(
      <Button key="unreject" disabled={busy || opts.unreject !== 'ok'}
        title={opts.unreject !== 'ok' ? EXPIRED_HINT : undefined}
        onClick={() => review.request('unreject', upload)}>撤銷退回</Button>,
    )
  }
  if (state === 'published') buttons.push(<Link key="reports" to="/admin/reports" className={c.btn}>前往研報管理</Link>)
  if (opts.publish) {
    buttons.push(
      <Button key="publish" variant="primary" disabled={busy} onClick={() => review.request('publish', upload)}>發布</Button>,
    )
  }
  const footer = buttons.length > 0 || opts.note ? (
    <div className={u.foot} role="group" aria-label="審核動作">
      {opts.note && <DrawerFootNote>{opts.note}</DrawerFootNote>}
      {buttons}
    </div>
  ) : undefined

  const sub = [title, isInFlight(state) ? '處理中，畫面每 5 秒自動更新' : null].filter(Boolean).join('・')
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
          <p className={u.note}>內容與原檔預覽只在處理完成（草稿）或已發布時提供；隔離區與被攔截的檔案永遠不提供下載。</p>
        </DrawerSection>
      )}
    </Drawer>
  )
}
