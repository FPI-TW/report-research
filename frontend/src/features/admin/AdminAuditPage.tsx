import { useSearchParams } from 'react-router'
import {
  Button, Card, ConsolePage, Drawer, DrawerFootNote, DrawerSection, PageHeader, Segmented, useQueryParam, type SegOption,
} from '../../components/console'
import c from '../../components/console/Console.module.css'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import type { AuditEntry } from '../../lib/adminSchemas'
import { ApiError } from '../../lib/api'
import { adminCsvUrls } from '../../lib/generated/adminApi'
import { ExportCsvButton } from './ExportCsvButton'
import {
  AUDIT_CATEGORIES, AUDIT_CATEGORY_LABELS, auditCategory, auditCategoryQuery, type AuditFilter,
} from './auditCategories'
import { actionLabel, actorLabel, auditSummary, fmtDateTime } from './auditLabels'
import { AUDIT_PAGE_SIZE } from './useAdmin'
import { isServerFiltered, parseEntryId, useAuditEntry, useAuditLog } from './useAuditLog'
import a from './Audit.module.css'
import { t } from '@/i18n'

/** 錯誤物件 → 給人看的訊息。 */
function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.adminAuditPage.couldnTLoadTryAgain')
}

/** URL 的 `?offset=` → 非負整數；亂填的值當成第一頁。 */
function parseOffset(raw: string | null): number {
  const n = raw == null ? 0 : Number(raw)
  return Number.isInteger(n) && n > 0 ? n : 0
}

/** 抽屜拿不到那一筆時的狀態：還在找、確定沒有、取不到，或（舊版後端）只知道不在這一頁。 */
type Missing =
  | { kind: 'loading' }
  | { kind: 'not_found' }
  | { kind: 'error'; message: string }
  | { kind: 'not_on_page' }

const MISSING_TITLE: Record<Missing['kind'], string> = {
  get loading() { return t('admin.adminAuditPage.loading') }, get not_found() { return t('admin.adminAuditPage.entryNotFound') }, get error() { return t('admin.adminAuditPage.couldnTLoadThisEntry') }, get not_on_page() { return t('admin.adminAuditPage.entryNotFound') },
}

function EntryDrawer({ entryId, entry, missing, onClose }: {
  entryId: string | null; entry: AuditEntry | undefined; missing: Missing; onClose: () => void
}) {
  if (entryId == null) return null
  if (!entry) {
    return (
      <Drawer open onClose={onClose} width={520} title={MISSING_TITLE[missing.kind]}>
        {missing.kind !== 'loading' && (
          <DrawerSection>
            {missing.kind === 'not_found' && <p className={a.note}>{t('admin.adminAuditPage.thereIsNoAuditEntry', { entryId })}</p>}
            {missing.kind === 'error' && <p className={c.error} role="alert">{t('admin.adminAuditPage.couldnTLoad', { entryId, message: missing.message })}</p>}
            {missing.kind === 'not_on_page' && (
              <p className={a.note}>{t('admin.adminAuditPage.isNotOnThisPage', { entryId })}</p>
            )}
          </DrawerSection>
        )}
      </Drawer>
    )
  }
  const hasDetail = Object.keys(entry.detail).length > 0
  return (
    <Drawer open onClose={onClose} width={520} title={actionLabel(entry.action)}
      subtitle={`${fmtDateTime(entry.created_at)}・${actorLabel(entry)}`}
      footer={<DrawerFootNote>{t('admin.adminAuditPage.entriesCanOnlyBeAppended')}</DrawerFootNote>}>
      <DrawerSection title={t('admin.adminAuditPage.thisAction')}>
        <dl className={c.kv}>
          <dt>{t('admin.adminAuditPage.id')}</dt><dd>#{entry.id}</dd>
          <dt>{t('admin.adminAuditPage.time')}</dt><dd title={entry.created_at ?? undefined}>{fmtDateTime(entry.created_at)}</dd>
          <dt>{t('admin.adminAuditPage.actor')}</dt>
          <dd>{actorLabel(entry)}{entry.actor_user_id && <span className={a.sub}>{entry.actor_user_id}</span>}</dd>
          <dt>{t('admin.adminAuditPage.action')}</dt><dd>{entry.action}</dd>
          <dt>{t('admin.adminAuditPage.category')}</dt><dd>{AUDIT_CATEGORY_LABELS[auditCategory(entry.action)]}</dd>
          <dt>{t('admin.adminAuditPage.target')}</dt><dd>{entry.target_type}{entry.target_id ? ` ${entry.target_id}` : ''}</dd>
          <dt>{t('admin.adminAuditPage.content')}</dt><dd>{auditSummary(entry)}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title={t('admin.adminAuditPage.fullDetail')}>
        {hasDetail
          ? <pre className={`${c.log} ${a.detail}`} aria-label={t('admin.adminAuditPage.fullDetail')}>{JSON.stringify(entry.detail, null, 2)}</pre>
          : <p className={a.note}>{t('admin.adminAuditPage.thisActionHasNoAdditional')}</p>}
      </DrawerSection>
    </Drawer>
  )
}

