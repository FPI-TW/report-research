import { useState } from 'react'
import { Link } from 'react-router'
import { Button, Field, Lamp, Segmented } from '../../../components/console'
import c from '../../../components/console/Console.module.css'
import { ApiError, requestJSON } from '../../../lib/api'
import {
  qaContentSchema,
  type QaContent, type ReviewItem, type ReviewKind, type ReviewStatus, type ReviewVerification,
} from '../../../lib/reviewSchemas'
import { reasonText } from '../reviewReasons'
import styles from '../ReviewQueue.module.css'
import {
  CLEARED_HINT, CLEARED_TEXT, STATUS_LABEL, STATUS_TONE, VERIFY_LABEL, askerText, fmtDay, itemId, subjectOf,
} from './reviewItem'

export interface ReviewUpdate { status: ReviewStatus; note: string; verification: ReviewVerification }

const NOTE_MAX = 1000
const STATUS_OPTIONS = (['open', 'resolved', 'dismissed'] as const).map(value => ({ value, label: STATUS_LABEL[value] }))
const VERIFY_OPTIONS = (['untested', 'passed', 'failed'] as const).map(value => ({ value, label: VERIFY_LABEL[value] }))

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

/**
 * 「查看內容」：逐筆讀取一筆問答原文。每按一次就是一次有稽核的讀取，所以內容只放在這個元件的狀態
 * （不進 query 快取）：收起、或切到別筆（元件以 qa_id 為 key 重掛）就丟掉，再看就再讀一次。
 * 沒有 `qa_content.read` 的人看得到按鈕但按不了，旁邊說明缺什麼；擋人的是後端（403 missing_scope）。
 */
function QaContentView({ qaId, canRead }: { qaId: string; canRead: boolean }) {
  const [state, setState] = useState<ContentState>({ status: 'idle' })
  const load = () => {
    setState({ status: 'loading' })
    requestJSON(`/api/review/qa/${encodeURIComponent(qaId)}/access`, qaContentSchema, { method: 'POST', cache: 'no-store' })
      .then(content => setState({ status: 'shown', content }))
      .catch(err => setState({ status: 'error', message: contentError(err) }))
  }
  if (state.status === 'shown') {
    return (
      <div className={styles.block}>
        <div className={styles.contentHead}>
          <span className={styles.contentAudit}>提問與回答（這次查看已記入稽核）</span>
          <button type="button" className={c.linkBtn} onClick={() => setState({ status: 'idle' })}>收起</button>
        </div>
        <div className={styles.contentLabel}>提問</div>
        <p className={styles.contentQuestion}>{state.content.question}</p>
        <div className={styles.contentLabel}>回答</div>
        <p className={styles.contentAnswer}>{state.content.answer || '（沒有回答）'}</p>
      </div>
    )
  }
  return (
    <div className={`${styles.block} ${styles.contentBar}`}>
      <Button size="sm" onClick={load} disabled={!canRead || state.status === 'loading'}
        title={canRead ? '每次查看都會記入稽核' : '需要 qa_content.read 權限'}>
        {state.status === 'loading' ? '讀取中…' : '查看內容'}
      </Button>
      {state.status === 'error'
        ? <span className={styles.contentError} role="alert">{state.message}</span>
        : <span className={styles.contentNote}>{canRead ? '每次查看都會記入稽核' : '需要 qa_content.read 權限'}</span>}
    </div>
  )
}

function ExtractionReasons({ item }: { item: ReviewItem }) {
  const reasons = item.review_reasons ?? []
  return (
    <div className={`${styles.block} ${styles.reasons}`}>
      <span className={styles.reasonLabel}>標記原因</span>
      {reasons.length > 0
        ? reasons.map(r => <span key={r} className={styles.reason}>{reasonText(item, r)}</span>)
        : <span className={styles.reason} data-cleared title={CLEARED_HINT}>{CLEARED_TEXT}</span>}
      {item.file_hash && (
        <Link className={`${c.btn} ${c.btnSm} ${styles.openReport}`} to={`/report/${item.file_hash}`}
          target="_blank" rel="noopener">
          開啟閱讀頁
        </Link>
      )}
    </div>
  )
}

