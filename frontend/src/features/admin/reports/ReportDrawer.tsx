import { Link } from 'react-router'
import { Button, Drawer, DrawerFootNote, DrawerSection, Status } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { displayTitle } from '../../../lib/displayTitle'
import type { AdminReportItem } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { DRAFT_REVIEW_PATH, STATUS_LABEL, STATUS_TONE, statusOf } from './reportStatus'
import s from './Reports.module.css'

/** 抽屜拿不到那一篇時的狀態：還在找、確定沒有，或取不到（說出原因）。 */
export type ReportMissing = { kind: 'loading' } | { kind: 'not_found' } | { kind: 'error'; message: string }

interface Props {
  /** URL 上的 `?report=`；null＝關著。 */
  fileHash: string | null
  /** 那一篇：目前這一頁清單裡的，或用單筆端點取來的；還沒拿到是 undefined。 */
  report: AdminReportItem | undefined
  /** `report` 是 undefined 時為什麼沒有。 */
  missing: ReportMissing
  busy: boolean
  onClose: () => void
  onHide: (r: AdminReportItem) => void
  onRestore: (r: AdminReportItem) => void
}

const dash = (v: string | null | undefined) => (v == null || v === '' ? '—' : v)

const MISSING_TITLE: Record<ReportMissing['kind'], string> = {
  loading: '載入中…', not_found: '找不到這份研報', error: '無法載入這份研報',
}

/**
 * 研報詳情抽屜。內容優先取目前這一頁的清單；連結開在別的篩選或頁次、清單裡沒有那一篇時，
 * 由頁面用單筆端點取來（`useAdminReport`）。
 */
export function ReportDrawer({ fileHash, report, missing, busy, onClose, onHide, onRestore }: Props) {
  if (!fileHash) return null
  if (!report) {
    return (
      <Drawer open onClose={onClose} title={MISSING_TITLE[missing.kind]} width={520}>
        <DrawerSection>
          {missing.kind === 'loading' && <p className={s.note}>載入中…</p>}
          {missing.kind === 'not_found' && (
            <p className={s.note} role="status">沒有這個 file_hash 的研報。確認連結是否完整，或用標題、檔名、券商搜尋。</p>
          )}
          {missing.kind === 'error' && <p className={c.error} role="alert">載入失敗：{missing.message}</p>}
          <dl className={c.kv}><dt>file_hash</dt><dd className={s.hash}>{fileHash}</dd></dl>
        </DrawerSection>
      </Drawer>
    )
  }
  const status = statusOf(report)
  const footer = status === 'draft' ? (
    <>
      <DrawerFootNote>草稿不能隱藏或恢復，到上傳審核發布或退回。</DrawerFootNote>
      <Link to={DRAFT_REVIEW_PATH} className={`${c.btn} ${c.btnPrimary}`}>前往審核</Link>
    </>
  ) : status === 'hidden' ? (
    <>
      <DrawerFootNote>恢復後立即重新出現在檢索、問答與閱讀頁。</DrawerFootNote>
      <Button variant="primary" disabled={busy} onClick={() => onRestore(report)}>恢復</Button>
    </>
  ) : (
    <>
      <Link to={`/report/${report.file_hash}`} className={c.btn}>開啟閱讀頁</Link>
      {/* 這一步只是開原因表單：紅字不填色，實心紅留給表單的最後一步。 */}
      <Button variant="dangerText" disabled={busy} onClick={() => onHide(report)}>隱藏</Button>
    </>
  )
  return (
    <Drawer open onClose={onClose} title={displayTitle(report)} width={520}
      status={<Status tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Status>}
      subtitle={report.title ? report.file_name : undefined} footer={footer}>
      <DrawerSection title="研報">
        <dl className={c.kv}>
          <dt>檔名</dt><dd>{report.file_name}</dd>
          <dt>券商</dt><dd>{dash(report.source)}</dd>
          <dt>市場</dt><dd>{dash(report.market)}</dd>
          <dt>報告日</dt><dd>{report.report_date ? report.report_date.slice(0, 10) : '—'}</dd>
          <dt>入庫</dt><dd>{fmtDateTime(report.created_at)}</dd>
          <dt>file_hash</dt><dd className={s.hash}>{report.file_hash}</dd>
        </dl>
      </DrawerSection>
      {(status === 'hidden' || report.visibility_updated_at) && (
        <DrawerSection title="可見性">
          <dl className={c.kv}>
            {status === 'hidden' && <><dt>隱藏原因</dt><dd className={s.prose}>{dash(report.hidden_reason)}</dd></>}
            <dt>最後變更</dt><dd>{dash(report.visibility_updated_by)}</dd>
            <dt>變更時間</dt><dd>{fmtDateTime(report.visibility_updated_at)}</dd>
          </dl>
        </DrawerSection>
      )}
    </Drawer>
  )
}
