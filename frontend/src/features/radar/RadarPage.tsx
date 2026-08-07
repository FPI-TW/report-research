import { useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import type { CatalogSort, Market, StanceFilter, Window } from '../../lib/radarSchemas'
import { marketSchema, windowSchema } from '../../lib/radarSchemas'
import { catalogPatch, instrumentHref, parseCatalogState } from './catalogState'
import { InstrumentPicker } from './InstrumentPicker'
import { RadarOverview } from './RadarOverview'
import styles from './RadarPage.module.css'

function parseWindow(raw: string | null): Window {
  const r = windowSchema.safeParse(raw ?? '90')
  return r.success ? r.data : '90'
}

export default function RadarPage() {
  const [params, setParams] = useSearchParams()
  const navigate = useNavigate()
  const parsedMarket = marketSchema.safeParse(params.get('market'))
  const market = parsedMarket.success ? parsedMarket.data : ''
  const code = params.get('code') || ''
  const window = parseWindow(params.get('window'))
  const catalog = parseCatalogState(params)

  const patch = useCallback((next: Record<string, string | null>) => {
    setParams(prev => {
      const sp = new URLSearchParams(prev)
      for (const [k, v] of Object.entries(next)) {
        if (v == null || v === '') sp.delete(k)
        else sp.set(k, v)
      }
      return sp
    }, { replace: true })
  }, [setParams])

  const onWindowChange = useCallback((w: Window) => {
    patch({ window: w })
  }, [patch])

  const onClearInstrument = useCallback(() => {
    patch({ code: null })
  }, [patch])

  const onBrowseReports = useCallback(() => {
    const search = new URLSearchParams({ q: code, market })
    navigate(`/search?${search}`)
  }, [code, market, navigate])

  // 清單狀態一律走同一支 patch（replace，不堆歷史）：搜尋是逐字打出來的，每一次
  // debounce 都 push 一筆會讓上一頁按十幾次才離得開。
  const onQueryChange = useCallback((q: string) => {
    patch(catalogPatch({ q }))
  }, [patch])
  const onSortChange = useCallback((sort: CatalogSort) => {
    patch(catalogPatch({ sort }))
  }, [patch])
  const onStanceChange = useCallback((stance: StanceFilter | null) => {
    patch(catalogPatch({ stance }))
  }, [patch])

  // 詳情頁連結以當前網址為基底，所以 q／sort／stance 一路帶著；從詳情按返回
  // （`patch({ code: null })`）就自動回到原本的篩選，不必另外記一份狀態。
  const hrefFor = useCallback(
    (mkt: Market, c: string) => instrumentHref(params, mkt, c),
    [params],
  )

  return (
    <div className={styles.page}>
      <div className={styles.scroll}>
        <div className={styles.inner}>
          {market && code ? (
            <RadarOverview
              market={market}
              code={code}
              window={window}
              onWindowChange={onWindowChange}
              onBack={onClearInstrument}
              onBrowseReports={onBrowseReports}
            />
          ) : (
            <InstrumentPicker
              market={market || undefined}
              q={catalog.q}
              sort={catalog.sort}
              stance={catalog.stance}
              onQueryChange={onQueryChange}
              onSortChange={onSortChange}
              onStanceChange={onStanceChange}
              onMarketChange={m => patch({ market: m || null })}
              hrefFor={hrefFor}
            />
          )}
          <p className={styles.disclaimer}>
            本區整理券商研報中的已擷取觀點與數值變化，非系統預測或投資建議。
          </p>
        </div>
      </div>
    </div>
  )
}
