import type { KeyboardEvent } from 'react'

type ViewValue = 'group' | 'table'

interface ViewSwitchProps {
  value: ViewValue
  onChange: (v: ViewValue) => void
}

const OPTIONS: { value: ViewValue; label: string }[] = [
  { value: 'group', label: '列表' },
  { value: 'table', label: '表格' },
]

export function ViewSwitch({ value, onChange }: ViewSwitchProps) {
  function handleKeyDown(e: KeyboardEvent<HTMLButtonElement>, current: ViewValue) {
    const currentIdx = OPTIONS.findIndex((o) => o.value === current)
    let nextIdx: number | null = null

    if (e.key === 'ArrowRight' || e.key === 'ArrowDown') {
      nextIdx = (currentIdx + 1) % OPTIONS.length
    } else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') {
      nextIdx = (currentIdx - 1 + OPTIONS.length) % OPTIONS.length
    } else if (e.key === 'Enter' || e.key === ' ') {
      onChange(current)
      e.preventDefault()
      return
    }

    if (nextIdx !== null) {
      e.preventDefault()
      onChange(OPTIONS[nextIdx].value)
      // Move focus to the newly activated button
      const group = (e.currentTarget as HTMLElement).closest('[role="radiogroup"]')
      if (group) {
        const buttons = group.querySelectorAll<HTMLButtonElement>('button[role="radio"]')
        buttons[nextIdx]?.focus()
      }
    }
  }

  return (
    <div role="radiogroup" aria-label="檢視方式" className="view-switch">
      {OPTIONS.map((opt) => {
        const isChecked = opt.value === value
        return (
          <button
            key={opt.value}
            type="button"
            role="radio"
            aria-checked={isChecked}
            tabIndex={isChecked ? 0 : -1}
            onClick={() => onChange(opt.value)}
            onKeyDown={(e) => handleKeyDown(e, opt.value)}
          >
            {opt.label}
          </button>
        )
      })}
    </div>
  )
}
