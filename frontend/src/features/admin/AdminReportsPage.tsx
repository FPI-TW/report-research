import { useState, type FormEvent } from 'react'
import { Link } from 'react-router'
import { ConfirmDialog } from '../../components/primitives/ConfirmDialog'
import { Modal } from '../../components/primitives/Modal'
import { RequireAdmin } from '../../components/shell/RequireAdmin'
import { displayTitle } from '../../lib/displayTitle'
import { adminCsvUrls, type AdminReportItem } from '../../lib/generated/adminApi'
import { AdminHeader } from './AdminHeader'
import { BulkResultPanel, SelectAllBox } from './BulkParts'
import { ExportCsvButton } from './ExportCsvButton'
import { fmtDateTime } from './auditLabels'
import { RequireScope } from './RequireScope'
import {
  REPORTS_PAGE_SIZE, useAdminReports, useBulkReportVisibility, useReportVisibility, type HiddenFilter,
} from './useAdminReports'
import { namesPreview, useSelection, type BulkOutcome } from './useSelection'
import styles from './Admin.module.css'

// 與後端 app/services/visibility.py 的 REASON_MAX_CHARS 相同；這裡只是提早提示，後端仍會再驗一次。
const REASON_MAX = 500

/** 錯誤物件 → 給人看的訊息。後端 400／403／404 的 detail 由 requestJSON 放進 message，原樣顯示。 */
function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

function HideDialog({ report, onClose, onDone }: {
  report: AdminReportItem | null; onClose: () => void; onDone: (msg: string) => void
}) {
  const visibility = useReportVisibility()
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const close = () => { setReason(''); setError(null); onClose() }

  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (!report) return
    const note = reason.trim()
    if (!note) { setError('隱藏研報必須填寫原因'); return }
    if (note.length > REASON_MAX) { setError(`原因最多 ${REASON_MAX} 字`); return }
    setError(null)
    visibility.mutate({ fileHash: report.file_hash, hidden: true, reason: note }, {
      onSuccess: () => { const name = displayTitle(report); close(); onDone(`已隱藏「${name}」`) },
      onError: err => setError(messageOf(err)),
    })
  }

  return (
    <Modal open={report != null} onClose={close} title={report ? `隱藏「${displayTitle(report)}」` : ''}>
      <form className={styles.dialogForm} onSubmit={submit} noValidate>
        <p className={styles.hint}>
          隱藏後所有使用者（含管理員走一般頁面時）的檢索、問答、閱讀頁、雷達、總覽、簡報與原檔都看不到這份研報；
          批次照常處理，恢復即生效。
        </p>
        <label className={styles.field}>原因（必填，最多 {REASON_MAX} 字；只有管理員看得到）
          <textarea value={reason} onChange={e => setReason(e.target.value)} rows={3} maxLength={REASON_MAX * 2} />
        </label>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.action} onClick={close}>取消</button>
          <button type="submit" className={styles.primary} disabled={visibility.isPending}>
            {visibility.isPending ? '隱藏中…' : '隱藏'}
          </button>
        </div>
      </form>
    </Modal>
  )
}

/** 批次隱藏：列出對象（最多 5 個名稱）並要求原因；原因規則與單筆相同，後端仍會再驗一次。 */
function BulkHideDialog({ targets, onClose, onSubmit }: {
  targets: AdminReportItem[] | null; onClose: () => void; onSubmit: (reason: string) => Promise<void>
}) {
  const [reason, setReason] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const close = () => { setReason(''); setError(null); onClose() }

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    const note = reason.trim()
    if (!note) { setError('隱藏研報必須填寫原因'); return }
    if (note.length > REASON_MAX) { setError(`原因最多 ${REASON_MAX} 字`); return }
    setError(null)
    setBusy(true)
    try {
      await onSubmit(note)
      setReason('')
      onClose()
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal open={targets != null} onClose={close} title={targets ? `批次隱藏 ${targets.length} 份研報` : ''}>
      <form className={styles.dialogForm} onSubmit={submit} noValidate>
        {targets && <p className={styles.bulkTargets}>{namesPreview(targets.map(r => displayTitle(r)))}</p>}
        <p className={styles.hint}>
          隱藏後所有使用者都看不到這些研報；同一個原因套用到每一份，每一份各留一筆操作紀錄。
          草稿或已不存在的研報會逐筆略過，其餘照常處理。
        </p>
        <label className={styles.field}>原因（必填，最多 {REASON_MAX} 字；只有管理員看得到）
          <textarea value={reason} onChange={e => setReason(e.target.value)} rows={3} maxLength={REASON_MAX * 2} />
        </label>
        {error && <p className={styles.error} role="alert">{error}</p>}
        <div className={styles.dialogActions}>
          <button type="button" className={styles.action} onClick={close}>取消</button>
          <button type="submit" className={styles.primary} disabled={busy}>
            {busy ? '隱藏中…' : `隱藏 ${targets?.length ?? 0} 份`}
          </button>
        </div>
      </form>
    </Modal>
  )
}

