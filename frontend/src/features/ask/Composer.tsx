import { useEffect, useRef, type KeyboardEvent } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { TF_DUR, tfInstant } from '../../lib/motionTokens'
import { useWebSearch } from '../../lib/useWebSearch'
import { ComposerTools } from './ComposerTools'
import styles from './Composer.module.css'

interface Props {
  value: string
  onChange: (v: string) => void
  onSubmit: (q: string) => void
  disabled?: boolean
  onStop?: () => void
  variant?: 'center' | 'bottom'
}

export function Composer({ value, onChange, onSubmit, disabled, onStop, variant = 'bottom' }: Props) {
  const reduced = useReducedMotion()
  // 只為了免責文案而讀；開關本身在 ComposerTools 內。直接讀共享 store 而非由
  // AskPage 往下傳：中央（空狀態）與底部兩個 Composer 實例同時存在時，prop 版會
  // 各自持有一份、按了哪個就只有那個亮。
  const web = useWebSearch()
  const ref = useRef<HTMLTextAreaElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 140) + 'px'
  }, [value])

  function fire() {
    const q = value.trim()
    if (!q || disabled) return
    onSubmit(q)
  }
  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); fire() }
  }

  return (
    <div className={`${styles.wrap} ${styles[variant]}`}>
      <div className={styles.box}>
        <ComposerTools />
        <textarea
          ref={ref}
          className={styles.input}
          value={value}
          rows={1}
          placeholder="輸入你的問題…"
          aria-label="輸入你的問題"
          onChange={e => onChange(e.target.value)}
          onKeyDown={onKey}
        />
        <Pressable
          className={styles.send}
          onClick={disabled ? onStop : fire}
          aria-label={disabled ? '停止生成' : '送出'}
          title={disabled ? '停止生成' : '送出'}
        >
          <AnimatePresence mode="wait" initial={false}>
            <motion.span
              key={disabled ? 'stop' : 'send'}
              style={{ display: 'inline-flex' }}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              transition={reduced ? tfInstant : { duration: TF_DUR.d1 }}
            >
              <Icon name={disabled ? 'x' : 'send'} size={disabled ? 18 : 19} />
            </motion.span>
          </AnimatePresence>
        </Pressable>
      </div>
      {variant === 'bottom' && (
        <div className={styles.note}>
          {web
            ? '回答由 AI 依券商研報與網路公開資訊生成，網路資訊非受信任行情來源，投資決策請以原始研報與官方揭露為準。'
            : '回答由 AI 依券商研報生成，投資決策請以原始研報與公開資訊為準。'}
        </div>
      )}
    </div>
  )
}
