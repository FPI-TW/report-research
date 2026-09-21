import { useState } from 'react'
import { Link } from 'react-router'
import { displayTitle } from '../../lib/displayTitle'
import type { ReviewItem, ReviewKind } from '../../lib/reviewSchemas'
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
 * 刻意沒有「標記已處理」：那需要狀態欄位與處理人歸因，而本站是共用帳號。
 */
const TABS: { kind: ReviewKind; label: string; hint: string }[] = [
  { kind: 'faithfulness', label: '忠實度低分', hint: '近 30 天內抽查分數低於門檻的回答，最低分在前' },
  { kind: 'feedback', label: '倒讚', hint: '近 30 天內使用者按了倒讚的回答' },
  { kind: 'extraction', label: '抽取品質', hint: '抽取品質標為 needs_review 的研報（照樣入庫、可檢索），分數最低在前；右側是被標記的原因' },
]

function fmtDay(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 10) : '—'
}

function QaRow({ item, kind }: { item: ReviewItem; kind: ReviewKind }) {
  return (
    <li className={styles.rvRow}>
      <Link className={styles.rvLink} to={`/ask?c=${encodeURIComponent(item.conversation_id ?? '')}`}>
        {item.question}
      </Link>
      <span className={styles.rvMeta}>
        {kind === 'faithfulness' && item.faithfulness_score != null && (
          <span className={styles.fWarn}>{item.faithfulness_score.toFixed(3)}</span>
        )}
        <span>{fmtDay(item.created_at)}</span>
      </span>
    </li>
  )
}

function ExtractionRow({ item }: { item: ReviewItem }) {
  const reasons = item.review_reasons ?? []
  return (
    <li className={styles.rvRow}>
      <Link className={styles.rvLink} to={`/report/${item.file_hash ?? ''}`}>{displayTitle(item)}</Link>
      <span className={styles.rvMeta}>
        {item.source && <span>{item.source}</span>}
        {reasons.length > 0 ? (
          reasons.map(r => <span key={r} className={styles.fWarn}>{reasonText(item, r)}</span>)
        ) : (
          // 原因以現行門檻重算；入庫後調過門檻的話可能一條都不成立。
          <span title="以現行門檻已不需複核；重跑該篇回填即會解除標記">現行門檻下已達標</span>
        )}
      </span>
    </li>
  )
}

export function ReviewQueuePanel() {
  const [kind, setKind] = useState<ReviewKind>('faithfulness')
  const q = useReviewQueue(kind)
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
                  ? <ExtractionRow key={item.report_id} item={item} />
                  : <QaRow key={item.qa_id} item={item} kind={kind} />,
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
      </div>
    </div>
  )
}
