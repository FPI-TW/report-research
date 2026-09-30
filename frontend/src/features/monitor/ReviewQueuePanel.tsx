import { useState } from 'react'
import { Link } from 'react-router'
import { displayTitle } from '../../lib/displayTitle'
import type { ReviewItem, ReviewKind, ReviewStatus, ReviewVerification } from '../../lib/reviewSchemas'
import { isNewDeepSeekScale, newScaleText } from './judgeScale'
import type { EvalSource } from './progressSchema'
import { reasonText } from './reviewReasons'
import { useReviewQueue } from './useReviewQueue'
import styles from './MonitorPage.module.css'

/**
 * 待複核佇列。
 *
 * 忠實度卡與抽取品質卡只有**筆數**（「待複核 3」），要知道是哪三筆得開 psql；倒讚則
 * 連筆數都沒有出口。這張卡把三者的個體列出來，每一筆都連得回去：問答連到那一串對話，
 * 研報連到閱讀頁。
 *
 * 人工處理狀態、註記與驗證結果另存；本站是共用帳號，不顯示處理人。
 *
 * `scale` 是監控頁 `/api/progress` 的問答忠實度統計（與忠實度卡同一份）：判定尺剛換成 DeepSeek、
 * 窗期內還有舊尺的列時，忠實度分頁比照忠實度卡標「新量尺」——換尺頭幾天佇列近乎是空的，不說清楚
 * 會被讀成「低分變少了」。佇列本身仍自己取數、不跟 5 秒輪詢（理由見 useReviewQueue）。
 */
const TABS: { kind: ReviewKind; label: string; hint: string }[] = [
  { kind: 'faithfulness', label: '忠實度低分', hint: '近 30 天內抽查分數低於門檻的回答，最低分在前；只列現行判定尺量的分數（換尺前的舊分數不列入）' },
  { kind: 'feedback', label: '倒讚', hint: '近 30 天內使用者按了倒讚的回答' },
  { kind: 'extraction', label: '抽取品質', hint: '抽取品質標為 needs_review 的研報（照樣入庫、可檢索），分數最低在前；右側是被標記的原因' },
]

function fmtDay(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : '—'
}

type ReviewSave = (id: string, update: { status: ReviewStatus; note: string; verification: ReviewVerification }) => Promise<void>

function ReviewEditor({ item, id, save, disabled }: {
  item: ReviewItem; id: string; save: ReviewSave; disabled: boolean
}) {
  const [status, setStatus] = useState<ReviewStatus>(item.review_status ?? 'open')
  const [verification, setVerification] = useState<ReviewVerification>(item.verification ?? 'untested')
  const [note, setNote] = useState(item.review_note ?? '')
  const [error, setError] = useState(false)
  return (
    <form className={styles.rvEditor} onSubmit={e => {
      e.preventDefault()
      setError(false)
      void save(id, { status, note, verification }).catch(() => setError(true))
    }}>
      <label>處理狀態
        <select value={status} onChange={e => setStatus(e.target.value as ReviewStatus)} disabled={disabled}>
          <option value="open">{item.review_status === 'open' || !item.review_status ? '待處理' : '重新打開'}</option>
          <option value="resolved">已處理</option><option value="dismissed">略過</option>
        </select>
      </label>
      <label>人工驗證
        <select value={verification} onChange={e => setVerification(e.target.value as ReviewVerification)} disabled={disabled}>
          <option value="untested">未驗證</option><option value="passed">通過</option><option value="failed">未通過</option>
        </select>
      </label>
      <label className={styles.rvNote}>處理註記
        <textarea value={note} onChange={e => setNote(e.target.value)} maxLength={1000} rows={2} disabled={disabled} />
      </label>
      <button type="submit" disabled={disabled}>儲存</button>
      {error && <span role="alert">儲存失敗，請重試</span>}
    </form>
  )
}

function QaRow({ item, kind, save, disabled }: {
  item: ReviewItem; kind: ReviewKind; save: ReviewSave; disabled: boolean
}) {
  return (
    <li className={styles.rvRow}>
      <div className={styles.rvMain}>
        <Link className={styles.rvLink} to={`/ask?c=${encodeURIComponent(item.conversation_id ?? '')}`}>
          {item.question}
        </Link>
        <span className={styles.rvMeta}>
          {kind === 'faithfulness' && item.faithfulness_score != null && (
            <span className={styles.fWarn}>{item.faithfulness_score.toFixed(3)}</span>
          )}
          {kind === 'faithfulness' && item.judge_model && <span>{item.judge_model}</span>}
          <span>{fmtDay(item.created_at)}</span>
        </span>
      </div>
      <ReviewEditor item={item} id={item.qa_id ?? ''} save={save} disabled={disabled} />
    </li>
  )
}

