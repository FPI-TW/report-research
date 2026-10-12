import { useRef, useState } from 'react'
import { Button, Dialog, isElevationCancelled, toast } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { Icon } from '../../../components/primitives/Icon'
import {
  adminApi, FlagExportDocumentSchema, type FlagExportDocument, type FlagImportResponse,
} from '../../../lib/generated/adminApi'
import { exportFilename, exportOverrideSummary, IMPORT_ACTION_LABELS } from './flagText'
import s from './Flags.module.css'

type Elevated = <T>(what: string, action: () => Promise<T>) => Promise<T>

const WHAT = '匯入功能旗標'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
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
      toast(`已下載 ${download(await adminApi.exportFlags())}`)
    } catch (err) {
      toast(`匯出失敗：${messageOf(err)}`, { tone: 'bad' })
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
      toast('這不是功能旗標的匯出檔（需要「匯出 JSON」下載的檔案）', { tone: 'bad' })
      return
    }
    setBusy(true)
    try {
      const res = await elevated(WHAT, () => adminApi.importFlags({ dry_run: true, document: parsed }))
      setDoc(parsed)
      setPreview(res)
    } catch (err) {
      if (!isElevationCancelled(err)) toast(`匯入預覽失敗：${messageOf(err)}`, { tone: 'bad' })
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
      onApplied(`已套用匯入：${n} 個旗標有變更，每一筆都已寫入操作紀錄`)
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
      <Button disabled={busy} onClick={() => { void exportNow() }} title="下載目前所有覆寫（不含環境上限）">
        <Icon name="download" size={14} />匯出 JSON
      </Button>
      <Button disabled={busy || !canOperate} onClick={() => fileRef.current?.click()}
        title={canOperate ? '選擇先前匯出的 JSON 檔；套用前會先列出每個旗標的差異' : '匯入需要 ops.operate 權限'}>
        <Icon name="upload" size={14} />匯入
      </Button>
      {canOperate && (
        <input ref={fileRef} type="file" hidden aria-label="匯入檔案並預覽" accept="application/json,.json" disabled={busy}
          onChange={(e) => { void pick(e.target.files?.[0]) }} />
      )}
      <Dialog open={preview != null} onClose={reset} title="匯入預覽" width={720} closeButton locked={busy}>
        {preview && (
          <>
            {!preview.registry_version_match && (
              <div className={c.banner} data-tone="warn" role="note">
                匯出檔的旗標清單版本（{preview.document_registry_version ?? '未知'}）與這個環境（{preview.registry_version}）不同：
                兩邊程式版本可能不一致，請確認差異後再套用。
              </div>
            )}
            {preview.errors.length > 0 && (
              <div className={c.error} role="alert">
                <p className={s.problemsHead}>有 {preview.errors.length} 個錯誤，整份不能套用：</p>
                <ul className={s.problems}>
                  {preview.errors.map((e, i) => <li key={`${e.key}-${i}`}><code>{e.key ?? '—'}</code>：{e.detail}</li>)}
                </ul>
              </div>
            )}
            <div className={s.previewTable}>
              <table className={c.table}>
                <thead><tr><th>旗標</th><th>動作</th><th>目前</th><th>匯入後</th></tr></thead>
                <tbody>
                  {preview.changes.map((ch) => (
                    <tr key={ch.key}>
                      <td className={`${c.mono} ${c.t1}`}>{ch.key}</td>
                      <td className={ch.action === 'unchanged' ? c.muted : c.nowrap}>{IMPORT_ACTION_LABELS[ch.action]}</td>
                      <td className={s.wrap}>{exportOverrideSummary(ch.before)}</td>
                      <td className={s.wrap}>{ch.action === 'delete' ? '沒有覆寫' : exportOverrideSummary(ch.after)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className={s.hint}>帳號以名稱對應（兩個環境的帳號 UUID 不同）；檔案裡沒列出的旗標不會被動到。</p>
            {error && <p className={c.error} role="alert">{error}</p>}
            <div className={s.previewActions}>
              <Button disabled={busy} onClick={reset}>取消</Button>
              <Button variant="primary" disabled={busy || preview.errors.length > 0 || changed.length === 0}
                onClick={() => { void apply() }}>
                {changed.length === 0 ? '沒有變更' : `套用 ${changed.length} 項變更`}
              </Button>
            </div>
          </>
        )}
      </Dialog>
    </>
  )
}
