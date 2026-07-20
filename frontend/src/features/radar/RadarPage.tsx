import { useCallback } from 'react'
import { useNavigate, useSearchParams } from 'react-router'
import type { Market, Window } from '../../lib/radarSchemas'
import { marketSchema, windowSchema } from '../../lib/radarSchemas'
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

  const onSelectInstrument = useCallback((mkt: Market, c: string) => {
    patch({ market: mkt, code: c, window: window || '90' })
  }, [patch, window])

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
              onSelect={onSelectInstrument}
              onMarketChange={m => patch({ market: m || null })}
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
