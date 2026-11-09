import type { ReactNode } from 'react'
import { Button, Chip, Drawer, DrawerFootNote, DrawerSection, Status } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { marketLabel } from '../../../lib/meta'
import { fmtDateTime } from '../auditLabels'
import type { AdminApiClient } from '../useAdminApiClients'
import { OPTIONAL_DIMS, keyPrefixText } from './clientFields'
import local from '../ApiClients.module.css'
import { t } from '@/i18n'

export type ClientAction = 'rotate' | 'limits' | 'scope' | 'note' | 'disable' | 'enable'

/** 區段標題＋右側的編輯入口（文字短，無障礙名稱另外給完整的動作名）。 */
function SectionTitle({ children, action, label, onAction }: {
  children: ReactNode; action: string; label: string; onAction: () => void
}) {
  return (
    <span className={local.sectionTitle}>
      {children}
      <button type="button" className={c.linkBtn} aria-label={label} onClick={onAction}>{action}</button>
    </span>
  )
}

/** API 用戶端的詳情抽屜（`?client=<id>`）。內容全部來自清單 API；完整金鑰不在這裡，只有前綴。 */
export function ClientDrawer({ client, busy, onClose, onAction }: {
  client: AdminApiClient | null
  busy: boolean
  onClose: () => void
  onAction: (action: ClientAction, client: AdminApiClient) => void
}) {
  if (!client) return null
  const act = (action: ClientAction) => () => onAction(action, client)
  const e = client.entitlements
  return (
    <Drawer open onClose={onClose} title={client.name} width={520}
      status={<Status tone={client.enabled ? 'ok' : 'idle'}>{client.enabled ? t('admin.apiClients.clientDrawer.enabled') : t('admin.apiClients.clientDrawer.disabled')}</Status>}
      subtitle={<span className={c.mono}>{keyPrefixText(client)}</span>}
      footer={<>
        <DrawerFootNote>{t('admin.apiClients.clientDrawer.creatingAndRotatingKeysRequires')}</DrawerFootNote>
        {client.enabled
          ? <Button variant="dangerText" onClick={act('disable')} disabled={busy}>{t('admin.apiClients.clientDrawer.disabled')}</Button>
          : <Button onClick={act('enable')} disabled={busy}>{t('admin.apiClients.clientDrawer.enabled')}</Button>}
        <Button variant="primary" onClick={act('rotate')} disabled={busy}>{t('admin.apiClients.clientDrawer.rotateKey')}</Button>
      </>}>
      <DrawerSection title={t('admin.apiClients.clientDrawer.key')}>
        <dl className={c.kv}>
          <dt>{t('admin.apiClients.clientDrawer.prefix')}</dt><dd>{keyPrefixText(client)}</dd>
          <dt>{t('admin.apiClients.clientDrawer.lastRotated')}</dt><dd>{client.key_rotated_at ? fmtDateTime(client.key_rotated_at) : t('admin.apiClients.clientDrawer.notRotatedSinceCreation')}</dd>
          <dt>{t('admin.apiClients.clientDrawer.lastUsed')}</dt><dd>{client.last_used_at ? fmtDateTime(client.last_used_at) : t('admin.apiClients.clientDrawer.neverUsed')}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title={<SectionTitle action={t('admin.apiClients.clientDrawer.adjust')} label={t('admin.apiClients.clientDrawer.adjustRateLimitAndQuota')} onAction={act('limits')}>{t('admin.apiClients.clientDrawer.rateLimitAndQuota')}</SectionTitle>}>
        <dl className={c.kv}>
          <dt>{t('admin.apiClients.clientDrawer.rateLimitPerMinute')}</dt><dd>{t('admin.apiClients.clientDrawer.min', { rate_limit_per_min: client.rate_limit_per_min.toLocaleString() })}</dd>
          <dt>{t('admin.apiClients.clientDrawer.dailyQuota')}</dt><dd>{t('admin.apiClients.clientDrawer.day', { daily_quota: client.daily_quota.toLocaleString() })}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title={<SectionTitle action={t('admin.apiClients.clientDrawer.change')} label={t('admin.apiClients.clientDrawer.changeAccessScope')} onAction={act('scope')}>{t('admin.apiClients.clientDrawer.accessScope')}</SectionTitle>}>
        <dl className={c.kv}>
          <dt>{t('admin.apiClients.clientDrawer.endpoints')}</dt>
          <dd>
            {client.scopes.length
              ? <span className={local.chips}>{client.scopes.map(s => <Chip key={s} mono>{s}</Chip>)}</span>
              : t('admin.apiClients.clientDrawer.none')}
          </dd>
          <dt>{t('admin.apiClients.clientDrawer.markets')}</dt>
          <dd><span className={local.chips}>{e.market.map(m => <Chip key={m} title={m}>{marketLabel(m)}</Chip>)}</span></dd>
          {OPTIONAL_DIMS.map(({ key, label }) => (
            <DimRow key={key} label={label} values={e[key]} />
          ))}
        </dl>
      </DrawerSection>
      <DrawerSection title={<SectionTitle action={t('admin.apiClients.clientDrawer.edit')} label={t('admin.apiClients.clientDrawer.editNote')} onAction={act('note')}>{t('admin.apiClients.clientDrawer.note')}</SectionTitle>}>
        <p className={local.note}>{client.note || t('admin.apiClients.clientDrawer.noNote')}</p>
      </DrawerSection>
      <DrawerSection title={t('admin.apiClients.clientDrawer.history')}>
        <dl className={c.kv}>
          <dt>{t('admin.apiClients.clientDrawer.created')}</dt><dd>{fmtDateTime(client.created_at)}</dd>
          <dt>{t('admin.apiClients.clientDrawer.lastUpdated')}</dt><dd>{fmtDateTime(client.updated_at)}</dd>
        </dl>
      </DrawerSection>
    </Drawer>
  )
}

function DimRow({ label, values }: { label: string; values: string[] | null | undefined }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{values?.length ? values.join(t('admin.apiClients.clientDrawer.text')) : t('admin.apiClients.clientDrawer.unrestricted')}</dd>
    </>
  )
}
