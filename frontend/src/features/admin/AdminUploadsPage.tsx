import { useEffect, useRef, useState, type DragEvent } from 'react'
import { Link, useSearchParams } from 'react-router'
import { useQueryClient } from '@tanstack/react-query'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import type { AdminUpload, AdminUploadScanner } from '../../lib/generated/adminApi'
import { AdminHeader } from './AdminHeader'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import {
  UPLOAD_TABS, failureLabel, fmtBytes, fmtDuration, stateLabel, tabOf, type UploadTab, type UploadTabKey,
} from './uploadLabels'
import {
  TAB_LIMIT, UPLOADS_KEY, useUploadQueue, useUploadTab, type QueueItem, type QueueStatus,
} from './useAdminUploads'
import styles from './Admin.module.css'
import u from './Uploads.module.css'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

// ── 拖放區與上傳佇列 ─────────────────────────────────────────────────────

function Dropzone({ maxBytes, onFiles }: { maxBytes: number; onFiles: (files: File[]) => void }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [over, setOver] = useState(false)
  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    setOver(false)
    const files = Array.from(e.dataTransfer?.files ?? [])
    if (files.length) onFiles(files)
  }
  return (
    <div
      className={`${u.dropzone} ${over ? u.dropzoneOver : ''}`}
      data-testid="upload-dropzone"
      onDragOver={e => { e.preventDefault(); setOver(true) }}
      onDragEnter={e => { e.preventDefault(); setOver(true) }}
      onDragLeave={() => setOver(false)}
      onDrop={onDrop}
    >
      <p className={u.dropTitle}>把 PDF 拖到這裡，或</p>
      <button type="button" className={styles.primary} onClick={() => inputRef.current?.click()}>選擇檔案</button>
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,application/pdf"
        multiple
        hidden
        aria-label="選擇要上傳的 PDF"
        onChange={e => {
          const files = Array.from(e.target.files ?? [])
          e.target.value = '' // 同一份檔案再選一次也要觸發 change
          if (files.length) onFiles(files)
        }}
      />
      <p className={styles.hint}>
        可一次選多份，會逐一上傳；只收 PDF、單檔上限 {fmtBytes(maxBytes)}。檔案先進隔離區掃毒，處理完成後成為草稿，
        發布前一般使用者看不到。
      </p>
    </div>
  )
}

const QUEUE_LABEL: Record<QueueStatus, string> = {
  queued: '排隊中',
  uploading: '上傳中',
  done: '已上傳',
  error: '上傳失敗',
  skipped: '未上傳',
  cancelled: '已取消',
}

function QueueRow({ item, onCancel }: { item: QueueItem; onCancel: (id: string) => void }) {
  const pct = Math.round(item.progress * 100)
  const bad = item.status === 'error' || item.status === 'skipped'
  return (
    <li className={u.queueItem} aria-label={item.name}>
      <div className={u.queueHead}>
        <span className={u.queueName}>{item.name}</span>
        <span className={styles.muted}>{fmtBytes(item.size)}</span>
        <span className={`${styles.badge} ${bad ? styles.badgeOff : ''}`}>{QUEUE_LABEL[item.status]}</span>
        {(item.status === 'queued' || item.status === 'uploading') && (
          <button type="button" className={styles.action} onClick={() => onCancel(item.id)}>取消</button>
        )}
      </div>
      {item.status === 'uploading' && (
        <div className={u.progress} role="progressbar" aria-label={`${item.name} 上傳進度`}
          aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct}>
          <div className={u.progressBar} style={{ width: `${pct}%` }} />
          <span className={u.progressText}>{pct}%</span>
        </div>
      )}
      {item.message && (
        <p className={bad ? u.queueError : u.queueNote} role={bad ? 'alert' : undefined}>
          {item.message}
          {item.uploadId && <> <Link to={`/admin/uploads/${item.uploadId}`}>查看這筆上傳</Link></>}
        </p>
      )}
    </li>
  )
}

