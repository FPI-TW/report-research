import { useRef, type CSSProperties, type KeyboardEvent } from 'react'
import styles from './Segmented.module.css'

export interface SegmentedOption<T extends string> {
  value: T
  label: string
}

interface Props<T extends string> {
  /** radiogroup 的可及名稱（畫面上不顯示）。 */
  label: string
  options: ReadonlyArray<SegmentedOption<T>>
  value: T
  onChange: (value: T) => void
  /** 每一格的寬度（px）。滑動指示以 `translateX(索引 × 寬度)` 移動，所以各格必須等寬。 */
  itemWidth: number
}

/**
 * 分段控制（交接包：立場「全部／偏多／中立／偏空」、期間「30／90／180 天／全部」）。
 *
 * 只有一塊白底指示，以 transform 滑到選中那一格；減少動態時直接跳到定位。
 * 鍵盤行為照 radiogroup：只有選中那格在 Tab 順序裡，方向鍵移動並同時選取，Home／End 到兩端。
 */
export function Segmented<T extends string>({ label, options, value, onChange, itemWidth }: Props<T>) {
  const index = Math.max(0, options.findIndex(o => o.value === value))
  const buttons = useRef<Array<HTMLButtonElement | null>>([])

  function onKeyDown(e: KeyboardEvent<HTMLButtonElement>, i: number) {
    const last = options.length - 1
    let next = -1
    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = i === last ? 0 : i + 1
    else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = i === 0 ? last : i - 1
    else if (e.key === 'Home') next = 0
    else if (e.key === 'End') next = last
    if (next < 0) return
    e.preventDefault()
    onChange(options[next].value)
    buttons.current[next]?.focus()
  }

  return (
    <div
      className={styles.seg}
      role="radiogroup"
      aria-label={label}
      style={{ '--seg-w': `${itemWidth}px` } as CSSProperties}
    >
      <span
        className={styles.indicator}
        aria-hidden="true"
        style={{ transform: `translateX(${index * itemWidth}px)` }}
      />
      {options.map((o, i) => (
        <button
          key={o.value}
          ref={el => { buttons.current[i] = el }}
          type="button"
          role="radio"
          aria-checked={i === index}
          tabIndex={i === index ? 0 : -1}
          className={styles.option}
          data-on={i === index || undefined}
          onClick={() => onChange(o.value)}
          onKeyDown={e => onKeyDown(e, i)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
