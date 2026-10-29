import { Lamp, PageHeader, Segmented, Tabs, toast } from '../../components/console'
import { ApiError } from '../../lib/api'
import c from '../../components/console/Console.module.css'
import type { JudgeScale, ReviewItem } from '../../lib/reviewSchemas'
import { isNewDeepSeekScale, newScaleText } from '../../lib/judgeScale'
import { ReviewDetail, type ReviewUpdate } from './reviews/ReviewDetail'
import {
  KIND_TABS, STATUS_FILTERS, STATUS_LABEL, STATUS_TONE, VERIFY_LABEL, hintText, itemId, metaLine, subjectOf,
} from './reviews/reviewItem'
import { useReviewParams } from './reviews/useReviewParams'
import { useReviewItem, useReviewOpenCounts, useReviewQueue } from './useReviewQueue'
import styles from './ReviewQueue.module.css'

/**
 * 待複核頁的內容：頁首（右側是狀態篩選）、三個分頁籤（數字是待處理數）、左清單右詳情。
 *
 * 清單列出忠實度低分、倒讚與抽取品質三種項目的個體，順序照後端（分數最低在前）。問答**刻意不顯示原文**
 * （後端佇列不回提問、回答與提問者帳號），只列中繼資料與提問者代號（`asker_code`，同一人同一代號、
 * 看不出是誰），也不連到對話串（那只有擁有者打得開）；研報連到閱讀頁。
 *
 * 有 `qa_content.read` 的人（`canReadContent`，由 `/api/me` 的 scopes 判斷，只是顯示層）可以逐筆
 * 「查看內容」，規則見 `reviews/ReviewDetail.tsx`。
 *
 * 人工處理狀態、註記與驗證結果另存，每筆帶最後處理人（`reviewer`）。共用帳號時期的舊資料兩者都是 null：
 * 提問者標「共用帳號」，處理人不顯示。整頁限管理員（後端 `/api/review/*` 對一般使用者回 403）。
 *
 * 分頁、狀態與選中的那筆都在 URL query（`reviews/useReviewParams.ts`）。`?review=` 指的那筆不在已載入的
 * 清單裡（已處理、在後面的分頁）就用單筆端點取來，右欄照樣顯示它（清單不動，註明它不在清單裡）；
 * 取不到（已不在佇列、超過 30 天、屬於另一個分頁，或舊版後端沒有那支端點）才顯示第一筆。
 *
 * `scale` 是 `/api/review/judge-scale` 的判定尺（與管線分頁的忠實度卡同一份快照）：判定尺剛換成 DeepSeek、
 * 窗期內還有舊尺的列時，忠實度分頁比照忠實度卡標「新量尺」——換尺頭幾天佇列近乎是空的，不說清楚
 * 會被讀成「低分變少了」。佇列本身仍自己取數、不跟 5 秒輪詢（理由見 useReviewQueue）。
 */
const HELP = '系統偵測到需要人看的問答與研報；處理狀態、註記與處理人另存，原始品質訊號不變。'
  + '問答刻意不顯示原文，只列中繼資料與提問者代號；有 qa_content.read 的人可逐筆「查看內容」，每次查看都寫稽核。'

function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '請重試'
}

/** 清單上的分數顏色：原型以 0.5 分界，更低的用紅字。只是顯示上的強調，與複核門檻（`min_score`）無關。 */
const SCORE_LOW = 0.5