function UploadPanel() {
  const client = useQueryClient()
  const queue = useUploadQueue({
    onUploaded: () => { void client.invalidateQueries({ queryKey: UPLOADS_KEY }) },
  })
  const { busy } = queue
  // 還有檔案在傳時關分頁要先問：離開這一頁會中止上傳（佇列只活在這一頁）。
  useEffect(() => {
    if (!busy) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault() }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [busy])
  const finished = queue.items.some(it => it.status !== 'queued' && it.status !== 'uploading')
  return (
    <section className={styles.card} aria-labelledby="upload-panel-title">
      <h2 id="upload-panel-title" className={styles.ctitle}>上傳研報</h2>
      <Dropzone maxBytes={queue.maxBytes} onFiles={queue.addFiles} />
      {queue.items.length > 0 && (
        <>
          <ul className={u.queue} aria-label="上傳佇列">
            {queue.items.map(it => <QueueRow key={it.id} item={it} onCancel={queue.cancel} />)}
          </ul>
          <div className={u.queueFoot}>
            {busy && <span className={styles.muted}>上傳中請留在這一頁，離開會中止尚未完成的上傳。</span>}
            {finished && <button type="button" className={styles.action} onClick={queue.clearFinished}>清除已結束的項目</button>}
          </div>
        </>
      )}
    </section>
  )
}

// ── 掃描器狀態橫幅 ──────────────────────────────────────────────────────

function ScannerBanner({ scanner }: { scanner: AdminUploadScanner | undefined }) {
  if (!scanner) return null
  const waiting = scanner.pending + scanner.scanning
  const oldest = scanner.oldest_pending_seconds != null ? `，最舊的已等待 ${fmtDuration(scanner.oldest_pending_seconds)}` : ''
  if (scanner.last_error) {
    return (
      <section className={`${u.banner} ${u.bannerError}`} role="alert" aria-label="掃描器狀態">
        <strong>掃描服務暫停，檔案保留於隔離區。</strong>
        <span>待掃描 {scanner.pending} 件{scanner.scanning ? `（掃描中 ${scanner.scanning} 件）` : ''}{oldest}。</span>
        <span className={u.bannerDetail}>
          最後錯誤：{scanner.last_error}{scanner.last_error_at ? `（${fmtDateTime(scanner.last_error_at)}）` : ''}
        </span>
      </section>
    )
  }
  return (
    <section className={u.banner} role="status" aria-label="掃描器狀態">
      {waiting > 0
        ? <span>掃描器運作中：待掃描 {scanner.pending} 件{scanner.scanning ? `、掃描中 ${scanner.scanning} 件` : ''}{oldest}。</span>
        : <span>掃描器：目前沒有等待掃描的檔案。</span>}
    </section>
  )
}

// ── 清單 ────────────────────────────────────────────────────────────────

function StateCell({ item }: { item: AdminUpload }) {
  const off = item.state === 'failed' || item.state === 'infected' || item.state === 'blocked' || item.state === 'rejected'
  return (
    <>
      <span className={`${styles.badge} ${off ? styles.badgeOff : ''}`}>{stateLabel(item.state)}</span>
      {item.state === 'failed' && item.failure_kind && <div className={styles.reason}>{failureLabel(item.failure_kind)}</div>}
      {item.state === 'infected' && item.scan_signature && <div className={u.danger}>{item.scan_signature}</div>}
      {item.state === 'rejected' && item.decision_reason && <div className={styles.reason}>{item.decision_reason}</div>}
      {item.state === 'quarantined' && item.scan_last_error && (
        <div className={styles.reason}>掃描未完成（第 {item.scan_attempts} 次）：{item.scan_last_error}</div>
      )}
    </>
  )
}

const EMPTY_TEXT: Record<UploadTabKey, string> = {
  processing: '沒有處理中的上傳',
  draft: '沒有待審的草稿',
  failed: '沒有處理失敗的上傳',
  blocked: '沒有被攔截的檔案',
  published: '還沒有發布過上傳的研報',
  rejected: '沒有退回的上傳',
  duplicate: '沒有重複的上傳',
}

