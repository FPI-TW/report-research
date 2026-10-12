import { Link } from 'react-router'
import { Card, Chip, Lamp, type LampTone } from '../../../../components/console'
import c from '../../../../components/console/Console.module.css'
import type { Progress } from '../../../../lib/progressSchema'
import { SECTION_ID, attentionLevel, pipelineAttention, type AttentionTarget } from './attention'
import { PIPELINE_ROWS } from './pipelineMeta'
import { PROGRESS_POLL_MS } from './useProgress'
import { fmtInt } from './rate'
import styles from './Pipeline.module.css'

const TARGET_TEXT: Record<AttentionTarget, string> = { schedule: '看排程與執行', quality: '看品質' }

const VERDICT: Record<'ok' | 'warn' | 'bad', { lamp: LampTone; text: (n: number) => string }> = {
  ok: { lamp: 'ok', text: () => '管線正常' },
  warn: { lamp: 'warn', text: n => `需要注意・${n} 項` },
  bad: { lamp: 'bad', text: n => `有異常・${n} 項` },
}

/**
 * 結論：一句話＋逐條原因（判準見 attention.ts）＋中性的「待人看」。
 * `stale`：重抓失敗、畫面還是上一筆（keepPreviousData）時明說，不讓舊資料冒充即時。
 * 「重新整理」在頁首（`OpsPipelinePage` 的 PageHeader）。
 */
export function PipelineSummary({ progress: p, stale }: { progress: Progress; stale: boolean }) {
  const items = pipelineAttention(p)
  const level = attentionLevel(items)
  const running = PIPELINE_ROWS.filter(r => r.key !== 'web' && p.pipelines[r.key]).length
  const qa = p.evaluation?.qa
  const review: string[] = []
  if (qa) review.push(`忠實度低於門檻 ${fmtInt(qa.below_min)} 筆`)
  if (p.extraction) review.push(`抽取品質需複核 ${fmtInt(p.extraction.needs_review)} 篇`)

  return (
    <Card title="管線狀態" labelledBy="pipeline-summary-title">
      <div className={styles.body}>
        <div className={styles.verdictRow}>
          <div className={styles.verdict}>
            {/* 結論的文字已經說明狀態，燈號當裝飾 */}
            <Lamp tone={VERDICT[level].lamp} label="" size={8} />
            <span className={styles.verdictText}>{VERDICT[level].text(items.length)}</span>
          </div>
          <p className={c.meta}>
            <span>更新於 <b>{p.ts}</b>（每 {PROGRESS_POLL_MS / 1000} 秒，分頁在背景時暫停）</span>
            <span>執行中的批次 <b>{running}</b></span>
            <span>研報 <b>{fmtInt(p.db.reports)}</b> 篇</span>
          </p>
        </div>
        {stale && (
          <p className={c.banner} data-tone="warn" role="alert">
            最近一次更新失敗，畫面是上一次成功取得的資料；會在下一輪自動重試。
          </p>
        )}
        {items.length > 0 && (
          <ul className={styles.items} aria-label="需要處理的項目">
            {items.map(it => (
              <li key={it.text} className={`${c.banner} ${styles.item}`} data-tone={it.level}>
                <Chip tone={it.level}>{it.level === 'bad' ? '異常' : '注意'}</Chip>
                <span className={styles.itemText}>{it.text}</span>
                <a className={c.link} href={`#${SECTION_ID[it.target]}`}>{TARGET_TEXT[it.target]}</a>
              </li>
            ))}
          </ul>
        )}
        {review.length > 0 && (
          <p className={styles.review}>
            <span>待人看：</span>
            {review.map(t => <span key={t}>{t}</span>)}
            <Link className={c.link} to="/admin/reviews">前往待複核</Link>
          </p>
        )}
      </div>
    </Card>
  )
}
