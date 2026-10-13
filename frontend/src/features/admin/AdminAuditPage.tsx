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

/** 錯誤物件 → 給人看的訊息。 */
function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '載入失敗，請重試'
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
  loading: '載入中…', not_found: '找不到這筆紀錄', error: '無法載入這筆紀錄', not_on_page: '找不到這筆紀錄',
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
            {missing.kind === 'not_found' && <p className={a.note}>沒有編號 #{entryId} 的操作紀錄。</p>}
            {missing.kind === 'error' && <p className={c.error} role="alert">#{entryId} 載入失敗：{missing.message}</p>}
            {missing.kind === 'not_on_page' && (
              <p className={a.note}>#{entryId} 不在目前這一頁。新的操作會把舊紀錄往後推，請翻頁找，或匯出 CSV。</p>
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
      footer={<DrawerFootNote>紀錄只能新增，不含任何密碼或註記全文。</DrawerFootNote>}>
      <DrawerSection title="這筆操作">
        <dl className={c.kv}>
          <dt>編號</dt><dd>#{entry.id}</dd>
          <dt>時間</dt><dd title={entry.created_at ?? undefined}>{fmtDateTime(entry.created_at)}</dd>
          <dt>操作者</dt>
          <dd>{actorLabel(entry)}{entry.actor_user_id && <span className={a.sub}>{entry.actor_user_id}</span>}</dd>
          <dt>動作</dt><dd>{entry.action}</dd>
          <dt>分類</dt><dd>{AUDIT_CATEGORY_LABELS[auditCategory(entry.action)]}</dd>
          <dt>對象</dt><dd>{entry.target_type}{entry.target_id ? ` ${entry.target_id}` : ''}</dd>
          <dt>內容</dt><dd>{auditSummary(entry)}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title="完整 detail">
        {hasDetail
          ? <pre className={`${c.log} ${a.detail}`} aria-label="完整 detail">{JSON.stringify(entry.detail, null, 2)}</pre>
          : <p className={a.note}>這筆操作沒有額外的 detail。</p>}
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
    { value: 'all', label: '全部', count: pageOnly ? items.length : null },
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
    <Card title="最近的操作" labelledBy="admin-audit-title"
      right={<ExportCsvButton href={adminCsvUrls.exportAudit(auditCategoryQuery(filter))} what="操作紀錄" />}>
      <div className={`${c.toolbar} ${a.toolbar}`}>
        <Segmented label={pageOnly ? '分類（只篩這一頁）' : '分類'} options={options} value={filter} onChange={setFilter} />
        {pageOnly && filter !== 'all' && <span className={c.muted}>分類只篩目前這一頁</span>}
      </div>
      {q.isPending ? (
        <p className={c.empty}>載入中…</p>
      ) : q.isError ? (
        <div className={c.cardBody}><p className={c.error} role="alert">操作紀錄載入失敗：{messageOf(q.error)}</p></div>
      ) : q.data.total === 0 && filter === 'all' ? (
        <p className={c.empty}>還沒有任何管理操作</p>
      ) : (
        <>
          {rows.length === 0 ? (
            <p className={c.empty}>
              {filter === 'all' ? '這一頁沒有紀錄'
                : pageOnly ? `這一頁沒有「${AUDIT_CATEGORY_LABELS[filter]}」的操作`
                  : q.data.total === 0 ? `沒有「${AUDIT_CATEGORY_LABELS[filter]}」的操作` : '這一頁沒有紀錄'}
            </p>
          ) : (
            <div className={c.tableWrap}>
              <table className={c.table}>
                <thead><tr><th>時間</th><th>操作者</th><th>動作</th><th>內容</th></tr></thead>
                <tbody>
                  {rows.map(e => (
                    <tr key={e.id} className={`${c.rowClickable} ${String(e.id) === entryParam ? c.rowSelected : ''}`}
                      onClick={() => setEntryParam(String(e.id))}>
                      <td className={c.num}>{fmtDateTime(e.created_at)}</td>
                      <td className={e.actor_username ? c.t1 : c.muted}>{actorLabel(e)}</td>
                      <td className={c.nowrap}>
                        {/* 列的鍵盤入口：整列可點，但能聚焦的是這顆按鈕。 */}
                        <button type="button" className={c.rowLink} title="看這筆操作的完整內容"
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
                ? `第 ${offset + 1}–${offset + q.data.items.length} 筆，共 ${q.data.total} 筆`
                : `共 ${q.data.total} 筆`}
              {pageOnly && filter !== 'all' && `・這一頁符合 ${rows.length} 筆`}
            </span>
            <div className={c.pagerBtns}>
              <Button size="sm" disabled={offset === 0} onClick={() => goTo(Math.max(0, offset - AUDIT_PAGE_SIZE))}>上一頁</Button>
              <Button size="sm" disabled={q.data.next_offset == null}
                onClick={() => { if (q.data.next_offset != null) goTo(q.data.next_offset) }}>下一頁</Button>
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
        summary="誰在何時做了什麼；新的在前，不含任何密碼或註記全文。"
        help="建帳、改角色、停用、重設密碼、強制登出、處理待複核與隱藏研報，誰在何時做了什麼；新的在前。不含任何密碼或註記全文。操作者為「指令列（CLI）」表示由主機上的指令列工具執行。點任一列看完整的 detail；分類與匯出 CSV 用同一組條件，匯出的就是目前篩出來的清單（最多一萬筆）。"
      />
      <AuditLog />
    </ConsolePage>
  )
}

export default function AdminAuditPage() {
  return <RequireAdmin><AdminAudit /></RequireAdmin>
}
