import { useState, type FormEvent, type MouseEvent } from 'react'
import { Link } from 'react-router'
import {
  Button, Card, ConfirmDialog, ConsolePage, PageHeader, RowActions, SearchInput, Segmented, StatTile, Status, toast,
  useQueryParam, type SegOption,
} from '../../components/console'
import c from '../../components/console/Console.module.css'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { displayTitle } from '../../lib/displayTitle'
import { adminCsvUrls, type AdminReportItem } from '../../lib/generated/adminApi'
import { BulkResultPanel, SelectAllBox } from './BulkParts'
import { ExportCsvButton } from './ExportCsvButton'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import { BulkHideDialog, HideDialog } from './reports/ReportDialogs'
import { ReportDrawer } from './reports/ReportDrawer'
import { DRAFT_REVIEW_PATH, STATUS_LABEL, STATUS_TONE, messageOf, short, statusOf } from './reports/reportStatus'
import s from './reports/Reports.module.css'
import {
  REPORTS_PAGE_SIZE, statusQuery, useAdminReports, useBulkReportVisibility, useReportCounts, useReportVisibility,
  type StatusFilter,
} from './useAdminReports'
import { namesPreview, useSelection, type BulkOutcome } from './useSelection'

const STATUS_OPTIONS: readonly SegOption<StatusFilter>[] = [
  { value: 'all', label: '全部' },
  { value: 'published', label: '已發布' },
  { value: 'draft', label: '草稿' },
  { value: 'hidden', label: '已隱藏' },
]

const stop = (e: MouseEvent) => e.stopPropagation()
const fmtCount = (n: number | undefined) => (n == null ? '—' : n.toLocaleString())

