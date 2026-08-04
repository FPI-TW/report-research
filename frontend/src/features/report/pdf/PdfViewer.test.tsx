import { act, fireEvent, render, screen } from '@testing-library/react'
import { Component, useEffect, useReducer, type ReactNode } from 'react'
import { afterEach, beforeEach, expect, test, vi } from 'vitest'

/**
 * 只驗一件事：**引擎「就緒但文件永遠開不起來」時，逾時會把它變成可降級的例外**。
 *
 * 2026-07-31 的生產事故正是這個形狀——引擎回了 handle、EmbedPDF 掛載了、PDF 也被抓
 * 下來了（伺服器日誌 200），但 `activeDocumentId` 恆為 falsy，畫面只剩空 div，
 * 不拋錯、不降級、console 零訊息。`ViewerBoundary` 只接得住拋出來的例外，接不住
 * 「永遠不完成」，於是降級機制形同不存在。
 *
 * EmbedPDF 整組在此以最小替身取代：這裡要驗的是**本元件的逾時語意**，不是套件行為。
 */

/** 各案例用它決定引擎與文件的狀態 */
const state = {
  activeDocumentId: undefined as string | undefined,
  isLoaded: false,
  /** 每頁的內建 /Rotate；預設全部未旋轉 */
  pages: [{ rotation: 0 }, { rotation: 0 }, { rotation: 0 }] as { rotation: number }[],
}

/**
 * 搜尋替身。**原本的 `provides: null` 會讓所有跳轉測試恆綠而一行真行為都沒驗到**
 * （`search?.searchAllPages` 的 optional chaining 直接短路），所以這裡給一個會記錄
 * 呼叫、且回傳 Task-like 的真替身。
 */
type Hit = { pageIndex: number; rects: { origin: { x: number; y: number } }[] }

/**
 * 搜尋替身：**引擎狀態的最小模型**，不是空殼。
 *
 * 第一版是 `provides: null`，跳轉測試會恆綠而一行真行為都沒驗到（optional chaining
 * 直接短路）。第二版有回傳值但 `query` 寫死 `''`、`stopSearch` 是空的 vi.fn、
 * `searchAllPages` 沒有快取概念——後果是工具列的「無相符」狀態**結構性不可達**、
 * 搜尋框的 Enter 分流（同關鍵字＝跳下一個 vs 換字＝重搜）測不到，而外掛真正的
 * 快取短路（在途重按會回報假的「找不到」，#183 修的那條）在測試裡根本不存在。
 *
 * 現在照 @embedpdf/plugin-search 的 reducer 語意模型化三件事：
 *   START_SEARCH   → query 設為關鍵字、results 清空（**這是快取短路會回 0 的原因**）
 *   STOP_SEARCH    → query/results/activeIndex 全部重設
 *   快取短路        → query 已等於該關鍵字時直接回目前的 results，不重跑
 */
const search = {
  /** needle → 命中；沒列到的 needle 就是 0 命中 */
  hits: {} as Record<string, Hit[]>,
  /** 設成某個 needle 時，搜尋該字會 reject（模擬引擎中止） */
  abortOn: null as string | null,
  /** 設了就讓搜尋卡在這個 promise 上，測試才能在「搜尋進行中」做事 */
  gate: null as Promise<void> | null,
  queries: [] as string[],
  goneTo: [] as number[],
  // ── 以下三個是引擎狀態，測試不要直接寫，用 runSearch() 或走 UI ──
  query: '',
  results: [] as Hit[],
  activeIndex: -1,
}

/** 訂閱者：真的 useSearch 走 onStateChange → setState，替身也必須觸發重繪，
 *  否則工具列的計數／「無相符」永遠停在初值，那幾個狀態就測不到。 */
const searchListeners = new Set<() => void>()
function notifySearch() {
  searchListeners.forEach(f => f())
}

