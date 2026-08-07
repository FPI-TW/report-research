import { Icon } from '../../components/primitives/Icon'
import styles from './SelectPill.module.css'

export interface SelectOption<T extends string> {
  value: T
  label: string
}

interface Props<T extends string> {
  /** 前綴（「排序」「篩選」）；同時是這個控制項的可及名稱。 */
  kicker: string
  value: T
  options: ReadonlyArray<SelectOption<T>>
  onChange: (value: T) => void
}

/**
 * 膠囊外觀的下拉，內裡是**原生 `<select>`**。
 *
 * 刻意不用 `Popover` ＋ `MenuItem` 自製一個：那組是給「選單面板」用的，套在單選控制項上
 * 得自己補 roving tabindex、型別選字、`aria-activedescendant`、行動裝置的原生輪盤——
 * 每一項少做都不會報錯，只會讓鍵盤與讀屏使用者少一種操作方式。原生 select 全部免費，
 * 代價只有「選項面板長相由作業系統決定」，而那正是 macOS 風格本來就要的樣子。
 *
 * 前綴刻意畫在 select 外面（`aria-hidden`），選中值才在 select 內：把「排序：」也塞進
 * option 文字會讓每一個選項都以同樣四個字開頭，型別選字直接失效。
 */
export function SelectPill<T extends string>({ kicker, value, options, onChange }: Props<T>) {
  const current = options.find(o => o.value === value)
  return (
    <span className={styles.pill}>
      <span className={styles.kicker} aria-hidden="true">{kicker}</span>
      <span className={styles.value} aria-hidden="true">{current?.label ?? ''}</span>
      <Icon name="chevronDown" size={14} className={styles.chevron} />
      <select
        className={styles.select}
        aria-label={kicker}
        value={value}
        onChange={e => onChange(e.target.value as T)}
      >
        {options.map(o => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    </span>
  )
}