function AdminReports() {
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<StatusFilter>('all')
  const [offset, setOffset] = useState(0)
  const [openHash, setOpenHash] = useQueryParam('report')
  const [hideFor, setHideFor] = useState<AdminReportItem | null>(null)
  const [restoreFor, setRestoreFor] = useState<AdminReportItem | null>(null)
  const reports = useAdminReports({ q, status, offset })
  const counts = useReportCounts()
  const visibility = useReportVisibility()
  const bulk = useBulkReportVisibility()
  const sel = useSelection()
  const [bulkHideFor, setBulkHideFor] = useState<AdminReportItem[] | null>(null)
  const [bulkRestoreFor, setBulkRestoreFor] = useState<AdminReportItem[] | null>(null)
  const [outcome, setOutcome] = useState<BulkOutcome | null>(null)

  // 勾選只作用在目前這一頁：換頁、改篩選就清掉。草稿不能隱藏也不能恢復，不給勾。
  const items = reports.data?.items ?? []
  const selectable = items.filter(r => statusOf(r) !== 'draft').map(r => r.file_hash)
  const chosen = items.filter(r => sel.selected.has(r.file_hash) && statusOf(r) !== 'draft')
  const toHide = chosen.filter(r => !r.hidden)
  const toRestore = chosen.filter(r => r.hidden)
  const filtered = q !== '' || status !== 'all'

  const search = (e: FormEvent) => { e.preventDefault(); setQ(draft); setOffset(0); sel.clear() }
  const goTo = (next: number) => { setOffset(next); sel.clear() }
  /** 隱藏或恢復之後：那一篇可能已經不在篩選結果裡，抽屜開著它就一併關掉。 */
  const closeDrawerFor = (r: AdminReportItem) => { if (openHash === r.file_hash) setOpenHash(null) }

  /** 送出批次並把逐筆結果整理成給人看的清單；錯誤（含 422）往上拋給對話框或 toast 顯示。 */
  const runBulk = async (targets: AdminReportItem[], hide: boolean, reason?: string) => {
    const names = new Map(targets.map(r => [r.file_hash, displayTitle(r)]))
    const res = await bulk.mutateAsync({ fileHashes: targets.map(r => r.file_hash), hidden: hide, reason })
    sel.clear()
    const title = hide ? '批次隱藏' : '批次恢復'
    const skipped = res.results.filter(r => r.status === 'skipped').map(r => ({
      name: names.get(r.file_hash) ?? r.file_hash.slice(0, 12), detail: r.detail ?? r.code ?? '略過',
    }))
    toast(`${title}：成功 ${res.ok} 筆、略過 ${skipped.length} 筆`)
    // 有被略過的才留下逐筆原因（toast 2.6 秒就消失，原因要看得完）。
    setOutcome(skipped.length > 0 ? { title, ok: res.ok, skipped } : null)
  }
  const restore = (r: AdminReportItem) => {
    setRestoreFor(null)
    visibility.mutate({ fileHash: r.file_hash, hidden: false }, {
      onSuccess: () => { closeDrawerFor(r); toast(`已恢復「${short(displayTitle(r))}」`) },
      onError: err => toast(`恢復失敗：${messageOf(err)}`, { tone: 'bad' }),
    })
  }

  return (
    <ConsolePage>
      <PageHeader
        summary="查研報、隱藏不該出現的研報或恢復；可勾選多筆一次處理。"
        help={<>
          隱藏必須填原因，每一筆操作都會留在「操作紀錄」（不含原因全文）。被隱藏或尚未發布的研報從檢索、問答、閱讀頁、雷達、總覽、
          簡報與原檔全部排除，批次照常處理、恢復即生效。隱藏以檔案內容（file_hash）為鍵，重新入庫也不會失效。
          草稿是上傳後還沒發布的研報，要到「上傳研報」發布或退回。
        </>}
      />
      <div className={c.tiles} role="group" aria-label="研報數量">
        <StatTile label="語料" value={fmtCount(counts.data?.total)} unit="篇" />
        <StatTile label="已隱藏" value={fmtCount(counts.data?.hidden)} />
        <StatTile label="上傳草稿" value={fmtCount(counts.data?.draft)} />
      </div>
      <div className={c.toolbar}>
        <form className={s.search} onSubmit={search} role="search">
          <SearchInput value={draft} onChange={v => setDraft(v.slice(0, 200))} placeholder="標題／檔名／券商關鍵字" width={260} />
          <Button type="submit">搜尋</Button>
        </form>
        <Segmented label="狀態" options={STATUS_OPTIONS} value={status} onChange={v => { setStatus(v); goTo(0) }} />
        <div className={c.toolbarSpacer} />
        {chosen.length > 0 && (
          <div className={c.bulk} role="toolbar" aria-label="批次操作">
            <span className={s.bulkCount}>已選 {chosen.length} 篇</span>
            <Button size="sm" variant="dangerText" disabled={toHide.length === 0 || bulk.isPending}
              title={toHide.length === 0 ? '選取的研報都已隱藏' : undefined}
              onClick={() => setBulkHideFor(toHide)}>批次隱藏（{toHide.length}）</Button>
            <Button size="sm" disabled={toRestore.length === 0 || bulk.isPending}
              title={toRestore.length === 0 ? '選取的研報都沒有被隱藏' : undefined}
              onClick={() => setBulkRestoreFor(toRestore)}>批次恢復（{toRestore.length}）</Button>
            <Button size="sm" className={c.btnGhost} onClick={sel.clear}>清除</Button>
          </div>
        )}
      </div>
      <BulkResultPanel outcome={outcome} onClose={() => setOutcome(null)} />
      <Card title="研報清單" labelledBy="admin-reports-title"
        right={<ExportCsvButton href={adminCsvUrls.exportReports({ q: q || undefined, ...statusQuery(status) })} what="研報清單" />}>
        {reports.isPending ? (
          <p className={c.empty}>載入中…</p>
        ) : reports.isError ? (
          <div className={c.cardBody}><p className={c.error} role="alert">研報清單載入失敗：{messageOf(reports.error)}</p></div>
        ) : items.length === 0 ? (
          <p className={c.empty}>{filtered ? '沒有符合條件的研報' : '還沒有任何研報'}</p>
        ) : (
          <>
            <div className={c.tableWrap}>
              <table className={c.table}>
                <thead>
                  <tr>
                    <th className={c.thCheck}>
                      <SelectAllBox ids={selectable} selected={sel.selected} onChange={on => sel.setAll(selectable, on)} />
                    </th>
                    <th>研報</th><th>券商／市場</th><th>報告日／入庫</th><th>狀態</th><th className={c.tdRight}>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map(r => {
                    const st = statusOf(r)
                    const name = displayTitle(r)
                    const checked = st !== 'draft' && sel.selected.has(r.file_hash)
                    return (
                      <tr key={r.file_hash} className={`${c.rowClickable} ${checked ? c.rowSelected : ''}`}
                        onClick={() => setOpenHash(r.file_hash)}>
                        <td className={c.tdCheck} onClick={stop}>
                          <input type="checkbox" className={c.check} aria-label={`選取「${name}」`}
                            checked={checked} disabled={st === 'draft'}
                            title={st === 'draft' ? '草稿不能隱藏或恢復，到上傳審核發布或退回' : undefined}
                            onChange={() => sel.toggle(r.file_hash)} />
                        </td>
                        <td className={s.titleCell}>
                          <button type="button" className={c.rowLink} aria-haspopup="dialog"
                            onClick={e => { e.stopPropagation(); setOpenHash(r.file_hash) }}>{name}</button>
                          {r.title && <div className={c.sub}>{r.file_name}</div>}
                        </td>
                        <td>{r.source || '—'}<div className={c.sub}>{r.market || '—'}</div></td>
                        <td className={c.num}>
                          <div title="報告日">{r.report_date ? r.report_date.slice(0, 10) : '—'}</div>
                          <div className={c.sub} title="入庫">{fmtDateTime(r.created_at)}</div>
                        </td>
                        <td className={s.statusCell}>
                          <Status tone={STATUS_TONE[st]}>{STATUS_LABEL[st]}</Status>
                          {st === 'hidden' && r.hidden_reason && <div className={s.reason}>{r.hidden_reason}</div>}
                          {r.visibility_updated_at && (
                            <div className={c.sub}>{r.visibility_updated_by ?? '—'}・{fmtDateTime(r.visibility_updated_at)}</div>
                          )}
                        </td>
                        <td className={c.tdActions}>
                          {st === 'draft' ? (
                            <Link to={DRAFT_REVIEW_PATH} className={`${c.btn} ${c.btnSm}`} title="到上傳審核發布或退回"
                              onClick={stop}>前往審核</Link>
                          ) : st === 'hidden' ? (
                            <RowActions primary={{ label: '恢復', onClick: () => setRestoreFor(r), disabled: visibility.isPending }} />
                          ) : (
                            <RowActions primary={{ label: '隱藏', onClick: () => setHideFor(r), disabled: visibility.isPending }} />
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <div className={c.pager}>
              <span>{`第 ${offset + 1}–${offset + items.length} 筆，共 ${reports.data.total} 筆`}</span>
              <div className={c.pagerBtns}>
                <Button size="sm" disabled={offset === 0}
                  onClick={() => goTo(Math.max(0, offset - REPORTS_PAGE_SIZE))}>上一頁</Button>
                <Button size="sm" disabled={reports.data.next_offset == null}
                  onClick={() => { if (reports.data.next_offset != null) goTo(reports.data.next_offset) }}>下一頁</Button>
              </div>
            </div>
          </>
        )}
      </Card>
      <ReportDrawer
        fileHash={openHash}
        report={items.find(r => r.file_hash === openHash)}
        loading={reports.isPending}
        busy={visibility.isPending}
        onClose={() => setOpenHash(null)}
        onHide={setHideFor}
        onRestore={setRestoreFor}
      />
      <HideDialog report={hideFor} onClose={() => setHideFor(null)}
        onDone={r => { closeDrawerFor(r); toast(`已隱藏「${short(displayTitle(r))}」`) }} />
      <ConfirmDialog
        open={restoreFor != null}
        tone="safe"
        title={restoreFor ? `恢復「${displayTitle(restoreFor)}」？` : ''}
        body="恢復後立即重新出現在檢索、問答與閱讀頁。"
        confirmLabel="恢復"
        onConfirm={() => { if (restoreFor) restore(restoreFor) }}
        onCancel={() => setRestoreFor(null)}
      />
      <BulkHideDialog targets={bulkHideFor} onClose={() => setBulkHideFor(null)}
        onSubmit={reason => runBulk(bulkHideFor ?? [], true, reason)} />
      <ConfirmDialog
        open={bulkRestoreFor != null}
        tone="safe"
        title={`恢復 ${bulkRestoreFor?.length ?? 0} 篇研報？`}
        body={bulkRestoreFor
          ? `${namesPreview(bulkRestoreFor.map(r => displayTitle(r)))}。恢復後立即重新出現在檢索與問答；每篇各留一筆操作紀錄。`
          : ''}
        confirmLabel={`恢復 ${bulkRestoreFor?.length ?? 0} 篇`}
        onConfirm={() => {
          const targets = bulkRestoreFor ?? []
          setBulkRestoreFor(null)
          runBulk(targets, false).catch(err => toast(`批次恢復失敗：${messageOf(err)}`, { tone: 'bad' }))
        }}
        onCancel={() => setBulkRestoreFor(null)}
      />
    </ConsolePage>
  )
}

export default function AdminReportsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="reports.manage" title="研報管理"><AdminReports /></RequireScope>
    </RequireAdmin>
  )
}
