import { useRef, useState } from 'react'
import { Button, Dialog, isElevationCancelled, toast } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { Icon } from '../../../components/primitives/Icon'
import {
  adminApi, FlagExportDocumentSchema, type FlagExportDocument, type FlagImportResponse,
} from '../../../lib/generated/adminApi'
import { exportFilename, exportOverrideSummary, IMPORT_ACTION_LABELS } from './flagText'
import s from './Flags.module.css'
import { t } from '@/i18n'

type Elevated = <T>(what: string, action: () => Promise<T>) => Promise<T>

const WHAT = '匯入功能旗標'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.flags.flagTransfer.actionFailedTryAgain')
}

function download(doc: FlagExportDocument): string {
  const name = exportFilename(doc.source_environment)
  const blob = new Blob([JSON.stringify(doc, null, 2) + '\n'], { type: 'application/json' })
  const href = URL.createObjectURL(blob)
  try {
    const a = document.createElement('a')
    a.href = href
    a.download = name
    a.rel = 'noopener'
    document.body.appendChild(a)
    a.click()
    a.remove()
  } finally {
    setTimeout(() => URL.revokeObjectURL(href), 0)
  }
  return name
}

/**
 * 設定搬移（定案 16）：測試環境匯出 → 正式環境匯入。只做 API 與頁面，不自動同步。
 * 畫面是頁首的兩顆按鈕（匯出 JSON、匯入）＋匯入預覽的對話框。
 *
 * 匯入一律先預覽（dry-run）：列出每個 key 會新增／更新／刪除／不變，以及錯誤（未登記的 key、這個環境找不到的
 * 帳號名稱）。有錯誤或沒有變更時「套用」停用；套用時後端在同一筆交易重算一次差異，有錯誤整份不寫。
 * 預覽與套用都需要 ops.operate＋重新驗證（後端守門；這裡經 `useElevate` 先驗證身分）。
 */
