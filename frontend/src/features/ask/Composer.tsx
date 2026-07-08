import { useEffect, useRef, type KeyboardEvent } from 'react'
import { Icon } from '../../components/primitives/Icon'
import styles from './Composer.module.css'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: (q: string) => void
  disabled?: boolean
  variant?: 'center' | 'bottom'
}

export function Composer({ value, onChange, onSubmit, disabled, variant = 'bottom' }: Props) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 140) + 'px'
  }, [value])

  function fire() {
    const q = value.trim()
    if (!q || disabled) return
    onSubmit(q)
  }
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); fire() }
  }

  return (
    <div className={`${styles.wrap} ${styles[variant]}`}>
      <div className={styles.box}>
        <textarea
          ref={ref}
          className={styles.input}
          value={value}
          rows={1}
          placeholder="輸入你的問題…"
          aria-label="輸入你的問題"
          onChange={e => onChange(e.target.value)}
          onKeyDown={onKey}
        />
        <button type="button" className={styles.send} onClick={fire} disabled={disabled} aria-label="送出" title="送出">
          <Icon name="send" size={20} />
        </button>
      </div>
    </div>
  )
}