const searchApi = {
  startSearch: vi.fn(),
  // STOP_SEARCH 會把 query 一併重設為 ""，不只是清 results
  stopSearch: vi.fn(() => {
    search.query = ''
    search.results = []
    search.activeIndex = -1
    notifySearch()
  }),
  // **狀態變更是同步發生的，不是等到有人 await 才發生**：產品程式碼有一處
  // （搜尋框 Enter）呼叫了 searchAllPages 卻不取 task，把工作全放進 toPromise
  // 的替身會讓那條路徑完全沒反應。
  searchAllPages: (keyword: string) => {
    search.queries.push(keyword)
    // 快取短路發生在任何非同步之前（外掛的 dispatch 是同步的）
    if (search.query === keyword) {
      const cached = { total: search.results.length, results: search.results }
      return { toPromise: async () => cached }
    }
    search.query = keyword
    search.results = []
    search.activeIndex = -1
    notifySearch()
    const task = (async () => {
      if (search.gate) await search.gate
      if (search.abortOn === keyword) throw new Error('TaskAbortedError')
      const results = search.hits[keyword] ?? []
      search.results = results
      search.activeIndex = results.length ? 0 : -1
      notifySearch()
      return { total: results.length, results }
    })()
    // 沒有人 await 時（Enter 那條）避免 unhandled rejection；awaiter 仍收得到
    task.catch(() => {})
    return { toPromise: () => task }
  },
  goToResult: (i: number) => {
    search.goneTo.push(i)
    search.activeIndex = i
    notifySearch()
    return i
  },
  nextResult: () => {
    if (!search.results.length) return -1
    const i = search.activeIndex >= search.results.length - 1 ? 0 : search.activeIndex + 1
    return searchApi.goToResult(i)
  },
  previousResult: () => {
    if (!search.results.length) return -1
    const i = search.activeIndex <= 0 ? search.results.length - 1 : search.activeIndex - 1
    return searchApi.goToResult(i)
  },
}

const scrollApi = { scrollToPage: vi.fn(), scrollToNextPage: vi.fn() }

vi.mock('@embedpdf/core', () => ({ createPluginRegistration: () => ({}) }))
vi.mock('@embedpdf/core/react', () => ({
  EmbedPDF: ({ children }: { children: (s: { activeDocumentId?: string }) => ReactNode }) =>
    children({ activeDocumentId: state.activeDocumentId }),
  // 頁面的**內建**旋轉由這裡來；捲動時要據此決定給不給 pageCoordinates
  useDocumentState: () => ({ document: { pages: state.pages }, scale: 1 }),
}))
vi.mock('@embedpdf/engines/react', () => ({
  usePdfiumEngine: () => ({ engine: {}, isLoading: false, error: null }),
}))
vi.mock('@embedpdf/plugin-document-manager/react', () => ({
  DocumentManagerPluginPackage: {},
  DocumentContent: ({ children }: { children: (s: { isLoaded: boolean }) => ReactNode }) =>
    children({ isLoaded: state.isLoaded }),
}))
vi.mock('@embedpdf/plugin-render/react', () => ({
  RenderLayer: () => null,
  RenderPluginPackage: {},
}))
// Scroller 真的呼叫 renderPage：紙張那一層（PagePointerProvider 巢狀、選取層、
// 原生拖曳的攔截）本來完全沒有測試碰得到，回 null 等於把它整片藏起來。
vi.mock('@embedpdf/plugin-scroll/react', () => ({
  Scroller: ({
    renderPage,
  }: {
    renderPage: (p: { width: number; height: number; pageIndex: number }) => ReactNode
  }) => <div>{renderPage({ width: 600, height: 800, pageIndex: 0 })}</div>,
  ScrollPluginPackage: {},
  useScroll: () => ({ provides: scrollApi, state: { currentPage: 1, totalPages: 3 } }),
}))
vi.mock('@embedpdf/plugin-thumbnail/react', () => ({
  ThumbImg: () => null,
  ThumbnailsPane: () => null,
  ThumbnailPluginPackage: {},
}))
vi.mock('@embedpdf/plugin-viewport/react', () => ({
  Viewport: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  ViewportPluginPackage: {},
  useViewportScrollActivity: () => ({ isScrolling: false }),
}))
vi.mock('@embedpdf/plugin-zoom/react', () => ({
  ZoomMode: { FitWidth: 'fit-width' },
  ZoomPluginPackage: {},
  useZoom: () => ({ provides: null, state: { currentZoomLevel: 1 } }),
}))
vi.mock('@embedpdf/plugin-rotate/react', () => ({
  Rotate: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  RotatePluginPackage: {},
  useRotate: () => ({ rotation: 0, provides: null }),
}))
/** 選取外掛替身：可由測試推送「有／無選取」，並決定抽字結果。 */
const selection = {
  listeners: [] as ((sel: unknown) => void)[],
  text: ['台積電第三季營收創高'] as string[],
}
/** provides 必須是**穩定物件**：每次 render 換新的會讓訂閱 effect 無限重跑 */
const selectionScope = {
  onSelectionChange(cb: (sel: unknown) => void) {
    selection.listeners.push(cb)
    return () => {
      selection.listeners = selection.listeners.filter(f => f !== cb)
    }
  },
  getSelectedText: () => ({ toPromise: async () => selection.text }),
}
const selectionCapability = { provides: { forDocument: () => selectionScope } }

