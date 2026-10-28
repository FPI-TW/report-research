import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import pdfiumWasmUrl from '@embedpdf/pdfium/pdfium.wasm?url'
import { useReducedMotion } from 'motion/react'
import { createPluginRegistration } from '@embedpdf/core'
import { EmbedPDF, useDocumentState } from '@embedpdf/core/react'
import { usePdfiumEngine } from '@embedpdf/engines/react'
import {
  DocumentContent,
  DocumentManagerPluginPackage,
} from '@embedpdf/plugin-document-manager/react'
import {
  InteractionManagerPluginPackage,
  PagePointerProvider,
} from '@embedpdf/plugin-interaction-manager/react'
import { RenderLayer, RenderPluginPackage } from '@embedpdf/plugin-render/react'
// **刻意從基礎套件匯入 SelectionPluginPackage，不是從 `/react`。** `/react` 版把內建的
// CopyToClipboard 工具綁進 package（見其 dist：`.addUtility(CopyToClipboard)`），那支直接
// 呼叫 navigator.clipboard.writeText —— 而本站的區網入口是 HTTP ＋ 私有 IP，非安全情境下
// 那個 API 根本不存在，複製會靜默失敗。複製改由本檔自理，走 lib/clipboard 的 execCommand
// 後備。元件與 hook 仍取自 `/react`。
import { SelectionPluginPackage } from '@embedpdf/plugin-selection'
import { SelectionLayer, useSelectionCapability } from '@embedpdf/plugin-selection/react'
import { Rotate, RotatePluginPackage, useRotate } from '@embedpdf/plugin-rotate/react'
import { SearchLayer, SearchPluginPackage, useSearch } from '@embedpdf/plugin-search/react'
import { Scroller, ScrollPluginPackage, useScroll } from '@embedpdf/plugin-scroll/react'
import { ThumbImg, ThumbnailsPane, ThumbnailPluginPackage } from '@embedpdf/plugin-thumbnail/react'
import { Viewport, ViewportPluginPackage } from '@embedpdf/plugin-viewport/react'
import { ZoomMode, ZoomPluginPackage, useZoom } from '@embedpdf/plugin-zoom/react'
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Copy,
  Minus,
  PanelLeft,
  Plus,
  Search,
  X,
} from 'lucide-react'
import { copyText } from '../../../lib/clipboard'
import { showToast } from '../../../lib/toast'
import { useIsMac } from '../../../lib/useIsMac'
import { StateBox } from '../ReportStates'
import stateStyles from '../ReportStates.module.css'
import { CJK_FONT_FALLBACK } from './fontFallback'
import { needleLadder } from './quoteNeedle'
import { scrollToSearchResult } from './searchScroll'
import { useTakeawayPages, type MarkSource } from './useTakeawayPages'
import styles from './PdfViewer.module.css'

/** 有引文的摘錄（縮圖列標金色編號用）。 */
export type JumpMarkSource = MarkSource

/**
 * PDF 檔本身的載入結果，回報給 ReportPage。
 * `failed`＝引擎好好的、但這份檔開不起來（網路、檔案毀損）：畫布中央給錯誤狀態，
 * 摘錄整批不可點；讀者按「重試」且載入成功後回報 `ready`。
 * 引擎本身起不來是另一條路（拋給 ViewerBoundary 降級成內建檢視），不經這裡。
 */
export type PdfLoadState = 'ready' | 'failed'

interface Props {
  /** 研報原始檔位址（`/api/report/{id}/file`，同源，帶 session cookie） */
  url: string
  /** 無障礙名稱，用研報的顯示標題 */
  title: string
  /** 左欄摘錄送進來的跳轉請求；由 Chrome 消費（那裡才拿得到 search capability） */
  jump?: JumpRequest | null
  /** 有引文的摘錄：縮圖列據此標出各頁的金色編號 */
  marks?: readonly JumpMarkSource[]
  onJumpResult?: (r: JumpResult) => void
  onLoadState?: (s: PdfLoadState) => void
}

/**
 * 引擎＋文件的就緒上限，逾時即拋 → `ViewerBoundary` 退回瀏覽器內建檢視。
 *
 * **存在理由是 2026-07-31 的生產事故**：引擎回傳了 handle、`EmbedPDF` 也掛載了，
 * 但文件永遠開不起來（`activeDocumentId` 恆為 falsy），畫面只剩一個空的 root div
 * ——不拋錯、不降級、console 零訊息。`ViewerBoundary` 只接得住**拋出來的例外**，
 * 接不住「永遠不完成」，所以那次降級機制等同不存在，讀者對著空白乾等到放棄。
 *
 * 15 秒是給冷啟動留足餘裕後的值：`worker: false` 之後 4.6MB 的 wasm 要在主執行緒
 * instantiate，正常情況遠低於此。**寧可偶爾誤判降級到內建檢視（讀者仍讀得到），
 * 也不要再有一次無聲的空白**。
 */
const READY_TIMEOUT_MS = 15_000

/** 縮圖寬度（px）。縮圖列 128px（PdfViewer.module.css 的 .thumbs），縮圖本身 74px 置中。 */
const THUMB_WIDTH = 74

/** 縮放上下限：工具列的 −／＋ 到頭時停用，而不是按了沒反應。 */
const MIN_ZOOM = 0.5
const MAX_ZOOM = 4

/** 文件內搜尋：邊打邊搜，停手這麼久才真的送出（PDFium 在主執行緒跑，每個字都搜會卡）。 */
const FIND_DEBOUNCE_MS = 250

/** 縮圖列開關是個人偏好，存在這台瀏覽器（交接包「State Management」）。 */
const THUMBS_PREF_KEY = 'tf.reader.thumbs'

function readThumbsPref(): boolean {
  try {
    return localStorage.getItem(THUMBS_PREF_KEY) !== '0'
  } catch {
    return true
  }
}

function writeThumbsPref(open: boolean): void {
  try {
    localStorage.setItem(THUMBS_PREF_KEY, open ? '1' : '0')
  } catch {
    // 隱私模式或封鎖了網站資料：偏好記不住，不影響使用
  }
}

