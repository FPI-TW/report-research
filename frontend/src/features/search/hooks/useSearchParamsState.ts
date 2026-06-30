import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router'
import {
  type Allowlists,
  type Filters,
  type ViewState,
  filtersToSearchParams,
  searchParamsToFilters,
  viewStateToParams,
  paramsToViewState,
} from '../lib/filters'

const VIEW_LS_KEY = 'rm_view'

function readStoredView(): 'group' | 'table' | null {
  try {
    const v = localStorage.getItem(VIEW_LS_KEY)
    return v === 'group' || v === 'table' ? v : null
  } catch {
    return null
  }
}

/** 合併 filters + viewState 成單一 URLSearchParams */
function merge(filters: Filters, vs: ViewState): URLSearchParams {
  const sp = filtersToSearchParams(filters)
  for (const [k, v] of viewStateToParams(vs)) sp.set(k, v)
  return sp
}

export function useSearchParamsState(allow: Allowlists) {
  const [sp, setSp] = useSearchParams()

  const filters = useMemo(() => searchParamsToFilters(sp, allow), [sp, allow])

  // 優先序：URL view > localStorage rm_view > 預設 'group'
  const viewState = useMemo<ViewState>(() => {
    const fromUrl = paramsToViewState(sp)
    if (!sp.get('view')) {
      const stored = readStoredView()
      if (stored) return { ...fromUrl, view: stored }
    }
    return fromUrl
  }, [sp])

  // setFilters：用 hook 內已算好的 viewState（含 localStorage 退回）作合併來源，
  // 確保無論 view 從 URL 或 localStorage 取得，setFilters 都能保留它。
  const setFilters = useCallback(
    (next: Filters) => setSp(merge(next, viewState), { replace: true }),
    [setSp, viewState],
  )

  // setViewState：先寫 localStorage，再合併目前 filters 與新 viewState 寫回 URL
  const setViewState = useCallback(
    (next: ViewState) => {
      try {
        localStorage.setItem(VIEW_LS_KEY, next.view)
      } catch {
        /* 測試環境或隱私模式忽略 */
      }
      setSp(merge(searchParamsToFilters(sp, allow), next), { replace: true })
    },
    [setSp, sp, allow],
  )

  return { filters, viewState, setFilters, setViewState }
}
