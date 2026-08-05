import { useCallback, useMemo, useRef, useState } from 'react'
import { useParams } from 'react-router'
import type { ReadingDoc } from '../../lib/readingSchemas'
import { PdfPane } from './PdfPane'
import type { JumpRequest, JumpResult } from './pdf/PdfViewer'
import { ReportHeader } from './ReportHeader'
import { ReportSkeleton } from './ReportSkeleton'
import { ReportLoadError, ReportNotFound } from './ReportStates'
import { SignalCard } from './SignalCard'
import { SimilarReports } from './SimilarReports'
import { TakeawayList } from './TakeawayList'
import { TextPane } from './TextPane'
import { isValidHash } from './readingFormat'
import { isNotFound, useReadingDoc, useReadingText, useSimilarReports } from './useReading'
import styles from './ReportPage.module.css'

/**
 * 能不能內嵌原始 PDF。
 *
 * 閱讀頁只有一種文件檢視，**沒有給讀者的切換**：回 true 掛 PDF 檢視器，回 false 落到
 * 文字後備。落到後備由資料現實決定（.docx 內嵌不了、或檔案不在磁碟上），不是使用者
 * 的選項 —— 所以判斷只看 doc 的兩個欄位，不看網址、不看意圖。
 * 舊網址上的 `?view=text`／`?chunk=N` 一律無作用（不報錯，也不改變任何行為）。
 */
function pdfViewable(doc: Pick<ReadingDoc, 'has_file' | 'is_pdf'>): boolean {
  return doc.has_file && doc.is_pdf
}

