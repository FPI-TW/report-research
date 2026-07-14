import { useState } from 'react'
import styles from './UserMessage.module.css'

interface Props {
  text: string
  onEdit: (t: string) => void
  disabled?: boolean
}

export function UserMessage({ text, onEdit, disabled = false }: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(text)

  if (editing) {
    return (
      <div className={styles.row}>
        <div className={styles.editBox}>
          <textarea
            className={styles.textarea}
            value={draft}
            onChange={e => setDraft(e.target.value)}
            aria-label="編輯提問"
            disabled={disabled}
          />
          <div className={styles.editActions}>
            <button type="button" onClick={() => setEditing(false)} aria-label="取消" disabled={disabled}>取消</button>
            <button
              type="button"
              onClick={() => {
                const v = draft.trim()
                if (v) { onEdit(v); setEditing(false) }
              }}
              aria-label="送出"
              disabled={disabled}
            >
              送出
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className={styles.row}>
      <div className={styles.bubble}>{text}</div>
      <button
        type="button"
        className={styles.editBtn}
        onClick={() => { setDraft(text); setEditing(true) }}
        aria-label="編輯"
        title="編輯"
        disabled={disabled}
      >
        編輯
      </button>
    </div>
  )
}