/**
 * 選取高亮色。SelectionLayer 以 `mixBlendMode: multiply` 疊色，所以要給**不透明**的
 * 淺色（像螢光筆：白底變成該色、黑字仍是黑字），不是半透明色。
 *
 * 刻意比全站 `::selection` 的 `--tf-gold-tint`(#f8f1e0) 深一階，取 `--tf-gold-line`
 * 的值：那個 tint 是為了配深色網頁底色調的，疊在 PDF 的白紙上幾乎看不見。
 * 值寫死而非讀 CSS 變數，因為這是傳給外掛的 inline style 字串、拿不到 CSS Modules 的類別。
 * 改 tokens.css 的金色系時記得一起看這裡。
 */
const SELECTION_TINT = '#ead9ae'

/** 文件內搜尋的命中色（交接包：目前命中橘色、其他命中淡黃）。同樣是傳給外掛的 inline style。 */
const FIND_HIT = 'rgba(255, 214, 10, 0.4)'
const FIND_CURRENT = '#ffb340'

/** 引擎啟動中：給紙張骨架而不是轉圈圈，載入完成時版面不跳動。 */
function Booting() {
  return (
    <div className={styles.booting} role="status">
      <div className={styles.bootPaper} />
      <span>正在啟動 PDF 引擎…</span>
    </div>
  )
}

/** 左欄送進來的一次跳轉請求。 */
export interface JumpRequest {
  ordinal: number
  quote: string
  /** 連點同一條摘錄也要重跑，所以帶遞增序號 */
  nonce: number
}

export interface JumpResult {
  nonce: number
  ok: boolean
  /** 1-based 頁碼，成功才有 */
  page?: number
  /** 同一份 PDF 裡的命中總數；>1 代表有歧義 */
  total?: number
}

/** 搜尋命中的最小形狀（rects 為頁面座標，未乘縮放）。 */
interface Hit {
  pageIndex: number
  rects: { origin: { x: number; y: number }; size?: { width: number; height: number } }[]
}

/** 跳到原文後畫在頁面上的標記：引文高亮＋頁緣同號金點。 */
interface JumpMark {
  nonce: number
  ordinal: number
  pageIndex: number
  rects: Hit['rects']
}

/** 該頁的**內建**旋轉（PDF 自帶的 /Rotate），不是使用者按 R 轉的那個。 */
function pageRotation(
  pages: readonly { rotation?: number }[] | undefined,
  pageIndex: number | undefined,
): number | undefined {
  if (pageIndex == null) return undefined
  return pages?.[pageIndex]?.rotation
}

/** 頁緣金點的直徑（px）。 */
const MARK_DOT = 20

/**
 * 跳到原文後的標記：引文淡金高亮（進場加深 1.4s）＋在該段左側頁緣浮出同號金點，
 * 與左欄的編號對應。
 *
 * 位置用搜尋命中的 rects（頁面座標 × 縮放），所以要放在 `Rotate` 之內，與搜尋高亮
 * 同一個座標系。金點放在命中段落左側；段落貼著頁緣時退到頁緣內 4px。
 */
function JumpMarkLayer({ mark, scale }: { mark: JumpMark; scale: number }) {
  const first = mark.rects[0]
  if (!first) return null
  const minX = Math.min(...mark.rects.map(r => r.origin.x))
  const lineH = (first.size?.height ?? 0) * scale
  const top = Math.max(0, first.origin.y * scale + lineH / 2 - MARK_DOT / 2)
  const left = Math.max(4, minX * scale - MARK_DOT - 10)
  return (
    <div className={styles.markLayer} aria-hidden="true">
      {mark.rects.map((r, i) => (
        <div
          key={i}
          className={styles.hit}
          style={{
            top: r.origin.y * scale,
            left: r.origin.x * scale,
            width: (r.size?.width ?? 0) * scale,
            height: (r.size?.height ?? 0) * scale,
          }}
        />
      ))}
      <span className={styles.markDot} style={{ top, left, width: MARK_DOT, height: MARK_DOT }}>
        {mark.ordinal}
      </span>
    </div>
  )
}

interface FindBarProps {
  documentId: string
  /** 每次遞增就把焦點送回輸入框並全選（再按一次 Ctrl/⌘ F） */
  focusTick: number
  onClose: () => void
  /** 真的送出一次搜尋時通知（清掉摘錄跳轉留下的標記） */
  onSearch: () => void
  scrollToHit: (hit: Hit | undefined) => void
}

/**
 * 文件內搜尋列：右上浮出（交接包「閱讀頁」PDF 畫布）。
 * 邊打邊搜；Enter 下一個、Shift Enter 上一個、Esc 關閉。輸入法組字中不處理 Enter。
 * 每次打開都從空白開始（摘錄跳轉用的關鍵字不會出現在這裡）。
 */
