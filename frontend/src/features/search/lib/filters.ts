export interface Filters {
  q: string
  market: string
  instrument: string
  stock: boolean
  futures: boolean
  type: string
  sort: string
}

export interface Allowlists {
  markets: string[]
  instruments: string[]
  types: string[]
}

export const SEARCH_SORTS = ['relevance', 'date_desc', 'date_asc']
export const BROWSE_SORTS = ['date_desc', 'date_asc']

export const DEFAULT_FILTERS: Filters = {
  q: '',
  market: '全部',
  instrument: '全部',
  stock: false,
  futures: false,
  type: '全部',
  sort: '',
}

export function filtersToSearchParams(f: Filters): URLSearchParams {
  const sp = new URLSearchParams()
  if (f.q.trim()) sp.set('q', f.q.trim())
  if (f.market !== '全部') sp.set('market', f.market)
  if (f.instrument !== '全部') sp.set('instrument', f.instrument)
  if (f.stock) sp.set('stock', '1')
  if (f.futures) sp.set('futures', '1')
  if (f.type !== '全部') sp.set('type', f.type)
  if (f.sort) sp.set('sort', f.sort)
  return sp
}

const pick = (v: string | null, allow: string[]) => (v && allow.includes(v) ? v : '全部')

export function searchParamsToFilters(sp: URLSearchParams, allow: Allowlists): Filters {
  const q = (sp.get('q') ?? '').trim()
  const sorts = q ? SEARCH_SORTS : BROWSE_SORTS
  const rawSort = sp.get('sort')
  const sort = rawSort && sorts.includes(rawSort) ? rawSort : sorts[0]
  return {
    q,
    market: pick(sp.get('market'), allow.markets),
    instrument: pick(sp.get('instrument'), allow.instruments),
    stock: sp.get('stock') === '1',
    futures: sp.get('futures') === '1',
    type: pick(sp.get('type'), allow.types),
    sort,
  }
}

// ── ViewState（呈現狀態，與資料維度 Filters 分層；不進 query key）─────────────
export const VIEWS = ['group', 'table'] as const
export const GROUPS = ['month', 'market'] as const
export type ViewName = (typeof VIEWS)[number]
export type GroupName = (typeof GROUPS)[number]

export interface ViewState {
  view: ViewName
  group: GroupName
}

export const DEFAULT_VIEW_STATE: ViewState = { view: 'group', group: 'month' }

// 對齊 url.js：view≠group 才寫 view；view=group 且 group≠month 才寫 group
export function viewStateToParams(vs: ViewState): [string, string][] {
  const out: [string, string][] = []
  if (vs.view !== 'group') out.push(['view', vs.view])
  if (vs.view === 'group' && vs.group !== 'month') out.push(['group', vs.group])
  return out
}

export function paramsToViewState(sp: URLSearchParams): ViewState {
  const rawView = sp.get('view')
  const rawGroup = sp.get('group')
  const view: ViewName = (VIEWS as readonly string[]).includes(rawView ?? '')
    ? (rawView as ViewName)
    : 'group'
  const group: GroupName = (GROUPS as readonly string[]).includes(rawGroup ?? '')
    ? (rawGroup as GroupName)
    : 'month'
  return { view, group }
}
