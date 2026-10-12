import { Link } from 'react-router'
import { Button, Drawer, DrawerFootNote, DrawerSection, Status } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { displayTitle } from '../../../lib/displayTitle'
import type { AdminReportItem } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { DRAFT_REVIEW_PATH, STATUS_LABEL, STATUS_TONE, statusOf } from './reportStatus'
import s from './Reports.module.css'

interface Props {
  /** URL 上的 `?report=`；null＝關著。 */
  fileHash: string | null
  /** 目前這一頁清單裡對得上的那一筆；清單還在載入或這一頁沒有它時是 undefined。 */
  report: AdminReportItem | undefined
  loading: boolean
  busy: boolean
  onClose: () => void
  onHide: (r: AdminReportItem) => void
  onRestore: (r: AdminReportItem) => void
}

const dash = (v: string | null | undefined) => (v == null || v === '' ? '—' : v)

/**
 * 研報詳情抽屜。後端沒有「依 file_hash 取一篇」的管理端點，內容取自目前這一頁的清單；
 * 連結開在別的篩選或頁次時對不上，只能請人用關鍵字找。
 */
export function ReportDrawer({ fileHash, report, loading, busy, onClose, onHide, onRestore }: Props) {
  if (!fileHash) return null
  if (!report) {
    return (
      <Drawer open onClose={onClose} title={loading ? '載入中…' : '找不到這份研報'} width={520}>
        <DrawerSection>
          {loading
            ? <p className={s.note}>載入中…</p>
            : <p className={s.note} role="status">這份研報不在目前這一頁的清單裡。調整篩選，或用標題、檔名、券商搜尋後再開啟。</p>}
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
