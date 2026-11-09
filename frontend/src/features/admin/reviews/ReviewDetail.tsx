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
import { t } from '@/i18n'

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
  if (err instanceof ApiError && err.status === 404) return t('admin.reviews.reviewDetail.thisItemIsNoLonger')
  if (err instanceof ApiError && err.code === 'missing_scope') return t('admin.reviews.reviewDetail.noPermissionToViewQ')
  return t('admin.reviews.reviewDetail.couldnTLoadTryAgain')
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
          <span className={styles.contentAudit}>{t('admin.reviews.reviewDetail.questionAndAnswerThisView')}</span>
          <button type="button" className={c.linkBtn} onClick={() => setState({ status: 'idle' })}>{t('admin.reviews.reviewDetail.collapse')}</button>
        </div>
        <div className={styles.contentLabel}>{t('admin.reviews.reviewDetail.question')}</div>
        <p className={styles.contentQuestion}>{state.content.question}</p>
        <div className={styles.contentLabel}>{t('admin.reviews.reviewDetail.answer')}</div>
        <p className={styles.contentAnswer}>{state.content.answer || t('admin.reviews.reviewDetail.noAnswer')}</p>
      </div>
    )
  }
  return (
    <div className={`${styles.block} ${styles.contentBar}`}>
      <Button size="sm" onClick={load} disabled={!canRead || state.status === 'loading'}
        title={canRead ? t('admin.reviews.reviewDetail.everyViewIsAudited') : t('admin.reviews.reviewDetail.requiresTheQaContentRead')}>
        {state.status === 'loading' ? t('admin.reviews.reviewDetail.loading') : t('admin.reviews.reviewDetail.viewContent')}
      </Button>
      {state.status === 'error'
        ? <span className={styles.contentError} role="alert">{state.message}</span>
        : <span className={styles.contentNote}>{canRead ? t('admin.reviews.reviewDetail.everyViewIsAudited') : t('admin.reviews.reviewDetail.requiresTheQaContentRead')}</span>}
    </div>
  )
}

function ExtractionReasons({ item }: { item: ReviewItem }) {
  const reasons = item.review_reasons ?? []
  return (
    <div className={`${styles.block} ${styles.reasons}`}>
      <span className={styles.reasonLabel}>{t('admin.reviews.reviewDetail.flagReason')}</span>
      {reasons.length > 0
        ? reasons.map(r => <span key={r} className={styles.reason}>{reasonText(item, r)}</span>)
        : <span className={styles.reason} data-cleared title={CLEARED_HINT}>{CLEARED_TEXT}</span>}
      {item.file_hash && (
        <Link className={`${c.btn} ${c.btnSm} ${styles.openReport}`} to={`/report/${item.file_hash}`}
          target="_blank" rel="noopener">
          {t('admin.reviews.reviewDetail.openReadingPage')}
        </Link>
      )}
    </div>
  )
}

/** 中繼資料格。問答刻意只有這些：後端佇列不回提問、回答與提問者帳號。 */
function metaRows(kind: ReviewKind, item: ReviewItem): [string, string][] {
  const rows: [string, string][] = []
  if (kind === 'extraction') {
    rows.push([t('admin.reviews.reviewDetail.fileName'), item.file_name || '—'], [t('admin.reviews.reviewDetail.broker'), item.source || '—'])
  } else {
    if (kind === 'faithfulness' && item.faithfulness_score != null) rows.push([t('admin.reviews.reviewDetail.faithfulness'), item.faithfulness_score.toFixed(3)])
    if (kind === 'faithfulness' && item.judge_model) rows.push([t('admin.reviews.reviewDetail.judge'), item.judge_model])
    rows.push([t('admin.reviews.reviewDetail.asker'), askerText(item)], [t('admin.reviews.reviewDetail.date'), fmtDay(item.created_at)])
  }
  // 沒處理過（或共用帳號時期處理的）就不印處理人。
  if (item.reviewer) rows.push([t('admin.reviews.reviewDetail.handler'), t('admin.reviews.reviewDetail.text', { reviewer: item.reviewer, reviewed_at: fmtDay(item.reviewed_at) })])
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
            <span className={styles.segLabel}>{t('admin.reviews.reviewDetail.status')}</span>
            <Segmented label={t('admin.reviews.reviewDetail.status')} options={STATUS_OPTIONS} value={status} onChange={setStatus} />
          </div>
          <div className={styles.segField}>
            <span className={styles.segLabel}>{t('admin.reviews.reviewDetail.humanVerification')}</span>
            <Segmented label={t('admin.reviews.reviewDetail.humanVerification')} options={VERIFY_OPTIONS} value={verification} onChange={setVerification} />
          </div>
        </div>
        <Field label={t('admin.reviews.reviewDetail.handlingNote')} counter={t('admin.reviews.reviewDetail.text2', { length: note.length, NOTE_MAX })}>
          <textarea className={c.textarea} value={note} onChange={e => setNote(e.target.value)} maxLength={NOTE_MAX} rows={3} />
        </Field>
        <div className={styles.editorFoot}>
          <Button type="submit" variant="primary" disabled={!dirty}>{t('admin.reviews.reviewDetail.save')}</Button>
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
