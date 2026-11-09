import { useState, type FormEvent, type MouseEvent } from 'react'
import { Link } from 'react-router'
import {
  Button, Card, ConfirmDialog, ConsolePage, PageHeader, RowActions, SearchInput, Segmented, StatTile, Status, toast,
  useQueryParam, type SegOption,
} from '../../components/console'
import c from '../../components/console/Console.module.css'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { ApiError } from '../../lib/api'
import { displayTitle } from '../../lib/displayTitle'
import { adminCsvUrls, type AdminReportItem } from '../../lib/generated/adminApi'
import { BulkResultPanel, SelectAllBox } from './BulkParts'
import { ExportCsvButton } from './ExportCsvButton'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import { BulkHideDialog, HideDialog } from './reports/ReportDialogs'
import { ReportDrawer, type ReportMissing } from './reports/ReportDrawer'
import { DRAFT_REVIEW_PATH, STATUS_LABEL, STATUS_TONE, messageOf, short, statusOf } from './reports/reportStatus'
import s from './reports/Reports.module.css'
import {
  REPORTS_PAGE_SIZE, statusQuery, useAdminReport, useAdminReports, useBulkReportVisibility, useReportCounts,
  useReportVisibility, type StatusFilter,
} from './useAdminReports'
import { namesPreview, useSelection, type BulkOutcome } from './useSelection'
import { t } from '@/i18n'

