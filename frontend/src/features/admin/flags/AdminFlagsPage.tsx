import { useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import {
  Card, ConfirmDialog, ConsolePage, PageHeader, RowActions, Status, isElevationCancelled, toast, useElevate, type LampTone,
} from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { RequireAdmin } from '../../../components/shell/RequireAdmin'
import { adminApi, type FlagItem, type FlagUpdateRequest } from '../../../lib/generated/adminApi'
import { FEATURES_KEY } from '../../../lib/useFeatures'
import { useHasScope, useMe } from '../../../lib/useMe'
import { fmtDateTime } from '../auditLabels'
import { AUDIT_KEY } from '../useAdmin'
import { RequireScope } from '../RequireScope'
import { FlagEditDialog } from './FlagEditDialog'
import { EFFECTIVE_LABELS, overrideSummary } from './flagText'
import { FlagTransfer } from './FlagTransfer'
import { FLAGS_KEY, useFlags } from './useFlags'
import s from './Flags.module.css'

const WHAT = '變更功能旗標'

/** 關閉是設定出來的狀態，不是異常：不亮紅燈（燈號只留給異常）。 */
const EFFECTIVE_TONE: Record<FlagItem['effective'], LampTone> = { on: 'ok', scoped: 'warn', off: 'idle' }

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

function FlagRow({ flag, canOperate, onEdit, onReset }: {
  flag: FlagItem
  canOperate: boolean
  onEdit: () => void
  onReset: () => void
}) {
  const o = flag.override ?? null
  return (
    <tr>
      <td className={s.keyCell}>
        <div className={`${c.mono} ${c.t1}`}>{flag.key}</div>
        <div className={s.desc}>{flag.description}</div>
      </td>
      <td><Status tone={EFFECTIVE_TONE[flag.effective]}>{EFFECTIVE_LABELS[flag.effective]}</Status></td>
      <td title="這個功能現在對你的帳號是否生效">{flag.effective_for_me ? '開' : '關'}</td>
      <td>
        <span className={`${c.mono} ${c.nowrap}`}>{flag.ceiling_env} {flag.ceiling ? '開' : '關'}</span>
        {!flag.ceiling && <div className={s.desc}>需先在環境檔開啟並重啟 web 才能調整</div>}
      </td>
      <td className={s.wrap}>
        {overrideSummary(o, flag.default)}
        {o?.note && <div className={s.desc}>註記：{o.note}</div>}
      </td>
      <td>
        {o ? (
          <>
            <span className={c.t1}>{o.updated_by ?? '—'}</span>
            <div className={`${s.desc} ${s.when} ${c.num}`}>{fmtDateTime(o.updated_at)}</div>
          </>
        ) : <span className={c.muted}>—</span>}
      </td>
      {canOperate && (
        <td className={c.tdActions}>
          <RowActions label={`${flag.key} 的更多操作`}
            primary={{
              label: '調整', onClick: onEdit, disabled: !flag.ceiling,
              hint: flag.ceiling ? undefined : `環境上限關閉：先在環境檔設 ${flag.ceiling_env}=1 並重啟 web`,
            }}
            more={[o && { label: '恢復預設', onClick: onReset }]} />
        </td>
      )}
    </tr>
  )
}

function AdminFlags() {
  const flags = useFlags()
  const me = useMe()
  const canOperate = useHasScope('ops.operate')
  const client = useQueryClient()
  const { elevated, dialog } = useElevate()
  const [editing, setEditing] = useState<FlagItem | null>(null)
  const [resetting, setResetting] = useState<FlagItem | null>(null)
  const [resetBusy, setResetBusy] = useState(false)

  const refresh = () => {
    void client.invalidateQueries({ queryKey: [...FLAGS_KEY] })
    void client.invalidateQueries({ queryKey: [...AUDIT_KEY] })
    void client.invalidateQueries({ queryKey: [...FEATURES_KEY] })
  }

  const save = async (flag: FlagItem, body: FlagUpdateRequest) => {
    // 錯誤（含取消驗證）往上拋給表單處理；成功才關閉對話框。
    await elevated(WHAT, () => adminApi.setFlag(flag.key, body))
    setEditing(null)
    toast(`已更新「${flag.key}」，已寫入操作紀錄並立即生效`)
    refresh()
  }

  const reset = async (flag: FlagItem) => {
    setResetBusy(true)
    try {
      await elevated(WHAT, () => adminApi.clearFlag(flag.key))
      toast(`「${flag.key}」已恢復預設`)
      refresh()
    } catch (err) {
      if (!isElevationCancelled(err)) toast(messageOf(err), { tone: 'bad' })
    } finally {
      setResetBusy(false)
      setResetting(null)
    }
  }

  return (
    <ConsolePage>
      <PageHeader
        summary="在環境上限內開關派生功能；實際值＝環境上限且開關開啟。"
        help={<>
          在環境設定允許的範圍內開關派生功能。實際值＝環境上限且開關開啟；安全相關設定不在這裡。修改需要 <code>ops.operate</code> 權限並重新驗證，
          每一筆都會留在操作紀錄。環境上限關閉時需先在環境檔開啟並重啟 web 才能調整。
          匯出與匯入用於把測試環境調好的設定搬到正式環境：在測試環境匯出、到正式環境匯入；匯入前先預覽每個旗標的前後差異。
          帳號以名稱對應（兩個環境的帳號 UUID 不同），檔案裡沒列出的旗標不會被動到；環境變數上限不在匯出檔裡，兩邊各自以環境檔設定。
        </>}
        actions={<FlagTransfer canOperate={canOperate} elevated={elevated}
          onApplied={(msg) => { toast(msg); refresh() }} />}
      />
      {!canOperate && me.data && (
        <div className={c.banner} role="note">你可以查看與匯出；修改需要另外授予的 <code>ops.operate</code> 權限。</div>
      )}
      {flags.isPending && <p className={c.empty}>載入中…</p>}
      {flags.isError && <p className={c.error} role="alert">無法載入功能旗標：{messageOf(flags.error)}</p>}
      {flags.data && (
        <Card title="旗標" labelledBy="flags-title" hint={`${flags.data.items.length} 個`}>
          <div className={c.tableWrap}>
            <table className={c.table}>
              <thead>
                <tr>
                  <th>旗標</th><th>實際值</th><th>對我</th><th>環境上限</th><th>覆寫</th><th>最後修改</th>
                  {canOperate && <th />}
                </tr>
              </thead>
              <tbody>
                {flags.data.items.map((f) => (
                  <FlagRow key={f.key} flag={f} canOperate={canOperate}
                    onEdit={() => setEditing(f)} onReset={() => setResetting(f)} />
                ))}
              </tbody>
            </table>
          </div>
          {flags.data.ignored_keys.length > 0 && (
            <p className={s.foot}>
              資料庫裡有程式沒登記的旗標（不生效）：{flags.data.ignored_keys.map((k) => <code key={k}>{k} </code>)}
            </p>
          )}
        </Card>
      )}
      <FlagEditDialog flag={editing} onClose={() => setEditing(null)} onSave={save} />
      <ConfirmDialog open={resetting != null} tone="safe" busy={resetBusy} confirmLabel="恢復預設"
        title={resetting ? `「${resetting.key}」恢復預設？` : ''}
        body="刪除這筆覆寫，回到程式內建的預設值。"
        onConfirm={() => { if (resetting) void reset(resetting) }}
        onCancel={() => setResetting(null)} />
      {dialog}
    </ConsolePage>
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
