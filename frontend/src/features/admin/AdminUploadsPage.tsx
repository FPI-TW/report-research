import { useEffect, useRef } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Card, ConsolePage, PageHeader, RowActions, StatTile, Status, Tabs, useQueryParam, type SegOption,
} from '../../components/console'
import c from '../../components/console/Console.module.css'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import type { AdminUpload, AdminUploadScanner } from '../../lib/generated/adminApi'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import {
  UPLOAD_TABS, failureLabel, fmtBytes, fmtDuration, stateLabel, tabOf, type UploadTab, type UploadTabKey,
} from './uploadLabels'
import { UploadDrawer } from './uploads/UploadDrawer'
import { UploadIntake } from './uploads/UploadIntake'
import { EXPIRED_HINT, notRetryableHint, reviewOptions, stateTone } from './uploads/reviewRules'
import { useNow } from './uploads/useNow'
import { useUploadReview, type UploadReview } from './uploads/useUploadReview'
import { TAB_LIMIT, UPLOADS_KEY, useUploadTab, useUploadTabCounts } from './useAdminUploads'
import u from './Uploads.module.css'
import { t } from '@/i18n'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.adminUploadsPage.couldnTLoadTryAgain')
}

// ── 掃描器 ──────────────────────────────────────────────────────────────

const oldestWait = (scanner: AdminUploadScanner) =>
  (scanner.oldest_pending_seconds != null ? fmtDuration(scanner.oldest_pending_seconds) : null)

/** 掃描服務出錯時的橫幅：檔案留在隔離區，不會放行未掃描的檔案。沒出錯就不佔位置（數字在「掃描佇列」那一格）。 */
function ScannerAlert({ scanner }: { scanner: AdminUploadScanner | undefined }) {
  if (!scanner?.last_error) return null
  const oldest = oldestWait(scanner)
  return (
    <div className={c.banner} data-tone="bad" role="alert" aria-label={t('admin.adminUploadsPage.scannerStatus')}>
      <div className={u.bannerBody}>
        <strong>{t('admin.adminUploadsPage.scanningIsPausedFilesStay')}</strong>
        {t('admin.adminUploadsPage.waitingForScan', { pending: scanner.pending })}{scanner.scanning ? t('admin.adminUploadsPage.scanning', { scanning: scanner.scanning }) : ''}{oldest ? t('admin.adminUploadsPage.theOldestHasWaited', { oldest }) : ''}{t('admin.adminUploadsPage.text')}
        <span className={u.bannerDetail}>
          {t('admin.adminUploadsPage.lastError', { last_error: scanner.last_error })}{scanner.last_error_at ? t('admin.adminUploadsPage.text2', { last_error_at: fmtDateTime(scanner.last_error_at) }) : ''}
        </span>
      </div>
    </div>
  )
}

/** 「掃描佇列」那一格：待掃描件數，補充掃描中件數與最舊的等待時間。 */
function ScannerTile({ scanner }: { scanner: AdminUploadScanner | undefined }) {
  const oldest = scanner ? oldestWait(scanner) : null
  const sub = !scanner ? null
    : [scanner.scanning ? t('admin.adminUploadsPage.scanning2', { scanning: scanner.scanning }) : null, oldest ? t('admin.adminUploadsPage.oldestHasWaited', { oldest }) : null]
      .filter(Boolean).join(t('admin.adminUploadsPage.text3')) || t('admin.adminUploadsPage.noFilesWaitingForScan')
  return (
    <StatTile role="status" name={t('admin.adminUploadsPage.scannerStatus')} label={t('admin.adminUploadsPage.scanQueue')} value={scanner ? scanner.pending : '—'}
      unit={scanner ? t('admin.adminUploadsPage.waitingForScan2') : undefined} tone={scanner?.last_error ? 'bad' : undefined} sub={sub} />
  )
}

// ── 清單 ────────────────────────────────────────────────────────────────

function StateCell({ item }: { item: AdminUpload }) {
  return (
    <>
      <Status tone={stateTone(item.state)}>{stateLabel(item.state)}</Status>
      {item.state === 'failed' && item.failure_kind && <div className={u.reason}>{failureLabel(item.failure_kind)}</div>}
      {item.state === 'infected' && item.scan_signature && <div className={`${u.reason} ${u.bad}`}>{item.scan_signature}</div>}
      {item.state === 'rejected' && item.decision_reason && <div className={u.reason}>{item.decision_reason}</div>}
      {item.state === 'quarantined' && item.scan_last_error && (
        <div className={u.reason}>{t('admin.adminUploadsPage.scanIncompleteAttempt')}{' '}{item.scan_attempts}{' '}{t('admin.adminUploadsPage.text4', { scan_last_error: item.scan_last_error })}</div>
      )}
    </>
  )
}

