import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { TF_DUR, tfInstant } from '../../lib/motionTokens'
import { useWebSearch, WEB_SEARCH_PAUSED } from '../../lib/useWebSearch'
import { ComposerTools } from './ComposerTools'
import styles from './Composer.module.css'

// 輸入框長高的上限（超過就內部捲動）。CSS 的 max-height 必須與它一致，否則會出現
// 「捲軸關掉了但內容其實已被 max-height 切掉」——那是完全看不見的截斷。
const MAX_INPUT_HEIGHT = 140

// AI 生成揭露（DeepSeek 遷移 PR-U）：DeepSeek 條款要求向終端使用者揭露並標示 AI 生成，
// 所以兩個變體都顯示——空狀態（中央）是第一題送出前唯一看得到的輸入框。
// 網搜版不寫供應商：網搜仍走另一個模型（`ASK_WEB_MODEL`），寫 DeepSeek 會是錯的。
export const AI_NOTE =
  '回答由 AI（DeepSeek）依券商研報內容生成，可能有誤，投資決策請以原始研報與公開資訊為準。'
export const AI_NOTE_WEB =
  '回答由 AI 依券商研報與網路公開資訊生成，可能有誤；網路資訊非受信任行情來源，投資決策請以原始研報與官方揭露為準。'

/** 單行文字的高度（px）。
 *
 * `getComputedStyle().lineHeight` 不同引擎回的東西不一樣：瀏覽器多半解析成
 * `'22.4px'`，jsdom 原樣回 CSS 寫的無單位倍數 `'1.6'`，未設時則是 `'normal'`。
 * 直接 parseFloat 會在後兩種情況拿到 1.6 或 NaN——1.6 特別惡劣，它不會報錯，
 * 只會讓「換行了沒」的門檻塌到幾乎恆真，於是輸入框打第一個字就跳成兩列。
 */
function lineHeightPx(cs: CSSStyleDeclaration): number {
  const fontSize = parseFloat(cs.fontSize) || 14
  const raw = parseFloat(cs.lineHeight)
  if (!Number.isFinite(raw)) return fontSize * 1.6  // 'normal' 或空字串
  return raw < 4 ? raw * fontSize : raw             // 小於 4 只可能是無單位倍數
}

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
  // 網搜暫停期間（WEB_SEARCH_PAUSED）殘留的開啟偏好不算數：請求一律送 web=false，文案也跟著。
  const web = useWebSearch() && !WEB_SEARCH_PAUSED
  const ref = useRef<HTMLTextAreaElement>(null)
  // 文字換到第二行之後版面要改成兩列（見 .multiline）。判準只能是**量出來的高度**：
  // 字數在 CJK／英數混排與不同視窗寬度下換行的時機完全不同，數字數一定會錯。
  const [multiline, setMultiline] = useState(false)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.style.height = 'auto'
    const content = el.scrollHeight
    el.style.height = Math.min(content, MAX_INPUT_HEIGHT) + 'px'
    // 沒到上限就關掉捲軸：高度被設成 scrollHeight 之後兩者理論上相等，但行盒是
    // 22.4px 這種非整數，四捨五入差一點就會讓 textarea 判定「內容超出」而畫出一條
    // 捲軸（打第一個字就冒出來，看起來像故障）。到上限時才需要真的能捲。
    el.style.overflowY = content > MAX_INPUT_HEIGHT ? 'auto' : 'hidden'
    // 門檻由 computed style 推而不是寫死常數：行高與內距都在 Composer.module.css，
    // 寫死的話那邊一改，這裡就會安靜地變成「永遠單行」或「永遠兩列」。
    const cs = getComputedStyle(el)
    const pad = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0)
    setMultiline(content > lineHeightPx(cs) * 1.5 + pad)  // 1.5 行＝介於一行與兩行之間
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
      <div className={`${styles.box} ${multiline ? styles.multiline : ''}`}>
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
              <Icon name={disabled ? 'x' : 'send'} size={disabled ? 15 : 16} />
            </motion.span>
          </AnimatePresence>
        </Pressable>
      </div>
      <div className={styles.note}>{web ? AI_NOTE_WEB : AI_NOTE}</div>
    </div>
  )
}