/** 推一次選取變更給訂閱者（模擬使用者拖曳選字／點掉選取） */
function emitSelection(has: boolean) {
  selection.listeners.forEach(cb => cb(has ? { start: {}, end: {} } : null))
}

vi.mock('@embedpdf/plugin-interaction-manager/react', () => ({
  InteractionManagerPluginPackage: {},
  PagePointerProvider: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}))
vi.mock('@embedpdf/plugin-selection', () => ({ SelectionPluginPackage: {} }))
vi.mock('@embedpdf/plugin-selection/react', () => ({
  SelectionLayer: () => null,
  useSelectionCapability: () => selectionCapability,
}))

/** 複製走 lib/clipboard（非安全情境有 execCommand 後備），這裡只驗有沒有被呼叫、拿到什麼 */
const copySpy = vi.fn<(t: string) => Promise<void>>(async () => {})
vi.mock('../../../lib/clipboard', () => ({ copyText: (t: string) => copySpy(t) }))

vi.mock('@embedpdf/plugin-search/react', () => ({
  SearchLayer: () => null,
  SearchPluginPackage: {},
  useSearch: () => {
    // 訂閱狀態變更並強制重繪，比照真的 useSearch（onStateChange → setState）
    const [, force] = useReducer((x: number) => x + 1, 0)
    useEffect(() => {
      searchListeners.add(force)
      return () => {
        searchListeners.delete(force)
      }
    }, [])
    return {
    provides: searchApi,
    state: {
      flags: [],
      results: search.results,
      total: search.results.length,
      activeResultIndex: search.activeIndex,
      showAllResults: true,
      // **不可寫死 ''**：工具列以 `searchState.query === query` 分流「同關鍵字＝跳下一個」
      // 與「換字＝重搜」，也以它決定要不要顯示「無相符」。寫死等於把兩者都關掉。
      query: search.query,
      loading: false,
      active: Boolean(search.query),
    },
    }
  },
}))

const { default: PdfViewer } = await import('./PdfViewer')

/** 站在 PdfViewer 上方接住例外，模擬正式環境的 ViewerBoundary */
class Catch extends Component<{ children: ReactNode }, { msg: string | null }> {
  state = { msg: null as string | null }
  static getDerivedStateFromError(e: Error) {
    return { msg: e.message }
  }
  render() {
    return this.state.msg ? <div data-testid="fallback">{this.state.msg}</div> : this.props.children
  }
}