function FindBar({ documentId, focusTick, onClose, onSearch, scrollToHit }: FindBarProps) {
  const { provides: search, state } = useSearch(documentId)
  const [query, setQuery] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)
  // 最近一次送出的關鍵字：Enter 已立即送出時，延遲計時器到點不再重送一次。
  // 初值是空字串＝「空白已經是現況」：剛打開時不送空搜尋，摘錄跳轉留下的標記要等讀者
  // 真的打字才清掉（原型行為）。
  const lastRunRef = useRef('')

  useEffect(() => {
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [focusTick])

  const run = useCallback(
    (q: string) => {
      if (!search) return
      lastRunRef.current = q
      onSearch()
      const task = search.searchAllPages(q)
      // 送出就捲到第一個命中。被下一次搜尋取代時外掛會中止這個任務（reject），那種不捲。
      task.toPromise().then(
        out => { if (out.total > 0 && lastRunRef.current === q) scrollToHit(out.results[0] as Hit) },
        () => {},
      )
    },
    [search, onSearch, scrollToHit],
  )

  const q = query.trim()
  useEffect(() => {
    if (lastRunRef.current === q) return
    const t = setTimeout(() => {
      if (lastRunRef.current !== q) run(q)
    }, q ? FIND_DEBOUNCE_MS : 0)
    return () => clearTimeout(t)
  }, [q, run])

  const hits = state.results.length
  const settled = q !== '' && state.query === q && !state.loading
  const count = !q ? '' : !settled ? '' : hits > 0 ? `${state.activeResultIndex + 1} / ${hits}` : '0 / 0'

  /**
   * 命中索引 → 捲過去。`goToResult()` 只換 active index，不移動視窗。
   *
   * 從 `state.results` 取值（渲染當下的快照），所以**只給上／下一個命中用**；摘錄跳轉
   * 在 await 之後才拿到結果，要直接把命中物件交給 scrollToHit，見 Chrome 的說明。
   */
  const goToHit = (index: number | undefined) => {
    if (index == null || index < 0) return
    scrollToHit(state.results[index] as Hit | undefined)
  }
  const step = (back: boolean) => goToHit(back ? search?.previousResult() : search?.nextResult())

  return (
    <div className={styles.findBar} role="search">
      <input
        ref={inputRef}
        className={styles.findInput}
        type="search"
        placeholder="在文件中搜尋"
        aria-label="在文件中搜尋"
        value={query}
        onChange={e => setQuery(e.target.value)}
        onKeyDown={e => {
          if (e.key === 'Escape') {
            e.preventDefault()
            e.stopPropagation()
            onClose()
            return
          }
          if (e.key !== 'Enter' || e.nativeEvent.isComposing) return
          e.preventDefault()
          // 同一組關鍵字按 Enter＝跳下一個命中；換了字（延遲計時器還沒到點）就立刻搜
          if (settled && hits > 0) step(e.shiftKey)
          else if (q && state.query !== q) run(q)
        }}
      />
      <span className={styles.findCount} aria-live="polite">{count}</span>
      <button
        type="button"
        className={styles.ib}
        aria-label="上一個命中"
        title="上一個命中（Shift Enter）"
        disabled={!settled || hits === 0}
        onClick={() => step(true)}
      >
        <ChevronUp size={14} strokeWidth={2} aria-hidden />
      </button>
      <button
        type="button"
        className={styles.ib}
        aria-label="下一個命中"
        title="下一個命中（Enter）"
        disabled={!settled || hits === 0}
        onClick={() => step(false)}
      >
        <ChevronDown size={14} strokeWidth={2} aria-hidden />
      </button>
      <button type="button" className={styles.ib} aria-label="關閉搜尋" onClick={onClose}>
        <X size={13} strokeWidth={2} aria-hidden />
      </button>
    </div>
  )
}

interface ChromeProps {
  documentId: string
  thumbsOpen: boolean
  onToggleThumbs: () => void
  findOpen: boolean
  onToggleFind: () => void
  onCloseFind: () => void
  /** 由 ViewerBody 的鍵盤處理器遞增，用來把焦點送進搜尋框（每次遞增觸發一次） */
  focusFindTick: number
  /** 目前有沒有選取文字；有才出現複製鈕 */
  canCopy: boolean
  /** 複製選取文字；回傳是否成功，由本元件給回饋 */
  onCopy: () => Promise<boolean>
  /** 待處理的摘錄跳轉；nonce 讓「再點同一條」也會重跑 */
  jump: JumpRequest | null
  /** 跳轉結果回報給左欄（成功帶頁碼與命中數，失敗只帶 ok:false） */
  onJumpResult: (r: JumpResult) => void
  /** 跳轉開始：收起文件內搜尋列、清掉上一次的標記 */
  onBeginJump: () => void
  /** 跳轉命中：在頁面上畫標記 */
  onJumpHit: (m: JumpMark) => void
  /** 文件內搜尋送出（清掉摘錄跳轉的標記） */
  onFindSearch: () => void
}

/**
 * 底部浮動工具列、文件內搜尋列，以及摘錄跳轉的執行端。
 *
 * 所有控制項都掛在 EmbedPDF provider 之內，因為 useZoom/useScroll 需要 plugin context。
 */
