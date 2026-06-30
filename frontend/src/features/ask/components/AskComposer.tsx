import { useState } from 'react'
import styles from './AskPage.module.css'

interface AskComposerProps {
  onSend: (q: string) => void
  disabled?: boolean
  examples?: string[]
}

export function AskComposer({ onSend, disabled, examples = [] }: AskComposerProps) {
  const [value, setValue] = useState('')
  const submit = (text?: string) => {
    const q = (text ?? value).trim()
    if (!q || disabled) return
    onSend(q)
    setValue('')
  }
  return (
    <div className={styles.composer}>
      {examples.length > 0 && (
        <div className={styles.examples}>
          {examples.map((ex) => (
            <button key={ex} type="button" className={styles.ex} onClick={() => submit(ex)}>
              {ex}
            </button>
          ))}
        </div>
      )}
      <textarea
        data-testid="ask-input"
        className={styles.input}
        value={value}
        placeholder="輸入你的問題…"
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault()
            submit()
          }
        }}
      />
      <button type="button" data-testid="ask-send" className={styles.send} disabled={disabled} onClick={() => submit()}>
        送出
      </button>
    </div>
  )
}