export default function ReportPage() {
  const { hash = '' } = useParams()

  const valid = isValidHash(hash)

  const doc = useReadingDoc(hash, valid)
  const similar = useSimilarReports(hash, valid)

  const d = doc.data
  // 只有「內嵌不了 PDF 且確定有全文」才抓正典文字：骨架未回來前還不知道要不要抓，
  // 若不等 doc 就發，每一篇 PDF 研報都會白打一次用不到的請求。
  const needsText = Boolean(d && !pdfViewable(d) && d.text_state === 'ok')
  const text = useReadingText(hash, valid && needsText)

  const similarItems = useMemo(() => similar.data?.items ?? [], [similar.data])

  // 摘錄跳轉：狀態下送、結果上回。**都綁 hash**，換研報時就地失效 ——
  // 沿用 PdfViewer 對 timedOutUrl 的同一招，不用 effect 重設（那會壞，而且壞得很安靜）。
  const [jumpState, setJumpState] = useState<(JumpRequest & { hash: string }) | null>(null)
  const [jumpResult, setJumpResult] = useState<(JumpResult & { ordinal: number; hash: string }) | null>(null)
  // 引擎降級成瀏覽器內建檢視＝沒有搜尋能力，摘錄整批退回非互動。
  // **綁 hash**：換到未造訪過的研報時整棵子樹會因骨架而卸載重建、引擎重試且通常會成功，
  // 但 ReportPage 自己沒卸載——不綁的話會變成「檢視器好好的、摘錄卻全部不能點」，
  // 零錯誤零提示，重整就好，是最難被回報的那種故障。
  const [degradedHash, setDegradedHash] = useState<string | null>(null)
  const docRef = useRef<HTMLElement>(null)
  // 遞增計數器而非 Date.now()：同毫秒兩次點擊會產生相同 nonce，被消費端的
  // doneNonceRef 當成重複而擋掉 → 永久停在「尋找中…」。也讓測試不依賴掛鐘。
  const nonceRef = useRef(0)

  const jump = jumpState?.hash === hash ? jumpState : null
  const result = jumpResult?.hash === hash ? jumpResult : null
  const degraded = degradedHash === hash

  const onJump = useCallback(
    (t: { ordinal: number; quote?: string | null }) => {
      if (!t.quote) return
      setJumpState(prev => {
        // 在途時忽略對**同一條**的重複點擊（jumpState 非 null ⇔ 在途，見下方消費即清除）：
        // 外掛的 searchAllPages 對「query 已等於這個關鍵字」直接回快取，而搜尋一開始就把
        // results 清空、total 設 0 —— 重按會拿到假的「找不到」，同時第一次的搜尋跑完又把
        // 高亮畫上去。點**別條**則照常接管（舊的由 effect cleanup 中止）。
        if (prev?.hash === hash && prev.ordinal === t.ordinal) return prev
        nonceRef.current += 1
        return { hash, ordinal: t.ordinal, quote: t.quote as string, nonce: nonceRef.current }
      })
      setJumpResult(null)
    },
    [hash],
  )

  const onJumpResult = useCallback(
    (r: JumpResult) => {
      // 比對 nonce：連點兩條時，前一次的結果可能晚於後一次抵達，不能讓它蓋掉。
      // 讀 jumpState 而不是在 setState 的 updater 裡做事——updater 必須是純函式，
      // StrictMode 會重跑它。identity 變動不要緊：消費端有 doneNonceRef 擋重複執行。
      if (!jumpState || jumpState.nonce !== r.nonce) return
      setJumpResult({ ...r, ordinal: jumpState.ordinal, hash: jumpState.hash })
      // 消費即清除：留著的話離開研報再回來（元件重建、hash 相同）會被當成新的
      // 待處理請求而自動重播上一次跳轉。
      setJumpState(null)
      // 窄螢幕把兩欄改成上下堆疊，檢視器內部確實捲了，但整個檢視器在視窗外
      // ——只做 scrollToPage 等於只做一半，畫面一動也不動。
      if (r.ok && window.matchMedia('(max-width: 1023px)').matches) {
        docRef.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
      }
    },
    [jumpState],
  )

  if (!valid) return <ReportNotFound />
  if (doc.isLoading) return <ReportSkeleton />
  if (doc.isError) return isNotFound(doc.error) ? <ReportNotFound /> : <ReportLoadError onRetry={() => doc.refetch()} />
  if (!d) return <ReportNotFound />

  const showSignals = d.signals_state === 'available' && d.signals.length > 0
  const showTakeaways = d.takeaways.length > 0

  return (
    <div className={styles.page}>
      <ReportHeader doc={d} />

      <div className={styles.main}>
        <aside className={styles.intel}>
          {/* 四區收在同一張卡內：摘要與重點摘錄為主體，觀點與相似研報以 .foot 退為卡內頁腳。
              包一層而不是讓 aside 自己當卡片 —— aside 同時是捲動容器，圓角與陰影套在
              捲動容器上會跟著內容一起被裁掉。 */}
          <div className={styles.card}>
            {d.summary && (
              <section className={styles.sec}>
                <h2 className={styles.secH}>摘要</h2>
                <p className={styles.sum}>{d.summary}</p>
              </section>
            )}

            {showTakeaways && (
              <section className={styles.sec}>
                <h2 className={styles.secH}>重點摘錄</h2>
                <TakeawayList
                  takeaways={d.takeaways}
                  canJump={pdfViewable(d) && !degraded}
                  pendingOrdinal={jump ? jump.ordinal : null}
                  result={result}
                  onJump={onJump}
                />
              </section>
            )}

            {/* 觀點：無訊號時整區不進 DOM（不是空框、不是骨架）。
                全語料僅 0.68% 有訊號，這是常態不是錯誤，版面只變短不跳動。 */}
            {showSignals && (
              <section className={`${styles.sec} ${styles.foot}`}>
                <h2 className={styles.secH}>觀點</h2>
                <p className={styles.signalNote}>本篇已擷取結構化訊號 — 全語料僅 0.68% 有。</p>
                {d.signals.map(s => (
                  <SignalCard key={`${s.market}-${s.instrument_code}`} signal={s} />
                ))}
              </section>
            )}

            {/* 相似研報：卡片最末，預設收合成一行（自理狀態：錯誤→展開＋重試、
                載入中/空→不渲染，見其元件說明）。 */}
            <SimilarReports
              items={similarItems}
              isLoading={similar.isLoading}
              isError={similar.isError}
              onRetry={() => similar.refetch()}
            />
          </div>
        </aside>

        <section className={styles.doc} ref={docRef}>
          <div className={styles.docBar}>
            <span className={styles.docFile}>{d.file_name}</span>
          </div>

          {/* 三態，且刻意以 needsText（而非 pdfViewable）分派：
              內嵌得了 → PdfPane 掛檢視器；內嵌不了但有全文 → TextPane；
              **內嵌不了又沒有全文 → 也走 PdfPane**，由它既有的兩個分支給出終態
              （無檔＝「找不到原始檔。」／非 PDF＝「請下載查看」＋下載連結）。
              少了第三態，停用中的 /text 查詢會讓 TextPane 卡在 `!text` 的骨架分支上
              ——無錯誤、無重試、連請求都不發，畫面永遠是 8 條 Skeleton。 */}
          {needsText ? (
            <TextPane
              text={text.data}
              isLoading={text.isLoading}
              isError={text.isError}
              onRetry={() => text.refetch()}
              hasFile={d.has_file}
            />
          ) : (
            <PdfPane
              doc={d}
              jump={jump}
              onJumpResult={onJumpResult}
              // 降級不只要熄掉互動，**還要把在途的那次收成終態**：Chrome 從未掛載過，
              // 沒有任何人會送 JumpResult 進來，光 setDegraded 會讓「尋找中…」永久
              // 掛在一個已經不能點的條目上——比「什麼都沒發生」更糟，它主動宣稱系統在工作。
              onDegraded={() => {
                setDegradedHash(hash)
                // 不要在 setState 的 updater 裡呼叫另一個 setter——updater 必須是純函式，
                // StrictMode 會重跑它。直接讀這一輪的 jumpState 即可。
                if (jumpState) {
                  setJumpResult({
                    nonce: jumpState.nonce,
                    ok: false,
                    ordinal: jumpState.ordinal,
                    hash: jumpState.hash,
                  })
                }
                setJumpState(null)
              }}
            />
          )}
        </section>

        {/* 合規要求：免責恆常駐，不得依賴訊號/相似研報等任何選擇性區塊。
            它是 .main 的第三個網格項而非 .page 的最後一格：寬版釘在左欄底部
            （文件欄跨兩列，不再被這條扣掉高度）、窄版堆疊時落到整頁最末。
            位置全由 grid-template-areas 決定，**DOM 只有這一份** ——
            寬窄各放一份會讓 getByText 撞到兩個節點而整批測試爆掉。 */}
        <div className={styles.disc}>
          本平台內容彙整自券商研究報告，僅供內部研究參考，不構成任何投資建議或要約。評等、目標價與論點均為原報告發布券商之意見，非廷豐之立場。投資請自行判斷並承擔風險。
        </div>
      </div>
    </div>
  )
}
