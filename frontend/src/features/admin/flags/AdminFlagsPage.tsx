import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { adminApi, type FlagItem, type FlagUpdateRequest } from '../../../lib/generated/adminApi'
import { FEATURES_KEY } from '../../../lib/useFeatures'
import { useHasScope, useMe } from '../../../lib/useMe'
import { ElevationCancelledError, useElevationGate } from '../../account/useElevationGate'
import { AdminHeader } from '../AdminHeader'
import { fmtDateTime } from '../auditLabels'
import { AUDIT_KEY } from '../useAdmin'
import { RequireScope } from '../RequireScope'
import adminStyles from '../Admin.module.css'
import { FlagEditDialog } from './FlagEditDialog'
import { EFFECTIVE_LABELS, overrideSummary } from './flagText'
import { FlagTransfer } from './FlagTransfer'
import { FLAGS_KEY, useFlags } from './useFlags'
import styles from './Flags.module.css'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

function FlagCard({ flag, canOperate, busy, onEdit, onReset }: {
  flag: FlagItem
  canOperate: boolean
  busy: boolean
  onEdit: () => void
  onReset: () => void
}) {
  const o = flag.override ?? null
  return (
    <article className={styles.flag} aria-labelledby={`flag-${flag.key}`}>
      <header className={styles.flagHead}>
        <h2 id={`flag-${flag.key}`} className={styles.flagKey}><code>{flag.key}</code></h2>
        <span className={flag.effective === 'off' ? `${adminStyles.badge} ${adminStyles.badgeOff}` : adminStyles.badge}>
          {EFFECTIVE_LABELS[flag.effective]}
        </span>
      </header>
      <p className={styles.flagDesc}>{flag.description}</p>
      <dl className={styles.facts}>
        <div>
          <dt>環境上限</dt>
          <dd><code>{flag.ceiling_env}</code> {flag.ceiling ? '開' : '關'}</dd>
        </div>
        <div>
          <dt>覆寫</dt>
          <dd>{overrideSummary(o, flag.default)}</dd>
        </div>
        <div>
          <dt>對我</dt>
          <dd>{flag.effective_for_me ? '開' : '關'}</dd>
        </div>
        {o && (
          <div>
            <dt>最後修改</dt>
            <dd>{o.updated_by ?? '—'}・{fmtDateTime(o.updated_at)}</dd>
          </div>
        )}
      </dl>
      {o?.note && <p className={styles.note}>註記：{o.note}</p>}
      {!flag.ceiling && (
        <p className={adminStyles.hint} role="note">
          環境上限關閉：需先在環境檔開啟（<code>{flag.ceiling_env}=1</code>，重啟 web）才能調整；在那之前這個功能一律關閉。
        </p>
      )}
      {canOperate && (
        <div className={styles.flagActions}>
          <button type="button" className={adminStyles.action} disabled={busy || !flag.ceiling} onClick={onEdit}>調整</button>
          {o && (
            <button type="button" className={adminStyles.action} disabled={busy} onClick={onReset}>恢復預設</button>
          )}
        </div>
      )}
    </article>
  )
}

function AdminFlags() {
  const flags = useFlags()
  const me = useMe()
  const canOperate = useHasScope('ops.operate')
  const client = useQueryClient()
  const { guard, dialog, open: elevating } = useElevationGate(adminApi.elevate, Boolean(me.data?.totp_enabled))
  const [editing, setEditing] = useState<FlagItem | null>(null)
  const [busyKey, setBusyKey] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ msg: string; isError: boolean } | null>(null)

  const refresh = () => {
    void client.invalidateQueries({ queryKey: [...FLAGS_KEY] })
    void client.invalidateQueries({ queryKey: [...AUDIT_KEY] })
    void client.invalidateQueries({ queryKey: [...FEATURES_KEY] })
  }

  const save = async (flag: FlagItem, body: FlagUpdateRequest) => {
    // 錯誤（含取消驗證）往上拋給表單顯示；成功才關閉對話框。
    await guard(() => adminApi.setFlag(flag.key, body))
    setEditing(null)
    setNotice({ msg: `已更新「${flag.key}」，已寫入操作紀錄並立即生效`, isError: false })
    refresh()
  }

  const reset = async (flag: FlagItem) => {
    setBusyKey(flag.key)
    setNotice(null)
    try {
      await guard(() => adminApi.clearFlag(flag.key))
      setNotice({ msg: `「${flag.key}」已恢復預設`, isError: false })
      refresh()
    } catch (err) {
      if (!(err instanceof ElevationCancelledError)) setNotice({ msg: messageOf(err), isError: true })
    } finally {
      setBusyKey(null)
    }
  }

  return (
    <div className={adminStyles.page}>
      <div className={adminStyles.inner}>
        <AdminHeader
          title="功能旗標"
          subtitle="在環境設定允許的範圍內開關派生功能。實際值＝環境上限且開關開啟；安全相關設定不在這裡。修改需要 ops.operate 權限並重新驗證，每一筆都會留在操作紀錄。"
        />
        {notice && (
          <p className={notice.isError ? adminStyles.error : adminStyles.ok} role={notice.isError ? 'alert' : 'status'}>
            {notice.msg}
          </p>
        )}
        {!canOperate && me.data && (
          <p className={adminStyles.hint}>你可以查看與匯出；修改需要另外授予的 <code>ops.operate</code> 權限。</p>
        )}
        {flags.isPending && <p className={adminStyles.idle}>載入中…</p>}
        {flags.isError && <p className={adminStyles.error} role="alert">無法載入功能旗標：{messageOf(flags.error)}</p>}
        {flags.data && (
          <>
            <div className={styles.grid}>
              {flags.data.items.map((f) => (
                <FlagCard key={f.key} flag={f} canOperate={canOperate} busy={busyKey === f.key}
                  onEdit={() => setEditing(f)} onReset={() => void reset(f)} />
              ))}
            </div>
            {flags.data.ignored_keys.length > 0 && (
              <p className={adminStyles.hint}>
                資料庫裡有程式沒登記的旗標（不生效）：{flags.data.ignored_keys.map((k) => <code key={k}>{k} </code>)}
              </p>
            )}
          </>
        )}
        <FlagTransfer canOperate={canOperate} guard={guard}
          onApplied={(msg) => { setNotice({ msg, isError: false }); refresh() }} />
        <FlagEditDialog flag={editing} onClose={() => { if (!elevating) setEditing(null) }} onSave={save} />
        {dialog}
      </div>
    </div>
  )
}

/** 功能旗標（/app/admin/flags；Admin v2 Flags lane）。後端 /api/admin/flags*（ops.read；寫入 ops.operate＋重新驗證）。 */
export default function AdminFlagsPage() {
  return (
    <RequireAdmin>
      <RequireScope scope="ops.read" title="功能旗標">
        <AdminFlags />
      </RequireScope>
    </RequireAdmin>
  )
}
