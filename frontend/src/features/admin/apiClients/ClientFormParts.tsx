import { CheckRow, Field, FieldGroup } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { MARKET_ORDER, marketLabel } from '../../../lib/meta'
import type { ApiClientScope } from '../useAdminApiClients'
import {
  NOTE_MAX, OPTIONAL_DIMS, QUOTA_RANGE, RATE_RANGE, SCOPE_LABELS, SCOPE_ORDER, type DimText,
} from './clientFields'
import local from '../ApiClients.module.css'

const toggle = <T extends string>(list: readonly T[], v: T): T[] => (list.includes(v) ? list.filter(x => x !== v) : [...list, v])

/** 可呼叫的端點（key scope）。可以一個都不選：這把金鑰就什麼端點都打不了。 */
export function ScopeChecks({ value, onChange }: { value: ApiClientScope[]; onChange: (v: ApiClientScope[]) => void }) {
  return (
    <FieldGroup label="可呼叫的端點">
      <div className={local.checkList}>
        {SCOPE_ORDER.map(s => (
          <CheckRow key={s} checked={value.includes(s)} onChange={() => onChange(toggle(value, s))}>{SCOPE_LABELS[s]}</CheckRow>
        ))}
      </div>
    </FieldGroup>
  )
}

export function MarketChecks({ value, onChange }: { value: string[]; onChange: (v: string[]) => void }) {
  return (
    <FieldGroup label="授權市場（必選，至少一個）">
      <div className={local.checkGrid}>
        {MARKET_ORDER.map(m => (
          <CheckRow key={m} checked={value.includes(m)} onChange={() => onChange(toggle(value, m))}>{marketLabel(m)}（{m}）</CheckRow>
        ))}
      </div>
    </FieldGroup>
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
