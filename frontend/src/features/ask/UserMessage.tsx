import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { Pencil } from 'lucide-react'
import { CopyIconButton } from './CopyIconButton'
import styles from './UserMessage.module.css'
import { t } from '@/i18n'

interface Props {
  text: string
  onEdit: (item: string) => void
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
          aria-label={t('ask.userMessage.editQuestion')}
          disabled={disabled}
        />
        <div className={styles.editActions}>
          <button type="button" className={styles.btn} onClick={() => setEditing(false)} disabled={disabled}>{t('ask.userMessage.cancel')}</button>
          <button type="button" className={`${styles.btn} ${styles.btnPrimary}`} onClick={submitEdit} disabled={disabled}>{t('ask.userMessage.askAgain')}</button>
        </div>
      </div>
    )
  }

  // 動作列在氣泡「下方」自成一列：併排時它會一直佔住右邊那段寬度，氣泡因此永遠貼不到
  // 欄位右緣。列本身預設隱形、滑入該則訊息（或鍵盤 focus 進來）才浮現；隱形走 opacity
  // 而非 display:none，占位高度留著，浮現時版面不會跳動。觸控裝置由 @media (hover: none) 常駐。
  return (
    <div className={styles.row}>
      <div className={styles.bubble}>{text}</div>
      <div className={styles.actions}>
        <CopyIconButton text={text} label={t('ask.userMessage.copyQuestion')} className={styles.act} checkClassName={styles.copied} />
        <button
          type="button"
          className={styles.act}
          onClick={() => { setDraft(text); setEditing(true) }}
          aria-label={t('ask.userMessage.edit')}
          title={t('ask.userMessage.edit')}
          disabled={disabled}
        >
          <Pencil size={15} strokeWidth={1.8} aria-hidden />
        </button>
      </div>
    </div>
  )
}
