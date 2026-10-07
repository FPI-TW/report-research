import { useRef, useState } from 'react'
import {
  adminApi, FlagExportDocumentSchema, type FlagExportDocument, type FlagImportResponse,
} from '../../../lib/generated/adminApi'
import { ElevationCancelledError } from '../../account/useElevationGate'
import adminStyles from '../Admin.module.css'
import { exportFilename, exportOverrideSummary, IMPORT_ACTION_LABELS } from './flagText'
import styles from './Flags.module.css'

type Guard = <T>(action: () => Promise<T>) => Promise<T>

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

function download(doc: FlagExportDocument) {
  const blob = new Blob([JSON.stringify(doc, null, 2) + '\n'], { type: 'application/json' })
  const href = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = href
    a.download = exportFilename(doc.source_environment)
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    setTimeout(() => URL.revokeObjectURL(href), 0)
  }
}

/**
 * 設定搬移（定案 16）：測試環境匯出 → 正式環境匯入。只做 API 與頁面，不自動同步。
 *
 * 匯入一律先預覽（dry-run）：列出每個 key 會新增／更新／刪除／不變，以及錯誤（未登記的 key、這個環境找不到的
 * 帳號名稱）。有錯誤或沒有變更時「套用」停用；套用時後端在同一筆交易重算一次差異，有錯誤整份不寫。
 * 預覽與套用都需要 ops.operate＋重新驗證（後端守門；這裡的 guard 只是彈出驗證框後自動重試）。
 */
export function FlagTransfer({ canOperate, guard, onApplied }: {
  canOperate: boolean
  guard: Guard
  onApplied: (msg: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [doc, setDoc] = useState<FlagExportDocument | null>(null)
  const [preview, setPreview] = useState<FlagImportResponse | null>(null)

  const exportNow = async () => {
    setError(null)
    setBusy(true)
    try {
      download(await adminApi.exportFlags())
    } catch (err) {
      setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  const reset = () => {
    setDoc(null)
    setPreview(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  const pick = async (file: File | undefined) => {
    setError(null)
    setPreview(null)
    setDoc(null)
    if (!file) return
    let parsed: FlagExportDocument
    try {
      const result = FlagExportDocumentSchema.safeParse(JSON.parse(await file.text()))
      if (!result.success) throw new Error('format')
      parsed = result.data
    } catch {
      setError('這不是功能開關的匯出檔（需要「匯出設定」下載的 JSON）')
      return
    }
    setBusy(true)
    try {
      const res = await guard(() => adminApi.importFlags({ dry_run: true, document: parsed }))
      setDoc(parsed)
      setPreview(res)
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) setError(messageOf(err))
      if (fileRef.current) fileRef.current.value = ''
    } finally {
      setBusy(false)
    }
  }

  const apply = async () => {
    if (!doc) return
    setError(null)
    setBusy(true)
    try {
      const res = await guard(() => adminApi.importFlags({ dry_run: false, document: doc }))
      const n = res.changes.filter((c) => c.action !== 'unchanged').length
      reset()
      onApplied(`已套用匯入：${n} 個旗標有變更，每一筆都已寫入操作紀錄`)
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  const changed = preview?.changes.filter((c) => c.action !== 'unchanged') ?? []

  return (
    <section className={adminStyles.card} aria-labelledby="flags-transfer-title">
      <h2 id="flags-transfer-title" className={adminStyles.ctitle}>匯出與匯入</h2>
      <p className={adminStyles.hint}>
        用於把測試環境調好的設定搬到正式環境：在測試環境匯出、到正式環境匯入。使用者以帳號名稱對應（兩個環境的帳號
        UUID 不同）；檔案裡沒列出的旗標不會被動到。環境變數上限不在匯出檔裡，兩邊各自以環境檔設定。
      </p>
      <div className={styles.transferRow}>
        <button type="button" className={adminStyles.action} disabled={busy} onClick={() => void exportNow()}>
          匯出設定（JSON）
        </button>
        {canOperate ? (
          <label className={styles.fileLabel}>
            匯入檔案並預覽
            <input ref={fileRef} type="file" accept="application/json,.json" disabled={busy}
              onChange={(e) => void pick(e.target.files?.[0])} />
          </label>
        ) : (
          <span className={adminStyles.muted}>匯入需要 ops.operate 權限</span>
        )}
      </div>
      {error && <p className={adminStyles.error} role="alert">{error}</p>}
      {preview && (
        <div className={styles.preview} aria-label="匯入預覽">
          {!preview.registry_version_match && (
            <p className={adminStyles.hint} role="note">
              匯出檔的旗標清單版本（{preview.document_registry_version ?? '未知'}）與這個環境（{preview.registry_version}）不同：
              兩邊程式版本可能不一致，請確認差異後再套用。
            </p>
          )}
          {preview.errors.length > 0 && (
            <div className={adminStyles.error} role="alert">
              <p className={styles.previewHead}>有 {preview.errors.length} 個錯誤，整份不能套用：</p>
              <ul className={styles.problems}>
                {preview.errors.map((e, i) => <li key={`${e.key}-${i}`}><code>{e.key ?? '—'}</code>：{e.detail}</li>)}
              </ul>
            </div>
          )}
          <div className={adminStyles.tableWrap}>
            <table className={adminStyles.table}>
              <thead><tr><th>旗標</th><th>動作</th><th>目前</th><th>匯入後</th></tr></thead>
              <tbody>
                {preview.changes.map((c) => (
                  <tr key={c.key}>
                    <td><code>{c.key}</code></td>
                    <td className={c.action === 'unchanged' ? adminStyles.muted : undefined}>{IMPORT_ACTION_LABELS[c.action]}</td>
                    <td className={adminStyles.wrapCell}>{exportOverrideSummary(c.before)}</td>
                    <td className={adminStyles.wrapCell}>{c.action === 'delete' ? '沒有覆寫' : exportOverrideSummary(c.after)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className={adminStyles.dialogActions}>
            <button type="button" className={adminStyles.action} disabled={busy} onClick={reset}>取消</button>
            <button type="button" className={adminStyles.primary}
              disabled={busy || preview.errors.length > 0 || changed.length === 0} onClick={() => void apply()}>
              {changed.length === 0 ? '沒有變更' : `套用 ${changed.length} 項變更`}
            </button>
          </div>
        </div>
      )}
    </section>
  )
}