export function ReviewQueuePanel({ scale = null, canReadContent = false }: {
  scale?: JudgeScale | null; canReadContent?: boolean
} = {}) {
  const { kind, status, review, setKind, setStatus, select } = useReviewParams()
  const q = useReviewQueue(kind, status)
  const counts = useReviewOpenCounts()
  const tab = KIND_TABS.find(t => t.value === kind)!
  // 選中的那筆：清單裡有就用清單的；沒有就向後端取那一筆；再取不到才退回第一筆。
  const listed = review ? q.items.find(item => itemId(kind, item) === review) : undefined
  // 儲存中不查：存完那筆可能剛離開目前的篩選，等一下就會被取消選取，不必為它多打一次。
  const single = useReviewItem(kind, review, !q.isLoading && !q.isSaving && !listed)
  const pinned = !listed && review ? single.data : undefined
  const lookingUp = !listed && single.isPending && single.fetchStatus !== 'idle'
  const current = listed ?? pinned ?? (lookingUp ? null : q.items[0] ?? null)
  // 後端明確說不在佇列（404）才提示；舊版後端沒有這支端點（405）就安靜地照舊顯示第一筆。
  const gone = !listed && !pinned && single.error instanceof ApiError && single.error.status === 404

  const save = (item: ReviewItem, update: ReviewUpdate) => {
    q.save(itemId(kind, item), update).then(() => {
      toast(`已儲存：${subjectOf(kind, item)}・${STATUS_LABEL[update.status]}・${VERIFY_LABEL[update.verification]}`)
      // 存完就不在目前這個篩選裡的，不再指著它：清單會補上下一筆。要同步清掉：`isSaving` 轉回 false 的
      // 那次 render（React Query 排在下一個 macrotask）若還看得到舊的 `?review=`，單筆查詢會為這筆多打一次，
      // 右欄也會閃一下「載入中…」；預設的 transition 在主機忙時就會晚到。
      if (status !== 'all' && status !== update.status) select(null, { flushSync: true })
    }).catch(() => toast('儲存失敗，請重試', { tone: 'bad' }))
  }

  return (
    <>
      <PageHeader
        summary="忠實度低分、倒讚與抽取品質需要人工確認的項目。"
        help={HELP}
        actions={<Segmented label="複核狀態" options={STATUS_FILTERS} value={status} onChange={setStatus} />}
      />
      <Tabs label="待複核種類" value={kind} onChange={setKind}
        tabs={KIND_TABS.map(t => ({ ...t, count: counts[t.value] }))} />
      <div className={styles.cols} role="tabpanel" aria-label={tab.label}>
        <section className={`${c.card} ${styles.listCard}`}>
          {q.isLoading ? (
            <div className={styles.listEmpty}>載入中…</div>
          ) : q.isError ? (
            <div className={styles.listError}>
              <p className={c.error} role="alert">佇列載入失敗：{messageOf(q.error)}</p>
              <button type="button" className={c.linkBtn} onClick={q.refetch}>重試</button>
            </div>
          ) : q.items.length === 0 ? (
            <div className={styles.listEmpty}>目前沒有項目</div>
          ) : (
            <>
              <ul className={styles.list} aria-label="待複核清單">
                {q.items.map(item => {
                  const id = itemId(kind, item)
                  const itemStatus = item.review_status ?? 'open'
                  const score = kind === 'faithfulness' ? item.faithfulness_score : null
                  return (
                    <li key={id}>
                      <button type="button" className={styles.item} aria-current={item === current ? 'true' : undefined}
                        onClick={() => select(id)}>
                        <span className={styles.itemRow}>
                          <span className={`${styles.itemSubject} ${kind === 'extraction' ? '' : c.mono}`}>{subjectOf(kind, item)}</span>
                          {score != null && (
                            <span className={styles.score} data-low={score < SCORE_LOW || undefined}>{score.toFixed(3)}</span>
                          )}
                        </span>
                        <span className={styles.itemRow}>
                          <span className={styles.itemMeta}>{metaLine(kind, item)}</span>
                          <span className={styles.itemStatus} data-status={itemStatus}>
                            <Lamp tone={STATUS_TONE[itemStatus]} label="" size={5} />{STATUS_LABEL[itemStatus]}
                          </span>
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
              {q.hasMore && (
                <button type="button" className={styles.more} onClick={q.loadMore} disabled={q.isFetchingMore}>
                  {q.isFetchingMore ? '載入中…' : `載入更多（共 ${q.total} 筆）`}
                </button>
              )}
            </>
          )}
          <div className={styles.listFoot}>
            {pinned && <p role="status">連結指的這一筆不在目前載入的清單裡（已處理，或在後面的分頁）；詳情在右邊。</p>}
            {gone && <p role="status">連結指的那一筆已不在這個分頁的佇列裡（或已超過 30 天）{q.items.length > 0 ? '，右邊顯示第一筆' : ''}。</p>}
            <p>{hintText(kind, q.minScore)}</p>
            {kind === 'faithfulness' && scale && isNewDeepSeekScale(scale) && (
              <p>{`判定尺 ${scale.judge_model} 是${newScaleText(scale)}：分數與換尺前的不可直接比較`}</p>
            )}
          </div>
        </section>
        <section className={c.card} aria-label="詳情">
          {current
            ? <ReviewDetail kind={kind} item={current} canReadContent={canReadContent} saving={q.isSaving} onSave={save} />
            : <div className={styles.detailEmpty}>{lookingUp ? '載入中…' : '從左邊選一筆'}</div>}
        </section>
      </div>
    </>
  )
}
