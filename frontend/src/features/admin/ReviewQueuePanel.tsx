import { useState } from 'react'
import { Link } from 'react-router'
import { ApiError, requestJSON } from '../../lib/api'
import { displayTitle } from '../../lib/displayTitle'
import {
  qaContentSchema,
  type QaContent, type ReviewItem, type ReviewKind, type ReviewStatus, type ReviewVerification,
} from '../../lib/reviewSchemas'
import { isNewDeepSeekScale, newScaleText } from '../../lib/judgeScale'
import type { EvalSource } from '../../lib/progressSchema'
import { reasonText } from './reviewReasons'
import { useReviewQueue } from './useReviewQueue'
import styles from './ReviewQueue.module.css'

/**
 * 待複核佇列。
 *
 * 忠實度卡與抽取品質卡只有**筆數**（「待複核 3」），要知道是哪三筆得開 psql；倒讚則
 * 連筆數都沒有出口。這張卡把三者的個體列出來：研報連到閱讀頁；問答**刻意不顯示原文**
 * （後端佇列不回提問、回答與提問者帳號），只列中繼資料與提問者代號（`asker_code`，同一人
 * 同一代號、看不出是誰），也不連到對話串（那只有擁有者打得開）。
 *
 * 有 `qa_content.read` 的人（`canReadContent`，由 `/api/me` 的 scopes 判斷，只是顯示層）每筆問答
 * 多一個「查看內容」：按下才 POST `/api/review/qa/{qa_id}/access` 取這一筆的提問與回答。每次查看
 * 後端都寫稽核，所以內容只放在該列的元件狀態（不進 query 快取），收起就丟掉、再看就再讀一次。
 *
 * 人工處理狀態、註記與驗證結果另存，每列帶最後處理人（`reviewer`）。共用帳號時期的舊資料
 * 兩者都是 null：提問者標「共用帳號」，處理人不顯示。
 * 整張卡限管理員（後端 `/api/review/*` 對一般使用者回 403），所以只出現在管理頁。
 *
 * `scale` 是 `/api/progress` 的問答忠實度統計（與管線分頁的忠實度卡同一份）：判定尺剛換成 DeepSeek、
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

/** 最後處理人與時間；沒處理過（或共用帳號時期處理的）就不印。 */
function ReviewedBy({ item }: { item: ReviewItem }) {
  if (!item.reviewer) return null
  return <span className={styles.who}>{`處理人 ${item.reviewer}（${fmtDay(item.reviewed_at)}）`}</span>
}

type ContentState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'shown'; content: QaContent }
  | { status: 'error'; message: string }

function contentError(err: unknown): string {
  if (err instanceof ApiError && err.status === 404) return '這筆已不在待複核佇列（或已超過 30 天），無法查看內容'
  if (err instanceof ApiError && err.code === 'missing_scope') return '沒有查看問答內容的權限'
  return '讀取失敗，請重試'
}

/** 「查看內容」：逐筆讀取一筆問答原文。每按一次就是一次有稽核的讀取。 */
function QaContentView({ qaId }: { qaId: string }) {
  const [state, setState] = useState<ContentState>({ status: 'idle' })
  const load = () => {
    setState({ status: 'loading' })
    requestJSON(`/api/review/qa/${encodeURIComponent(qaId)}/access`, qaContentSchema, { method: 'POST', cache: 'no-store' })
      .then(content => setState({ status: 'shown', content }))
      .catch(err => setState({ status: 'error', message: contentError(err) }))
  }
  if (state.status === 'shown') {
    return (
      <div className={styles.rvContent}>
        <div className={styles.rvContentHead}>
          <span>提問與回答（本次查看已記入稽核）</span>
          <button type="button" className={styles.rvRetry} onClick={() => setState({ status: 'idle' })}>收起</button>
        </div>
        <div className={styles.rvContentLabel}>提問</div>
        <p className={styles.rvContentText}>{state.content.question}</p>
        <div className={styles.rvContentLabel}>回答</div>
        <p className={styles.rvContentText}>{state.content.answer || '（沒有回答）'}</p>
      </div>
    )
  }
  return (
    <div className={styles.rvContentBar}>
      <button type="button" className={styles.rvRetry} onClick={load} disabled={state.status === 'loading'}
        title="每次查看都會留下稽核紀錄">
        {state.status === 'loading' ? '讀取中…' : '查看內容'}
      </button>
      {state.status === 'error' && <span role="alert">{state.message}</span>}
    </div>
  )
}

/** 問答列的標題：沒有原文可顯示，以 qa_id 前 8 碼辨識（與稽核紀錄的 target_id 對得上）。 */
function qaLabel(item: ReviewItem): string {
  return `問答 ${(item.qa_id ?? '').slice(0, 8) || '—'}`
}

function QaRow({ item, kind, save, disabled, canReadContent }: {
  item: ReviewItem; kind: ReviewKind; save: ReviewSave; disabled: boolean; canReadContent: boolean
}) {
  return (
    <li className={styles.rvRow}>
      <div className={styles.rvMain}>
        <span className={styles.rvSubject} title={item.qa_id ?? undefined}>{qaLabel(item)}</span>
        <span className={styles.rvMeta}>
          {kind === 'faithfulness' && item.faithfulness_score != null && (
            <span className={styles.fWarn}>{item.faithfulness_score.toFixed(3)}</span>
          )}
          {kind === 'faithfulness' && item.judge_model && <span>{item.judge_model}</span>}
          {/* 共用帳號時期的舊提問沒有擁有者（null），標「共用帳號」而不是留白，免得被讀成資料缺漏 */}
          <span className={styles.who}>{`提問者 ${item.asker_code ? `#${item.asker_code}` : '共用帳號'}`}</span>
          <span>{fmtDay(item.created_at)}</span>
          <ReviewedBy item={item} />
        </span>
      </div>
      {canReadContent && item.qa_id && <QaContentView qaId={item.qa_id} />}
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
          <ReviewedBy item={item} />
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

export function ReviewQueuePanel({ scale = null, canReadContent = false }: {
  scale?: EvalSource | null; canReadContent?: boolean
} = {}) {
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
                  : <QaRow key={`${item.qa_id}-${item.reviewed_at}`} item={item} kind={kind} save={q.save} disabled={q.isSaving}
                      canReadContent={canReadContent} />,
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
