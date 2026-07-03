import { useState, type KeyboardEvent } from 'react'
import { Icon } from '../../components/primitives/Icon'
import styles from './SearchBar.module.css'

interface Props { initial: string; onSubmit: (q: string) => void }

export function SearchBar({ initial, onSubmit }: Props) {
  const [draft, setDraft] = useState(initial)
  // 父層外部重設 q（如「瀏覽全部」）時同步 draft：於 render 期間調整 state（React 官方
  // 「adjust state during render」模式），避免在 effect 內 setState 造成串聯渲染。
  const [prevInitial, setPrevInitial] = useState(initial)
  if (initial !== prevInitial) {
    setPrevInitial(initial)
    setDraft(initial)
  }
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
        placeholder="搜尋主題、公司、事件…"
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