beforeEach(() => {
  vi.useFakeTimers()
  // 邊界接住例外時 React 會 console.error，測試輸出留乾淨
  vi.spyOn(console, 'error').mockImplementation(() => {})
  selection.listeners = []
  selection.text = ['台積電第三季營收創高']
  copySpy.mockReset()
  copySpy.mockResolvedValue(undefined)
  state.pages = [{ rotation: 0 }, { rotation: 0 }, { rotation: 0 }]
  search.hits = {}
  search.abortOn = null
  search.gate = null
  search.queries = []
  search.goneTo = []
  search.query = ''
  search.results = []
  search.activeIndex = -1
  scrollApi.scrollToPage.mockReset()
  searchApi.stopSearch.mockClear()
  searchApi.startSearch.mockClear()
  searchListeners.clear()
})

afterEach(() => {
  vi.useRealTimers()
  vi.restoreAllMocks()
})

test('文件永遠開不起來 → 逾時拋錯，交給邊界降級', async () => {
  state.activeDocumentId = undefined
  state.isLoaded = false

  render(
    <Catch>
      <PdfViewer url="/api/report/r1/file" title="研報" />
    </Catch>,
  )
  // 逾時之前不得降級：否則正常但稍慢的載入會被誤殺
  expect(screen.queryByTestId('fallback')).toBeNull()

  await act(async () => {
    vi.advanceTimersByTime(15_000)
  })

  expect(screen.getByTestId('fallback').textContent).toContain('逾時未就緒')
})

test('文件正常載入 → 逾時不觸發（反轉實驗）', async () => {
  state.activeDocumentId = 'doc-1'
  state.isLoaded = true

  render(
    <Catch>
      <PdfViewer url="/api/report/r1/file" title="研報" />
    </Catch>,
  )

  await act(async () => {
    vi.advanceTimersByTime(60_000)
  })

  // ViewerBody 掛上就解除逾時；這一條若失效，上一條的綠燈毫無意義
  expect(screen.queryByTestId('fallback')).toBeNull()
})

/**
 * ── 選取與複製 ──
 *
 * 這一組存在的理由是：SelectionLayer **不是文字層**，它只畫 pointerEvents:none 的色塊，
 * DOM 裡一個文字節點都沒有。所以瀏覽器原生的 Ctrl+C 會複製到空字串——複製這條路
 * 完全由本元件自理，沒有任何上游行為可以依賴。
 */
async function renderLoaded() {
  state.activeDocumentId = 'doc-1'
  state.isLoaded = true
  const r = render(<PdfViewer url="/api/report/r1/file" title="研報" />)
  await act(async () => {})
  return r
}

test('無選取時 Ctrl+C 不攔截——使用者在頁面別處的正常複製不能被吃掉', async () => {
  await renderLoaded()
  await act(async () => {
    emitSelection(false)
  })

  const e = new KeyboardEvent('keydown', { key: 'c', ctrlKey: true, cancelable: true })
  await act(async () => {
    window.dispatchEvent(e)
  })

  expect(e.defaultPrevented).toBe(false)
  expect(copySpy).not.toHaveBeenCalled()
})

test('有選取時 Ctrl+C 攔截並複製抽出的文字', async () => {
  await renderLoaded()
  await act(async () => {
    emitSelection(true)
  })

  const e = new KeyboardEvent('keydown', { key: 'c', ctrlKey: true, cancelable: true })
  await act(async () => {
    window.dispatchEvent(e)
  })

  expect(e.defaultPrevented).toBe(true)
  expect(copySpy).toHaveBeenCalledWith('台積電第三季營收創高')
})

test('跨頁選取：逐頁抽出的段落以換行接起來', async () => {
  selection.text = ['第一頁結尾', '第二頁開頭']
  await renderLoaded()
  await act(async () => {
    emitSelection(true)
  })

  await act(async () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'c', ctrlKey: true, cancelable: true }))
  })

  expect(copySpy).toHaveBeenCalledWith('第一頁結尾\n第二頁開頭')
})

