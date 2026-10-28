import { useRef, useState, type KeyboardEvent } from 'react'
import { Search, X } from 'lucide-react'
import styles from './SearchBar.module.css'

interface Props {
  /** 已套用的查詢（網址上的 q）。外部清空（「清除條件」）時輸入框跟著清空。 */
  initial: string
  onSubmit: (q: string) => void
}

/**
 * 研報庫搜尋框（56px、圓角 16）。按 Enter 才送出（交接包：Enter 後先顯示骨架），
 * 打字中不發請求——語意檢索每次都要做一次查詢嵌入，逐字送出只是在燒後端。
 *
 * `aria-label="搜尋研報"` 是外殼「按 / 聚焦搜尋框」找這個輸入框的依據（lib/useGlobalShortcuts.ts），不要改。
 */
export function SearchBar({ initial, onSubmit }: Props) {
  const [draft, setDraft] = useState(initial)
  const inputRef = useRef<HTMLInputElement>(null)

  // 外部改了 q（清除條件、瀏覽器上一頁）就同步：render 期間調整 state（React「adjust state during render」）。
  const [prevInitial, setPrevInitial] = useState(initial)
  if (initial !== prevInitial) {
    setPrevInitial(initial)
    setDraft(initial)
  }

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    // React 19 的 e.isComposing 恆 undefined，要讀 nativeEvent：IME 確認選字的 Enter 不送出
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) {
      e.preventDefault()
      onSubmit(draft.trim())
    }
  }
  const clear = () => {
    setDraft('')
    onSubmit('')
    inputRef.current?.focus()
  }

  return (
    <div className={styles.field}>
      <Search size={20} strokeWidth={1.8} className={styles.icon} aria-hidden />
      <input
        ref={inputRef}
        type="search"
        className={styles.input}
        value={draft}
        placeholder="搜尋產業、公司、事件"
        aria-label="搜尋研報"
        enterKeyHint="search"
        onChange={e => setDraft(e.target.value)}
        onKeyDown={onKeyDown}
      />
      {draft && (
        <button type="button" className={styles.clear} aria-label="清除搜尋" onClick={clear}>
          <X size={16} strokeWidth={1.8} aria-hidden />
        </button>
      )}
    </div>
  )
}
