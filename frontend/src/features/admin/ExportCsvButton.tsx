import { useState } from 'react'
import { downloadCsv } from './exportCsv'
import { Icon } from '../../components/primitives/Icon'
import c from '../../components/console/Console.module.css'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '匯出失敗，請重試'
}

/**
 * 管理清單的「匯出 CSV」按鈕：用目前的篩選條件下載（網址由產生的 `adminCsvUrls` 建）。
 * 成功時說明筆數（達上限時註明只含最新的那幾筆）；失敗時原樣顯示後端的 detail（403 缺權限、503 稽核寫不進去…）。
 * 後端每次匯出都會記在操作紀錄；這裡只是顯示層。
 */
export function ExportCsvButton({ href, what, label = '匯出 CSV' }: { href: string; what: string; label?: string }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ text: string; error: boolean } | null>(null)

  const run = async () => {
    setBusy(true)
    setMsg(null)
    try {
      const r = await downloadCsv(href)
      const n = r.rows ?? 0
      setMsg({ text: r.truncated ? `已下載 ${n} 筆（達匯出上限，只含最新的 ${n} 筆）` : `已下載 ${n} 筆`, error: false })
    } catch (err) {
      setMsg({ text: `${what}匯出失敗：${messageOf(err)}`, error: true })
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
        title="下載目前篩選條件下的清單；每次匯出都會記在操作紀錄"
      >
        <Icon name="download" size={14} />{busy ? '匯出中…' : label}
      </button>
    </span>
  )
}