test('工具列複製鈕：無選取時停用，有選取才可按', async () => {
  await renderLoaded()
  const btn = screen.getByLabelText('複製選取的文字')
  expect(btn).toBeDisabled()

  await act(async () => {
    emitSelection(true)
  })
  expect(screen.getByLabelText('複製選取的文字')).toBeEnabled()
})

// 區網入口是 HTTP ＋ 私有 IP，複製真的會失敗。靜默假裝成功是最糟的結果。
test('複製失敗 → 據實回報，不假裝成功', async () => {
  copySpy.mockRejectedValue(new Error('copy failed'))
  await renderLoaded()
  await act(async () => {
    emitSelection(true)
  })

  await act(async () => {
    screen.getByLabelText('複製選取的文字').click()
  })

  expect(screen.getByRole('status').textContent).toContain('複製失敗')
})

test('複製成功 → 播報已複製', async () => {
  await renderLoaded()
  await act(async () => {
    emitSelection(true)
  })

  await act(async () => {
    screen.getByLabelText('複製選取的文字').click()
  })

  expect(screen.getByRole('status').textContent).toContain('已複製')
})

// 實測回報：選字時整頁 PDF 的殘影會跟著游標跑。原因是 RenderLayer 渲染的是 <img>，
// 拖曳會啟動瀏覽器原生的圖片拖放，把選取打斷。dragstart 從 img 冒泡到紙張容器，
// 在那裡 preventDefault 是唯一跨瀏覽器（含 Firefox）有效的攔法。
test('在頁面上拖曳不得觸發原生圖片拖放', async () => {
  const { container } = await renderLoaded()
  const page = container.querySelector('div[style*="width: 600px"]')
  expect(page).not.toBeNull()

  const drag = new Event('dragstart', { bubbles: true, cancelable: true })
  await act(async () => {
    page!.dispatchEvent(drag)
  })

  expect(drag.defaultPrevented).toBe(true)
})

/**
 * ── 摘錄跳轉 ──
 *
 * 跳轉＝以引文跑一次真的 PDFium 搜尋（全語料實測原句 95.3%、加階梯 98.6–99.1%），
 * 再自己捲過去。**`goToResult()` 不捲動**，這組測試最重要的一條就是釘住捲動真的發生。
 */
const HIT = { pageIndex: 11, rects: [{ origin: { x: 72, y: 430 } }] }

async function renderWithJump(quote: string, nonce = 1) {
  state.activeDocumentId = 'doc-1'
  state.isLoaded = true
  const onJumpResult = vi.fn()
  const r = render(
    <PdfViewer
      url="/api/report/r1/file"
      title="研報"
      jump={{ ordinal: 1, quote, nonce }}
      onJumpResult={onJumpResult}
    />,
  )
  await act(async () => {})
  return { ...r, onJumpResult }
}

test('跳轉：搜到就 goToResult ＋ 真的捲過去，並回報頁碼', async () => {
  search.hits['視 NYPCB 為首選'] = [HIT]
  const { onJumpResult } = await renderWithJump('視 NYPCB 為首選')

  expect(search.queries).toEqual(['視 NYPCB 為首選'])
  expect(search.goneTo).toEqual([0])
  // 這一條是整組的核心：goToResult 只換 active index，捲動必須是我們自己做的
  // behavior（smooth/auto）由 reduce-motion 決定，已在 searchScroll.test.ts 釘死，
  // 這裡只驗「捲到正確的頁與位置」
  expect(scrollApi.scrollToPage).toHaveBeenCalledWith(
    expect.objectContaining({ pageNumber: 12, pageCoordinates: { x: 72, y: 430 }, alignY: 25 }),
  )
  expect(onJumpResult).toHaveBeenCalledWith({ nonce: 1, ok: true, page: 12, total: 1 })
})

