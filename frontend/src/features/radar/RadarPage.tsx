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

/**
 * 券商共識（`/app/consensus`）：沒有 `market`＋`code` 時是標的清單，有就是該標的的詳情。
 * 兩者的狀態都在網址裡（清單的 q／sort／stance／market，詳情的 window／broker），
 * 所以從詳情按返回會回到原本的篩選，貼出去的連結也是同一個畫面。
 */
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
    patch({ code: null, broker: null })
  }, [patch])

  // 展開哪家券商進網址（replace，不堆歷史），貼出去的連結才看得到同一家的歷程。
  const openBroker = params.get('broker')
  const onOpenBrokerChange = useCallback((brokerKey: string | null) => {
    patch({ broker: brokerKey })
  }, [patch])

  const onBrowseReports = useCallback(() => {
    const search = new URLSearchParams({ q: code, market })
    navigate(`/library?${search}`)
  }, [code, market, navigate])

  // 清單狀態一律走同一支 patch（replace）：搜尋是逐字打出來的，每次 debounce 都 push 一筆
  // 會讓上一頁按十幾次才離得開。
  const onQueryChange = useCallback((q: string) => {
    patch(catalogPatch({ q }))
  }, [patch])
  const onSortChange = useCallback((sort: CatalogSort) => {
    patch(catalogPatch({ sort }))
  }, [patch])
  const onStanceChange = useCallback((stance: StanceFilter | null) => {
    patch(catalogPatch({ stance }))
  }, [patch])

  // 詳情連結以目前網址為基底，q／sort／stance 一路帶著；返回（刪掉 code）就回到原本的篩選。
  const hrefFor = useCallback(
    (mkt: Market, c: string) => instrumentHref(params, mkt, c),
    [params],
  )

  return (
    <div className={styles.page}>
      <div className={styles.scroll} data-radar-scroll>
        <div className={styles.inner}>
          {market && code ? (
            <RadarOverview
              key={`${market}:${code}`}
              market={market}
              code={code}
              window={window}
              onWindowChange={onWindowChange}
              onBack={onClearInstrument}
              onBrowseReports={onBrowseReports}
              openBroker={openBroker}
              onOpenBrokerChange={onOpenBrokerChange}
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
              onClearFilters={() => patch({ ...catalogPatch({ q: '', stance: null }), market: null })}
              hrefFor={hrefFor}
            />
          )}
          <p className={styles.disclaimer}>
            內容由 AI 整理自券商研報的已擷取觀點與數值，可能有誤，非投資建議。
          </p>
        </div>
      </div>
    </div>
  )
}
