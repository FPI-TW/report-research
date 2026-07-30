import { useCallback, useEffect, useMemo, useState } from 'react'
import { useParams, useSearchParams } from 'react-router'
import { Icon } from '../../components/primitives/Icon'
import { Pressable } from '../../components/primitives/Pressable'
import type { ReadingDoc } from '../../lib/readingSchemas'
import { DocViewSwitch, type DocView } from './DocViewSwitch'
import { PdfPane } from './PdfPane'
import { ReportHeader } from './ReportHeader'
import { ReportSkeleton } from './ReportSkeleton'
import { ReportLoadError, ReportNotFound } from './ReportStates'
import { SignalCard } from './SignalCard'
import { SimilarReports } from './SimilarReports'
import { TakeawayList } from './TakeawayList'
import { TextPane, type JumpTarget } from './TextPane'
import { isValidHash } from './readingFormat'
import { isNotFound, useReadingDoc, useReadingText, useSimilarReports } from './useReading'
import styles from './ReportPage.module.css'

function parseChunk(raw: string | null): number | null {
  if (raw == null) return null
  const n = Number(raw)
  return Number.isInteger(n) && n >= 0 ? n : null
}

/**
 * 檢視態解析：URL 為真相，缺 view 參數時由 chunk 決定預設
 * （從檢索命中進來＝文字；直接開啟＝原文）。
 * 資料現實會覆寫意圖：無全文只能看原文，無 PDF 只能看文字。
 */
export function resolveView(
  raw: string | null,
  chunk: number | null,
  doc?: Pick<ReadingDoc, 'text_state' | 'has_file' | 'is_pdf'>,
): DocView {
  const wanted: DocView = raw === 'text' || raw === 'pdf' ? raw : chunk != null ? 'text' : 'pdf'
  if (!doc) return wanted
  if (doc.text_state === 'missing') return 'pdf'
  if (!doc.has_file) return 'text'
  return wanted
}

/** 兩種檢視是否都拿得出東西；否則不給切換鈕（text 缺席＝只給 PDF，不是錯誤）。 */
function canSwitch(doc: Pick<ReadingDoc, 'text_state' | 'has_file'>): boolean {
  return doc.text_state === 'ok' && doc.has_file
}

