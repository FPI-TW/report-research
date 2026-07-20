import { useEffect, useMemo, useRef } from 'react'
import { useReducedMotion } from 'motion/react'
import { Skeleton } from '../../components/primitives/Skeleton'
import type { ReadingText, Takeaway } from '../../lib/readingSchemas'
import { buildTextSegments, quoteAttr, type HitRange } from './readingFormat'
import styles from './TextPane.module.css'

export interface JumpTarget {
  /** 摘錄的 ordinal，或 'hit'（?chunk= 帶進來的檢索命中段）。 */
  target: number | 'hit'
  /** 重複跳同一個目標也要重播，故帶遞增 nonce。 */
  nonce: number
}

interface Props {
  text: ReadingText | undefined
  takeaways: Takeaway[]
  /** 正典文字未漂移（doc.text_sha256 === text.text_sha256）才上引文標記。 */
  canJump: boolean
  /** 檢索命中段的字元區間；null＝沒帶 ?chunk 或後端錨不到 → 不標。 */
  hit: HitRange | null
  jump: JumpTarget | null
  isLoading: boolean
  isError: boolean
}

/** 文字檢視：正典文字＋依後端 offset 標出的引文段與檢索命中段。 */
export function TextPane({ text, takeaways, canJump, hit, jump, isLoading, isError }: Props) {
  const reduced = useReducedMotion()
  const stageRef = useRef<HTMLDivElement>(null)
  // 已經執行過的 jump nonce；內容未就緒而暫緩的 jump 不會記進來（見下方 effect）。
  const doneNonceRef = useRef<number | null>(null)

  const segments = useMemo(() => {
    if (!text) return []
    // offset 一律由後端算好，前端只切片。canJump 為否＝正典文字已漂移，整篇不標引文；
    // 命中段不受影響：它的 offset 與 text 出自同一個回應，必然同源。
    return buildTextSegments(text.text, canJump ? takeaways : [], hit)
  }, [text, takeaways, canJump, hit])

  // 正文是否已在 DOM 上。載入／錯誤分支既沒有可跳的目標、也沒掛 stageRef，
  // 故 jump 在那兩個狀態下一律無法執行，只能等就緒後重放。
  const ready = segments.length > 0

  useEffect(() => {
    // 內容未就緒就先擱著：**不可**記成已執行，否則就緒後不會補跳。
    // 這是「從預設的原文檢視點摘錄」的必經路徑 —— 那一刻 /text 才剛開始抓，
    // TextPane 掛載時走的是載入分支。deps 只有 [jump] 時，全文抵達後 jump 物件
    // 沒變、effect 不再執行 → 第一次點永遠不捲動、不 flash（要點第二次才動）。
    // 故 ready 必須進 deps：由 false 轉 true 時重放這個待補的 jump。
    if (!jump || !ready || doneNonceRef.current === jump.nonce) return
    const sel = jump.target === 'hit' ? '[data-hit]' : `[data-q="${quoteAttr(jump.target)}"]`
    const el = stageRef.current?.querySelector(sel)
    if (!(el instanceof HTMLElement)) return
    doneNonceRef.current = jump.nonce
    // jsdom 未實作 scrollIntoView，故守門（缺席時仍套 flash）。
    // ReportPage.test.tsx 會 stub 一顆上去，才驗得到「跳轉真的發生」。
    if (typeof el.scrollIntoView === 'function') {
      el.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' })
    }
    // 命中段本來就有常駐淡底、不再閃一次：flash 的終點色是引文的底色，
    // 套到命中段會在動畫結束移除類名的瞬間閃色。
    if (jump.target === 'hit') return
    // 就地重播：移除 → 強制 reflow → 再加，讓連點同一條也會重新播。
    el.classList.remove(styles.flash)
    void el.offsetWidth
    el.classList.add(styles.flash)
    const t = setTimeout(() => el.classList.remove(styles.flash), 1600)
    return () => clearTimeout(t)
  }, [jump, ready, reduced])

  // 只有真的失敗才說失敗：查詢剛啟用、尚未進 fetching 的那一拍 isLoading 仍為 false 而 data 未到，
  // 若把「沒資料」當錯誤會閃出假的錯誤態。故 !text 一律視為載入中。
  if (isError) {
    return (
      <div className={styles.stage}>
        <div className={styles.state} role="status">全文載入失敗，請稍後再試。</div>
      </div>
    )
  }

  if (isLoading || !text) {
    return (
      <div className={styles.stage}>
        <article className={styles.reader} aria-busy="true" data-testid="text-skeleton">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} width={i % 3 === 2 ? '68%' : '100%'} height={14} radius={4} style={{ marginBottom: 12 }} />
          ))}
        </article>
      </div>
    )
  }

  return (
    <div className={styles.stage} ref={stageRef}>
      <article className={styles.reader}>
        <div className={styles.note}>
          文字檢視為 PDF 抽取結果，圖表與表格排版不會保留 — 需要完整版面請切「原文」。
        </div>
        {/* data-hit 同時掛在命中段的內文與引文上：它是捲動錨點（querySelector 取第一個），
            故引文即使不套 hit 底色（自己已有更明確的引文標記）也要掛，
            否則命中段剛好從引文開頭起算時就會找不到錨點。 */}
        <div className={styles.body}>
          {segments.map(seg =>
            seg.ordinal == null ? (
              <span key={seg.key} className={seg.hit ? styles.hit : undefined} data-hit={seg.hit ? '' : undefined}>
                {seg.text}
              </span>
            ) : (
              <mark
                key={seg.key}
                className={styles.quote}
                data-q={quoteAttr(seg.ordinal)}
                data-hit={seg.hit ? '' : undefined}
              >
                {seg.text}
              </mark>
            ),
          )}
        </div>
        {text.truncated && (
          <p className={styles.truncated}>
            全文過長，此處僅顯示前段 — 需要完整內容請切「原文」。
          </p>
        )}
      </article>
    </div>
  )
}