function UploadList({ tab }: { tab: UploadTab }) {
  const list = useUploadTab(tab)
  if (list.isPending) return <p className={styles.idle}>載入中…</p>
  if (list.isError) return <p className={styles.error} role="alert">上傳清單載入失敗：{messageOf(list.error)}</p>
  const { items, total, truncated } = list.data
  if (items.length === 0) return <p className={styles.idle}>{EMPTY_TEXT[tab.key]}</p>
  return (
    <>
      <div className={u.listWrap}>
        <table className={`${styles.table} ${u.listTable}`} aria-label={`${tab.label}清單`}>
          <thead>
            <tr><th>檔案</th><th>狀態</th><th>上傳</th><th>狀態更新</th></tr>
          </thead>
          <tbody>
            {items.map(it => (
              <tr key={it.upload_id}>
                <td data-label="檔案" className={u.nameCell}>
                  <Link to={`/admin/uploads/${it.upload_id}`}>{it.original_name}</Link>
                  <div className={styles.muted}>{fmtBytes(it.size_bytes)}</div>
                </td>
                <td data-label="狀態" className={u.wrap}><StateCell item={it} /></td>
                <td data-label="上傳" className={styles.num}>
                  <div>{it.uploaded_by ?? '—'}</div>
                  <div className={styles.muted}>{fmtDateTime(it.uploaded_at)}</div>
                </td>
                <td data-label="狀態更新" className={styles.num}>{fmtDateTime(it.state_changed_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className={styles.hint}>
        共 {total} 筆{truncated ? `；每種狀態只列最新的 ${TAB_LIMIT} 筆` : ''}。
        {tab.key === 'processing' && '清單每 5 秒自動更新，直到沒有處理中的項目。'}
      </p>
    </>
  )
}

function Tabs({ active, counts, onSelect }: {
  active: UploadTabKey; counts: Partial<Record<UploadTabKey, number>>; onSelect: (k: UploadTabKey) => void
}) {
  return (
    <div className={u.tabs} role="tablist" aria-label="依狀態篩選">
      {UPLOAD_TABS.map(t => {
        const n = counts[t.key]
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            id={`upload-tab-${t.key}`}
            aria-selected={active === t.key}
            aria-controls="upload-tabpanel"
            className={`${u.tab} ${active === t.key ? u.tabOn : ''}`}
            onClick={() => onSelect(t.key)}
          >
            {t.label}{n ? <span className={u.tabCount}>{n}</span> : null}
          </button>
        )
      })}
    </div>
  )
}

function AdminUploads() {
  const [params, setParams] = useSearchParams()
  const tab = tabOf(params.get('tab'))
  const client = useQueryClient()
  // 處理中與待審兩個分頁籤一直掛著：處理中提供掃描器摘要與輪詢，兩者的筆數顯示在分頁籤上。
  const processing = useUploadTab(UPLOAD_TABS[0])
  const drafts = useUploadTab(UPLOAD_TABS[1])

  // 處理中的項目離開了（掃完、入庫成草稿、失敗…）：其他分頁籤的資料過時了，一併重抓。
  const prevIds = useRef<Set<string> | null>(null)
  useEffect(() => {
    if (!processing.data) return
    const ids = new Set(processing.data.items.map(it => it.upload_id))
    const prev = prevIds.current
    prevIds.current = ids
    if (prev && [...prev].some(id => !ids.has(id))) {
      void client.invalidateQueries({
        queryKey: [...UPLOADS_KEY, 'list'],
        predicate: q => q.queryKey[3] !== 'processing',
      })
    }
  }, [processing.data, client])

  const select = (k: UploadTabKey) => setParams(k === 'processing' ? {} : { tab: k }, { replace: true })
  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <AdminHeader
          title="上傳研報"
          subtitle="上傳 PDF 研報：掃毒、抽字與標註完成後成為草稿，審核後發布或退回；每一筆操作都會留在「操作紀錄」。"
        />
        <ScannerBanner scanner={processing.data?.scanner} />
        <UploadPanel />
        <section className={styles.card} aria-labelledby="upload-list-title">
          <h2 id="upload-list-title" className={styles.ctitle}>上傳紀錄</h2>
          <Tabs active={tab.key} counts={{ processing: processing.data?.total, draft: drafts.data?.total }} onSelect={select} />
          <div id="upload-tabpanel" role="tabpanel" aria-labelledby={`upload-tab-${tab.key}`} className={u.tabPanel}>
            <UploadList key={tab.key} tab={tab} />
          </div>
        </section>
      </div>
    </div>
  )
}

export default function AdminUploadsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="reports.manage" title="研報管理"><AdminUploads /></RequireScope>
    </RequireAdmin>
  )
}
