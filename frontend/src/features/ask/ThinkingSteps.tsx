import { useState } from 'react'
import { motion } from 'motion/react'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import { Spin } from '../../components/primitives/motionLoops'
import { TF_EASE_OUT } from '../../lib/motionTokens'
import { stagesToSteps } from '../../lib/thinkingStages'
import type { Turn } from '../../lib/askReducer'
import styles from './ThinkingSteps.module.css'

export function ThinkingSteps({ turn }: { turn: Turn }) {
  const live = turn.phase === 'thinking' || turn.phase === 'streaming'
  const [open, setOpen] = useState(true)
  const rawSteps = stagesToSteps(turn.stages, turn.webUsed)
  // stopped 也是終局：少了它，停止後的思考卡會留一顆永遠轉圈的 spinner。
  const terminal = turn.phase === 'done' || turn.phase === 'notice' || turn.phase === 'error' || turn.phase === 'stopped'
  const settled = terminal ? rawSteps.map(s => (s.state === 'active' ? { ...s, state: 'done' as const } : s)) : rawSteps
  // 停止的輪不會再前進：pending 步驟一律收掉，殘留的灰點會讀成「還在等」，
  // 與下方的「已停止生成」互相矛盾（2026-08-03 實際回報的版面缺陷）。
  const steps = turn.phase === 'stopped' ? settled.filter(s => s.state !== 'pending') : settled
  const sec = Math.round((turn.thinkingMs ?? 0) / 1000)
  // 排隊中要說出來。後端在取得併發名額前先送 queued（web/concurrency.py）；沿用
  // 「思考中…」會讓人以為已經在算，實際上一個字都還沒開始跑。
  const queued = turn.queuePosition !== null
  // 停止在思考完成之前（thinkingMs 還沒量到）不能寫「已思考 0 秒」——那是沒發生
  // 過的事；誠實說「思考已中斷」。
  const label = queued
    ? (turn.queuePosition && turn.queuePosition > 1 ? `排隊中（第 ${turn.queuePosition} 位）…` : '排隊中…')
    : live ? '思考中…'
    : turn.phase === 'stopped' && turn.thinkingMs == null ? '思考已中斷'
    : `已思考 ${sec} 秒`
  // 兩個條件缺一不可：stages 空（歷史重播的舊列）時 stagesToSteps 仍回整排
  // pending，只看 steps.length 會顯示一份假清單；stopped 過濾後可能一步不剩，
  // 只看 stages.length 會留下空清單的收合箭頭。
  const hasSteps = turn.stages.length > 0 && steps.length > 0

  return (
    <div className={styles.card}>
      <Pressable className={styles.head} onClick={() => setOpen(o => !o)} hoverScale={1}>
        <span>{label}</span>
        {hasSteps && (
          <motion.span className={styles.chev} animate={{ rotate: open ? 180 : 0 }} transition={{ duration: 0.2, ease: TF_EASE_OUT }}>
            <Icon name="chevronDown" size={15} />
          </motion.span>
        )}
      </Pressable>
      {queued && <div className={styles.queueNote}>伺服器同時處理量已滿，輪到你就會自動開始。</div>}
      {hasSteps && open && (
        <div className={styles.steps}>
          {steps.map(s => (
            <div key={s.key} className={styles.step}>
              {s.state === 'done' && <Icon name="check" size={16} className={styles.done} />}
              {s.state === 'active' && <Spin className={styles.spin}><Icon name="spinner" size={16} /></Spin>}
              {s.state === 'pending' && <span className={styles.dot} />}
              <span className={s.state === 'pending' ? styles.pendingText : undefined}>{s.name}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