/** 中繼資料格。問答刻意只有這些：後端佇列不回提問、回答與提問者帳號。 */
function metaRows(kind: ReviewKind, item: ReviewItem): [string, string][] {
  const rows: [string, string][] = []
  if (kind === 'extraction') {
    rows.push(['檔名', item.file_name || '—'], ['券商', item.source || '—'])
  } else {
    if (kind === 'faithfulness' && item.faithfulness_score != null) rows.push(['忠實度', item.faithfulness_score.toFixed(3)])
    if (kind === 'faithfulness' && item.judge_model) rows.push(['判定尺', item.judge_model])
    rows.push(['提問者', askerText(item)], ['日期', fmtDay(item.created_at)])
  }
  // 沒處理過（或共用帳號時期處理的）就不印處理人。
  if (item.reviewer) rows.push(['處理人', `${item.reviewer}（${fmtDay(item.reviewed_at)}）`])
  return rows
}

/** 處理表單。草稿只在這個元件裡：換一筆、或儲存後該筆的 `reviewed_at` 變了，就以伺服器的值重來。 */
function ReviewEditor({ item, saving, onSave }: {
  item: ReviewItem; saving: boolean; onSave: (update: ReviewUpdate) => void
}) {
  const saved: ReviewUpdate = {
    status: item.review_status ?? 'open', verification: item.verification ?? 'untested', note: item.review_note ?? '',
  }
  const [status, setStatus] = useState(saved.status)
  const [verification, setVerification] = useState(saved.verification)
  const [note, setNote] = useState(saved.note)
  const dirty = status !== saved.status || verification !== saved.verification || note !== saved.note
  return (
    <form className={styles.editor} onSubmit={e => {
      e.preventDefault()
      if (dirty) onSave({ status, note, verification })
    }}>
      <fieldset className={styles.fieldset} disabled={saving}>
        <div className={styles.segRow}>
          <div className={styles.segField}>
            <span className={styles.segLabel}>處理狀態</span>
            <Segmented label="處理狀態" options={STATUS_OPTIONS} value={status} onChange={setStatus} />
          </div>
          <div className={styles.segField}>
            <span className={styles.segLabel}>人工驗證</span>
            <Segmented label="人工驗證" options={VERIFY_OPTIONS} value={verification} onChange={setVerification} />
          </div>
        </div>
        <Field label="處理註記" counter={`${note.length}／${NOTE_MAX}`}>
          <textarea className={c.textarea} value={note} onChange={e => setNote(e.target.value)} maxLength={NOTE_MAX} rows={3} />
        </Field>
        <div className={styles.editorFoot}>
          <Button type="submit" variant="primary" disabled={!dirty}>儲存</Button>
        </div>
      </fieldset>
    </form>
  )
}

/** 右欄：選中那一筆的詳情與處理表單。 */
export function ReviewDetail({ kind, item, canReadContent, saving, onSave }: {
  kind: ReviewKind; item: ReviewItem; canReadContent: boolean; saving: boolean
  onSave: (item: ReviewItem, update: ReviewUpdate) => void
}) {
  const id = itemId(kind, item)
  const status = item.review_status ?? 'open'
  const isQa = kind !== 'extraction'
  return (
    <>
      <div className={styles.detailHead}>
        <h2 className={`${styles.detailTitle} ${isQa ? c.mono : ''}`} title={isQa ? id : undefined}>{subjectOf(kind, item)}</h2>
        <span className={styles.detailStatus}><Lamp tone={STATUS_TONE[status]} label="" />{STATUS_LABEL[status]}</span>
      </div>
      <dl className={styles.meta}>
        {metaRows(kind, item).map(([k, v]) => (
          <div key={k} className={styles.metaCell}><dt>{k}</dt><dd>{v}</dd></div>
        ))}
      </dl>
      {isQa
        ? id && <QaContentView key={id} qaId={id} canRead={canReadContent} />
        : <ExtractionReasons item={item} />}
      <ReviewEditor key={`${id}-${item.reviewed_at ?? ''}`} item={item} saving={saving} onSave={update => onSave(item, update)} />
    </>
  )
}