function AuditLog() {
  // 頁碼、分類與開著的那一筆都放在 URL：連結可以分享、重新整理也還在。
  const [params, setParams] = useSearchParams()
  const [entryParam, setEntryParam] = useQueryParam('entry')
  const offset = parseOffset(params.get('offset'))
  const filter: AuditFilter = AUDIT_CATEGORIES.find(k => k === params.get('category')) ?? 'all'
  const q = useAuditLog(offset, filter)

  const items = q.data?.items ?? []
  // 舊版後端不認得分類參數，回的是沒篩的一頁：退回只篩目前這一頁（筆數與翻頁仍是全部的）。
  const pageOnly = q.data != null && !isServerFiltered(q.data)
  const rows = pageOnly && filter !== 'all' ? items.filter(e => auditCategory(e.action) === filter) : items
  const options: SegOption<AuditFilter>[] = [
    { value: 'all', label: t('admin.adminAuditPage.all'), count: pageOnly ? items.length : null },
    ...AUDIT_CATEGORIES.map(k => ({
      value: k, label: AUDIT_CATEGORY_LABELS[k],
      count: pageOnly ? items.filter(e => auditCategory(e.action) === k).length : null,
    })),
  ]
  const goTo = (next: number) => setParams(prev => {
    const p = new URLSearchParams(prev)
    if (next > 0) p.set('offset', String(next))
    else p.delete('offset')
    return p
  })
  /** 換分類：後端篩的時候筆數跟著變，回到第一頁；只篩這一頁的時候留在原頁。 */
  const setFilter = (next: AuditFilter) => setParams(prev => {
    const p = new URLSearchParams(prev)
    if (next === 'all') p.delete('category')
    else p.set('category', next)
    if (!pageOnly) p.delete('offset')
    return p
  })

  // `?entry=` 指的那筆不在這一頁（別的頁次、別的分類）：向後端取那一筆。舊版後端沒有這支端點，不問。
  const entryId = parseEntryId(entryParam)
  const onPage = items.find(e => String(e.id) === entryParam)
  const single = useAuditEntry(entryId, !q.isPending && !pageOnly && !onPage)
  const entry = onPage ?? (entryId != null && !pageOnly ? single.data : undefined)
  const missing: Missing = q.isPending || (single.isPending && single.fetchStatus !== 'idle') ? { kind: 'loading' }
    : pageOnly ? { kind: 'not_on_page' }
    : single.isError && !(single.error instanceof ApiError && single.error.status === 404)
      ? { kind: 'error', message: messageOf(single.error) }
      : { kind: 'not_found' }

  return (
    <Card title={t('admin.adminAuditPage.recentActions')} labelledBy="admin-audit-title"
      right={<ExportCsvButton href={adminCsvUrls.exportAudit(auditCategoryQuery(filter))} what={t('admin.adminAuditPage.auditLog')} />}>
      <div className={`${c.toolbar} ${a.toolbar}`}>
        <Segmented label={pageOnly ? t('admin.adminAuditPage.categoryThisPageOnly') : t('admin.adminAuditPage.category')} options={options} value={filter} onChange={setFilter} />
        {pageOnly && filter !== 'all' && <span className={c.muted}>{t('admin.adminAuditPage.categoryFiltersOnlyTheCurrent')}</span>}
      </div>
      {q.isPending ? (
        <p className={c.empty}>{t('admin.adminAuditPage.loading')}</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">{t('admin.adminAuditPage.couldnTLoadTheAudit', { error: messageOf(q.error) })}</p></div>
      ) : q.data.total === 0 && filter === 'all' ? (
        <p className={c.empty}>{t('admin.adminAuditPage.noAdminActionsYet')}</p>
      ) : (
        <>
          {rows.length === 0 ? (
            <p className={c.empty}>
              {filter === 'all' ? t('admin.adminAuditPage.noEntriesOnThisPage')
                : pageOnly ? t('admin.adminAuditPage.noActionsOnThisPage', { AUDIT_CATEGORY_LABELS: AUDIT_CATEGORY_LABELS[filter] })
                  : q.data.total === 0 ? t('admin.adminAuditPage.noActions', { AUDIT_CATEGORY_LABELS: AUDIT_CATEGORY_LABELS[filter] }) : t('admin.adminAuditPage.noEntriesOnThisPage')}
            </p>
          ) : (
            <div className={c.tableWrap}>
              <table className={c.table}>
                <thead><tr><th>{t('admin.adminAuditPage.time')}</th><th>{t('admin.adminAuditPage.actor')}</th><th>{t('admin.adminAuditPage.action')}</th><th>{t('admin.adminAuditPage.content')}</th></tr></thead>
                <tbody>
                  {rows.map(e => (
                    <tr key={e.id} className={`${c.rowClickable} ${String(e.id) === entryParam ? c.rowSelected : ''}`}
                      onClick={() => setEntryParam(String(e.id))}>
                      <td className={c.num}>{fmtDateTime(e.created_at)}</td>
                      <td className={e.actor_username ? c.t1 : c.muted}>{actorLabel(e)}</td>
                      <td className={c.nowrap}>
                        {/* 列的鍵盤入口：整列可點，但能聚焦的是這顆按鈕。 */}
                        <button type="button" className={c.rowLink} title={t('admin.adminAuditPage.viewTheFullContentOf')}
                          onClick={ev => { ev.stopPropagation(); setEntryParam(String(e.id)) }}>
                          {actionLabel(e.action)}
                        </button>
                      </td>
                      <td className={a.summary}>{auditSummary(e)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className={c.pager}>
            <span>
              {q.data.items.length > 0
                ? t('admin.adminAuditPage.of', { v: offset + 1, v2: offset + q.data.items.length, total: q.data.total })
                : t('admin.adminAuditPage.total', { total: q.data.total })}
              {pageOnly && filter !== 'all' && t('admin.adminAuditPage.matchOnThisPage', { length: rows.length })}
            </span>
            <div className={c.pagerBtns}>
              <Button size="sm" disabled={offset === 0} onClick={() => goTo(Math.max(0, offset - AUDIT_PAGE_SIZE))}>{t('admin.adminAuditPage.previous')}</Button>
              <Button size="sm" disabled={q.data.next_offset == null}
                onClick={() => { if (q.data.next_offset != null) goTo(q.data.next_offset) }}>{t('admin.adminAuditPage.next')}</Button>
            </div>
          </div>
        </>
      )}
      <EntryDrawer entryId={entryParam} entry={entry} missing={missing} onClose={() => setEntryParam(null)} />
    </Card>
  )
}

function AdminAudit() {
  return (
    <ConsolePage>
      <PageHeader
        summary={t('admin.adminAuditPage.whoDidWhatAndWhen')}
        help={t('admin.adminAuditPage.accountCreationRoleChangesDisabling')}
      />
      <AuditLog />
    </ConsolePage>
  )
}

export default function AdminAuditPage() {
  return <RequireAdmin><AdminAudit /></RequireAdmin>
}