function Chrome({
  documentId,
  thumbsOpen,
  onToggleThumbs,
  findOpen,
  onToggleFind,
  onCloseFind,
  focusFindTick,
  canCopy,
  onCopy,
  jump,
  onJumpResult,
  onBeginJump,
  onJumpHit,
  onFindSearch,
}: ChromeProps) {
  const { provides: zoom, state: zoomState } = useZoom(documentId)
  const { provides: scroll, state: scrollState } = useScroll(documentId)
  const { provides: search } = useSearch(documentId)
  const docState = useDocumentState(documentId)
  const reduced = useReducedMotion() ?? false
  const isMac = useIsMac()

  const level = zoomState?.currentZoomLevel ?? 1
  const pct = Math.round(level * 100)
  const current = scrollState?.currentPage ?? 1
  const total = scrollState?.totalPages ?? 0

  // 頁碼輸入：只在使用者實際編輯時才有草稿值，其餘時間直接顯示目前頁碼。
  // 這樣就不需要「用 effect 把 state 同步到 prop」——那正是本 repo 唯一那處
  // eslint-disable 的來源，不該再開第二處。
  const [pageDraft, setPageDraft] = useState<string | null>(null)
  const goPage = (n: number) => {
    if (Number.isInteger(n) && n >= 1 && n <= total) scroll?.scrollToPage({ pageNumber: n })
  }
  const commitPage = () => {
    goPage(Number(pageDraft))
    setPageDraft(null)
  }

  // 複製結果走全站提示訊息。**失敗一定要說**：非安全情境（區網 HTTP）下複製真的會失敗，
  // 靜默成功是最糟的謊。
  const onCopyClick = () => {
    void onCopy().then(ok => {
      if (ok) showToast('已複製', { ok: true })
      else showToast('複製失敗，請手動複製')
    })
  }

  const pages = docState?.document?.pages ?? undefined

  /** 直接捲到某個命中物件。**跳轉路徑一定要走這支**（goToResult 不捲動，見 searchScroll.ts）。 */
  const scrollToHit = useCallback(
    (hit: Hit | undefined) => {
      scrollToSearchResult(scroll, pageRotation(pages, hit?.pageIndex), hit, reduced)
    },
    [scroll, pages, reduced],
  )

  // 摘錄跳轉＝以該條引文跑一次搜尋。**刻意與文件內搜尋共用同一個 search session**：
  // SearchDocumentState 每份文件只有一個 query/results，另造一套獨立搜尋是與資料模型作對。
  // 跳轉的高亮則是自己畫的（JumpMarkLayer）：文件內搜尋列此時是收起的，外掛的搜尋高亮
  // 只在搜尋列開著時才掛（見 ViewerBody 的 renderPage）。
  //
  // 由 nonce 驅動而非事件：檢視器是 lazy + Suspense + 引擎暖機 + 等文件開啟，共四段
  // 窗口，事件在任一段都會被丟掉。狀態則是「Chrome 掛載時帶著當下的值一起來」。
  //
  // **deps 只有 [jump, search]，其餘一律走 ref。** 這不是效能考量，是正確性：
  // 搜尋是非同步的，而 cleanup 會把 cancelled 設成 true。只要 deps 裡有任何一個
  // 在搜尋進行中換了 identity（scrollToHit 隨 useDocumentState 變、上游 callback
  // 隨父層 state 變），effect 就會取消自己重跑，而重跑立刻被 doneNonceRef 擋掉
  // ——搜尋被中止、沒有任何回報、畫面什麼都不發生。**2026-08-04 的實際災情就是這個。**
  const latest = useRef({ scrollToHit, onBeginJump, onJumpHit, onJumpResult })
  // 宣告在 jump effect **之前**：同一個 commit 內 effect 依宣告順序執行，
  // 所以跳轉讀到的一定是這一輪的最新值。
  useEffect(() => {
    latest.current = { scrollToHit, onBeginJump, onJumpHit, onJumpResult }
  })

  const doneNonceRef = useRef(0)
  useEffect(() => {
    if (!jump || !search || doneNonceRef.current === jump.nonce) return
    doneNonceRef.current = jump.nonce
    let cancelled = false
    latest.current.onBeginJump()
    void (async () => {
      for (const needle of needleLadder(jump.quote)) {
        let out
        try {
          out = await search.searchAllPages(needle).toPromise()
        } catch {
          // **要分辨中止來源，不能一律靜默。** 被下一次點擊（或卸載）取代時，cleanup 已在
          // 新 effect 執行前把 cancelled 設 true，所以那種情況仍然不回報——回報會蓋掉
          // 接手的那一次。但還有兩條沒有後繼者的 reject：使用者關掉文件內搜尋（stopSearch
          // 會對 currentTask 呼叫 abort）、以及引擎層自己失敗。那兩種若也靜默，
          // 「尋找中…」就永遠不會結束。
          if (!cancelled) latest.current.onJumpResult({ nonce: jump.nonce, ok: false })
          return
        }
        if (cancelled) return
        if (out.total > 0) {
          const hit = out.results[0] as Hit
          // **必須等 toPromise 之後才 goToResult**：搜尋過程中的進度事件會把
          // activeResultIndex 設 0，最終 resolve 又硬設一次，提早呼叫會被打回去。
          search.goToResult(0)
          // 用手上的 out.results[0]：await 之後閉包裡的 results 還是搜尋前的空陣列
          latest.current.scrollToHit(hit)
          latest.current.onJumpHit({
            nonce: jump.nonce,
            ordinal: jump.ordinal,
            pageIndex: hit.pageIndex,
            rects: hit.rects,
          })
          latest.current.onJumpResult({
            nonce: jump.nonce,
            ok: true,
            page: hit.pageIndex + 1,
            total: out.total,
          })
          return
        }
      }
      latest.current.onJumpResult({ nonce: jump.nonce, ok: false })
    })()
    return () => {
      cancelled = true
    }
  }, [jump, search])

  return (
    <>
      {findOpen && (
        <FindBar
          documentId={documentId}
          focusTick={focusFindTick}
          onClose={onCloseFind}
          onSearch={onFindSearch}
          scrollToHit={scrollToHit}
        />
      )}

      {/* 置中用 left/right 0＋margin auto，不用 translateX(-50%)：進場動畫的 transform
          會把它蓋掉，整條往右偏（交接包「實測抓到的坑」第 2 條）。 */}
      <div className={styles.dock} role="toolbar" aria-label="文件工具">
        <span className={styles.thumbsGroup}>
          <button
            type="button"
            className={thumbsOpen ? `${styles.ib} ${styles.ibOn}` : styles.ib}
            aria-label="頁面縮圖"
            title="頁面縮圖"
            aria-pressed={thumbsOpen}
            onClick={onToggleThumbs}
          >
            <PanelLeft size={16} strokeWidth={1.6} aria-hidden />
          </button>
          <span className={styles.sep} aria-hidden="true" />
        </span>

        <button
          type="button"
          className={styles.ib}
          aria-label="上一頁"
          title="上一頁（PageUp）"
          disabled={current <= 1}
          onClick={() => goPage(current - 1)}
        >
          <ChevronLeft size={15} strokeWidth={2} aria-hidden />
        </button>
        {/* 頁碼可直接輸入跳頁：長研報要翻到指定頁不必一直捲。外觀像文字，hover/focus 才顯形。 */}
        <span className={styles.pageBox}>
          <input
            className={styles.pageInput}
            type="text"
            inputMode="numeric"
            value={pageDraft ?? String(current)}
            aria-label={`頁碼，共 ${total} 頁`}
            onChange={e => setPageDraft(e.target.value.replace(/\D/g, ''))}
            onFocus={e => e.currentTarget.select()}
            onBlur={commitPage}
            onKeyDown={e => {
              if (e.key === 'Enter') e.currentTarget.blur()
              if (e.key === 'Escape') {
                setPageDraft(null)
                e.currentTarget.blur()
              }
            }}
          />
          <span className={styles.pageTotal}>/ {total}</span>
        </span>
        <button
          type="button"
          className={styles.ib}
          aria-label="下一頁"
          title="下一頁（PageDown）"
          disabled={current >= total}
          onClick={() => goPage(current + 1)}
        >
          <ChevronRight size={15} strokeWidth={2} aria-hidden />
        </button>

        <span className={styles.sep} aria-hidden="true" />

        <button
          type="button"
          className={styles.ib}
          aria-label="縮小"
          title="縮小"
          disabled={level <= MIN_ZOOM + 0.001}
          onClick={() => zoom?.zoomOut()}
        >
          <Minus size={15} strokeWidth={2} aria-hidden />
        </button>
        {/* 點百分比＝重設：回到「頁寬，但不超過 100%」 */}
        <button
          type="button"
          className={`${styles.ib} ${styles.zoomPct}`}
          aria-label={`重設縮放，目前 ${pct}%`}
          title="重設縮放"
          onClick={() => zoom?.requestZoom(ZoomMode.Automatic)}
        >
          {pct}%
        </button>
        <button
          type="button"
          className={styles.ib}
          aria-label="放大"
          title="放大"
          disabled={level >= MAX_ZOOM - 0.001}
          onClick={() => zoom?.zoomIn()}
        >
          <Plus size={15} strokeWidth={2} aria-hidden />
        </button>

        <span className={styles.sep} aria-hidden="true" />

        <button
          type="button"
          className={findOpen ? `${styles.ib} ${styles.ibOn}` : styles.ib}
          aria-label="在文件中搜尋"
          title={`在文件中搜尋（${isMac ? '⌘' : 'Ctrl'} F）`}
          aria-expanded={findOpen}
          onClick={onToggleFind}
        >
          <Search size={15} strokeWidth={2} aria-hidden />
        </button>

        {/* 複製選取文字。快捷鍵 Ctrl/⌘+C 之外還要有這顆鈕：選取是外掛畫的色塊，
            使用者沒有理由知道鍵盤還能用。只在真的有選取時出現。 */}
        {canCopy && (
          <>
            <span className={styles.sep} aria-hidden="true" />
            <button
              type="button"
              className={styles.ib}
              aria-label="複製選取的文字"
              title={`複製選取的文字（${isMac ? '⌘' : 'Ctrl'} C）`}
              onClick={onCopyClick}
            >
              <Copy size={15} strokeWidth={1.8} aria-hidden />
            </button>
          </>
        )}
      </div>
    </>
  )
}

