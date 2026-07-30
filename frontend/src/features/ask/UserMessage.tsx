import { useState } from 'react'
import { Pressable } from '../../components/primitives/Pressable'
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
      <div className={styles.editBox}>
        <textarea
          className={styles.textarea}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          aria-label="編輯提問"
          disabled={disabled}
        />
        <div className={styles.editActions}>
          <Pressable onClick={() => setEditing(false)} aria-label="取消" disabled={disabled}>取消</Pressable>
          <Pressable
            onClick={() => {
              const v = draft.trim()
              if (v) { onEdit(v); setEditing(false) }
            }}
            aria-label="送出"
            disabled={disabled}
          >
            送出
          </Pressable>
        </div>
      </div>
    )
  }

  // 編輯鈕在氣泡「下方」自成一列，而不是併排在右側：併排時它會一直佔住右邊那段寬度，
  // 氣泡因此永遠貼不到欄位右緣。移到下方後也就不必再靠 hover 才顯示（隱形但可點的按鈕，
  // 且留白會撐開版面），改為常駐弱化樣式，與助理訊息下方的動作列一致。
  return (
    <div className={styles.row}>
      <div className={styles.bubble}>{text}</div>
      <Pressable
        className={styles.editBtn}
        onClick={() => { setDraft(text); setEditing(true) }}
        aria-label="編輯"
        title="編輯"
        disabled={disabled}
      >
        編輯
      </Pressable>
    </div>
  )
}