export default function ReportPage() {
  const { hash = '' } = useParams()
  const [params, setParams] = useSearchParams()
  const [jump, setJump] = useState<JumpTarget | null>(null)

  const valid = isValidHash(hash)
  const chunk = parseChunk(params.get('chunk'))

  const doc = useReadingDoc(hash, valid)
  const similar = useSimilarReports(hash, valid)

  const view = resolveView(params.get('view'), chunk, doc.data)
  // 只有「確定有全文」才抓：骨架未回來前 view 尚未定案（?chunk 會先算成 text），
  // 若不等 doc 就發，text_state=missing 的報告每次都會白打一次必然失敗的請求。
  // chunk 一併帶去：命中段的字元 offset 由後端 anchor.py 算（前端不重造比對）。
  const text = useReadingText(hash, valid && view === 'text' && doc.data?.text_state === 'ok', chunk)

  const patch = useCallback(
    (next: Record<string, string | null>) => {
      setParams(
        prev => {
          const sp = new URLSearchParams(prev)
          for (const [k, v] of Object.entries(next)) {
            if (v == null || v === '') sp.delete(k)
            else sp.set(k, v)
          }
          return sp
        },
        { replace: true },
      )
    },
    [setParams],
  )

  const onViewChange = useCallback((v: DocView) => patch({ view: v }), [patch])

  // nonce 讓「跳同一個目標」也會重播（連點同一條摘錄、重複按回到命中處）
  const jumpTo = useCallback((target: number | 'hit') => {
    setJump(j => ({ target, nonce: (j?.nonce ?? 0) + 1 }))
  }, [])

  // 點摘錄 → 切文字檢視 + 捲到該處 + q-flash
  const onJump = useCallback(
    (ordinal: number) => {
      patch({ view: 'text' })
      jumpTo(ordinal)
    },
    [patch, jumpTo],
  )

  // 關閉命中導航只收掉命中，不該把檢視態一起帶走：view 若不在網址上，
  // 光移除 chunk 會讓 resolveView 退回預設的原文檢視 —— 讀者正在讀的文字就沒了。
  const onCloseHits = useCallback(() => patch({ chunk: null, view }), [patch, view])

  const d = doc.data
  // 引文跳轉的啟用條件（原文檢視下 /text 尚未抓，故不可要求「已載入且相符」，
  // 否則預設的原文檢視裡每條摘錄都會變成不可跳，設計稿的核心互動就沒了）：
  //   有全文可跳 → 先樂觀提供；待 /text 回來若 sha 不符（正典文字已漂移）再收回。
  const textAvailable = d?.text_state === 'ok' && Boolean(d.text_sha256)
  const shaMismatch = Boolean(text.data && d?.text_sha256 && text.data.text_sha256 !== d.text_sha256)
  const canJump = textAvailable && !shaMismatch

  // 命中段的字元區間：後端（/text?chunk=）算好的 offset，錨不到就是 null ——
  // 前端不高亮、不給命中導航，頁面其餘照常（錨不到不是錯誤）。
  // 也綁 chunk：關掉命中導航後，即使快取裡還留著帶 offset 的回應也不該再標。
  const hit = useMemo(() => {
    const t = text.data
    if (chunk == null || !t || t.chunk_start == null || t.chunk_end == null) return null
    return { start: t.chunk_start, end: t.chunk_end }
  }, [chunk, text.data])

  // 全文一到就自動捲到命中段：從檢索命中點進來，要看的就是那一段。
  // 依 offset（而非 hit 物件）觸發：重抓回同一個位置時不該再把讀者拉回去一次。
  const hitStart = hit?.start ?? null
  // set-state-in-effect 在此刻意豁免：jumpTo 的作用是捲動 DOM（外部系統），
  // 它順帶記的狀態只是「目前停在哪個命中」。時機上也必須是 effect——要等全文
  // 真的掛上 DOM 之後才捲得到，render 期做不到。
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (hitStart != null) jumpTo('hit')
  }, [hitStart, jumpTo])

  const similarItems = useMemo(() => similar.data?.items ?? [], [similar.data])

  if (!valid) return <ReportNotFound />
  if (doc.isLoading) return <ReportSkeleton />
  if (doc.isError) return isNotFound(doc.error) ? <ReportNotFound /> : <ReportLoadError onRetry={() => doc.refetch()} />
  if (!d) return <ReportNotFound />

  const showSignals = d.signals_state === 'available' && d.signals.length > 0
  const showTakeaways = d.takeaways.length > 0
  // 只有真的錨到才給命中導航：錨不到卻留著一顆「回到命中處」＝按了什麼也不會發生。
  const showHits = view === 'text' && hit != null

  return (
    <div className={styles.page}>
      <ReportHeader doc={d} />

      <div className={styles.main}>
        <aside className={styles.intel}>
          {d.summary && (
            <section className={styles.sec}>
              <h2 className={styles.secH}>摘要</h2>
              <p className={styles.sum}>{d.summary}</p>
            </section>
          )}

          {showTakeaways && (
            <section className={styles.sec}>
              <h2 className={styles.secH}>重點摘錄</h2>
              <TakeawayList takeaways={d.takeaways} canJump={canJump} onJump={onJump} />
            </section>
          )}

          {/* 觀點：無訊號時整區不進 DOM（不是空框、不是骨架）。
              全語料僅 0.68% 有訊號，這是常態不是錯誤，版面只變短不跳動。 */}
          {showSignals && (
            <section className={styles.sec}>
              <h2 className={styles.secH}>觀點</h2>
              <p className={styles.signalNote}>本篇已擷取結構化訊號 — 全語料僅 0.68% 有。</p>
              {d.signals.map(s => (
                <SignalCard key={`${s.market}-${s.instrument_code}`} signal={s} />
              ))}
            </section>
          )}

          {/* 相似研報：側欄末區塊，自理狀態（錯誤→重試、載入中/空→不渲染，見其元件說明）。
              原為頁底整條橫幅卡片，太佔閱讀區垂直空間，改收進側欄緊湊清單。 */}
          <SimilarReports
            items={similarItems}
            isLoading={similar.isLoading}
            isError={similar.isError}
            onRetry={() => similar.refetch()}
          />
        </aside>

        <section className={styles.doc}>
          <div className={styles.docBar}>
            {canSwitch(d) && <DocViewSwitch value={view} onChange={onViewChange} />}

            {/* 命中導航：後端一次只錨 ?chunk= 的那一段，故這裡只有「一個」命中 ——
                不給 n/N 計數與上一個/下一個，那會讓人以為還有別的命中可翻。
                多命中導航等後端支援多段錨定再補（設計稿的版本）。 */}
            {showHits && (
              <div className={styles.hits}>
                <Pressable
                  className={styles.hitsGo}
                  hoverScale={1}
                  onClick={() => jumpTo('hit')}
                  title="捲回你從檢索點進來的那一段"
                >
                  回到命中處
                </Pressable>
                <Pressable className={styles.hitsX} aria-label="關閉命中導航" onClick={onCloseHits}>
                  <Icon name="x" size={11} strokeWidth={2.2} />
                </Pressable>
              </div>
            )}

            <span className={styles.docFile}>{d.file_name}</span>
          </div>

          {view === 'pdf' ? (
            <PdfPane doc={d} />
          ) : (
            <TextPane
              text={text.data}
              takeaways={d.takeaways}
              canJump={canJump}
              hit={hit}
              jump={jump}
              isLoading={text.isLoading}
              isError={text.isError}
              onRetry={() => text.refetch()}
              hasFile={d.has_file}
            />
          )}
        </section>
      </div>

      {/* 合規要求：頁底免責恆常駐，不得依賴訊號/相似研報等任何選擇性區塊。 */}
      <div className={styles.disc}>
        本平台內容彙整自券商研究報告，僅供內部研究參考，不構成任何投資建議或要約。評等、目標價與論點均為原報告發布券商之意見，非廷豐之立場。投資請自行判斷並承擔風險。
      </div>
    </div>
  )
}
