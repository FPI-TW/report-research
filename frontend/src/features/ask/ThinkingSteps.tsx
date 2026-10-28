import { useEffect, useState } from 'react'
import { Check, ChevronDown } from 'lucide-react'
import { stagesToSteps, type ThinkStep } from '../../lib/thinkingStages'
import type { Turn } from '../../lib/askReducer'
import styles from './ThinkingSteps.module.css'

// 開始寫答案之後多久自動收合步驟清單（交接包原型的節奏：讓「生成回答」那一格亮一下再收）。
const COLLAPSE_AFTER_MS = 700

interface Step extends ThinkStep {
  meta?: string
}

/** 思考中計時：自己掛一個 100ms 的計時器，只重畫這一小段，不讓整則訊息跟著每 0.1 秒重算。
 *  起點是掛載當下：每一次進入「思考中」（含重新生成）都是重新掛載。 */
function ThinkClock() {
  const [start] = useState(() => Date.now())
  const [now, setNow] = useState(start)
  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), 100)
    return () => window.clearInterval(id)
  }, [])
  return <span className={styles.clock}>{((now - start) / 1000).toFixed(1)} 秒</span>
}

function SpinArc() {
  return (
    <svg className={styles.spin} width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className={styles.arcTrack} cx="12" cy="12" r="9" strokeWidth="2.4" />
      <path className={styles.arc} d="M21 12a9 9 0 0 0-9-9" strokeWidth="2.4" strokeLinecap="round" />
    </svg>
  )
}

function HaltIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <circle className={styles.haltRing} cx="12" cy="12" r="9" strokeWidth="1.8" />
      <rect className={styles.haltSquare} x="9" y="9" width="6" height="6" rx="1" />
    </svg>
  )
}

interface Props {
  turn: Turn
  /** 目前顯示版本引用的研報篇數（完成後的「參考 N 篇研報」）。 */
  referenced: number
}

/**
 * 回答上方的思考區（交接包「研究助理／思考區」）。
 *
 * - 思考中：金色旋轉弧線＋目前步驟的流光文字＋計時；寫答案時改成「正在撰寫回答」。
 * - 完成後**不放圖示**，只有「已思考 N 秒」與「參考 N 篇研報」；停止或失敗是灰色停止圖示，
 *   思考還沒完成就被打斷時寫「思考已中斷」（不寫沒發生過的「已思考 0 秒」）。
 * - 步驟清單：完成＝石墨圓勾、進行中＝金色脈動點、未到＝空心圓；連接線從本步圖示下緣接到下一步。
 *   終局（完成、停止、失敗）不留空心圓：殘留的未到步驟會讀成「還在等」。
 */
