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

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
}

// ── 掃描器 ──────────────────────────────────────────────────────────────

const oldestWait = (scanner: AdminUploadScanner) =>
  (scanner.oldest_pending_seconds != null ? fmtDuration(scanner.oldest_pending_seconds) : null)

/** 掃描服務出錯時的橫幅：檔案留在隔離區，不會放行未掃描的檔案。沒出錯就不佔位置（數字在「掃描佇列」那一格）。 */
function ScannerAlert({ scanner }: { scanner: AdminUploadScanner | undefined }) {
  if (!scanner?.last_error) return null
  const oldest = oldestWait(scanner)
  return (
    <div className={c.banner} data-tone="bad" role="alert" aria-label="掃描器狀態">
      <div className={u.bannerBody}>
        <strong>掃描服務暫停，檔案保留於隔離區。</strong>
        待掃描 {scanner.pending} 件{scanner.scanning ? `（掃描中 ${scanner.scanning} 件）` : ''}{oldest ? `，最舊的已等待 ${oldest}` : ''}。
        <span className={u.bannerDetail}>
          最後錯誤：{scanner.last_error}{scanner.last_error_at ? `（${fmtDateTime(scanner.last_error_at)}）` : ''}
        </span>
      </div>
    </div>
  )
}

/** 「掃描佇列」那一格：待掃描件數，補充掃描中件數與最舊的等待時間。 */
function ScannerTile({ scanner }: { scanner: AdminUploadScanner | undefined }) {
  const oldest = scanner ? oldestWait(scanner) : null
  const sub = !scanner ? null
    : [scanner.scanning ? `掃描中 ${scanner.scanning} 件` : null, oldest ? `最舊的已等待 ${oldest}` : null]
      .filter(Boolean).join('，') || '目前沒有等待掃描的檔案'
  return (
    <StatTile role="status" name="掃描器狀態" label="掃描佇列" value={scanner ? scanner.pending : '—'}
      unit={scanner ? '件待掃描' : undefined} tone={scanner?.last_error ? 'bad' : undefined} sub={sub} />
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
        <div className={u.reason}>掃描未完成（第 {item.scan_attempts} 次）：{item.scan_last_error}</div>
      )}
    </>
  )
}

