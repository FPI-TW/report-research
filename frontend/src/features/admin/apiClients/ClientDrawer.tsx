import type { ReactNode } from 'react'
import { Button, Chip, Drawer, DrawerFootNote, DrawerSection, Status } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { marketLabel } from '../../../lib/meta'
import { fmtDateTime } from '../auditLabels'
import type { AdminApiClient } from '../useAdminApiClients'
import { OPTIONAL_DIMS, keyPrefixText } from './clientFields'
import local from '../ApiClients.module.css'

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
      status={<Status tone={client.enabled ? 'ok' : 'idle'}>{client.enabled ? '啟用' : '停用'}</Status>}
      subtitle={<span className={c.mono}>{keyPrefixText(client)}</span>}
      footer={<>
        <DrawerFootNote>建立與輪替金鑰需要重新驗證</DrawerFootNote>
        {client.enabled
          ? <Button variant="dangerText" onClick={act('disable')} disabled={busy}>停用</Button>
          : <Button onClick={act('enable')} disabled={busy}>啟用</Button>}
        <Button variant="primary" onClick={act('rotate')} disabled={busy}>輪替金鑰</Button>
      </>}>
      <DrawerSection title="金鑰">
        <dl className={c.kv}>
          <dt>前綴</dt><dd>{keyPrefixText(client)}</dd>
          <dt>最後輪替</dt><dd>{client.key_rotated_at ? fmtDateTime(client.key_rotated_at) : '建立後沒有輪替過'}</dd>
          <dt>最後使用</dt><dd>{client.last_used_at ? fmtDateTime(client.last_used_at) : '尚未使用'}</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title={<SectionTitle action="調整" label="調整限流與額度" onAction={act('limits')}>限流與額度</SectionTitle>}>
        <dl className={c.kv}>
          <dt>每分鐘限流</dt><dd>{client.rate_limit_per_min.toLocaleString()}／分</dd>
          <dt>每日額度</dt><dd>{client.daily_quota.toLocaleString()}／日</dd>
        </dl>
      </DrawerSection>
      <DrawerSection title={<SectionTitle action="變更" label="變更授權範圍" onAction={act('scope')}>授權範圍</SectionTitle>}>
        <dl className={c.kv}>
          <dt>端點</dt>
          <dd>
            {client.scopes.length
              ? <span className={local.chips}>{client.scopes.map(s => <Chip key={s} mono>{s}</Chip>)}</span>
              : '（無）'}
          </dd>
          <dt>市場</dt>
          <dd><span className={local.chips}>{e.market.map(m => <Chip key={m} title={m}>{marketLabel(m)}</Chip>)}</span></dd>
          {OPTIONAL_DIMS.map(({ key, label }) => (
            <DimRow key={key} label={label} values={e[key]} />
          ))}
        </dl>
      </DrawerSection>
      <DrawerSection title={<SectionTitle action="編輯" label="編輯備註" onAction={act('note')}>備註</SectionTitle>}>
        <p className={local.note}>{client.note || '沒有備註'}</p>
      </DrawerSection>
      <DrawerSection title="紀錄">
        <dl className={c.kv}>
          <dt>建立</dt><dd>{fmtDateTime(client.created_at)}</dd>
          <dt>最後更新</dt><dd>{fmtDateTime(client.updated_at)}</dd>
        </dl>
      </DrawerSection>
    </Drawer>
  )
}

function DimRow({ label, values }: { label: string; values: string[] | null | undefined }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{values?.length ? values.join('、') : '不限'}</dd>
    </>
  )
}