function ReportsTable({ onNotice }: { onNotice: (msg: string, isError?: boolean) => void }) {
  const [draft, setDraft] = useState('')
  const [q, setQ] = useState('')
  const [hidden, setHidden] = useState<HiddenFilter>('all')
  const [offset, setOffset] = useState(0)
  const [hideFor, setHideFor] = useState<AdminReportItem | null>(null)
  const [restoreFor, setRestoreFor] = useState<AdminReportItem | null>(null)
  const reports = useAdminReports({ q, hidden, offset })
  const visibility = useReportVisibility()
  const bulk = useBulkReportVisibility()
  const sel = useSelection()
  const [bulkHideFor, setBulkHideFor] = useState<AdminReportItem[] | null>(null)
  const [bulkRestoreFor, setBulkRestoreFor] = useState<AdminReportItem[] | null>(null)
  const [outcome, setOutcome] = useState<BulkOutcome | null>(null)

  // 勾選只作用在目前這一頁：換頁、改篩選就清掉。草稿不能隱藏也不能恢復，不給勾。
  const items = reports.data?.items ?? []
  const selectable = items.filter(r => r.publication !== 'draft').map(r => r.file_hash)
  const chosen = items.filter(r => sel.selected.has(r.file_hash) && r.publication !== 'draft')
  const toHide = chosen.filter(r => !r.hidden)
  const toRestore = chosen.filter(r => r.hidden)

  const search = (e: FormEvent) => { e.preventDefault(); setQ(draft); setOffset(0); sel.clear() }
  const goTo = (next: number) => { setOffset(next); sel.clear() }
  /** 送出批次並把逐筆結果整理成給人看的清單；錯誤（含 422）往上拋給對話框或通知列顯示。 */
  const runBulk = async (targets: AdminReportItem[], hide: boolean, reason?: string) => {
    const names = new Map(targets.map(r => [r.file_hash, displayTitle(r)]))
    const res = await bulk.mutateAsync({ fileHashes: targets.map(r => r.file_hash), hidden: hide, reason })
    sel.clear()
    setOutcome({
      title: hide ? '批次隱藏' : '批次恢復',
      ok: res.ok,
      skipped: res.results.filter(r => r.status === 'skipped').map(r => ({
        name: names.get(r.file_hash) ?? r.file_hash.slice(0, 12), detail: r.detail ?? r.code ?? '略過',
      })),
    })
  }
  const restore = (r: AdminReportItem) => {
    setRestoreFor(null)
    visibility.mutate({ fileHash: r.file_hash, hidden: false }, {
      onSuccess: () => onNotice(`已恢復「${displayTitle(r)}」`),
      onError: err => onNotice(`恢復失敗：${messageOf(err)}`, true),
    })
  }

  return (
    <section className={styles.card} aria-labelledby="admin-reports-title">
      <div className={styles.cardHead}>
        <h2 id="admin-reports-title" className={styles.ctitle}>研報清單</h2>
        <ExportCsvButton
          href={adminCsvUrls.exportReports({ q: q || undefined, hidden: hidden === 'all' ? undefined : hidden === 'hidden' })}
          what="研報清單"
        />
      </div>
      <form className={styles.form} onSubmit={search} role="search">
        <label className={styles.field}>關鍵字（標題／檔名／券商）
          <input type="search" value={draft} onChange={e => setDraft(e.target.value)} maxLength={200} />
        </label>
        <label className={styles.field}>狀態
          <select value={hidden} onChange={e => { setHidden(e.target.value as HiddenFilter); goTo(0) }}>
            <option value="all">全部</option>
            <option value="visible">顯示中</option>
            <option value="hidden">已隱藏</option>
          </select>
        </label>
        <button type="submit" className={styles.primary}>搜尋</button>
      </form>
      <div className={styles.spacer} />
      <BulkResultPanel outcome={outcome} onClose={() => setOutcome(null)} />
      {chosen.length > 0 && (
        <div className={styles.bulkBar} role="toolbar" aria-label="批次操作">
          <span className={styles.bulkCount}>已選 {chosen.length} 筆</span>
          <button type="button" className={`${styles.action} ${styles.danger}`}
            disabled={toHide.length === 0 || bulk.isPending} onClick={() => setBulkHideFor(toHide)}
            title={toHide.length === 0 ? '選取的研報都已隱藏' : undefined}>批次隱藏（{toHide.length}）</button>
          <button type="button" className={styles.action}
            disabled={toRestore.length === 0 || bulk.isPending} onClick={() => setBulkRestoreFor(toRestore)}
            title={toRestore.length === 0 ? '選取的研報都是顯示中' : undefined}>批次恢復（{toRestore.length}）</button>
          <button type="button" className={styles.action} onClick={sel.clear}>清除選取</button>
        </div>
      )}
      {reports.isPending ? (
        <p className={styles.idle}>載入中…</p>
      ) : reports.isError ? (
        <p className={styles.error} role="alert">研報清單載入失敗：{messageOf(reports.error)}</p>
      ) : reports.data.items.length === 0 ? (
        <p className={styles.idle}>{q || hidden !== 'all' ? '沒有符合條件的研報' : '還沒有任何研報'}</p>
      ) : (
        <>
          <div className={styles.tableWrap}>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th className={styles.checkCell}>
                    <SelectAllBox ids={selectable} selected={sel.selected} onChange={on => sel.setAll(selectable, on)} />
                  </th>
                  <th>研報</th><th>券商／市場</th><th>報告日／入庫</th><th>狀態</th><th>操作</th>
                </tr>
              </thead>
              <tbody>
                {reports.data.items.map(r => {
                  // 上傳後尚未發布的草稿：一般頁面看不到（閱讀頁 404），隱藏／恢復也不適用（後端回 409）。
                  const isDraft = r.publication === 'draft'
                  return (
                  <tr key={r.file_hash}>
                    <td className={styles.checkCell}>
                      <input type="checkbox" className={styles.rowCheck} aria-label={`選取「${displayTitle(r)}」`}
                        checked={!isDraft && sel.selected.has(r.file_hash)} disabled={isDraft}
                        title={isDraft ? '草稿不能隱藏或恢復，請到上傳審核發布或退回' : undefined}
                        onChange={() => sel.toggle(r.file_hash)} />
                    </td>
                    <td className={styles.wrapCell}>
                      {r.hidden || isDraft ? displayTitle(r) : <Link to={`/report/${r.file_hash}`}>{displayTitle(r)}</Link>}
                      {r.title && <div className={styles.muted}>{r.file_name}</div>}
                    </td>
                    <td>{r.source || '—'}<div className={styles.muted}>{r.market || '—'}</div></td>
                    <td className={styles.num}>
                      <div title="報告日">{r.report_date ? r.report_date.slice(0, 10) : '—'}</div>
                      <div className={styles.muted} title="入庫">{fmtDateTime(r.created_at)}</div>
                    </td>
                    <td className={styles.wrapCell}>
                      {isDraft
                        ? <span className={`${styles.badge} ${styles.badgeOff}`}>草稿</span>
                        : <span className={`${styles.badge} ${r.hidden ? styles.badgeOff : ''}`}>{r.hidden ? '已隱藏' : '顯示中'}</span>}
                      {r.hidden && r.hidden_reason && <div className={styles.reason}>{r.hidden_reason}</div>}
                      {r.visibility_updated_at && (
                        <div className={styles.muted}>
                          {r.visibility_updated_by ?? '—'}・{fmtDateTime(r.visibility_updated_at)}
                        </div>
                      )}
                    </td>
                    <td>
                      {isDraft ? (
                        <span className={styles.muted}>待發布</span>
                      ) : r.hidden ? (
                        <button type="button" className={styles.action} disabled={visibility.isPending}
                          onClick={() => setRestoreFor(r)}>恢復</button>
                      ) : (
                        <button type="button" className={`${styles.action} ${styles.danger}`}
                          disabled={visibility.isPending} onClick={() => setHideFor(r)}>隱藏</button>
                      )}
                    </td>
                  </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
          <div className={styles.pager}>
            <button type="button" className={styles.action} disabled={offset === 0}
              onClick={() => goTo(Math.max(0, offset - REPORTS_PAGE_SIZE))}>上一頁</button>
            <span>{`第 ${offset + 1}–${offset + reports.data.items.length} 筆，共 ${reports.data.total} 筆`}</span>
            <button type="button" className={styles.action} disabled={reports.data.next_offset == null}
              onClick={() => { if (reports.data.next_offset != null) goTo(reports.data.next_offset) }}>下一頁</button>
          </div>
        </>
      )}
      <p className={styles.hint}>
        隱藏以檔案內容（file_hash）為鍵，重新入庫也不會失效；隱藏與恢復都會留在「操作紀錄」（不含原因全文）。
      </p>
      <HideDialog report={hideFor} onClose={() => setHideFor(null)} onDone={msg => onNotice(msg)} />
      <ConfirmDialog
        open={restoreFor != null}
        title={restoreFor ? `恢復「${displayTitle(restoreFor)}」？` : ''}
        body="恢復後所有使用者立刻又能在檢索、問答與閱讀頁看到這份研報。"
        confirmLabel="恢復"
        onConfirm={() => { if (restoreFor) restore(restoreFor) }}
        onCancel={() => setRestoreFor(null)}
      />
      <BulkHideDialog targets={bulkHideFor} onClose={() => setBulkHideFor(null)}
        onSubmit={reason => runBulk(bulkHideFor ?? [], true, reason)} />
      <ConfirmDialog
        open={bulkRestoreFor != null}
        title={bulkRestoreFor ? `批次恢復 ${bulkRestoreFor.length} 份研報？` : ''}
        body={bulkRestoreFor
          ? `${namesPreview(bulkRestoreFor.map(r => displayTitle(r)))}。恢復後所有使用者立刻又能看到這些研報；每一份各留一筆操作紀錄。`
          : ''}
        confirmLabel={`恢復 ${bulkRestoreFor?.length ?? 0} 份`}
        onConfirm={() => {
          const targets = bulkRestoreFor ?? []
          setBulkRestoreFor(null)
          runBulk(targets, false).catch(err => onNotice(`批次恢復失敗：${messageOf(err)}`, true))
        }}
        onCancel={() => setBulkRestoreFor(null)}
      />
    </section>
  )
}

function AdminReports() {
  const [notice, setNotice] = useState<{ msg: string; isError: boolean } | null>(null)
  const onNotice = (msg: string, isError = false) => setNotice({ msg, isError })
  return (
    <div className={styles.page}>
      <div className={styles.inner}>
        <AdminHeader
          title="研報管理"
          subtitle="查研報、隱藏不該出現的研報（必填原因）或恢復，可勾選多筆一次處理；每一筆操作都會留在「操作紀錄」。"
        />
        {notice && (
          <p className={notice.isError ? styles.error : styles.ok} role={notice.isError ? 'alert' : 'status'}>{notice.msg}</p>
        )}
        <ReportsTable onNotice={onNotice} />
      </div>
    </div>
  )
}

export default function AdminReportsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="reports.manage" title="研報管理"><AdminReports /></RequireScope>
    </RequireAdmin>
  )
}