/** 列動作：草稿＝審核（開抽屜）＋更多（發布、退回）；失敗＝重試＋更多（退回）；已退回＝撤銷退回。其餘狀態沒有。 */
function UploadRowActions({ item, now, review, onOpen }: {
  item: AdminUpload; now: number; review: UploadReview; onOpen: (id: string) => void
}) {
  const opts = reviewOptions(item, now)
  const label = `${item.original_name} 的更多操作`
  const reject = opts.reject === 'ok'
    ? { label: '退回', danger: true, disabled: review.busy, onClick: () => review.request('reject', item) }
    : null
  if (item.state === 'draft') {
    return (
      <RowActions label={label}
        primary={{ label: '審核', onClick: () => onOpen(item.upload_id) }}
        more={[{ label: '發布', disabled: review.busy, onClick: () => review.request('publish', item) }, reject]} />
    )
  }
  if (opts.retry) {
    return (
      <RowActions label={label}
        primary={{
          label: '重試', onClick: () => review.request('retry', item), disabled: review.busy || opts.retry !== 'ok',
          hint: opts.retry !== 'ok' ? notRetryableHint(item.failure_kind) : undefined,
        }}
        more={[reject]} />
    )
  }
  if (opts.unreject) {
    return (
      <RowActions label={label}
        primary={{
          label: '撤銷退回', onClick: () => review.request('unreject', item), disabled: review.busy || opts.unreject !== 'ok',
          hint: opts.unreject === 'ok' ? `寬限期還剩 ${opts.grace}` : opts.unreject === 'purged' ? '檔案已清除，無法撤銷' : EXPIRED_HINT,
        }} />
    )
  }
  return null
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

/** 有列動作的分頁籤才畫「操作」欄。 */
const ACTION_TABS: readonly UploadTabKey[] = ['draft', 'failed', 'rejected']

function UploadList({ tab, now, review, onOpen }: {
  tab: UploadTab; now: number; review: UploadReview; onOpen: (id: string) => void
}) {
  const list = useUploadTab(tab)
  if (list.isPending) return <p className={c.empty}>載入中…</p>
  if (list.isError) {
    return <div className={c.cardBody}><p className={c.error} role="alert">上傳清單載入失敗：{messageOf(list.error)}</p></div>
  }
  const { items, total, truncated } = list.data
  if (items.length === 0) return <p className={c.empty}>{EMPTY_TEXT[tab.key]}</p>
  const hasActions = ACTION_TABS.includes(tab.key)
  return (
    <>
      <div className={`${c.tableWrap} ${u.listWrap}`}>
        <table className={`${c.table} ${u.listTable}`} aria-label={`${tab.label}清單`}>
          <thead>
            <tr>
              <th>檔案</th><th>狀態</th><th>上傳</th><th>狀態更新</th>
              {hasActions && <th className={c.tdRight}>操作</th>}
            </tr>
          </thead>
          <tbody>
            {items.map(it => (
              <tr key={it.upload_id} className={c.rowClickable} onClick={() => onOpen(it.upload_id)}>
                <td data-label="檔案" className={u.nameCell}>
                  <button type="button" className={u.rowLink} aria-haspopup="dialog"
                    onClick={e => { e.stopPropagation(); onOpen(it.upload_id) }}>{it.original_name}</button>
                  <div className={`${c.sub} ${c.num}`}>{fmtBytes(it.size_bytes)}</div>
                </td>
                <td data-label="狀態" className={u.stateCell}><StateCell item={it} /></td>
                <td data-label="上傳">
                  <div>{it.uploaded_by ?? '—'}</div>
                  <div className={`${c.sub} ${c.num}`}>{fmtDateTime(it.uploaded_at)}</div>
                </td>
                <td data-label="狀態更新" className={c.num}>{fmtDateTime(it.state_changed_at)}</td>
                {hasActions && (
                  <td className={c.tdActions}><UploadRowActions item={it} now={now} review={review} onOpen={onOpen} /></td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={c.cardFoot}>
        <span>
          共 {total} 筆{truncated ? `；每種狀態只列最新的 ${TAB_LIMIT} 筆` : ''}。
          {tab.key === 'processing' && '清單每 5 秒自動更新，直到沒有處理中的項目。'}
        </span>
      </div>
    </>
  )
}

// ── 頁面 ────────────────────────────────────────────────────────────────

/** 處理中與待審草稿的清單一直掛著（筆數取自清單本身）；其餘分頁籤的筆數另外查。 */
const COUNTED_TABS = UPLOAD_TABS.filter(t => t.key !== 'processing' && t.key !== 'draft')

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
  const tabs: SegOption<UploadTabKey>[] = UPLOAD_TABS.map(t => ({ value: t.key, label: t.label, count: counts[t.key] }))
  const scanner = processing.data?.scanner
  return (
    <ConsolePage>
      <PageHeader
        summary="PDF 經掃毒、抽字與標註後成為草稿，審核後發布或退回。"
        help={<>
          可一次選多份，會逐一上傳；只收 PDF。檔案先進隔離區掃毒，抽字與標註完成後成為草稿，發布前一般使用者看不到。
          退回後有寬限期可以撤銷，期滿由上傳 worker 清除。每一筆操作都會留在「操作紀錄」。
        </>}
      />
      <ScannerAlert scanner={scanner} />
      <div className={c.tiles} role="group" aria-label="上傳概況">
        <StatTile label="待審草稿" value={drafts.data?.total ?? '—'} />
        <StatTile label="處理中" value={processing.data?.total ?? '—'} />
        <ScannerTile scanner={scanner} />
      </div>
      <UploadIntake onOpen={setOpenId} />
      <Tabs label="依狀態篩選" tabs={tabs} value={tab.key} onChange={k => setTabParam(k === 'processing' ? null : k)} />
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
      <RequireScope scope="reports.manage" title="研報管理"><AdminUploads /></RequireScope>
    </RequireAdmin>
  )
}
