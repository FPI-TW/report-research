import { useEffect, useState } from 'react'
import { Icon } from '../../components/primitives/Icon'
import { Sweep } from '../../components/primitives/motionLoops'
import type { ReportState } from '../../lib/askReducer'
import {
  formatElapsed, formatEta, reportEtaMs, reportIsIndeterminate, reportPct, reportStageText,
} from '../../lib/reportProgress'
import styles from './ReportProgress.module.css'

/**
 * 深度研報的生成中畫面。
 *
 * 要解決的是「看起來很像沒有在動」：一份逐節研報要 5–12 分鐘，其中七成以上都在同一個
 * `writing` 階段裡，舊版就是一行「撰寫研報中…」配一根固定 50% 的掃光條。這裡改用後端
 * 真正的章節事件當分子分母，再補上兩個獨立於任何事件的活訊號——每秒跳動的已耗時，以及
 * 逐節點亮的章節清單。就算後端整整兩分鐘沒有事件，畫面上仍有東西在動。
 */
interface Props {
  report: ReportState
  onCancel?: (runId: string) => void
}

export function ReportProgress({ report, onCancel }: Props) {
  const { sections, stage, startedAt, runId, queuePosition } = report
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    // 1 秒一跳：已耗時是唯一保證會動的元素，比進度條更能回答「它還活著嗎」。
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const settled = sections.filter(s => s.state !== 'pending').length
  const progress = {
    stage,
    sectionsTotal: sections.length,
    sectionsDone: settled,
    elapsedMs: startedAt === null ? 0 : Math.max(0, now - startedAt),
  }
  const pct = reportPct(progress)
  const indeterminate = reportIsIndeterminate(progress)
  const eta = formatEta(reportEtaMs(progress))
  const current = sections.find(s => s.state === 'pending')
  const writing = stage === 'writing' || stage === 'searching_web'
  // 排隊中（後端還沒取得併發名額）：研報預設序列化，第二個人可能等上十分鐘。此時
  // 已耗時、章節、百分比全是零，畫面與「壞掉了」長得一模一樣——所以文案要先講實話。
  const queued = queuePosition !== null
  const queueText = queuePosition && queuePosition > 1
    ? `排隊等待中（第 ${queuePosition} 位）`
    : '排隊等待中'

  return (
    <div className={styles.gen}>
      <div className={styles.head}>
        <span className={styles.title}>深度研報生成中</span>
        <span className={styles.timing}>
          <span className={styles.elapsed}>{formatElapsed(progress.elapsedMs)}</span>
          {eta && <span className={styles.eta}>預估還需 {eta}</span>}
        </span>
      </div>

      <div className={styles.track} role="progressbar" aria-label="研報生成進度"
        aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={indeterminate || queued ? undefined : pct}>
        {indeterminate || queued
          ? <Sweep className={styles.indet} barClassName={styles.indetBar} />
          : <div className={styles.fill} style={{ width: `${pct}%` }} />}
      </div>

      <div className={styles.statusLine}>
        <span className={styles.stageText}>
          {queued ? queueText : reportStageText(stage)}
          {!queued && writing && sections.length > 0 && `（${Math.min(settled + 1, sections.length)}/${sections.length}）`}
          {!queued && writing && current && `：${current.heading}`}
        </span>
        {!indeterminate && !queued && <span className={styles.pct}>{pct}%</span>}
      </div>

      {sections.length > 0 && (
        <ol className={styles.sections}>
          {sections.map((s, i) => {
            // 「進行中」＝第一個還沒有結果的節。後端逐節序列執行，故這個推論是穩的。
            const active = writing && s.state === 'pending' && s.position === current?.position
            return (
              <li
                key={`${s.position}-${i}`}
                className={`${styles.section} ${styles[s.state]} ${active ? styles.active : ''}`}
              >
                <span className={styles.dot} aria-hidden="true">
                  {s.state === 'done' && <Icon name="check" size={11} />}
                  {s.state === 'skipped' && <Icon name="notComparable" size={11} />}
                </span>
                <span className={styles.heading}>{s.heading}</span>
                {s.state === 'skipped' && <span className={styles.skipNote}>時間不足，已略過</span>}
              </li>
            )
          })}
        </ol>
      )}

      <div className={styles.foot}>
        {/* 背景執行的整個重點：使用者可以走開。不明說的話，多數人會枯坐著等。 */}
        <span className={styles.hint}>可以離開此頁，生成會在背景繼續，回來自動接回進度。</span>
        {runId && onCancel && (
          <button type="button" className={styles.cancel} onClick={() => onCancel(runId)}>
            取消生成
          </button>
        )}
      </div>
    </div>
  )
}
