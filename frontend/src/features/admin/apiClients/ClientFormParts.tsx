import type { ReactNode } from 'react'
import { Field } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { MARKET_ORDER, marketLabel } from '../../../lib/meta'
import type { ApiClientScope } from '../useAdminApiClients'
import {
  NOTE_MAX, OPTIONAL_DIMS, QUOTA_RANGE, RATE_RANGE, SCOPE_LABELS, SCOPE_ORDER, type DimText,
} from './clientFields'
import local from '../ApiClients.module.css'

const toggle = <T extends string>(list: readonly T[], v: T): T[] => (list.includes(v) ? list.filter(x => x !== v) : [...list, v])

function CheckGroup({ legend, grid, children }: { legend: string; grid?: boolean; children: ReactNode }) {
  return (
    <fieldset className={local.group}>
      <legend className={local.legend}>{legend}</legend>
      <div className={grid ? local.checkGrid : local.checkList}>{children}</div>
    </fieldset>
  )
}

/** 可呼叫的端點（key scope）。可以一個都不選：這把金鑰就什麼端點都打不了。 */
export function ScopeChecks({ value, onChange }: { value: ApiClientScope[]; onChange: (v: ApiClientScope[]) => void }) {
  return (
    <CheckGroup legend="可呼叫的端點">
      {SCOPE_ORDER.map(s => (
        <label key={s} className={local.checkItem}>
          <input type="checkbox" className={c.check} checked={value.includes(s)} onChange={() => onChange(toggle(value, s))} />
          {SCOPE_LABELS[s]}
        </label>
      ))}
    </CheckGroup>
  )
}

export function MarketChecks({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <CheckGroup legend="授權市場（必選，至少一個）" grid>
      {MARKET_ORDER.map(m => (
        <label key={m} className={local.checkItem}>
          <input type="checkbox" className={c.check} checked={value.includes(m)} onChange={() => onChange(toggle(value, m))} />
          {marketLabel(m)}（{m}）
        </label>
      ))}
    </CheckGroup>
  )
}

/** 券商、報告類型、商品類型：留空＝不限。 */
export function DimFields({ value, onChange }: { value: DimText; onChange: (v: DimText) => void }) {
  return (
    <>
      {OPTIONAL_DIMS.map(({ key, label, example }) => (
        <Field key={key} label={`${label}（留空＝不限；多個以頓號或逗號分隔）`}>
          <input className={c.input} value={value[key]} autoComplete="off" placeholder={`例如：${example}`}
            onChange={e => onChange({ ...value, [key]: e.target.value })} />
        </Field>
      ))}
    </>
  )
}

export function LimitFields({ rate, quota, onRate, onQuota }: {
  rate: string; quota: string; onRate: (v: string) => void; onQuota: (v: string) => void
}) {
  return (
    <div className={local.pair}>
      <Field label={`每分鐘限流（${RATE_RANGE[0]}–${RATE_RANGE[1]}）`}>
        <input className={`${c.input} ${c.inputMono}`} type="number" inputMode="numeric" min={RATE_RANGE[0]} max={RATE_RANGE[1]}
          value={rate} onChange={e => onRate(e.target.value)} />
      </Field>
      <Field label={`每日額度（${QUOTA_RANGE[0]}–${QUOTA_RANGE[1].toLocaleString()}）`}>
        <input className={`${c.input} ${c.inputMono}`} type="number" inputMode="numeric" min={QUOTA_RANGE[0]} max={QUOTA_RANGE[1]}
          value={quota} onChange={e => onQuota(e.target.value)} />
      </Field>
    </div>
  )
}

export function NoteField({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <Field label="備註（選填）" counter={`${value.length}／${NOTE_MAX}`}>
      <textarea className={c.textarea} value={value} rows={2} maxLength={NOTE_MAX} onChange={e => onChange(e.target.value)} />
    </Field>
  )
}
