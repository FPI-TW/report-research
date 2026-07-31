import { act, render, screen } from '@testing-library/react'
import { Component, type ReactNode } from 'react'
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
}

vi.mock('@embedpdf/core', () => ({ createPluginRegistration: () => ({}) }))
vi.mock('@embedpdf/core/react', () => ({
  EmbedPDF: ({ children }: { children: (s: { activeDocumentId?: string }) => ReactNode }) =>
    children({ activeDocumentId: state.activeDocumentId }),
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
vi.mock('@embedpdf/plugin-scroll/react', () => ({
  Scroller: () => null,
  ScrollPluginPackage: {},
  useScroll: () => ({ provides: null, state: { currentPage: 1, totalPages: 3 } }),
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
vi.mock('@embedpdf/plugin-search/react', () => ({
  SearchLayer: () => null,
  SearchPluginPackage: {},
  useSearch: () => ({
    provides: null,
    state: {
      flags: [],
      results: [],
      total: 0,
      activeResultIndex: -1,
      showAllResults: true,
      query: '',
      loading: false,
      active: false,
    },
  }),
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
