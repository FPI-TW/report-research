import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
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
  const taRef = useRef<HTMLTextAreaElement>(null)

  // 高度隨內容自動長高（同 Composer 的作法），上限之後改為內部捲動。resize 一律
  // 交給程式：容器把按鈕包在輸入框裡之後，右下角早已不是 resize 把手，而手動拖出
  // 的高度也會跟自動長高互相打架。
  useEffect(() => {
    if (!editing) return
    const el = taRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }, [draft, editing])

  function submitEdit() {
    const v = draft.trim()
    if (v && !disabled) { onEdit(v); setEditing(false) }
  }

  if (editing) {
    return (
      <div className={styles.editBox}>
        <textarea
          ref={taRef}
          rows={1}
          className={styles.textarea}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={(e: KeyboardEvent<HTMLTextAreaElement>) => {
            // isComposing 走 nativeEvent：IME 選字中的 Enter 是在選候選字，不是送出
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); submitEdit() }
            if (e.key === 'Escape') setEditing(false)
          }}
          // 進入編輯游標停在文末（比照 ChatGPT）；autoFocus 在部分瀏覽器落在文首
          onFocus={e => { const el = e.currentTarget; el.setSelectionRange(el.value.length, el.value.length) }}
          autoFocus
          aria-label="編輯提問"
          disabled={disabled}
        />
        <div className={styles.editActions}>
          <Pressable onClick={() => setEditing(false)} aria-label="取消" disabled={disabled}>取消</Pressable>
          <Pressable onClick={submitEdit} aria-label="送出" disabled={disabled}>
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