function ExtractionRow({ item, save, disabled }: { item: ReviewItem; save: ReviewSave; disabled: boolean }) {
  const reasons = item.review_reasons ?? []
  return (
    <li className={styles.rvRow}>
      <div className={styles.rvMain}>
        <Link className={styles.rvLink} to={`/report/${item.file_hash ?? ''}`}>{displayTitle(item)}</Link>
        <span className={styles.rvMeta}>
          {item.source && <span>{item.source}</span>}
          {reasons.length > 0 ? (
            reasons.map(r => <span key={r} className={styles.fWarn}>{reasonText(item, r)}</span>)
          ) : (
            <span title="以現行門檻已不需複核；重跑該篇回填即會解除標記">現行門檻下已達標</span>
          )}
        </span>
      </div>
      <ReviewEditor item={item} id={item.report_id ?? ''} save={save} disabled={disabled} />
    </li>
  )
}

function NewScaleNote({ scale }: { scale: EvalSource }) {
  return (
    <div className={styles.prate}>
      {`判定尺 ${scale.judge_model} 是${newScaleText(scale)}：分數與換尺前的不可直接比較`}
    </div>
  )
}

export function ReviewQueuePanel({ scale = null }: { scale?: EvalSource | null } = {}) {
  const [kind, setKind] = useState<ReviewKind>('faithfulness')
  const [status, setStatus] = useState<ReviewStatus | 'all'>('open')
  const q = useReviewQueue(kind, status)
  const tab = TABS.find(t => t.kind === kind)!

  return (
    <div className={`${styles.card} ${styles.panel}`}>
      <div className={styles.ptitle}>待複核佇列</div>
      <div className={styles.rvTabs} role="tablist" aria-label="待複核種類">
        {TABS.map(t => (
          <button
            key={t.kind}
            type="button"
            role="tab"
            aria-selected={t.kind === kind}
            className={`${styles.rvTab} ${t.kind === kind ? styles.rvTabOn : ''}`}
            onClick={() => setKind(t.kind)}
          >
            {t.label}
          </button>
        ))}
      </div>
      <div className={styles.rvTabs} role="group" aria-label="複核狀態">
        {([['open', '待處理'], ['resolved', '已處理'], ['dismissed', '略過'], ['all', '全部']] as const).map(([value, label]) => (
          <button key={value} type="button" className={`${styles.rvTab} ${status === value ? styles.rvTabOn : ''}`}
            aria-pressed={status === value} onClick={() => setStatus(value)}>{label}</button>
        ))}
      </div>
      <div role="tabpanel" aria-label={tab.label}>
        {q.isLoading ? (
          <div className={styles.pidle}>載入中…</div>
        ) : q.isError ? (
          <div className={styles.pidle}>
            佇列載入失敗。
            <button type="button" className={styles.rvRetry} onClick={q.refetch}>重試</button>
          </div>
        ) : q.items.length === 0 ? (
          <div className={styles.pidle}>沒有待複核的項目</div>
        ) : (
          <>
            <ul className={styles.rvList}>
              {q.items.map(item =>
                kind === 'extraction'
                  ? <ExtractionRow key={`${item.report_id}-${item.reviewed_at}`} item={item} save={q.save} disabled={q.isSaving} />
                  : <QaRow key={`${item.qa_id}-${item.reviewed_at}`} item={item} kind={kind} save={q.save} disabled={q.isSaving} />,
              )}
            </ul>
            {q.hasMore && (
              <button type="button" className={styles.rvMore} onClick={q.loadMore} disabled={q.isFetchingMore}>
                {q.isFetchingMore ? '載入中…' : `載入更多（共 ${q.total} 筆）`}
              </button>
            )}
          </>
        )}
        <div className={styles.prate}>
          {tab.hint}
          {kind === 'faithfulness' && q.minScore != null ? `（門檻 ${q.minScore}）` : ''}
        </div>
        {kind === 'faithfulness' && scale && isNewDeepSeekScale(scale) && <NewScaleNote scale={scale} />}
      </div>
    </div>
  )
}