test('跳轉：第一階落空時往下試階梯，命中即停', async () => {
  // 原句（折疊空白後）落空，最長片段命中
  search.hits['台積電第三季營收創歷史新高'] = [HIT]
  const { onJumpResult } = await renderWithJump('根據 台積電第三季營收創歷史新高 的說法')

  expect(search.queries).toEqual([
    '根據 台積電第三季營收創歷史新高 的說法',
    '台積電第三季營收創歷史新高',
  ])
  expect(onJumpResult).toHaveBeenCalledWith({ nonce: 1, ok: true, page: 12, total: 1 })
})

test('跳轉：整條階梯都落空 → 回報找不到，且不捲動', async () => {
  const { onJumpResult } = await renderWithJump('這段文字不在任何一頁裡面出現過喔喔喔喔喔')

  expect(onJumpResult).toHaveBeenCalledWith({ nonce: 1, ok: false })
  expect(scrollApi.scrollToPage).not.toHaveBeenCalled()
  expect(search.goneTo).toEqual([])
})

test('跳轉：多重命中要把總數一起回報（讀者才知道要用上下一個確認）', async () => {
  search.hits['毛利率上修至五成'] = [HIT, { pageIndex: 20, rects: [{ origin: { x: 10, y: 20 } }] }]
  const { onJumpResult } = await renderWithJump('毛利率上修至五成')

  expect(onJumpResult).toHaveBeenCalledWith({ nonce: 1, ok: true, page: 12, total: 2 })
})

// 只有「被下一次點擊取代」才該靜默：那時 cleanup 已把 cancelled 設 true，接手的那次
// 會自己回報，舊的再回報一次會蓋掉新的。**其他 reject（關搜尋列、引擎失敗）必須回報**
// ——見下方那條。舊版把兩者混為一談，等於把「永久尋找中」釘死成預期行為。
test('跳轉：被下一次點擊取代時，舊那次不得回報', async () => {
  search.hits['第一次'] = [HIT]
  search.hits['第二次'] = [HIT]
  let release!: () => void
  search.gate = new Promise<void>(r => { release = r })

  state.activeDocumentId = 'doc-1'
  state.isLoaded = true
  const onJumpResult = vi.fn()
  const { rerender } = render(
    <PdfViewer url="/api/report/r1/file" title="研報"
      jump={{ ordinal: 1, quote: '第一次', nonce: 1 }} onJumpResult={onJumpResult} />,
  )
  await act(async () => {})

  // 換 jump → 舊 effect 的 cleanup 執行（cancelled = true）
  search.gate = null
  await act(async () => {
    rerender(
      <PdfViewer url="/api/report/r1/file" title="研報"
        jump={{ ordinal: 2, quote: '第二次', nonce: 2 }} onJumpResult={onJumpResult} />,
    )
  })
  await act(async () => {
    release()
    await Promise.resolve()
  })

  // 只有第二次會回報，第一次靜默
  expect(onJumpResult).toHaveBeenCalledTimes(1)
  expect(onJumpResult).toHaveBeenCalledWith(expect.objectContaining({ nonce: 2, ok: true }))
})

// 內建旋轉頁的 pageCoordinates 會被 plugin-scroll 再套一次旋轉 → 只給頁碼
test('跳轉：內建旋轉的頁只捲到頁首，不給座標', async () => {
  state.pages = [{ rotation: 0 }, { rotation: 0 }, { rotation: 0 }]
  state.pages[11] = { rotation: 1 }
  search.hits['橫式頁上的句子'] = [HIT]
  await renderWithJump('橫式頁上的句子')

  expect(scrollApi.scrollToPage).toHaveBeenCalledWith(expect.objectContaining({ pageNumber: 12 }))
  expect(scrollApi.scrollToPage.mock.calls[0][0].pageCoordinates).toBeUndefined()
})

test('跳轉時把搜尋列打開並同步關鍵字（計數與「無相符」才不會對著別組字說話）', async () => {
  search.hits['視 NYPCB 為首選'] = [HIT]
  await renderWithJump('視 NYPCB 為首選')

  expect(screen.getByLabelText('搜尋研報原文')).toHaveValue('視 NYPCB 為首選')
})