export function FlagTransfer({ canOperate, elevated, onApplied }: {
  canOperate: boolean
  elevated: Elevated
  onApplied: (msg: string) => void
}) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [doc, setDoc] = useState<FlagExportDocument | null>(null)
  const [preview, setPreview] = useState<FlagImportResponse | null>(null)

  const exportNow = async () => {
    setBusy(true)
    try {
      toast(t('admin.flags.flagTransfer.downloaded', { download: download(await adminApi.exportFlags()) }))
    } catch (err) {
      toast(t('admin.flags.flagTransfer.exportFailed', { err: messageOf(err) }), { tone: 'bad' })
    } finally {
      setBusy(false)
    }
  }

  const reset = () => {
    setDoc(null)
    setPreview(null)
    setError(null)
    if (fileRef.current) fileRef.current.value = ''
  }

  const pick = async (file: File | undefined) => {
    reset()
    if (!file) return
    let parsed: FlagExportDocument
    try {
      const result = FlagExportDocumentSchema.safeParse(JSON.parse(await file.text()))
      if (!result.success) throw new Error('format')
      parsed = result.data
    } catch {
      toast(t('admin.flags.flagTransfer.thisIsnTAFeature'), { tone: 'bad' })
      return
    }
    setBusy(true)
    try {
      const res = await elevated(WHAT, () => adminApi.importFlags({ dry_run: true, document: parsed }))
      setDoc(parsed)
      setPreview(res)
    } catch (err) {
      if (!isElevationCancelled(err)) toast(t('admin.flags.flagTransfer.importPreviewFailed', { err: messageOf(err) }), { tone: 'bad' })
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
      const res = await elevated(WHAT, () => adminApi.importFlags({ dry_run: false, document: doc }))
      const n = res.changes.filter((ch) => ch.action !== 'unchanged').length
      reset()
      onApplied(t('admin.flags.flagTransfer.importAppliedFlagsChangedEach', { n }))
    } catch (err) {
      // 對話框還開著：錯誤留在裡面，看得到是哪一份預覽失敗。
      if (!isElevationCancelled(err)) setError(messageOf(err))
    } finally {
      setBusy(false)
    }
  }

  const changed = preview?.changes.filter((ch) => ch.action !== 'unchanged') ?? []

  return (
    <>
      <Button disabled={busy} onClick={() => { void exportNow() }} title={t('admin.flags.flagTransfer.downloadAllCurrentOverridesEnvironment')}>
        <Icon name="download" size={14} />{t('admin.flags.flagTransfer.exportJson')}
      </Button>
      <Button disabled={busy || !canOperate} onClick={() => fileRef.current?.click()}
        title={canOperate ? t('admin.flags.flagTransfer.chooseAPreviouslyExportedJson') : t('admin.flags.flagTransfer.importingRequiresTheOpsOperate')}>
        <Icon name="upload" size={14} />{t('admin.flags.flagTransfer.import')}
      </Button>
      {canOperate && (
        <input ref={fileRef} type="file" hidden aria-label={t('admin.flags.flagTransfer.importFileAndPreview')} accept="application/json,.json" disabled={busy}
          onChange={(e) => { void pick(e.target.files?.[0]) }} />
      )}
      <Dialog open={preview != null} onClose={reset} title={t('admin.flags.flagTransfer.importPreview')} width={720} closeButton locked={busy}>
        {preview && (
          <>
            {!preview.registry_version_match && (
              <div className={c.banner} data-tone="warn" role="note">
                {t('admin.flags.flagTransfer.theExportSFlagRegistry')}{preview.document_registry_version ?? t('admin.flags.flagTransfer.unknown')}{t('admin.flags.flagTransfer.differsFromThisEnvironmentS', { registry_version: preview.registry_version })}
              </div>
            )}
            {preview.errors.length > 0 && (
              <div className={c.error} role="alert">
                <p className={s.problemsHead}>{t('admin.flags.flagTransfer.errorsTheFileCanT', { length: preview.errors.length })}</p>
                <ul className={s.problems}>
                  {preview.errors.map((e, i) => <li key={`${e.key}-${i}`}><code>{e.key ?? '—'}</code>{t('admin.flags.flagTransfer.text', { detail: e.detail })}</li>)}
                </ul>
              </div>
            )}
            <div className={s.previewTable}>
              <table className={c.table}>
                <thead><tr><th>{t('admin.flags.flagTransfer.flag')}</th><th>{t('admin.flags.flagTransfer.action')}</th><th>{t('admin.flags.flagTransfer.current')}</th><th>{t('admin.flags.flagTransfer.afterImport')}</th></tr></thead>
                <tbody>
                  {preview.changes.map((ch) => (
                    <tr key={ch.key}>
                      <td className={`${c.mono} ${c.t1}`}>{ch.key}</td>
                      <td className={ch.action === 'unchanged' ? c.muted : c.nowrap}>{IMPORT_ACTION_LABELS[ch.action]}</td>
                      <td className={s.wrap}>{exportOverrideSummary(ch.before)}</td>
                      <td className={s.wrap}>{ch.action === 'delete' ? t('admin.flags.flagTransfer.noOverride') : exportOverrideSummary(ch.after)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className={s.hint}>{t('admin.flags.flagTransfer.accountsAreMatchedByName')}</p>
            {error && <p className={c.error} role="alert">{error}</p>}
            <div className={s.previewActions}>
              <Button disabled={busy} onClick={reset}>{t('admin.flags.flagTransfer.cancel')}</Button>
              <Button variant="primary" disabled={busy || preview.errors.length > 0 || changed.length === 0}
                onClick={() => { void apply() }}>
                {changed.length === 0 ? t('admin.flags.flagTransfer.noChanges') : t('admin.flags.flagTransfer.applyChanges', { length: changed.length })}
              </Button>
            </div>
          </>
        )}
      </Dialog>
    </>
  )
}
