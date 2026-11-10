import { Link } from 'react-router'
import { Button, Drawer, DrawerFootNote, DrawerSection, Status } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { displayTitle } from '../../../lib/displayTitle'
import type { AdminReportItem } from '../../../lib/generated/adminApi'
import { fmtDateTime } from '../auditLabels'
import { DRAFT_REVIEW_PATH, STATUS_LABEL, STATUS_TONE, statusOf } from './reportStatus'
import s from './Reports.module.css'
import { t } from '@/i18n'

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
  get loading() { return t('admin.reports.reportDrawer.loading') }, get not_found() { return t('admin.reports.reportDrawer.reportNotFound') }, get error() { return t('admin.reports.reportDrawer.couldnTLoadThisReport') },
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
          {missing.kind === 'loading' && <p className={s.note}>{t('admin.reports.reportDrawer.loading')}</p>}
          {missing.kind === 'not_found' && (
            <p className={s.note} role="status">{t('admin.reports.reportDrawer.thereSNoReportWith')}</p>
          )}
          {missing.kind === 'error' && <p className={c.error} role="alert">{t('admin.reports.reportDrawer.couldnTLoad', { message: missing.message })}</p>}
          <dl className={c.kv}><dt>file_hash</dt><dd className={s.hash}>{fileHash}</dd></dl>
        </DrawerSection>
      </Drawer>
    )
  }
  const status = statusOf(report)
  const footer = status === 'draft' ? (
    <>
      <DrawerFootNote>{t('admin.reports.reportDrawer.draftsCanTBeHidden')}</DrawerFootNote>
      <Link to={DRAFT_REVIEW_PATH} className={`${c.btn} ${c.btnPrimary}`}>{t('admin.reports.reportDrawer.goToReview')}</Link>
    </>
  ) : status === 'hidden' ? (
    <>
      <DrawerFootNote>{t('admin.reports.reportDrawer.onceRestoredItReappearsIn')}</DrawerFootNote>
      <Button variant="primary" disabled={busy} onClick={() => onRestore(report)}>{t('admin.reports.reportDrawer.restore')}</Button>
    </>
  ) : (
    <>
      <Link to={`/report/${report.file_hash}`} className={c.btn}>{t('admin.reports.reportDrawer.openReadingPage')}</Link>
      {/* 這一步只是開原因表單：紅字不填色，實心紅留給表單的最後一步。 */}
      <Button variant="dangerText" disabled={busy} onClick={() => onHide(report)}>{t('admin.reports.reportDrawer.hide')}</Button>
    </>
  )
  return (
    <Drawer open onClose={onClose} title={displayTitle(report)} width={520}
      status={<Status tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Status>}
      subtitle={report.title ? report.file_name : undefined} footer={footer}>
      <DrawerSection title={t('admin.reports.reportDrawer.report')}>
        <dl className={c.kv}>
          <dt>{t('admin.reports.reportDrawer.fileName')}</dt><dd>{report.file_name}</dd>
          <dt>{t('admin.reports.reportDrawer.broker')}</dt><dd>{dash(report.source)}</dd>
          <dt>{t('admin.reports.reportDrawer.market')}</dt><dd>{dash(report.market)}</dd>
          <dt>{t('admin.reports.reportDrawer.reportDate')}</dt><dd>{report.report_date ? report.report_date.slice(0, 10) : '—'}</dd>
          <dt>{t('admin.reports.reportDrawer.ingested')}</dt><dd>{fmtDateTime(report.created_at)}</dd>
          <dt>file_hash</dt><dd className={s.hash}>{report.file_hash}</dd>
        </dl>
      </DrawerSection>
      {(status === 'hidden' || report.visibility_updated_at) && (
        <DrawerSection title={t('admin.reports.reportDrawer.visibility')}>
          <dl className={c.kv}>
            {status === 'hidden' && <><dt>{t('admin.reports.reportDrawer.reasonForHiding')}</dt><dd className={s.prose}>{dash(report.hidden_reason)}</dd></>}
            <dt>{t('admin.reports.reportDrawer.lastChange')}</dt><dd>{dash(report.visibility_updated_by)}</dd>
            <dt>{t('admin.reports.reportDrawer.changedAt')}</dt><dd>{fmtDateTime(report.visibility_updated_at)}</dd>
          </dl>
        </DrawerSection>
      )}
    </Drawer>
  )
}
