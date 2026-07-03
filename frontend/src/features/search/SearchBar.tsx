import { useEffect, useState, type KeyboardEvent } from 'react'
import { Icon } from '../../components/primitives/Icon'
import styles from './SearchBar.module.css'

interface Props { initial: string; onSubmit: (q: string) => void }

export function SearchBar({ initial, onSubmit }: Props) {
  const [draft, setDraft] = useState(initial)
  useEffect(() => { setDraft(initial) }, [initial])   // 父層外部重設 q（如「瀏覽全部」）時同步
  const submit = () => onSubmit(draft.trim())
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    // React 19：e.isComposing 恆 undefined，必須讀 nativeEvent.isComposing
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); submit() }
  }
  return (
    <div className={styles.bar}>
      <Icon name="search" size={18} className={styles.icon} />
      <input
        className={styles.input}
        value={draft}
        placeholder="搜尋研報主題、個股、產業…"
        aria-label="搜尋研報"
        onChange={e => setDraft(e.target.value)}
        onKeyDown={onKey}
      />
      {draft && (
        <button type="button" className={styles.clear} aria-label="清除搜尋"
          onClick={() => { setDraft(''); onSubmit('') }}>
          <Icon name="x" size={16} />
        </button>
      )}
      <button type="button" className={styles.go} onClick={submit}>搜尋</button>
    </div>
  )
}
