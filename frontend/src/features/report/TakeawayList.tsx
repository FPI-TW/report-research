import { Pressable } from '../../components/primitives/Pressable'
import type { Takeaway } from '../../lib/readingSchemas'
import { isJumpable } from './readingFormat'
import styles from './TakeawayList.module.css'

interface Props {
  takeaways: Takeaway[]
  /** 引文可跳＝有 offset 且正典文字未漂移（sha 相符）。 */
  canJump: boolean
  onJump: (ordinal: number) => void
}

/** 跳轉箭頭：常駐（--tf-gold-line），hover 轉 --tf-gold-text。 */
function GoArrow() {
  return (
    <span className={styles.go} aria-hidden="true">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
        <path d="M7 17L17 7M9 7h8v8" />
      </svg>
    </span>
  )
}

function Body({ t }: { t: Takeaway }) {
  return (
    <>
      <span className={styles.n}>{t.ordinal}</span>
      <span className={styles.x}>
        {t.claim}
        {t.quote && <span className={styles.q}>{t.quote}</span>}
      </span>
    </>
  )
}

/** 重點摘錄。takeaways 為空時由呼叫端整區不渲染。 */
export function TakeawayList({ takeaways, canJump, onJump }: Props) {
  return (
    <div className={styles.list}>
      {takeaways.map(t => {
        const jumpable = canJump && isJumpable(t)
        // 錨不到（quote_start 為 null）或文字漂移 → 顯示條目但不可跳，
        // 且不給箭頭 hover 態，避免暗示一個不存在的動作。
        if (!jumpable) {
          return (
            <div key={t.ordinal} className={styles.tk} data-jumpable="false">
              <div className={styles.in}>
                <Body t={t} />
              </div>
            </div>
          )
        }
        return (
          <Pressable
            key={t.ordinal}
            as="div"
            className={`${styles.tk} ${styles.tkOn}`}
            data-jumpable="true"
            hoverScale={1}
            tapScale={0.99}
            role="button"
            tabIndex={0}
            aria-label={`跳至第 ${t.ordinal} 條摘錄的原文位置`}
            onClick={() => onJump(t.ordinal)}
            onKeyDown={e => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onJump(t.ordinal)
              }
            }}
          >
            <div className={styles.in}>
              <Body t={t} />
              <GoArrow />
            </div>
          </Pressable>
        )
      })}
    </div>
  )
}