/** 列動作：草稿＝審核（開抽屜）＋更多（發布、退回）；失敗＝重試＋更多（退回）；已退回＝撤銷退回。其餘狀態沒有。 */
function UploadRowActions({ item, now, review, onOpen }: {
  item: AdminUpload; now: number; review: UploadReview; onOpen: (id: string) => void
}) {
  const opts = reviewOptions(item, now)
  const label = t('admin.adminUploadsPage.moreActionsFor', { original_name: item.original_name })
  const reject = opts.reject === 'ok'
    ? { label: t('admin.adminUploadsPage.reject'), danger: true, disabled: review.busy, onClick: () => review.request('reject', item) }
    : null
  if (item.state === 'draft') {
    return (
      <RowActions label={label}
        primary={{ label: t('admin.adminUploadsPage.review'), onClick: () => onOpen(item.upload_id) }}
        more={[{ label: t('admin.adminUploadsPage.publish'), disabled: review.busy, onClick: () => review.request('publish', item) }, reject]} />
    )
  }
  if (opts.retry) {
    return (
      <RowActions label={label}
        primary={{
          label: t('admin.adminUploadsPage.retry'), onClick: () => review.request('retry', item), disabled: review.busy || opts.retry !== 'ok',
          hint: opts.retry !== 'ok' ? notRetryableHint(item.failure_kind) : undefined,
        }}
        more={[reject]} />
    )
  }
  if (opts.unreject) {
    return (
      <RowActions label={label}
        primary={{
          label: t('admin.adminUploadsPage.undoRejection'), onClick: () => review.request('unreject', item), disabled: review.busy || opts.unreject !== 'ok',
          hint: opts.unreject === 'ok' ? t('admin.adminUploadsPage.leftInGracePeriod', { grace: String(opts.grace) }) : opts.unreject === 'purged' ? t('admin.adminUploadsPage.fileAlreadyPurgedCanT') : EXPIRED_HINT,
        }} />
    )
  }
  return null
}

const EMPTY_TEXT: Record<UploadTabKey, string> = {
  get processing() { return t('admin.adminUploadsPage.noUploadsInProgress') },
  get draft() { return t('admin.adminUploadsPage.noDraftsAwaitingReview') },
  get failed() { return t('admin.adminUploadsPage.noFailedUploads') },
  get blocked() { return t('admin.adminUploadsPage.noBlockedFiles') },
  get published() { return t('admin.adminUploadsPage.noUploadedReportsPublishedYet') },
  get rejected() { return t('admin.adminUploadsPage.noRejectedUploads') },
  get duplicate() { return t('admin.adminUploadsPage.noDuplicateUploads') },
}

/** 有列動作的分頁籤才畫「操作」欄。 */
const ACTION_TABS: readonly UploadTabKey[] = ['draft', 'failed', 'rejected']

