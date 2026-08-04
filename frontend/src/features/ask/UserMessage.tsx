import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { CopyButton } from '../../components/animate-ui/components/buttons/copy'
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

  // 動作列在氣泡「下方」自成一列，而不是併排在右側：併排時它會一直佔住右邊那段寬度，
  // 氣泡因此永遠貼不到欄位右緣。列本身預設隱形、滑入該則訊息（或鍵盤 focus 進來）才浮現，
  // 比照 ChatGPT；隱形走 opacity 而非 display:none，占位高度留著，浮現時版面不會跳動。
  // 無 hover 的觸控裝置（沒有「滑過」這件事）由 CSS 的 @media (hover: none) 常駐顯示。
  return (
    <div className={styles.row}>
      <div className={styles.bubble}>{text}</div>
      <div className={styles.actions}>
        <CopyButton
          content={text}
          variant="ghost"
          size="xs"
          className={styles.act}
          hoverScale={1.02}
          tapScale={0.94}
          aria-label="複製提問"
          title="複製提問"
        />
        <Pressable
          className={styles.act}
          onClick={() => { setDraft(text); setEditing(true) }}
          aria-label="編輯"
          title="編輯"
          disabled={disabled}
        >
          <Icon name="pencil" size={15} />
        </Pressable>
      </div>
    </div>
  )
}