const STATUS_OPTIONS: readonly SegOption<StatusFilter>[] = [
  { value: 'all', get label() { return t('admin.adminReportsPage.all') } },
  { value: 'published', get label() { return t('admin.adminReportsPage.published') } },
  { value: 'draft', get label() { return t('admin.adminReportsPage.draft') } },
  { value: 'hidden', get label() { return t('admin.adminReportsPage.hidden') } },
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
  // 抽屜要開的那一篇：這一頁有就用清單的；沒有（連結開在別的篩選或頁次）就向後端取那一篇。
  const onPage = items.find(r => r.file_hash === openHash)
  const single = useAdminReport(openHash, !reports.isPending && !onPage)
  const openReport = onPage ?? single.data
  const missing: ReportMissing = reports.isPending || (single.isPending && single.fetchStatus !== 'idle')
    ? { kind: 'loading' }
    : single.isError && !(single.error instanceof ApiError && single.error.status === 404)
      ? { kind: 'error', message: messageOf(single.error) }
      : { kind: 'not_found' }

  const search = (e: FormEvent) => { e.preventDefault(); setQ(draft); setOffset(0); sel.clear() }
  const goTo = (next: number) => { setOffset(next); sel.clear() }
  /** 隱藏或恢復之後：那一篇可能已經不在篩選結果裡，抽屜開著它就一併關掉。 */
  const closeDrawerFor = (r: AdminReportItem) => { if (openHash === r.file_hash) setOpenHash(null) }

  /** 送出批次並把逐筆結果整理成給人看的清單；錯誤（含 422）往上拋給對話框或 toast 顯示。 */
  const runBulk = async (targets: AdminReportItem[], hide: boolean, reason?: string) => {
    const names = new Map(targets.map(r => [r.file_hash, displayTitle(r)]))
    const res = await bulk.mutateAsync({ fileHashes: targets.map(r => r.file_hash), hidden: hide, reason })
    sel.clear()
    const title = hide ? t('admin.adminReportsPage.hideSelected') : t('admin.adminReportsPage.restoreSelected')
    const skipped = res.results.filter(r => r.status === 'skipped').map(r => ({
      name: names.get(r.file_hash) ?? r.file_hash.slice(0, 12), detail: r.detail ?? r.code ?? t('admin.adminReportsPage.skipped'),
    }))
    toast(t('admin.adminReportsPage.succeededSkipped', { title, ok: res.ok, length: skipped.length }))
    // 有被略過的才留下逐筆原因（toast 2.6 秒就消失，原因要看得完）。
    setOutcome(skipped.length > 0 ? { title, ok: res.ok, skipped } : null)
  }
  const restore = (r: AdminReportItem) => {
    setRestoreFor(null)
    visibility.mutate({ fileHash: r.file_hash, hidden: false }, {
      onSuccess: () => { closeDrawerFor(r); toast(t('admin.adminReportsPage.restored', { short: short(displayTitle(r)) })) },
      onError: err => toast(t('admin.adminReportsPage.restoreFailed', { err: messageOf(err) }), { tone: 'bad' }),
    })
  }

  return (
    <ConsolePage>
      <PageHeader
        summary={t('admin.adminReportsPage.findReportsHideOnesThat')}
        help={<>
          {t('admin.adminReportsPage.hidingRequiresAReasonAnd')}
        </>}
      />
      <div className={c.tiles} role="group" aria-label={t('admin.adminReportsPage.reportCount')}>
        <StatTile label={t('admin.adminReportsPage.corpus')} value={fmtCount(counts.data?.total)} unit={t('admin.adminReportsPage.reports')} />
        <StatTile label={t('admin.adminReportsPage.hidden')} value={fmtCount(counts.data?.hidden)} />
        <StatTile label={t('admin.adminReportsPage.uploadDrafts')} value={fmtCount(counts.data?.draft)} />
      </div>
      <div className={`${c.toolbar} ${chosen.length > 0 ? s.toolbarSticky : ''}`}>
        <form className={s.search} onSubmit={search} role="search">
          <SearchInput value={draft} onChange={v => setDraft(v.slice(0, 200))} placeholder={t('admin.adminReportsPage.titleFileNameBrokerKeyword')} width={260} />
          <Button type="submit">{t('admin.adminReportsPage.search')}</Button>
        </form>
        <Segmented label={t('admin.adminReportsPage.status')} options={STATUS_OPTIONS} value={status} onChange={v => { setStatus(v); goTo(0) }} />
        <div className={c.toolbarSpacer} />
        {chosen.length > 0 && (
          <div className={c.bulk} role="toolbar" aria-label={t('admin.adminReportsPage.batchActions')}>
            <span className={s.bulkCount}>{t('admin.adminReportsPage.selected', { length: chosen.length })}</span>
            <Button size="sm" variant="dangerText" disabled={toHide.length === 0 || bulk.isPending}
              title={toHide.length === 0 ? t('admin.adminReportsPage.allSelectedReportsAreAlready') : undefined}
              onClick={() => setBulkHideFor(toHide)}>{t('admin.adminReportsPage.hideSelected2', { length: toHide.length })}</Button>
            <Button size="sm" disabled={toRestore.length === 0 || bulk.isPending}
              title={toRestore.length === 0 ? t('admin.adminReportsPage.noneOfTheSelectedReports') : undefined}
              onClick={() => setBulkRestoreFor(toRestore)}>{t('admin.adminReportsPage.restoreSelected2', { length: toRestore.length })}</Button>
            <Button size="sm" className={c.btnGhost} onClick={sel.clear}>{t('admin.adminReportsPage.clear')}</Button>
          </div>
        )}
      </div>
      <BulkResultPanel outcome={outcome} onClose={() => setOutcome(null)} />
      <Card title={t('admin.adminReportsPage.reportList')} labelledBy="admin-reports-title"
        right={<ExportCsvButton href={adminCsvUrls.exportReports({ q: q || undefined, ...statusQuery(status) })} what={t('admin.adminReportsPage.reportList')} />}>
        {reports.isPending ? (
          <p className={c.empty}>{t('admin.adminReportsPage.loading')}</p>
        ) : reports.isError ? (
          <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.adminReportsPage.couldnTLoadReports', { error: messageOf(reports.error) })}</p></div>
        ) : items.length === 0 ? (
          <p className={c.empty}>{filtered ? t('admin.adminReportsPage.noMatchingReports') : t('admin.adminReportsPage.noReportsYet')}</p>
        ) : (
          <>
            <div className={c.tableWrap}>
              <table className={c.table}>
                <thead>
                  <tr>
                    <th className={c.thCheck}>
                      <SelectAllBox ids={selectable} selected={sel.selected} onChange={on => sel.setAll(selectable, on)} />
                    </th>
                    <th>{t('admin.adminReportsPage.report')}</th><th>{t('admin.adminReportsPage.brokerMarket')}</th><th>{t('admin.adminReportsPage.reportDateIngested')}</th><th>{t('admin.adminReportsPage.status')}</th><th className={`${c.tdRight} ${c.stickyRight}`}>{t('admin.adminReportsPage.actions')}</th>
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
                          <input type="checkbox" className={c.check} aria-label={t('admin.adminReportsPage.select', { name })}
                            checked={checked} disabled={st === 'draft'}
                            title={st === 'draft' ? t('admin.adminReportsPage.draftsCanTBeHidden') : undefined}
                            onChange={() => sel.toggle(r.file_hash)} />
                        </td>
                        <td className={s.titleCell}>
                          <button type="button" className={c.rowLink} aria-haspopup="dialog"
                            onClick={e => { e.stopPropagation(); setOpenHash(r.file_hash) }}>{name}</button>
                          {r.title && <div className={c.sub}>{r.file_name}</div>}
                        </td>
                        <td>{r.source || '—'}<div className={c.sub}>{r.market || '—'}</div></td>
                        <td className={c.num}>
                          <div title={t('admin.adminReportsPage.reportDate')}>{r.report_date ? r.report_date.slice(0, 10) : '—'}</div>
                          <div className={c.sub} title={t('admin.adminReportsPage.ingested')}>{fmtDateTime(r.created_at)}</div>
                        </td>
                        <td className={s.statusCell}>
                          <Status tone={STATUS_TONE[st]}>{STATUS_LABEL[st]}</Status>
                          {st === 'hidden' && r.hidden_reason && (
                            <div className={`${c.truncate} ${s.reason}`} title={r.hidden_reason}>{r.hidden_reason}</div>
                          )}
                          {r.visibility_updated_at && (
                            <div className={c.sub}>{r.visibility_updated_by ?? '—'}{t('common.separator')}{fmtDateTime(r.visibility_updated_at)}</div>
                          )}
                        </td>
                        <td className={`${c.tdActions} ${c.stickyRight}`}>
                          {st === 'draft' ? (
                            <Link to={DRAFT_REVIEW_PATH} className={`${c.btn} ${c.btnSm}`} title={t('admin.adminReportsPage.publishOrRejectInUpload')}
                              onClick={stop}>{t('admin.adminReportsPage.goToReview')}</Link>
                          ) : st === 'hidden' ? (
                            <RowActions primary={{ label: t('admin.adminReportsPage.restore'), onClick: () => setRestoreFor(r), disabled: visibility.isPending }} />
                          ) : (
                            <RowActions primary={{ label: t('admin.adminReportsPage.hide'), onClick: () => setHideFor(r), disabled: visibility.isPending }} />
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <div className={c.pager}>
              <span>{t('admin.adminReportsPage.of', { v: offset + 1, v2: offset + items.length, total: reports.data.total })}</span>
              <div className={c.pagerBtns}>
                <Button size="sm" disabled={offset === 0}
                  onClick={() => goTo(Math.max(0, offset - REPORTS_PAGE_SIZE))}>{t('admin.adminReportsPage.previous')}</Button>
                <Button size="sm" disabled={reports.data.next_offset == null}
                  onClick={() => { if (reports.data.next_offset != null) goTo(reports.data.next_offset) }}>{t('admin.adminReportsPage.next')}</Button>
              </div>
            </div>
          </>
        )}
      </Card>
      <ReportDrawer
        fileHash={openHash}
        report={openReport}
        missing={missing}
        busy={visibility.isPending}
        onClose={() => setOpenHash(null)}
        onHide={setHideFor}
        onRestore={setRestoreFor}
      />
      <HideDialog report={hideFor} onClose={() => setHideFor(null)}
        onDone={r => { closeDrawerFor(r); toast(t('admin.adminReportsPage.hid', { short: short(displayTitle(r)) })) }} />
      <ConfirmDialog
        open={restoreFor != null}
        tone="safe"
        title={restoreFor ? t('admin.adminReportsPage.restore2', { restoreFor: displayTitle(restoreFor) }) : ''}
        body={t('admin.adminReportsPage.onceRestoredItReappearsIn')}
        confirmLabel={t('admin.adminReportsPage.restore')}
        onConfirm={() => { if (restoreFor) restore(restoreFor) }}
        onCancel={() => setRestoreFor(null)}
      />
      <BulkHideDialog targets={bulkHideFor} onClose={() => setBulkHideFor(null)}
        onSubmit={reason => runBulk(bulkHideFor ?? [], true, reason)} />
      <ConfirmDialog
        open={bulkRestoreFor != null}
        tone="safe"
        title={t('admin.adminReportsPage.restoreReports', { v: bulkRestoreFor?.length ?? 0 })}
        body={bulkRestoreFor
          ? t('admin.adminReportsPage.onceRestoredTheyReappearIn', { namesPreview: namesPreview(bulkRestoreFor.map(r => displayTitle(r))) })
          : ''}
        confirmLabel={t('admin.adminReportsPage.restoreReports2', { v: bulkRestoreFor?.length ?? 0 })}
        onConfirm={() => {
          const targets = bulkRestoreFor ?? []
          setBulkRestoreFor(null)
          runBulk(targets, false).catch(err => toast(t('admin.adminReportsPage.batchRestoreFailed', { err: messageOf(err) }), { tone: 'bad' }))
        }}
        onCancel={() => setBulkRestoreFor(null)}
      />
    </ConsolePage>
  )
}

export default function AdminReportsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="reports.manage" title={t('admin.adminReportsPage.reports2')}><AdminReports /></RequireScope>
    </RequireAdmin>
  )
}
