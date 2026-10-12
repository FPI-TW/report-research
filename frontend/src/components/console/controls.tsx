import type { ButtonHTMLAttributes, ReactNode } from 'react'
import { Icon } from '../primitives/Icon'
import { Lamp, type LampTone } from '../primitives/Lamp'
import c from './Console.module.css'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  /** primary＝金色（主要動作、可逆的確認）；danger＝實心紅（破壞性確認的最後一步）；dangerText＝紅字不填色（危險但不是主要動作）。 */
  variant?: 'default' | 'primary' | 'danger' | 'dangerText'
  /** md＝頁面按鈕 32px；sm＝列內按鈕 26px。 */
  size?: 'md' | 'sm'
}

export function Button({ variant = 'default', size = 'md', className, type = 'button', ...rest }: ButtonProps) {
  const cls = [c.btn, size === 'sm' && c.btnSm, variant === 'primary' && c.btnPrimary, variant === 'danger' && c.btnDanger,
    variant === 'dangerText' && c.btnDangerText, className]
    .filter(Boolean).join(' ')
  return <button type={type} className={cls} {...rest} />
}

export interface SegOption<T extends string> { value: T; label: ReactNode; count?: number | null }

/** 分段切換（單選）。`count` 給了就在標籤後面用等寬字顯示數量；`wrap` 讓選項很多時換行而不是橫向捲。 */
export function Segmented<T extends string>({ options, value, onChange, label, disabled, wrap }: {
  options: readonly SegOption<T>[]; value: T; onChange: (v: T) => void; label: string; disabled?: boolean; wrap?: boolean
}) {
  return (
    <div className={`${c.seg} ${wrap ? c.segWrap : ''}`} role="group" aria-label={label}>
      {options.map(o => (
        <button key={o.value} type="button" className={c.segBtn} aria-pressed={o.value === value} disabled={disabled}
          onClick={() => onChange(o.value)}>
          {o.label}{o.count != null && <span className={c.segCount}>{o.count}</span>}
        </button>
      ))}
    </div>
  )
}

/** 分頁籤（底線式）。內容區自己依 `value` 切換；`count` 是該分頁的筆數。 */
export function Tabs<T extends string>({ tabs, value, onChange, label }: {
  tabs: readonly SegOption<T>[]; value: T; onChange: (v: T) => void; label: string
}) {
  return (
    <div className={c.tabs} role="tablist" aria-label={label}>
      {tabs.map(t => (
        <button key={t.value} type="button" role="tab" className={c.tab} aria-selected={t.value === value} onClick={() => onChange(t.value)}>
          {t.label}{t.count != null && <span className={c.segCount}>{t.count}</span>}
        </button>
      ))}
    </div>
  )
}

export function SearchInput({ value, onChange, placeholder, label, width }: {
  value: string; onChange: (v: string) => void; placeholder: string; label?: string; width?: number
}) {
  return (
    <label className={c.searchBox} style={width ? { width } : undefined}>
      <Icon name="search" size={14} />
      <input value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} aria-label={label ?? placeholder}
        autoComplete="off" spellCheck={false} />
    </label>
  )
}

export type Tone = LampTone | 'gold'

/** 小標籤（你／super／2FA、依賴、原因）。`mono` 用等寬字（服務名、代號）。 */
export function Chip({ tone, mono, children, title }: { tone?: Tone; mono?: boolean; children: ReactNode; title?: string }) {
  return <span className={`${c.chip} ${mono ? c.chipMono : ''}`} data-tone={tone} title={title}>{children}</span>
}

/** 狀態：燈號＋文字。文字已經說明狀態，燈號當裝飾。 */
export function Status({ tone, children }: { tone: LampTone; children: ReactNode }) {
  return <span className={c.status} data-tone={tone}><Lamp tone={tone} label="" />{children}</span>
}

/** 卡片：panel 底＋l1 外框＋8px 圓角；有 `title` 就畫標題列（13px/600），`right` 放在標題列右側。 */
export function Card({ title, hint, right, children, labelledBy, className }: {
  title?: ReactNode; hint?: ReactNode; right?: ReactNode; children: ReactNode; labelledBy?: string; className?: string
}) {
  return (
    <section className={`${c.card} ${className ?? ''}`} aria-labelledby={title ? labelledBy : undefined}>
      {title && (
        <div className={c.cardHead}>
          <h2 id={labelledBy} className={c.cardTitle}>{title}</h2>
          {hint && <span className={c.cardHint}>{hint}</span>}
          {right && <div className={c.cardRight}>{right}</div>}
        </div>
      )}
      {children}
    </section>
  )
}

/**
 * 數字磚：標籤＋等寬大數字（＋單位）＋一行補充。
 * `name` 給了就是一個具名的群組（`role="group"`，會變動的狀態用 `role="status"`），測試與輔助科技找得到這一格；
 * 補充文字是單行截斷，`subTitle` 放完整內容。
 */
export function StatTile({ label, value, unit, sub, subTitle, tone, lamp, name, role }: {
  label: ReactNode; value: ReactNode; unit?: string; sub?: ReactNode; subTitle?: string
  tone?: 'ok' | 'warn' | 'bad'; lamp?: LampTone; name?: string; role?: 'group' | 'status'
}) {
  return (
    <div className={c.tile} role={name ? role ?? 'group' : role} aria-label={name}>
      <span className={c.tileLabel}>{lamp && <Lamp tone={lamp} />}{label}</span>
      <span className={c.tileValue} data-tone={tone}>{value}{unit && <span className={c.tileUnit}>{unit}</span>}</span>
      {sub != null && <span className={c.tileSub} title={subTitle}>{sub}</span>}
    </div>
  )
}

/** 表單欄位：上方 12px 標籤（右側可放字數計數），下面放輸入元件（用 `c.input`／`c.textarea`）。 */
export function Field({ label, counter, children }: { label: ReactNode; counter?: ReactNode; children: ReactNode }) {
  return (
    <label className={c.field}>
      <span className={c.fieldLabel}>{label}{counter != null && <span className={c.counter}>{counter}</span>}</span>
      {children}
    </label>
  )
}

/**
 * 一組控制項的欄位（分段、一排勾選框）：外觀和 `Field` 一樣，但不是 `<label>`——
 * `<label>` 包著好幾顆按鈕時，點標題會誤觸第一顆。
 */
export function FieldGroup({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <div className={c.field} role="group" aria-label={label}>
      <span className={c.fieldLabel}>{label}</span>
      {children}
      {hint && <p className={c.fieldHint}>{hint}</p>}
    </div>
  )
}

/** 表單裡的一個勾選項（調整權限的 scope、用戶端的端點與市場）。一排勾選項外面包 `c.checkGroup`。 */
export function CheckRow({ checked, onChange, disabled, children }: {
  checked: boolean; onChange: (on: boolean) => void; disabled?: boolean; children: ReactNode
}) {
  return (
    <label className={c.checkRow} data-disabled={disabled || undefined}>
      <input type="checkbox" className={c.check} checked={checked} disabled={disabled} onChange={e => onChange(e.target.checked)} />
      {children}
    </label>
  )
}