/**
 * 以 EmbedPDF（PDFium/WASM）渲染研報原檔，套用本站自訂的畫布：左側縮圖列、底部浮動
 * 工具列、右上文件內搜尋列。
 *
 * **本元件刻意不自理引擎失敗**：`usePdfiumEngine` 的 error 直接往上拋，由 `PdfPane`
 * 的邊界接住並退回瀏覽器內建 iframe。理由是「回退」是 PdfPane 的職責（它同時握有
 * 非 PDF、無檔案等其他分支），把降級決策分散到兩個地方會出現兩套不一致的降級規則。
 * 唯一的例外是「引擎好好的、但這份檔開不起來」：那不是降級（內建檢視一樣開不起來），
 * 畫布中央給「PDF 載入失敗」與重試。
 *
 * **選取／複製走 `@embedpdf/plugin-selection`（2026-08-03 導入），但那不是文字層。**
 * `SelectionLayer` 只畫 `pointerEvents:none` 的色塊，DOM 裡沒有任何文字節點 ——
 * 意思是：拖曳選字與複製可用（複製由本檔自理，見 Ctrl/⌘+C 與工具列複製鈕），
 * 但**螢幕閱讀器仍然讀不到研報內文**，瀏覽器原生的選取／複製也一樣無效。
 * 那個缺口不是這個外掛能補的，需要真正的 text layer；屬獨立工程，尚未排程。
 */
