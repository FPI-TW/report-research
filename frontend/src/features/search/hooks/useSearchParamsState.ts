import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router'
import {
  type Allowlists,
  type Filters,
  filtersToSearchParams,
  searchParamsToFilters,
} from '../lib/filters'

export function useSearchParamsState(allow: Allowlists) {
  const [sp, setSp] = useSearchParams()

  const filters = useMemo(() => searchParamsToFilters(sp, allow), [sp, allow])

  const setFilters = useCallback(
    (next: Filters) => setSp(filtersToSearchParams(next), { replace: true }),
    [setSp],
  )

  return { filters, setFilters }
}