export function ThinkingSteps({ turn, referenced }: Props) {
  const live = turn.phase === 'thinking' || turn.phase === 'streaming'
  const queued = live && turn.queuePosition !== null
  // 思考完成之前就結束（失敗，或停止時 thinkingMs 還沒量到）：最後一格沒有做完，不能標成完成。
  const halted = turn.phase === 'error' || (turn.phase === 'stopped' && turn.thinkingMs == null)

  // 展開狀態：思考中預設展開，寫答案後自動收合，失敗時收合；歷史紀錄掛載時就是收合。
  // 依 phase 的轉變調整，用「render 中比對前值」而不是 effect——前值要確實拿到（交接包坑 5）。
  const [open, setOpen] = useState(live)
  const [prevPhase, setPrevPhase] = useState(turn.phase)
  if (prevPhase !== turn.phase) {
    setPrevPhase(turn.phase)
    if (turn.phase === 'thinking') setOpen(true)
    else if (turn.phase === 'error') setOpen(false)
  }
  useEffect(() => {
    if (turn.phase !== 'streaming') return
    const id = window.setTimeout(() => setOpen(false), COLLAPSE_AFTER_MS)
    return () => window.clearTimeout(id)
  }, [turn.phase])

  const raw: Step[] = stagesToSteps(turn.stages, turn.webUsed).map(s => (
    s.key === 'retrieved' && s.state !== 'pending' && turn.retrievedCount != null
      ? { ...s, meta: `找到 ${turn.retrievedCount} 篇相關研報` }
      : s
  ))
  const steps: Step[] = live
    ? raw
    : halted
      ? raw.filter(s => s.state === 'done')
      : raw.map(s => (s.state === 'active' ? { ...s, state: 'done' as const } : s)).filter(s => s.state !== 'pending')
  // 兩個條件缺一不可：stages 空（歷史重播的舊列）時 stagesToSteps 仍回整排 pending；
  // 終局過濾後也可能一步不剩，只看 stages 會留下沒有內容的收合箭頭。
  const hasSteps = turn.stages.length > 0 && steps.length > 0

  const active = steps.find(s => s.state === 'active')
  let label: string
  let sub: string | null = null
  if (queued) {
    // 後端在取得併發名額前先送 queued（web/concurrency.py）；排第一位不報名次，那只是雜訊。
    const pos = turn.queuePosition ?? 0
    label = pos > 1 ? `排隊中（第 ${pos} 位）…` : '排隊中…'
  } else if (turn.phase === 'thinking') {
    label = active ? `${active.name}…` : '思考中…'
  } else if (turn.phase === 'streaming') {
    label = '正在撰寫回答'
  } else if (halted) {
    label = '思考已中斷'
  } else {
    label = turn.thinkingMs != null ? `已思考 ${Math.max(1, Math.round(turn.thinkingMs / 1000))} 秒` : '思考完成'
    if (referenced > 0) sub = `參考 ${referenced} 篇研報`
  }
  const icon = live ? <SpinArc /> : turn.phase === 'stopped' || turn.phase === 'error' ? <HaltIcon /> : null

  const headContent = (
    <>
      {icon && <span className={styles.icon}>{icon}</span>}
      <span className={`${styles.label} ${live ? styles.shimmer : ''}`}>{label}</span>
      {sub && <span className={styles.sub}>{sub}</span>}
      {turn.phase === 'thinking' && !queued && <ThinkClock />}
      {hasSteps && <ChevronDown size={15} strokeWidth={1.8} aria-hidden className={`${styles.chev} ${open ? styles.chevUp : ''}`} />}
    </>
  )

  return (
    <div className={styles.wrap}>
      {hasSteps ? (
        <button type="button" className={`${styles.head} ${styles.headBtn}`} aria-expanded={open} onClick={() => setOpen(o => !o)}>
          {headContent}
        </button>
      ) : (
        <div className={styles.head}>{headContent}</div>
      )}
      {hasSteps && (
        // 收合時仍留在 DOM 裡（高度動畫要有東西可收），所以用 inert 把它拿出焦點順序與無障礙樹。
        <div className={`${styles.collapse} ${open ? styles.open : ''}`} inert={!open} data-testid="thinking-steps">
          <div className={styles.inner}>
            <ol className={styles.steps}>
              {steps.map((s, i) => (
                <li key={s.key} className={styles.step} data-state={s.state}>
                  {i < steps.length - 1 && <span className={`${styles.line} ${s.state === 'done' ? styles.lineOn : ''}`} aria-hidden="true" />}
                  <span className={styles.ic} aria-hidden="true">
                    {s.state === 'done' && <span className={styles.doneDot}><Check size={11} strokeWidth={3} /></span>}
                    {s.state === 'active' && <span className={styles.activeDot}><span className={styles.ring} /><span className={styles.core} /></span>}
                    {s.state === 'pending' && <span className={styles.pendingDot} />}
                  </span>
                  <span className={styles.name}>{s.name}</span>
                  {s.meta && <span className={styles.meta}>{s.meta}</span>}
                </li>
              ))}
            </ol>
          </div>
        </div>
      )}
    </div>
  )
}
