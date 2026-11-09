import { useQueryClient } from '@tanstack/react-query'
import { Fragment, useState } from 'react'
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
import { t } from '@/i18n'

const what = () => t('admin.flags.adminFlagsPage.changeFeatureFlag')

/** 關閉是設定出來的狀態，不是異常：不亮紅燈（燈號只留給異常）。 */
const EFFECTIVE_TONE: Record<FlagItem['effective'], LampTone> = { on: 'ok', scoped: 'warn', off: 'idle' }

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : t('admin.flags.adminFlagsPage.actionFailedTryAgain')
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
      <td title={t('admin.flags.adminFlagsPage.whetherThisFeatureIsCurrently')}>{flag.effective_for_me ? t('admin.flags.adminFlagsPage.on') : t('admin.flags.adminFlagsPage.off')}</td>
      <td className={s.ceilingCell}>
        {/* 不鎖成一行：環境變數名很長時可以在底線後面折行，「開／關」也可以折到下一行。 */}
        <span className={c.mono}>
          {flag.ceiling_env.split('_').map((part, i, all) => (
            <Fragment key={i}>{part}{i < all.length - 1 && <>_<wbr /></>}</Fragment>
          ))} {flag.ceiling ? t('admin.flags.adminFlagsPage.on') : t('admin.flags.adminFlagsPage.off')}
        </span>
        {!flag.ceiling && <div className={s.desc}>{t('admin.flags.adminFlagsPage.enableItInTheEnvironment')}</div>}
      </td>
      <td className={s.override}>
        {overrideSummary(o, flag.default)}
        {o?.note && <div className={`${s.desc} ${c.clamp2}`} title={o.note}>{t('admin.flags.adminFlagsPage.note', { note: o.note })}</div>}
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
        <td className={`${c.tdActions} ${c.stickyRight}`}>
          <RowActions label={t('admin.flags.adminFlagsPage.moreActionsFor', { key: flag.key })}
            primary={{
              label: t('admin.flags.adminFlagsPage.adjust'), onClick: onEdit, disabled: !flag.ceiling,
              hint: flag.ceiling ? undefined : t('admin.flags.adminFlagsPage.environmentCeilingIsOffSet', { ceiling_env: flag.ceiling_env }),
            }}
            more={[o && { label: t('admin.flags.adminFlagsPage.restoreDefault'), onClick: onReset }]} />
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
    await elevated(what(), () => adminApi.setFlag(flag.key, body))
    setEditing(null)
    toast(t('admin.flags.adminFlagsPage.updatedRecordedInTheAudit', { key: flag.key }))
    refresh()
  }

  const reset = async (flag: FlagItem) => {
    setResetBusy(true)
    try {
      await elevated(what(), () => adminApi.clearFlag(flag.key))
      toast(t('admin.flags.adminFlagsPage.restoredToDefault', { key: flag.key }))
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
        summary={t('admin.flags.adminFlagsPage.toggleDerivedFeaturesWithinThe')}
        help={<>
          {t('admin.flags.adminFlagsPage.toggleDerivedFeaturesWithinWhat')}{' '}<code>ops.operate</code>{' '}{t('admin.flags.adminFlagsPage.permissionAndReVerificationAnd')}
        </>}
        actions={<FlagTransfer canOperate={canOperate} elevated={elevated}
          onApplied={(msg) => { toast(msg); refresh() }} />}
      />
      {!canOperate && me.data && (
        <div className={c.banner} role="note">{t('admin.flags.adminFlagsPage.viewingAndExportingAreAllowed')}{' '}<code>ops.operate</code>{' '}{t('admin.flags.adminFlagsPage.permission')}</div>
      )}
      {flags.isPending && <p className={c.empty}>{t('admin.flags.adminFlagsPage.loading')}</p>}
      {flags.isError && <p className={c.error} role="alert">{t('admin.flags.adminFlagsPage.couldnTLoadFeatureFlags', { error: messageOf(flags.error) })}</p>}
      {flags.data && (
        <Card title={t('admin.flags.adminFlagsPage.flag')} labelledBy="flags-title" hint={t('admin.flags.adminFlagsPage.flags', { length: flags.data.items.length })}>
          <div className={c.tableWrap}>
            <table className={c.table}>
              <thead>
                <tr>
                  <th>{t('admin.flags.adminFlagsPage.flag')}</th><th>{t('admin.flags.adminFlagsPage.effective')}</th><th>{t('admin.flags.adminFlagsPage.forMe')}</th><th>{t('admin.flags.adminFlagsPage.envCeiling')}</th><th>{t('admin.flags.adminFlagsPage.override')}</th><th>{t('admin.flags.adminFlagsPage.lastChanged')}</th>
                  {canOperate && <th className={`${c.tdRight} ${c.stickyRight}`}>{t('admin.flags.adminFlagsPage.actions')}</th>}
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
              {t('admin.flags.adminFlagsPage.flagsInTheDatabaseThat')}{flags.data.ignored_keys.map((k) => <code key={k}>{k} </code>)}
            </p>
          )}
        </Card>
      )}
      <FlagEditDialog flag={editing} onClose={() => setEditing(null)} onSave={save} />
      <ConfirmDialog open={resetting != null} tone="safe" busy={resetBusy} confirmLabel={t('admin.flags.adminFlagsPage.restoreDefault')}
        title={resetting ? t('admin.flags.adminFlagsPage.restoreToDefault', { key: resetting.key }) : ''}
        body={t('admin.flags.adminFlagsPage.deletesThisOverrideAndReturns')}
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
      <RequireScope scope="ops.read" title={t('admin.flags.adminFlagsPage.featureFlags')}>
        <AdminFlags />
      </RequireScope>
    </RequireAdmin>
  )
}
