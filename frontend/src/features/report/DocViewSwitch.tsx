import { type KeyboardEvent } from 'react'
import { motion } from 'motion/react'
import { Pressable } from '../../components/primitives/Pressable'
import { springThumb } from '../../lib/motionTokens'
import styles from './DocViewSwitch.module.css'

export type DocView = 'pdf' | 'text'

const OPTIONS: { value: DocView; label: string }[] = [
  { value: 'pdf', label: '原文' },
  { value: 'text', label: '文字' },
]

interface Props {
  value: DocView
  onChange: (v: DocView) => void
}

/**
 * [原文][文字] 分段控制；滑動 thumb 走 layoutId（layoutId 需全站唯一）。
 *
 * radiogroup 鍵盤契約：roving tabindex（只有選中的可 Tab 到）+ 方向鍵/Home/End 於
 * 群組內移動並選取。掛了 role=radio 卻不實作這套＝assistive tech 承諾了方向鍵導覽卻
 * 什麼都沒有，且兩顆都進 Tab 序（radiogroup 應只佔一個 Tab 站）。
 */
export function DocViewSwitch({ value, onChange }: Props) {
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const idx = OPTIONS.findIndex(o => o.value === value)
    // 不給初值：下面每個處理到的鍵都會賦值，其餘鍵一律 `default: return`。
    // 給了 `= idx` 反而讓「漏掉某個 case」變成靜默的無反應而非型別錯誤。
    let next: number
    switch (e.key) {
      case 'ArrowRight':
      case 'ArrowDown':
        next = (idx + 1) % OPTIONS.length
        break
      case 'ArrowLeft':
      case 'ArrowUp':
        next = (idx - 1 + OPTIONS.length) % OPTIONS.length
        break
      case 'Home':
        next = 0
        break
      case 'End':
        next = OPTIONS.length - 1
        break
      default:
        return
    }
    e.preventDefault()
    if (next === idx) return
    onChange(OPTIONS[next].value)
    // 焦點跟著選取移到新選中的 radio（Pressable 不轉發 ref，改走 DOM 兄弟節點）。
    const radios = e.currentTarget.parentElement?.querySelectorAll<HTMLElement>('[role="radio"]')
    radios?.[next]?.focus()
  }

  return (
    <div className={styles.seg} role="radiogroup" aria-label="文件檢視">
      {OPTIONS.map(opt => {
        const active = opt.value === value
        return (
          <Pressable
            key={opt.value}
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            hoverScale={1}
            className={`${styles.btn} ${active ? styles.active : ''}`}
            onClick={() => onChange(opt.value)}
            onKeyDown={onKeyDown}
          >
            {active && (
              <motion.span
                layoutId="doc-view-thumb"
                className={styles.thumb}
                transition={springThumb}
                aria-hidden="true"
              />
            )}
            <span className={styles.label}>{opt.label}</span>
          </Pressable>
        )
      })}
    </div>
  )
}
