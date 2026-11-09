import { useState } from 'react'
import { downloadCsv } from './exportCsv'
import { Icon } from '../../components/primitives/Icon'
import c from '../../components/console/Console.module.css'
import { t } from '@/i18n'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.exportCsvButton.exportFailedTryAgain')
}

/**
 * 管理清單的「匯出 CSV」按鈕：用目前的篩選條件下載（網址由產生的 `adminCsvUrls` 建）。
 * 成功時說明筆數（達上限時註明只含最新的那幾筆）；失敗時原樣顯示後端的 detail（403 缺權限、503 稽核寫不進去…）。
 * 後端每次匯出都會記在操作紀錄；這裡只是顯示層。
 */
export function ExportCsvButton({ href, what, label = t('admin.exportCsvButton.exportCsv') }: { href: string; what: string; label?: string }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null)

  const run = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const r = await downloadCsv(href)
      const n = r.rows ?? 0
      setMsg({ text: r.truncated ? t('admin.exportCsvButton.downloadedRowsExportLimitReached', { n }) : t('admin.exportCsvButton.downloadedRows', { n }), error: false })
    } catch (err) {
      setMsg({ text: t('admin.exportCsvButton.exportFailed', { what, err: messageOf(err) }), error: true })
    } finally {
      setBusy(false)
    }
  }

  return (
    <span className={c.inlineRow}>
      {msg && (
        <span role={msg.error ? 'alert' : 'status'} className={c.inlineMsg} data-error={msg.error || undefined}>
          {msg.text}
        </span>
      )}
      <button
        type="button"
        className={c.btn}
        onClick={run}
        disabled={busy}
        title={t('admin.exportCsvButton.downloadTheListUnderThe')}
      >
        <Icon name="download" size={14} />{busy ? t('admin.exportCsvButton.exporting') : label}
      </button>
    </span>
  )
}