// 工具列的上/下一個命中本來就不捲動（既有缺陷），與跳轉共用同一支捲動。
// **走真實流程而不是直接塞 search.results**：掛載時 searchOpen 為 false 會呼叫
// stopSearch，而那會把 query/results 一併重設——預設狀態的測試會被自己的元件清掉。
// 順帶把先前結構性不可達的兩件事一起蓋掉：搜尋框的 Enter 分流、以及「無相符」。
async function runSearchViaUI(keyword: string) {
  await act(async () => { screen.getByLabelText('搜尋原文').click() })
  const input = screen.getByLabelText('搜尋研報原文')
  await act(async () => { fireEvent.change(input, { target: { value: keyword } }) })
  await act(async () => { fireEvent.keyDown(input, { key: 'Enter' }) })
}

async function renderViewer() {
  state.activeDocumentId = 'doc-1'
  state.isLoaded = true
  render(<PdfViewer url="/api/report/r1/file" title="研報" />)
  await act(async () => {})
}

test('搜尋框 Enter：換了關鍵字＝重新全文搜尋', async () => {
  search.hits['AI 伺服器'] = [HIT, { pageIndex: 20, rects: [{ origin: { x: 10, y: 20 } }] }]
  await renderViewer()
  await runSearchViaUI('AI 伺服器')

  expect(search.queries).toEqual(['AI 伺服器'])
  expect(screen.getByText('1 / 2')).toBeInTheDocument()
})

// 這個狀態先前不可達：替身把 query 寫死 '' ⇒ `searchState.query === query` 永遠為假
test('搜尋無命中 → 工具列顯示「無相符」', async () => {
  await renderViewer()
  await runSearchViaUI('不存在的字串')

  expect(screen.getByText('無相符')).toBeInTheDocument()
})

test('搜尋框 Enter：同一組關鍵字再按＝跳下一個命中（不重搜）', async () => {
  search.hits['AI 伺服器'] = [HIT, { pageIndex: 20, rects: [{ origin: { x: 10, y: 20 } }] }]
  await renderViewer()
  await runSearchViaUI('AI 伺服器')
  scrollApi.scrollToPage.mockClear()

  await act(async () => {
    fireEvent.keyDown(screen.getByLabelText('搜尋研報原文'), { key: 'Enter' })
  })

  // 沒有再發一次搜尋，而是換到第二個命中並捲過去
  expect(search.queries).toEqual(['AI 伺服器'])
  expect(screen.getByText('2 / 2')).toBeInTheDocument()
  expect(scrollApi.scrollToPage).toHaveBeenCalledWith(
    expect.objectContaining({ pageNumber: 21, pageCoordinates: { x: 10, y: 20 } }),
  )
})

test('工具列「下一個命中」會捲動', async () => {
  search.hits['AI 伺服器'] = [HIT, { pageIndex: 20, rects: [{ origin: { x: 10, y: 20 } }] }]
  await renderViewer()
  await runSearchViaUI('AI 伺服器')
  scrollApi.scrollToPage.mockClear()

  await act(async () => { screen.getByLabelText('下一個命中').click() })

  expect(scrollApi.scrollToPage).toHaveBeenCalledWith(
    expect.objectContaining({ pageNumber: 21, pageCoordinates: { x: 10, y: 20 } }),
  )
})

test('工具列「上一個命中」會捲動（從第一個往回捲到最後一個）', async () => {
  search.hits['AI 伺服器'] = [HIT, { pageIndex: 20, rects: [{ origin: { x: 10, y: 20 } }] }]
  await renderViewer()
  await runSearchViaUI('AI 伺服器')
  scrollApi.scrollToPage.mockClear()

  await act(async () => { screen.getByLabelText('上一個命中').click() })

  expect(scrollApi.scrollToPage).toHaveBeenCalledWith(
    expect.objectContaining({ pageNumber: 21, pageCoordinates: { x: 10, y: 20 } }),
  )
})