export default function PdfViewer({ url, title, jump = null, marks, onJumpResult, onLoadState }: Props) {
  // WASM 自架，**刻意不用套件預設的 CDN**：本站在 Cloudflare Tunnel ＋ 登入牆之後，
  // 外連是一個新的失效點，而 WASM 載不到等於整個檢視器起不來。
  // 走 Vite 的 `?url` 讓產物落在 `assets/` —— 那是 `_ImmutableStatic` 已在服務、
  // 且免登入白名單（`/app/assets/`）已涵蓋的路徑；放進 `public/` 會被 SPA 的
  // catch-all 接走並回傳 index.html。
  // fontFallback 必須明確給值：**不給的話引擎會自己套用 jsDelivr CDN 設定**
  // （`fontFallback ?? cdnFontConfig`），等於在登入牆後偷偷開一條外連。理由見 fontFallback.ts。
  // `worker: false` ＝ 走 pdfium-direct-engine（主執行緒），刻意不用預設的 worker 引擎。
  //
  // 2026-07-31 生產實測：worker 路徑會**靜默卡死**——引擎 handle 正常回傳（過了
  // isLoading）、`EmbedPDF` 掛載、DocumentManager 也確實抓了 PDF（伺服器日誌 200），
  // 但 `activeDocumentId` 永遠是 falsy，畫面只剩一個空的 root div。逐項排除過：資產
  // 全 200、wasm 檔正確（`application/wasm`、4.6MB）、PDF 檔本身瀏覽器開得起來、
  // 無 CSP、worker 的 Blob 有帶 `application/javascript`、內嵌 worker 原始碼零 import。
  // 真正的斷點是 **worker 從頭到尾沒有 fetch 過 wasm**（開 DevTools「Disable cache」
  // 多次重載，伺服器日誌一筆 pdfium wasm 請求都沒有）。主執行緒確實送出了
  // `{type:"wasmInit", wasmUrl}`，而 worker 的 `self.onmessage` 對不符條件的訊息
  // **完全靜默、沒有 else 分支**，所以兩端都不會留下任何錯誤。
  //
  // 代價：pdfium 改在主執行緒跑，大檔渲染時會卡住 UI。這是為了先讓讀者看得到研報；
  // 要還原成 worker 只需刪掉這一行，但**還原前請先確認 wasm 真的被抓了**（看伺服器
  // 日誌有沒有 `pdfium-*.wasm` 的請求），否則會回到同一個無訊息的空白畫面。
  const { engine, isLoading, error } = usePdfiumEngine({
    wasmUrl: pdfiumWasmUrl,
    fontFallback: CJK_FONT_FALLBACK,
    worker: false,
  })

  // 「PDF 載入失敗」的重試次數。EmbedPDF 以它為 key 重新掛載＝重建 registry、重抓文件；
  // 引擎（WASM）不受影響，不必重新 instantiate。
  const [attempt, setAttempt] = useState(0)
  const loadKey = `${url}#${attempt}`

  // plugins 需與 url 綁定；每次 render 重建會讓 provider 反覆重載文件。
  // 重試不必換 plugins：registration 只是設定物件，新的 registry 照樣用它們重新初始化。
  const plugins = useMemo(() => buildPlugins(url), [url])

  // 逾時狀態只由計時器寫入、**不在 effect 裡重設**：換 url（或重試）時 `timedOutKey !== loadKey`
  // 自然就是 false，免掉一次 set-state-in-effect（本 repo 對該 lint 規則目前只有一處
  // 豁免且附了理由，不該再開第二處）。
  const [timedOutKey, setTimedOutKey] = useState<string | null>(null)
  // 存「哪一次載入已有結果」而不是布林旗標，於是**不需要在 effect 裡重設**。
  // 用布林＋重設會壞掉，而且壞得很安靜：React 的子 effect 先於父 effect 執行，
  // 所以 ViewerBody 標記就緒之後，父層 effect 才把旗標清回 false，逾時照樣觸發
  // ——正常載入的研報會在 15 秒後無預警跳成內建檢視。反轉測試抓到的正是這條。
  // 「載入失敗」同樣算有結果：錯誤狀態已經給讀者交代了，不能 15 秒後再被逾時改判成降級。
  const settledKeyRef = useRef<string | null>(null)
  // 量測起算點，與逾時同一個生命週期。**不在渲染期取值**——react-hooks 的
  // purity／refs 規則會擋，而那確實不安全。0 ＝「還沒設定就已經就緒」，只可能發生在
  // 同一個 commit 內既載入完又就緒（子 effect 先於父 effect）；那種情況寧可不印，
  // 也不要印以 0 為基準的假數字。
  const startRef = useRef(0)
  useEffect(() => {
    startRef.current = performance.now()
    const timer = setTimeout(() => {
      if (settledKeyRef.current !== loadKey) setTimedOutKey(loadKey)
    }, READY_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [loadKey])

  const onLoadStateRef = useRef(onLoadState)
  useEffect(() => {
    onLoadStateRef.current = onLoadState
  })

  // 就緒＝`ViewerBody` 掛上（＝文件已載入、頁面看得到），不是「引擎有 handle」——
  // 事故當下引擎正是「有 handle 但文件永遠開不起來」。
  const markReady = useCallback(() => {
    if (settledKeyRef.current === loadKey) return
    settledKeyRef.current = loadKey
    // `worker: false` 之後 pdfium 在主執行緒 instantiate（wasm 4.6MB），這條 log 就是
    // 那個成本的量測點——「開很慢」的客訴不必再靠感覺，直接看數字。只在真正就緒時
    // 印一次，換文件才會再印。
    if (startRef.current > 0) {
      console.info('[pdf] 引擎就緒 %dms', Math.round(performance.now() - startRef.current))
    }
    onLoadStateRef.current?.('ready')
  }, [loadKey])

  const markFailed = useCallback(() => {
    if (settledKeyRef.current === loadKey) return
    settledKeyRef.current = loadKey
    console.warn('[pdf] 文件載入失敗：%s', url)
    onLoadStateRef.current?.('failed')
  }, [loadKey, url])

  if (error) throw error
  // 放在 isLoading 之前：引擎卡在 isLoading 與文件卡在開不起來，兩種都要接住。
  if (timedOutKey === loadKey) {
    throw new Error(
      `PDF 引擎逾時未就緒（${READY_TIMEOUT_MS}ms）：` +
        `engine=${engine ? 'ready' : 'null'}、isLoading=${isLoading}`,
    )
  }
  if (isLoading || !engine) {
    return (
      <div className={styles.root}>
        <Booting />
      </div>
    )
  }

  return (
    <div className={styles.root}>
      <EmbedPDF key={attempt} engine={engine} plugins={plugins}>
        {({ activeDocumentId }) =>
          activeDocumentId ? (
            <DocumentContent documentId={activeDocumentId}>
              {({ isLoaded, isError }) =>
                isError ? (
                  <LoadFailed url={url} onShown={markFailed} onRetry={() => setAttempt(a => a + 1)} />
                ) : isLoaded ? (
                  <ViewerBody
                    documentId={activeDocumentId}
                    title={title}
                    engine={engine}
                    marks={marks}
                    onReady={markReady}
                    jump={jump}
                    onJumpResult={onJumpResult}
                  />
                ) : (
                  <Booting />
                )
              }
            </DocumentContent>
          ) : (
            <Booting />
          )
        }
      </EmbedPDF>
    </div>
  )
}

function buildPlugins(url: string) {
  return [
    createPluginRegistration(DocumentManagerPluginPackage, {
      initialDocuments: [{ url }],
    }),
    // 頁面四周留白 24px；底部多讓出浮動工具列的高度（見 .viewport 的 padding-bottom）
    createPluginRegistration(ViewportPluginPackage, { viewportGap: 24 }),
    createPluginRegistration(ScrollPluginPackage, { defaultPageGap: 20 }),
    createPluginRegistration(RenderPluginPackage),
    // Automatic＝min(頁寬, 100%)：頁面寬度在 100% 以下時撐滿可視寬度，但不放大超過原尺寸
    // （交接包：頁面寬度在 100% 以下時 min(100%, 頁寬)，不超過可視區）。
    createPluginRegistration(ZoomPluginPackage, {
      defaultZoomLevel: ZoomMode.Automatic,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
    }),
    // **寬度必須明講**：外掛預設 width 120，縮圖列只有 128px 且要留邊——不給值的話
    // 縮圖會貼齊兩側、金色編號沒地方放，而且不會有任何錯誤。改動要同時看 .thumbs。
    createPluginRegistration(ThumbnailPluginPackage, {
      width: THUMB_WIDTH,
      gap: 16,
      labelHeight: 24,
      paddingY: 12,
    }),
    createPluginRegistration(RotatePluginPackage),
    createPluginRegistration(SearchPluginPackage),
    // **順序有意義**：selection 的 peerDependency 就是 interaction-manager，它的
    // 指標處理器註冊在 interaction-manager 的預設 `pointerMode` 上。反過來註冊，
    // 選取會安靜地完全沒反應。
    createPluginRegistration(InteractionManagerPluginPackage),
    createPluginRegistration(SelectionPluginPackage),
  ]
}

/**
 * PDF 檔開不起來（網路、檔案毀損）。縮圖列與工具列都不在（沒有文件可操作），
 * 左欄的摘錄由 ReportPage 收成不可點。
 */
function LoadFailed({ url, onShown, onRetry }: { url: string; onShown: () => void; onRetry: () => void }) {
  useEffect(onShown, [onShown])
  return (
    <div className={styles.failed}>
      <StateBox
        role="alert"
        onCanvas
        icon={<AlertCircle size={18} strokeWidth={1.8} />}
        title="PDF 載入失敗"
        desc="左側的摘要與重點摘錄仍可閱讀。"
      >
        <button type="button" className={stateStyles.btn} onClick={onRetry}>重試</button>
        <a className={stateStyles.btn} href={url} download>下載原始檔</a>
      </StateBox>
    </div>
  )
}

/** 有沒有彈窗開在閱讀頁上面：開著時閱讀頁的快捷鍵一律讓給它（Esc 由最上層往下關）。 */
function modalOpen(): boolean {
  return Boolean(document.querySelector('[aria-modal="true"], [role="dialog"][data-state="open"]'))
}

/** provider 之內的實體：縮圖列 ＋ viewport ＋ 頁面 ＋ chrome（hooks 需要 plugin context） */
function ViewerBody({
  documentId,
  title,
  engine,
  marks,
  onReady,
  jump,
  onJumpResult,
}: {
  documentId: string
  title: string
  engine: unknown
  marks?: readonly JumpMarkSource[]
  onReady: () => void
  jump: JumpRequest | null
  onJumpResult?: (r: JumpResult) => void
}) {
  const [thumbsOpen, setThumbsOpen] = useState(readThumbsPref)
  const [findOpen, setFindOpen] = useState(false)
  const [focusFindTick, setFocusFindTick] = useState(0)
  const [jumpMark, setJumpMark] = useState<JumpMark | null>(null)
  const { provides: scroll, state: scrollState } = useScroll(documentId)
  const { provides: zoom } = useZoom(documentId)
  const { provides: rotate } = useRotate(documentId)
  const { provides: search } = useSearch(documentId)
  const { provides: selection } = useSelectionCapability()
  const docState = useDocumentState(documentId)
  const scale = docState?.scale ?? 1
  const markPages = useTakeawayPages(engine, docState?.document, marks)
  const stageRef = useRef<HTMLDivElement>(null)
  // 本元件掛上就代表文件真的載入了，於是解除上層的逾時。
  useEffect(onReady, [onReady])

  const toggleThumbs = () => {
    const next = !thumbsOpen
    setThumbsOpen(next)
    writeThumbsPref(next)
  }

  const openFind = useCallback(() => {
    setFindOpen(true)
    setFocusFindTick(n => n + 1)
  }, [])
  // 關掉文件內搜尋要同時結束外掛的搜尋 session：不 stopSearch 的話高亮會留在頁面上。
  // 焦點還給畫布（搜尋框卸載後焦點會掉到 body）。
  const closeFind = useCallback(() => {
    setFindOpen(false)
    search?.stopSearch()
    stageRef.current?.querySelector<HTMLElement>('[role="document"]')?.focus({ preventScroll: true })
  }, [search])

  // 這幾個要穩定：它們是 Chrome 內那個 jump effect 的依賴（經 ref），每次 render 換新的
  // 雖然不會重跑跳轉，但會讓 FindBar 的 run 跟著換 identity。
  // 跳轉開始時**只收起**搜尋列、不 stopSearch：跳轉自己的 searchAllPages 會接手 session，
  // stop 會把它中止成「找不到」。
  const beginJump = useCallback(() => {
    setFindOpen(false)
    setJumpMark(null)
  }, [])
  const clearJumpMark = useCallback(() => setJumpMark(null), [])
  const reportJump = useCallback((r: JumpResult) => onJumpResult?.(r), [onJumpResult])

  // 有沒有選取，決定 Ctrl/⌘+C 要不要攔、工具列的複製鈕要不要出現。
  // **必須訂閱而不是複製時才查**：沒有選取時不該攔截 Ctrl+C，那會把使用者在頁面
  // 其他地方（研報面板、摘錄）的正常複製一起吃掉。
  const [hasSelection, setHasSelection] = useState(false)
  useEffect(() => {
    if (!selection) return
    return selection.forDocument(documentId).onSelectionChange(sel => setHasSelection(Boolean(sel)))
  }, [selection, documentId])

  /** 複製目前選取的文字。回傳是否成功，供呼叫端給回饋。 */
  const copySelection = useCallback(async (): Promise<boolean> => {
    const scoped = selection?.forDocument(documentId)
    if (!scoped) return false
    try {
      // getSelectedText 逐頁回一段，跨頁選取要接起來
      const parts = await scoped.getSelectedText().toPromise()
      const text = parts.join('\n').trim()
      if (!text) return false
      await copyText(text)
      return true
    } catch {
      // 非安全情境連 execCommand 都失敗、或引擎抽字失敗。回 false 讓 UI 說實話，
      // 不要假裝複製成功——那正是內建 CopyToClipboard 的失敗方式。
      return false
    }
  }, [selection, documentId])

  // 鍵盤操作。掛在 window 是安全的：離開閱讀頁時 ReportPage 會整個卸載 PdfPane
  // （內嵌不了 PDF 的研報則根本不掛，見 ReportPage.tsx 的 pdfViewable），
  // 所以這個監聽只在 PDF 真的顯示時存在。
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing || modalOpen()) return
      const t = e.target as HTMLElement | null
      // 正在打字一律讓行——這個畫面上同時有搜尋框與頁碼輸入
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      if (e.altKey) return

      // Ctrl/⌘+F：**刻意攔截瀏覽器的尋找**。PDF 內容是 canvas，不在 DOM 裡，
      // 瀏覽器原生尋找對它完全無效——不攔截等於把使用者導向一個必然失望的功能。
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        openFind()
        return
      }

      // Ctrl/⌘+C：同樣得攔——選取是外掛自己畫的色塊，DOM 裡沒有任何文字節點，
      // 瀏覽器原生複製會複製到空字串。**只在真的有選取時攔**，否則會把使用者
      // 在研報面板或摘錄上的正常複製一起吃掉。
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c') {
        if (!hasSelection) return
        e.preventDefault()
        void copySelection().then(ok => {
          if (ok) showToast('已複製', { ok: true })
          else showToast('複製失敗，請手動複製')
        })
        return
      }
      if (e.ctrlKey || e.metaKey) return

      const total = scrollState?.totalPages ?? 0
      const page = scrollState?.currentPage ?? 1
      switch (e.key) {
        case '/':
          e.preventDefault()
          openFind()
          break
        case 'Escape':
          if (findOpen) closeFind()
          break
        case 'PageDown':
          e.preventDefault()
          scroll?.scrollToPage({ pageNumber: Math.min(page + 1, total || page + 1) })
          break
        case 'PageUp':
          e.preventDefault()
          scroll?.scrollToPage({ pageNumber: Math.max(page - 1, 1) })
          break
        case 'Home':
          e.preventDefault()
          scroll?.scrollToPage({ pageNumber: 1 })
          break
        case 'End':
          if (total) {
            e.preventDefault()
            scroll?.scrollToPage({ pageNumber: total })
          }
          break
        case '+':
        case '=':
          e.preventDefault()
          zoom?.zoomIn()
          break
        case '-':
          e.preventDefault()
          zoom?.zoomOut()
          break
        case '0':
          e.preventDefault()
          zoom?.requestZoom(ZoomMode.Automatic)
          break
        case 'r':
        case 'R':
          rotate?.rotateForward()
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [
    scroll,
    zoom,
    rotate,
    scrollState?.currentPage,
    scrollState?.totalPages,
    hasSelection,
    copySelection,
    findOpen,
    openFind,
    closeFind,
  ])

  const current = scrollState?.currentPage ?? 1

  return (
    <div className={styles.body}>
      {/* 縮圖列：與畫布並排（推擠而非覆蓋），收合時寬度歸零。收起時 inert，
          裡面的按鈕不進 Tab 序。<1024px 整列不顯示。 */}
      <nav
        className={thumbsOpen ? styles.thumbs : `${styles.thumbs} ${styles.thumbsOff}`}
        aria-label="頁面縮圖"
        inert={!thumbsOpen}
      >
        <ThumbnailsPane documentId={documentId} className={styles.thumbsScroll}>
          {meta => {
            const n = meta.pageIndex + 1
            const mk = markPages.get(meta.pageIndex) ?? []
            const on = n === current
            return (
              <button
                key={meta.pageIndex}
                type="button"
                className={on ? `${styles.thumb} ${styles.thumbOn}` : styles.thumb}
                style={{ top: meta.top, height: meta.wrapperHeight }}
                aria-label={`第 ${n} 頁${mk.length ? `，含重點摘錄 ${mk.join('、')}` : ''}`}
                aria-current={on ? 'page' : undefined}
                onClick={() => scroll?.scrollToPage({ pageNumber: n })}
              >
                <span className={styles.thumbImg} style={{ width: meta.width, height: meta.height }}>
                  <ThumbImg documentId={documentId} meta={meta} />
                  {mk.length > 0 && <span className={styles.thumbMark} aria-hidden="true">{mk.join(' ')}</span>}
                </span>
                <span className={styles.thumbNo} aria-hidden="true">{n}</span>
              </button>
            )
          }}
        </ThumbnailsPane>
      </nav>

      <div className={styles.stage} ref={stageRef}>
        {/* 畫布可聚焦：PageUp／PageDown 翻頁、Home／End 首末頁（鍵盤處理在上面的 window 監聽） */}
        <Viewport
          documentId={documentId}
          className={styles.viewport}
          tabIndex={0}
          role="document"
          aria-label={title}
        >
          <Scroller
            documentId={documentId}
            renderPage={({ width, height, pageIndex }) => (
              <div
                className={styles.page}
                style={{ width, height }}
                // **RenderLayer 渲染的是 `<img>`**，所以在頁面上拖曳會觸發瀏覽器原生的
                // 圖片拖放：整頁的半透明殘影跟著游標跑，選取還沒開始就被打斷。
                // dragstart 會從 img 冒泡上來，在這裡擋掉是唯一跨瀏覽器有效的做法
                // （`-webkit-user-drag` Firefox 不支援，而 img 的 draggable 屬性
                //   在外掛內部，我們設不到）。
                onDragStart={e => e.preventDefault()}
              >
                {/* Rotate 以 transform 包住頁面內容。紙張外框不必轉——scroll 外掛給的
                    width/height 已依旋轉算好；要轉的是裡面的內容。搜尋高亮與跳轉標記
                    必須放在 Rotate 之內，否則旋轉後會留在原座標系、指到錯的位置。

                    PagePointerProvider 也必須在 Rotate **之內**，理由同源但更隱蔽：
                    它的預設座標轉換是 `restorePosition(..., rotation, scale)`，也就是
                    「元素已被視覺旋轉，我把指標位置反算回原始頁座標」。放到 Rotate 之外
                    元素其實沒轉，那個反算就會多轉一次——旋轉後選取到的字會整片偏掉，
                    而且不會有任何錯誤，只是選錯地方。 */}
                <Rotate documentId={documentId} pageIndex={pageIndex}>
                  <PagePointerProvider documentId={documentId} pageIndex={pageIndex}>
                    <RenderLayer documentId={documentId} pageIndex={pageIndex} />
                    {/* 外掛的搜尋高亮只在文件內搜尋列開著時掛：摘錄跳轉也走同一個 session，
                        但它的高亮由 JumpMarkLayer 自己畫（只標第一處、淡金、帶頁緣金點）。 */}
                    {findOpen && (
                      <SearchLayer
                        documentId={documentId}
                        pageIndex={pageIndex}
                        highlightColor={FIND_HIT}
                        activeHighlightColor={FIND_CURRENT}
                      />
                    )}
                    <SelectionLayer
                      documentId={documentId}
                      pageIndex={pageIndex}
                      background={SELECTION_TINT}
                    />
                  </PagePointerProvider>
                  {jumpMark?.pageIndex === pageIndex && (
                    <JumpMarkLayer key={jumpMark.nonce} mark={jumpMark} scale={scale} />
                  )}
                </Rotate>
              </div>
            )}
          />
        </Viewport>
        <Chrome
          documentId={documentId}
          thumbsOpen={thumbsOpen}
          onToggleThumbs={toggleThumbs}
          findOpen={findOpen}
          onToggleFind={() => (findOpen ? closeFind() : openFind())}
          onCloseFind={closeFind}
          focusFindTick={focusFindTick}
          canCopy={hasSelection}
          onCopy={copySelection}
          jump={jump}
          onJumpResult={reportJump}
          onBeginJump={beginJump}
          onJumpHit={setJumpMark}
          onFindSearch={clearJumpMark}
        />
      </div>
    </div>
  )
}