function UploadList({ tab, now, review, onOpen }: {
  tab: UploadTab; now: number; review: UploadReview; onOpen: (id: string) => void
}) {
  const list = useUploadTab(tab)
  if (list.isPending) return <p className={c.empty}>{t('admin.adminUploadsPage.loading')}</p>
  if (list.isError) {
    return <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.adminUploadsPage.couldnTLoadUploads', { error: messageOf(list.error) })}</p></div>
  }
  const { items, total, truncated } = list.data
  if (items.length === 0) return <p className={c.empty}>{EMPTY_TEXT[tab.key]}</p>
  const hasActions = ACTION_TABS.includes(tab.key)
  return (
    <>
      <div className={`${c.tableWrap} ${u.listWrap}`}>
        <table className={`${c.table} ${u.listTable}`} aria-label={t('admin.adminUploadsPage.list', { label: tab.label })}>
          <thead>
            <tr>
              <th>{t('admin.adminUploadsPage.file')}</th><th>{t('admin.adminUploadsPage.status')}</th><th>{t('admin.adminUploadsPage.uploaded')}</th><th>{t('admin.adminUploadsPage.statusUpdated')}</th>
              {hasActions && <th className={`${c.tdRight} ${c.stickyRight}`}>{t('admin.adminUploadsPage.actions')}</th>}
            </tr>
          </thead>
          <tbody>
            {items.map(it => (
              <tr key={it.upload_id} className={c.rowClickable} onClick={() => onOpen(it.upload_id)}>
                <td data-label={t('admin.adminUploadsPage.file')} className={u.nameCell}>
                  <button type="button" className={c.rowLink} aria-haspopup="dialog"
                    onClick={e => { e.stopPropagation(); onOpen(it.upload_id) }}>
                    <span className={u.fileName}>{it.original_name}</span>
                  </button>
                  <div className={`${c.sub} ${c.num}`}>{fmtBytes(it.size_bytes)}</div>
                </td>
                <td data-label={t('admin.adminUploadsPage.status')} className={u.stateCell}><StateCell item={it} /></td>
                <td data-label={t('admin.adminUploadsPage.uploaded')}>
                  <div>{it.uploaded_by ?? (it.api_client ? t('admin.adminUploadsPage.api', { api_client: it.api_client }) : '—')}</div>
                  <div className={`${c.sub} ${c.num}`}>{fmtDateTime(it.uploaded_at)}</div>
                </td>
                <td data-label={t('admin.adminUploadsPage.statusUpdated')} className={c.num}>{fmtDateTime(it.state_changed_at)}</td>
                {hasActions && (
                  <td className={`${c.tdActions} ${c.stickyRight}`}>
                    <UploadRowActions item={it} now={now} review={review} onOpen={onOpen} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={c.cardFoot}>
        <span>
          {t('admin.adminUploadsPage.total', { total })}{truncated ? t('admin.adminUploadsPage.onlyTheLatestAreListed', { TAB_LIMIT }) : ''}{t('admin.adminUploadsPage.text')}
          {tab.key === 'processing' && t('admin.adminUploadsPage.theListRefreshesEvery5')}
        </span>
      </div>
    </>
  )
}

// ── 頁面 ────────────────────────────────────────────────────────────────

/** 處理中與待審草稿的清單一直掛著（筆數取自清單本身）；其餘分頁籤的筆數另外查。 */
const COUNTED_TABS = UPLOAD_TABS.filter(item => item.key !== 'processing' && item.key !== 'draft')

function AdminUploads() {
  const [tabParam, setTabParam] = useQueryParam('tab')
  const [openId, setOpenId] = useQueryParam('upload')
  const tab = tabOf(tabParam)
  const client = useQueryClient()
  const now = useNow()
  const review = useUploadReview(now)
  // 處理中與待審兩個分頁籤一直掛著：處理中提供掃描器摘要與輪詢，兩者的筆數顯示在分頁籤與上方的數字。
  const processing = useUploadTab(UPLOAD_TABS[0])
  const drafts = useUploadTab(UPLOAD_TABS[1])
  const others = useUploadTabCounts(COUNTED_TABS)

  // 處理中的項目離開了（掃完、入庫成草稿、失敗…）：其他分頁籤與導覽上的數字都過時了，一併重抓。
  const prevIds = useRef<Set<string> | null>(null)
  useEffect(() => {
    if (!processing.data) return
    const ids = new Set(processing.data.items.map(it => it.upload_id))
    const prev = prevIds.current
    prevIds.current = ids
    if (prev && [...prev].some(id => !ids.has(id))) {
      void client.invalidateQueries({
        queryKey: UPLOADS_KEY,
        predicate: q => q.queryKey[2] === 'count' || (q.queryKey[2] === 'list' && q.queryKey[3] !== 'processing'),
      })
    }
  }, [processing.data, client])

  const counts: Partial<Record<UploadTabKey, number>> = {
    ...others.data, processing: processing.data?.total, draft: drafts.data?.total,
  }
  const tabs: SegOption<UploadTabKey>[] = UPLOAD_TABS.map(item => ({ value: item.key, label: item.label, count: counts[item.key] }))
  const scanner = processing.data?.scanner
  return (
    <ConsolePage>
      <PageHeader
        summary={t('admin.adminUploadsPage.pdfsBecomeDraftsAfterVirus')}
        help={<>
          {t('admin.adminUploadsPage.selectSeveralFilesToUpload')}
        </>}
      />
      <ScannerAlert scanner={scanner} />
      <div className={c.tiles} role="group" aria-label={t('admin.adminUploadsPage.uploadSummary')}>
        <StatTile label={t('admin.adminUploadsPage.draftsAwaitingReview')} value={drafts.data?.total ?? '—'} />
        <StatTile label={t('admin.adminUploadsPage.inProgress')} value={processing.data?.total ?? '—'} />
        <ScannerTile scanner={scanner} />
      </div>
      <UploadIntake onOpen={setOpenId} />
      <Tabs label={t('admin.adminUploadsPage.filterByStatus')} tabs={tabs} value={tab.key} onChange={k => setTabParam(k === 'processing' ? null : k)} />
      <div role="tabpanel" aria-label={tab.label}>
        <Card><UploadList key={tab.key} tab={tab} now={now} review={review} onOpen={setOpenId} /></Card>
      </div>
      <UploadDrawer uploadId={openId} onClose={() => setOpenId(null)} review={review} now={now} />
      {review.dialogs}
    </ConsolePage>
  )
}

export default function AdminUploadsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="reports.manage" title={t('admin.adminUploadsPage.reports')}><AdminUploads /></RequireScope>
    </RequireAdmin>
  )
}
