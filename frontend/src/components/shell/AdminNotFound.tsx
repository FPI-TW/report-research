import { Link } from 'react-router'
import { ConsolePage } from '../console/ConsolePage'
import { PageHeader } from '../console/PageHeader'
import c from '../console/Console.module.css'
import { t } from '@/i18n'

/**
 * 管理後台裡的 404（`/admin/不存在`）：畫在 `AdminShell` 的內容區，用主控台的頁面骨架。
 * 研報平台那邊的 404 是 `App.tsx` 的 `NotFound`，兩邊各自一份。
 */
export default function AdminNotFound() {
  return (
    <ConsolePage>
      <PageHeader kicker={t('admin.shell.adminNotFound.adminConsole')} title={t('admin.shell.adminNotFound.pageNotFound')} summary={t('admin.shell.adminNotFound.noAdminPageMatchesThis')} />
      <div><Link to="/admin" className={c.btn}>{t('admin.shell.adminNotFound.backToOverview')}</Link></div>
    </ConsolePage>
  )
}
