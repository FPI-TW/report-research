import { useEffect, useRef, useState, type ChangeEvent, type CompositionEvent, type KeyboardEvent } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import styles from './SearchBar.module.css'

interface Props {
  initial: string
  onSubmit: (q: string) => void
  /** lg：檢索台主角大框（附 Enter 鍵帽）；md：結果頁精簡列。預設 md。 */
  size?: 'md' | 'lg'
}

const DEBOUNCE_MS = 350

export function SearchBar({ initial, onSubmit, size = 'md' }: Props) {
  const [draft, setDraft] = useState(initial)
  const [lastSubmitted, setLastSubmitted] = useState(initial.trim()) // 最近一次自身送出的值（state 才能於 render 讀取）
  const composing = useRef(false)                                     // IME 組字中
  const timer = useRef<number | undefined>(undefined)

  // 父層外部重設 q（如「瀏覽全部」清空）時同步 draft：render 期間調整 state（React 官方
  // 「adjust state during render」模式）。自身 debounce 送出造成的 initial 回聲（initial ===
  // 最近送出值）則略過，避免把使用者正在輸入的字覆寫掉。
  const [prevInitial, setPrevInitial] = useState(initial)
  if (initial !== prevInitial) {
    setPrevInitial(initial)
    if (initial !== lastSubmitted) setDraft(initial)
  }

  useEffect(() => () => window.clearTimeout(timer.current), [])

  const fire = (v: string) => {
    window.clearTimeout(timer.current)
    const q = v.trim()
    setLastSubmitted(q)
    onSubmit(q)
  }
  const schedule = (v: string) => {
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => fire(v), DEBOUNCE_MS)
  }

  const onChange = (e: ChangeEvent<HTMLInputElement>) => {
    const v = e.target.value
    setDraft(v)
    if (!composing.current) schedule(v)           // 組字中不排程，免對中間注音/拼音發搜尋
  }
  const onCompositionStart = () => { composing.current = true }
  const onCompositionEnd = (e: CompositionEvent<HTMLInputElement>) => {
    composing.current = false
    schedule((e.target as HTMLInputElement).value) // 選字完成才搜
  }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    // Enter 立即送出（取消待觸發的 debounce）；React 19：e.isComposing 恆 undefined，
    // 需讀 nativeEvent.isComposing，避免 IME 確認選字的 Enter 誤送。
    if (e.key === 'Enter' && !e.nativeEvent.isComposing) { e.preventDefault(); fire(draft) }
  }
  const clear = () => { setDraft(''); fire('') }

  return (
    <div className={`${styles.bar} ${size === 'lg' ? styles.lg : ''}`}>
      <Icon name="search" size={size === 'lg' ? 20 : 18} className={styles.icon} />
      <input
        className={styles.input}
        value={draft}
        placeholder={size === 'lg' ? '搜尋產業、公司、事件…' : '搜尋主題、公司、事件…'}
        aria-label="搜尋研報"
        onChange={onChange}
        onCompositionStart={onCompositionStart}
        onCompositionEnd={onCompositionEnd}
        onKeyDown={onKey}
      />
      {draft && (
        <Pressable className={styles.clear} aria-label="清除搜尋" onClick={clear}>
          <Icon name="x" size={16} />
        </Pressable>
      )}
      {/* 取代「搜尋」按鈕：告訴你怎麼送出，而不是多一顆鈕 */}
      {size === 'lg' && <kbd className={styles.key} aria-hidden="true">Enter</kbd>}
    </div>
  )
}
