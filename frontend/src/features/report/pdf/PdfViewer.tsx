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
import {
  Viewport,
  ViewportPluginPackage,
  useViewportScrollActivity,
} from '@embedpdf/plugin-viewport/react'
import { ZoomMode, ZoomPluginPackage, useZoom } from '@embedpdf/plugin-zoom/react'
import { Icon } from '../../../components/primitives/Icon'
import { copyText } from '../../../lib/clipboard'
import { CJK_FONT_FALLBACK } from './fontFallback'
import { needleLadder } from './quoteNeedle'
import { scrollToSearchResult } from './searchScroll'
import styles from './PdfViewer.module.css'

interface Props {
  /** 研報原始檔位址（`/api/report/{id}/file`，同源，帶 session cookie） */
  url: string
  /** 無障礙名稱，用研報的顯示標題 */
  title: string
  /** 左欄摘錄送進來的跳轉請求；由 Chrome 消費（那裡才拿得到 search capability） */
  jump?: JumpRequest | null
  onJumpResult?: (r: JumpResult) => void
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

/** 縮圖寬度（px）。與 PdfViewer.module.css 的 `.thumbs` 欄寬 96px 綁在一起，見註冊處。 */
const THUMB_WIDTH = 72

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

/** 引擎啟動中：給紙張骨架而不是轉圈圈，載入完成時版面不跳動。 */
function Booting() {
  return (
    <div className={styles.booting} role="status">
      <div className={styles.bootPaper} />
      <span>正在啟動 PDF 引擎…</span>
    </div>
  )
}

interface ChromeProps {
  documentId: string
  url: string
  title: string
  thumbsOpen: boolean
  onToggleThumbs: () => void
  searchOpen: boolean
  onToggleSearch: () => void
  /** 由 ViewerBody 的鍵盤處理器遞增，用來把焦點送進搜尋框（每次遞增觸發一次） */
  focusSearchTick: number
  /** 目前有沒有選取文字；否則複製鈕停用（而不是按了沒反應） */
  canCopy: boolean
  /** 複製選取文字；回傳是否成功，由本元件給回饋 */
  onCopy: () => Promise<boolean>
  /** 待處理的摘錄跳轉；nonce 讓「再點同一條」也會重跑 */
  jump: JumpRequest | null
  /** 跳轉結果回報給左欄（成功帶頁碼與命中數，失敗只帶 ok:false） */
  onJumpResult: (r: JumpResult) => void
  /** 打開搜尋列（**不是** toggle：跳轉時要確保它是開的） */
  onOpenSearch: () => void
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
  /** 同一份 PDF 裡的命中總數；>1 代表有歧義，讀者要用上下一個命中確認 */
  total?: number
}

/** 該頁的**內建**旋轉（PDF 自帶的 /Rotate），不是使用者按 R 轉的那個。 */
function pageRotation(
  pages: readonly { rotation?: number }[] | undefined,
  pageIndex: number | undefined,
): number | undefined {
  if (pageIndex == null) return undefined
  return pages?.[pageIndex]?.rotation
}

/**
 * 工具列與縮圖列。
 *
 * 所有控制項都掛在 EmbedPDF provider 之內，因為 useZoom/useScroll 需要 plugin context。
 */
function Chrome({
  documentId,
  url,
  title,
  thumbsOpen,
  onToggleThumbs,
  searchOpen,
  onToggleSearch,
  focusSearchTick,
  canCopy,
  onCopy,
  jump,
  onJumpResult,
  onOpenSearch,
}: ChromeProps) {
  const { provides: zoom, state: zoomState } = useZoom(documentId)
  const { provides: scroll, state: scrollState } = useScroll(documentId)
  const { provides: rotate } = useRotate(documentId)
  const { provides: search, state: searchState } = useSearch(documentId)
  const { isScrolling } = useViewportScrollActivity(documentId)
  const docState = useDocumentState(documentId)
  const reduced = useReducedMotion() ?? false

  const pct = Math.round((zoomState?.currentZoomLevel ?? 1) * 100)
  const current = scrollState?.currentPage ?? 1
  const total = scrollState?.totalPages ?? 0

  // 頁碼輸入：只在使用者實際編輯時才有草稿值，其餘時間直接顯示目前頁碼。
  // 這樣就不需要「用 effect 把 state 同步到 prop」——那正是本 repo 唯一那處
  // eslint-disable 的來源，不該再開第二處。
  const [pageDraft, setPageDraft] = useState<string | null>(null)
  const commitPage = () => {
    const n = Number(pageDraft)
    if (Number.isInteger(n) && n >= 1 && n <= total) scroll?.scrollToPage({ pageNumber: n })
    setPageDraft(null)
  }

  const [query, setQuery] = useState('')
  const searchInputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    if (focusSearchTick > 0) searchInputRef.current?.focus()
  }, [focusSearchTick])

  const hits = searchState.results.length
  const activeHit = searchState.activeResultIndex

  // 複製回饋：成功→「已複製」，失敗→「請手動複製」。都會在 1.6 秒後回復。
  // **失敗一定要說**：非安全情境（區網 HTTP）下複製真的會失敗，靜默成功是最糟的謊。
  const [copyMsg, setCopyMsg] = useState<'done' | 'fail' | null>(null)
  const copyTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => () => { if (copyTimer.current) clearTimeout(copyTimer.current) }, [])
  const onCopyClick = () => {
    void onCopy().then(ok => {
      setCopyMsg(ok ? 'done' : 'fail')
      if (copyTimer.current) clearTimeout(copyTimer.current)
      copyTimer.current = setTimeout(() => setCopyMsg(null), 1600)
    })
  }

  // 開關搜尋要同時開關引擎的搜尋 session：關掉時若不 stopSearch，高亮會留在頁面上。
  useEffect(() => {
    if (!search) return
    if (searchOpen) search.startSearch()
    else search.stopSearch()
  }, [search, searchOpen])

  const pages = docState?.document?.pages ?? undefined

  /** 直接捲到某個命中物件。**跳轉路徑一定要走這支** —— 見下面 goToHit 的說明。 */
  const scrollToHit = useCallback(
    (hit: { pageIndex: number; rects: { origin: { x: number; y: number } }[] } | undefined) => {
      scrollToSearchResult(scroll, pageRotation(pages, hit?.pageIndex), hit, reduced)
    },
    [scroll, pages, reduced],
  )

  /**
   * 命中索引 → 捲過去。`goToResult()` 只換 active index，不移動視窗。
   *
   * **只給工具列的上／下一個命中用**：它從 `searchState.results` 取值，而那是渲染當下的
   * 快照。摘錄跳轉不能用它——那條路徑在 await 之後才拿到結果，此時閉包裡的 results
   * 還是搜尋前的空陣列，會安靜地什麼都不捲。跳轉請直接把命中物件交給 scrollToHit。
   */
  const goToHit = useCallback(
    (index: number | undefined) => {
      if (index == null || index < 0) return
      scrollToHit(searchState.results[index])
    },
    [scrollToHit, searchState.results],
  )

  // 摘錄跳轉＝以該條引文跑一次搜尋。**刻意與工具列共用同一個 search session**：
  // SearchDocumentState 每份文件只有一個 query/results，另造一套獨立高亮是與資料模型
  // 作對，而共用之後計數（1 / 2）、上下一個命中、Esc 清除全部天然一致。
  //
  // 由 nonce 驅動而非事件：檢視器是 lazy + Suspense + 引擎暖機 + 等文件開啟，共四段
  // 窗口，事件在任一段都會被丟掉。狀態則是「Chrome 掛載時帶著當下的值一起來」。
  //
  // **deps 只有 [jump, search]，其餘一律走 ref。** 這不是效能考量，是正確性：
  // 搜尋是非同步的，而 cleanup 會把 cancelled 設成 true。只要 deps 裡有任何一個
  // 在搜尋進行中換了 identity（scrollToHit 隨 useDocumentState 變、上游 callback
  // 隨父層 state 變），effect 就會取消自己重跑，而重跑立刻被 doneNonceRef 擋掉
  // ——搜尋被中止、沒有任何回報、畫面什麼都不發生。**2026-08-04 的實際災情就是這個。**
  // setQuery 不在裡面：useState 的 setter 本來就保證穩定，放進來只會讓
  // exhaustive-deps 以為這個 effect 會觸發更新迴圈。
  const latest = useRef({ scrollToHit, onOpenSearch, onJumpResult })
  // 宣告在 jump effect **之前**：同一個 commit 內 effect 依宣告順序執行，
  // 所以跳轉讀到的一定是這一輪的最新值。
  useEffect(() => {
    latest.current = { scrollToHit, onOpenSearch, onJumpResult }
  })

  const doneNonceRef = useRef(0)
  useEffect(() => {
    if (!jump || !search || doneNonceRef.current === jump.nonce) return
    doneNonceRef.current = jump.nonce
    let cancelled = false
    latest.current.onOpenSearch()
    void (async () => {
      for (const needle of needleLadder(jump.quote)) {
        // 同步輸入框：不同步的話上面那個計數與「無相符」會對著另一組關鍵字說話
        setQuery(needle)
        let out
        try {
          out = await search.searchAllPages(needle).toPromise()
        } catch {
          // **要分辨中止來源，不能一律靜默。** 被下一次點擊（或卸載）取代時，cleanup 已在
          // 新 effect 執行前把 cancelled 設 true，所以那種情況仍然不回報——回報會蓋掉
          // 接手的那一次。但還有兩條沒有後繼者的 reject：使用者關掉搜尋列（stopSearch 會
          // 對 currentTask 呼叫 abort）、以及引擎層自己失敗。那兩種若也靜默，
          // 「尋找中…」就永遠不會結束。
          if (!cancelled) latest.current.onJumpResult({ nonce: jump.nonce, ok: false })
          return
        }
        if (cancelled) return
        if (out.total > 0) {
          // **必須等 toPromise 之後才 goToResult**：搜尋過程中的進度事件會把
          // activeResultIndex 設 0，最終 resolve 又硬設一次，提早呼叫會被打回去。
          search.goToResult(0)
          // 用手上的 out.results[0]，**不是** goToHit(0)：見 goToHit 的說明
          latest.current.scrollToHit(out.results[0])
          latest.current.onJumpResult({
            nonce: jump.nonce,
            ok: true,
            page: out.results[0].pageIndex + 1,
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
      <div className={thumbsOpen ? `${styles.thumbs} ${styles.thumbsOpen}` : styles.thumbs}>
        <ThumbnailsPane documentId={documentId} className={styles.thumbsScroll}>
          {meta => (
            <button
              key={meta.pageIndex}
              type="button"
              className={
                meta.pageIndex + 1 === current
                  ? `${styles.thumbBtn} ${styles.thumbCurrent}`
                  : styles.thumbBtn
              }
              style={{ top: meta.top, height: meta.wrapperHeight }}
              aria-label={`跳至第 ${meta.pageIndex + 1} 頁`}
              onClick={() => scroll?.scrollToPage({ pageNumber: meta.pageIndex + 1 })}
            >
              <span className={styles.thumbImg} style={{ width: meta.width, height: meta.height }}>
                <ThumbImg documentId={documentId} meta={meta} />
              </span>
              <span className={styles.thumbNo}>{meta.pageIndex + 1}</span>
            </button>
          )}
        </ThumbnailsPane>
      </div>

      <div className={isScrolling ? `${styles.island} ${styles.islandDim}` : styles.island}>
        <button
          type="button"
          className={thumbsOpen ? `${styles.btn} ${styles.btnOn}` : styles.btn}
          aria-label="縮圖列"
          aria-pressed={thumbsOpen}
          onClick={onToggleThumbs}
        >
          <Icon name="panel" size={15} />
        </button>
        <button
          type="button"
          className={searchOpen ? `${styles.btn} ${styles.btnOn}` : styles.btn}
          aria-label="搜尋原文"
          aria-pressed={searchOpen}
          onClick={onToggleSearch}
        >
          <Icon name="search" size={15} />
        </button>

        <span className={styles.sep} aria-hidden="true" />

        <button type="button" className={styles.btn} aria-label="縮小" onClick={() => zoom?.zoomOut()}>
          <Icon name="minus" size={15} />
        </button>
        <span className={styles.zoom}>{pct}%</span>
        <button type="button" className={styles.btn} aria-label="放大" onClick={() => zoom?.zoomIn()}>
          <Icon name="plus" size={15} />
        </button>
        <button
          type="button"
          className={styles.btn}
          aria-label="符合寬度"
          onClick={() => zoom?.requestZoom(ZoomMode.FitWidth)}
        >
          <Icon name="arrowsHorizontal" size={15} />
        </button>

        <button
          type="button"
          className={styles.btn}
          aria-label="順時針旋轉 90 度"
          onClick={() => rotate?.rotateForward()}
        >
          <Icon name="refresh" size={15} />
        </button>

        <span className={styles.sep} aria-hidden="true" />

        {/* 頁碼可直接輸入跳頁：原本只有捲動時浮現的唯讀膠囊，長研報要翻到指定頁只能捲。 */}
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

        <span className={styles.sep} aria-hidden="true" />

        {/* 複製選取文字。快捷鍵 Ctrl/⌘+C 之外還要有這顆鈕：選取是外掛畫的色塊，
            使用者沒有理由知道鍵盤還能用。無選取時 disabled，不做「按了沒反應」。 */}
        <button
          type="button"
          className={styles.btn}
          disabled={!canCopy}
          aria-label={copyMsg === 'fail' ? '複製失敗，請手動複製' : '複製選取的文字'}
          title={canCopy ? '複製選取的文字（Ctrl/⌘+C）' : '先在頁面上選取文字'}
          onClick={onCopyClick}
        >
          <Icon name={copyMsg === 'done' ? 'check' : 'copy'} size={15} />
        </button>
        {/* 結果只用 aria-live 報讀＋圖示變化，不插入會撐開工具列的文字 */}
        <span className={styles.srOnly} role="status">
          {copyMsg === 'done' ? '已複製' : copyMsg === 'fail' ? '複製失敗，請手動複製' : ''}
        </span>

        {/* 下載走原本的檔案端點，不經引擎 —— 使用者要的是券商原檔本身 */}
        <a className={styles.btn} href={url} download aria-label={`下載 ${title}`}>
          <Icon name="download" size={15} />
        </a>
      </div>

      {searchOpen && (
        <div className={styles.searchBar} role="search">
          <Icon name="search" size={14} />
          <input
            ref={searchInputRef}
            className={styles.searchInput}
            type="text"
            placeholder="搜尋研報原文"
            aria-label="搜尋研報原文"
            value={query}
            onChange={e => setQuery(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Escape') {
                onToggleSearch()
                return
              }
              if (e.key !== 'Enter') return
              e.preventDefault()
              // 同一組關鍵字按 Enter＝跳下一個命中；換了字才重新全文搜尋。
              if (searchState.query === query && hits > 0) {
                goToHit(e.shiftKey ? search?.previousResult() : search?.nextResult())
              } else if (query.trim()) {
                search?.searchAllPages(query)
              }
            }}
          />
          <span className={styles.searchCount} aria-live="polite">
            {searchState.loading
              ? '搜尋中…'
              : hits > 0
                ? `${activeHit + 1} / ${hits}`
                : searchState.query === query && query
                  ? '無相符'
                  : ''}
          </span>
          <button
            type="button"
            className={styles.btn}
            aria-label="上一個命中"
            disabled={hits === 0}
            onClick={() => goToHit(search?.previousResult())}
          >
            <Icon name="chevronDown" size={14} className={styles.flip} />
          </button>
          <button
            type="button"
            className={styles.btn}
            aria-label="下一個命中"
            disabled={hits === 0}
            onClick={() => goToHit(search?.nextResult())}
          >
            <Icon name="chevronDown" size={14} />
          </button>
          <button type="button" className={styles.btn} aria-label="關閉搜尋" onClick={onToggleSearch}>
            <Icon name="x" size={14} />
          </button>
        </div>
      )}

      <div className={isScrolling ? `${styles.pill} ${styles.pillOn}` : styles.pill} aria-hidden="true">
        {current} / {total}
      </div>
    </>
  )
}

/**
 * 以 EmbedPDF（PDFium/WASM）渲染研報原檔，套用本站自訂的 chrome。
 *
 * **本元件刻意不自理引擎失敗**：`usePdfiumEngine` 的 error 直接往上拋，由 `PdfPane`
 * 的邊界接住並退回瀏覽器內建 iframe。理由是「回退」是 PdfPane 的職責（它同時握有
 * 非 PDF、無檔案等其他分支），把降級決策分散到兩個地方會出現兩套不一致的降級規則。
 *
 * **選取／複製走 `@embedpdf/plugin-selection`（2026-08-03 導入），但那不是文字層。**
 * `SelectionLayer` 只畫 `pointerEvents:none` 的色塊，DOM 裡沒有任何文字節點 ——
 * 意思是：拖曳選字與複製可用（複製由本檔自理，見 Ctrl/⌘+C 與工具列複製鈕），
 * 但**螢幕閱讀器仍然讀不到研報內文**，瀏覽器原生的選取／複製也一樣無效。
 * 那個缺口不是這個外掛能補的，需要真正的 text layer；屬獨立工程，尚未排程。
 */
export default function PdfViewer({ url, title, jump = null, onJumpResult }: Props) {
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

  // plugins 需與 url 綁定；每次 render 重建會讓 provider 反覆重載文件。
  const plugins = useMemo(
    () => [
      createPluginRegistration(DocumentManagerPluginPackage, {
        initialDocuments: [{ url }],
      }),
      createPluginRegistration(ViewportPluginPackage, { viewportGap: 18 }),
      createPluginRegistration(ScrollPluginPackage),
      createPluginRegistration(RenderPluginPackage),
      createPluginRegistration(ZoomPluginPackage, { defaultZoomLevel: ZoomMode.FitWidth }),
      // **寬度必須明講**：外掛預設 width 150，而縮圖列只有 96px（見 PdfViewer.module.css
      // 的 .thumbs）且 overflow:hidden——不給值的話每張縮圖左右各被切掉約 27px，
      // 而且不會有任何錯誤。72 ＝ 96 −（6px 細捲軸）−（左右各 ~9px 呼吸空間）。
      // 改動任一邊都要同時改另一邊。
      createPluginRegistration(ThumbnailPluginPackage, { width: THUMB_WIDTH }),
      createPluginRegistration(RotatePluginPackage),
      createPluginRegistration(SearchPluginPackage),
      // **順序有意義**：selection 的 peerDependency 就是 interaction-manager，它的
      // 指標處理器註冊在 interaction-manager 的預設 `pointerMode` 上。反過來註冊，
      // 選取會安靜地完全沒反應。
      createPluginRegistration(InteractionManagerPluginPackage),
      createPluginRegistration(SelectionPluginPackage),
    ],
    [url],
  )

  // 逾時狀態只由計時器寫入、**不在 effect 裡重設**：換 url 時 `timedOutUrl !== url`
  // 自然就是 false，免掉一次 set-state-in-effect（本 repo 對該 lint 規則目前只有一處
  // 豁免且附了理由，不該再開第二處）。readyRef 是 ref，換 url 時就地重設不觸發渲染。
  const [timedOutUrl, setTimedOutUrl] = useState<string | null>(null)
  // 存「哪一個 url 已就緒」而不是布林旗標，於是**不需要在 effect 裡重設**。
  // 用布林＋重設會壞掉，而且壞得很安靜：React 的子 effect 先於父 effect 執行，
  // 所以 ViewerBody 標記就緒之後，父層 effect 才把旗標清回 false，逾時照樣觸發
  // ——正常載入的研報會在 15 秒後無預警跳成內建檢視。反轉測試抓到的正是這條。
  const readyUrlRef = useRef<string | null>(null)
  // 量測起算點，與逾時同一個 [url] 生命週期。**不在渲染期取值**——react-hooks 的
  // purity／refs 規則會擋，而那確實不安全。0 ＝「還沒設定就已經就緒」，只可能發生在
  // 同一個 commit 內既載入完又就緒（子 effect 先於父 effect）；實務上引擎要數秒、
  // ViewerBody 是好幾個 render 之後才掛上。那種情況寧可不印，也不要印以 0 為基準的
  // 假數字。
  const startRef = useRef(0)
  useEffect(() => {
    startRef.current = performance.now()
    const timer = setTimeout(() => {
      if (readyUrlRef.current !== url) setTimedOutUrl(url)
    }, READY_TIMEOUT_MS)
    return () => clearTimeout(timer)
  }, [url])
  // 就緒＝`ViewerBody` 掛上（＝文件已載入、頁面看得到），不是「引擎有 handle」——
  // 事故當下引擎正是「有 handle 但文件永遠開不起來」。
  const markReady = useCallback(() => {
    if (readyUrlRef.current === url) return
    readyUrlRef.current = url
    // `worker: false` 之後 pdfium 在主執行緒 instantiate（wasm 4.6MB），這條 log 就是
    // 那個成本的量測點——「開很慢」的客訴不必再靠感覺，直接看數字。只在真正就緒時
    // 印一次，換文件才會再印。
    if (startRef.current > 0) {
      console.info('[pdf] 引擎就緒 %dms', Math.round(performance.now() - startRef.current))
    }
  }, [url])

  if (error) throw error
  // 放在 isLoading 之前：引擎卡在 isLoading 與文件卡在開不起來，兩種都要接住。
  if (timedOutUrl === url) {
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
      <EmbedPDF engine={engine} plugins={plugins}>
        {({ activeDocumentId }) =>
          activeDocumentId && (
            <DocumentContent documentId={activeDocumentId}>
              {({ isLoaded }) =>
                isLoaded && (
                  <ViewerBody
                    documentId={activeDocumentId}
                    url={url}
                    title={title}
                    onReady={markReady}
                    jump={jump}
                    onJumpResult={onJumpResult}
                  />
                )
              }
            </DocumentContent>
          )
        }
      </EmbedPDF>
    </div>
  )
}

/** provider 之內的實體：viewport ＋ 頁面 ＋ chrome（hooks 需要 plugin context） */
function ViewerBody({
  documentId,
  url,
  title,
  onReady,
  jump,
  onJumpResult,
}: {
  documentId: string
  url: string
  title: string
  onReady: () => void
  jump: JumpRequest | null
  onJumpResult?: (r: JumpResult) => void
}) {
  // 縮圖列預設收合：側欄 380 ＋ 縮圖 96 ＋ 頁面，在 1280px 以下會開始擠。
  const [thumbsOpen, setThumbsOpen] = useState(false)
  const [searchOpen, setSearchOpen] = useState(false)
  const [focusSearchTick, setFocusSearchTick] = useState(0)
  const { provides: scroll, state: scrollState } = useScroll(documentId)
  const { provides: zoom } = useZoom(documentId)
  const { provides: rotate } = useRotate(documentId)
  const { provides: selection } = useSelectionCapability()
  // 本元件掛上就代表文件真的載入了，於是解除上層的逾時。
  useEffect(onReady, [onReady])

  // 這兩個要穩定：它們是 Chrome 內那個 jump effect 的依賴，每次 render 換新的
  // 會讓同一次跳轉被重跑（doneNonceRef 擋得住重複執行，但白跑一次搜尋）。
  const openSearch = useCallback(() => setSearchOpen(true), [])
  const reportJump = useCallback((r: JumpResult) => onJumpResult?.(r), [onJumpResult])

  // 有沒有選取，決定 Ctrl/⌘+C 要不要攔、工具列的複製鈕要不要啟用。
  // **必須訂閱而不是複製時才查**：沒有選取時不該攔截 Ctrl+C，那會把使用者在頁面
  // 其他地方（報頭、摘錄）的正常複製一起吃掉。
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
      const t = e.target as HTMLElement | null
      // 正在打字一律讓行——這個畫面上同時有搜尋框與頁碼輸入
      if (t && (t.tagName === 'INPUT' || t.tagName === 'TEXTAREA' || t.isContentEditable)) return
      if (e.altKey) return

      // Ctrl/⌘+F：**刻意攔截瀏覽器的尋找**。PDF 內容是 canvas，不在 DOM 裡，
      // 瀏覽器原生尋找對它完全無效——不攔截等於把使用者導向一個必然失望的功能。
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') {
        e.preventDefault()
        setSearchOpen(true)
        setFocusSearchTick(n => n + 1)
        return
      }

      // Ctrl/⌘+C：同樣得攔——選取是外掛自己畫的色塊，DOM 裡沒有任何文字節點，
      // 瀏覽器原生複製會複製到空字串。**只在真的有選取時攔**，否則會把使用者
      // 在報頭或側欄摘錄上的正常複製一起吃掉。
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'c') {
        if (!hasSelection) return
        e.preventDefault()
        void copySelection()
        return
      }
      if (e.ctrlKey || e.metaKey) return

      const total = scrollState?.totalPages ?? 0
      const page = scrollState?.currentPage ?? 1
      switch (e.key) {
        case '/':
          e.preventDefault()
          setSearchOpen(true)
          setFocusSearchTick(n => n + 1)
          break
        case 'Escape':
          setSearchOpen(false)
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
          zoom?.requestZoom(ZoomMode.FitWidth)
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
  ])

  return (
    <>
      <Viewport documentId={documentId} className={styles.viewport}>
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
                  width/height 已依旋轉算好；要轉的是裡面的內容。搜尋高亮必須放在
                  Rotate 之內，否則旋轉後高亮框會留在原座標系、指到錯的位置。

                  PagePointerProvider 也必須在 Rotate **之內**，理由同源但更隱蔽：
                  它的預設座標轉換是 `restorePosition(..., rotation, scale)`，也就是
                  「元素已被視覺旋轉，我把指標位置反算回原始頁座標」。放到 Rotate 之外
                  元素其實沒轉，那個反算就會多轉一次——旋轉後選取到的字會整片偏掉，
                  而且不會有任何錯誤，只是選錯地方。 */}
              <Rotate documentId={documentId} pageIndex={pageIndex}>
                <PagePointerProvider documentId={documentId} pageIndex={pageIndex}>
                  <RenderLayer documentId={documentId} pageIndex={pageIndex} />
                  <SearchLayer documentId={documentId} pageIndex={pageIndex} />
                  <SelectionLayer
                    documentId={documentId}
                    pageIndex={pageIndex}
                    background={SELECTION_TINT}
                  />
                </PagePointerProvider>
              </Rotate>
              <span className={styles.pageNo}>{pageIndex + 1}</span>
            </div>
          )}
        />
      </Viewport>
      <Chrome
        documentId={documentId}
        url={url}
        title={title}
        thumbsOpen={thumbsOpen}
        onToggleThumbs={() => setThumbsOpen(v => !v)}
        searchOpen={searchOpen}
        onToggleSearch={() => setSearchOpen(v => !v)}
        focusSearchTick={focusSearchTick}
        canCopy={hasSelection}
        onCopy={copySelection}
        jump={jump}
        onJumpResult={reportJump}
        // 刻意是「打開」不是 toggle：跳轉時要確保搜尋列是開的，
        // 否則 searchOpen 的 effect 會 stopSearch，把剛跳到的高亮清掉。
        onOpenSearch={openSearch}
      />
    </>
  )
}
