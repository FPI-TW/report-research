import { useLocation, useNavigate } from 'react-router'

/** 連進閱讀頁的來源頁。返回鈕的文字就是它的頁名（交接包「閱讀頁」第 1 點）。 */
export type ReportSource = 'library' | 'assistant' | 'consensus' | 'brief' | 'help'

const SOURCE_LABEL: Record<ReportSource, string> = {
  library: '研報庫',
  assistant: '研究助理',
  consensus: '券商共識',
  brief: '每日簡報',
  help: '使用說明',
}

/**
 * 閱讀頁的 `location.state`。
 *
 * - `from`：來源頁。連進閱讀頁的頁面**可以**帶（`<Link state={{ from: 'library' }}>`）；
 *   沒帶也行，下面會再從瀏覽紀錄推。
 * - `depth`：從來源頁算起第幾篇研報。相似研報一篇接一篇點下去時遞增，返回鈕據此
 *   一次退回來源頁（`navigate(-depth)`），而不是一篇一篇退 —— 原型的語意是「返回研報庫」，
 *   不是「返回上一篇研報」。
 */
export interface ReportLinkState {
  from?: ReportSource
  depth?: number
}

/** 站內路徑（不含 basename `/app`）→ 來源頁。舊網址一併認，轉址前的紀錄還會留在瀏覽紀錄裡。 */
export function sourceFromPath(pathname: string): ReportSource | null {
  const p = pathname.replace(/^\/app(?=\/|$)/, '') || '/'
  if (p === '/' || p.startsWith('/library') || p.startsWith('/search')) return 'library'
  if (p.startsWith('/assistant') || p.startsWith('/ask')) return 'assistant'
  if (p.startsWith('/consensus') || p.startsWith('/radar')) return 'consensus'
  if (p.startsWith('/brief')) return 'brief'
  if (p.startsWith('/help')) return 'help'
  return null
}

function readState(state: unknown): { from: ReportSource | null; depth: number } {
  const s = (state && typeof state === 'object' ? state : {}) as ReportLinkState
  const from = s.from && s.from in SOURCE_LABEL ? s.from : null
  const depth = Number.isInteger(s.depth) && (s.depth as number) > 0 ? (s.depth as number) : 1
  return { from, depth }
}

interface NavigationEntryLike { url: string | null; index: number }
interface NavigationLike {
  currentEntry: NavigationEntryLike | null
  entries: () => NavigationEntryLike[]
}

/**
 * 往回第 `depth` 筆瀏覽紀錄的站內路徑（Navigation API；Chromium 系有，其餘回 null）。
 *
 * 只是**推文字**用的漸進增強：沒有這個 API 時返回鈕仍會正確退回，只是文字退成「上一頁」。
 * 跨來源的紀錄（從別站點進來）一律不認。
 */
function previousPath(depth: number): string | null {
  try {
    const nav = (window as unknown as { navigation?: NavigationLike }).navigation
    const cur = nav?.currentEntry
    if (!nav || !cur) return null
    const prev = nav.entries()[cur.index - depth]
    if (!prev?.url) return null
    const url = new URL(prev.url)
    return url.origin === window.location.origin ? url.pathname : null
  } catch {
    return null
  }
}

/**
 * 返回鈕：文字是來源頁名，動作是退回來源頁。
 *
 * 退回用瀏覽紀錄（`navigate(-depth)`）而不是推一個新網址：研報庫的查詢字串、捲動位置、
 * 研究助理的對話都靠瀏覽紀錄還原。本分頁進站的第一筆（直接貼網址、從外部開新分頁）
 * 站內沒有上一頁可回 —— `history.back()` 會離開本站或什麼都不做 —— 改推研報庫。
 */
export function useBackNav() {
  const location = useLocation()
  const navigate = useNavigate()
  const { from, depth } = readState(location.state)
  const hasHistory = location.key !== 'default'

  if (!hasHistory) {
    return {
      label: SOURCE_LABEL.library,
      go: () => navigate('/library'),
      // 這一篇本身就是起點：從它點出去的相似研報，返回時退回這一篇
      childState: { depth: 1 } satisfies ReportLinkState,
    }
  }

  const prevPath = from ? null : previousPath(depth)
  const source = from ?? (prevPath ? sourceFromPath(prevPath) : null)
  return {
    label: source ? SOURCE_LABEL[source] : '上一頁',
    go: () => navigate(-depth),
    childState: { from: source ?? undefined, depth: depth + 1 } satisfies ReportLinkState,
  }
}
