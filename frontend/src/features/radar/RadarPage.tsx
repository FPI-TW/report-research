import { useCallback, useMemo } from 'react'
import { useSearchParams } from 'react-router'
import type { Window } from '../../lib/radarSchemas'
import { windowSchema } from '../../lib/radarSchemas'
import { InstrumentPicker } from './InstrumentPicker'
import { RadarOverview } from './RadarOverview'
import styles from './RadarPage.module.css'

function parseWindow(raw: string | null): Window {
  const r = windowSchema.safeParse(raw ?? '90')
  return r.success ? r.data : '90'
}

export default function RadarPage() {
  const [params, setParams] = useSearchParams()
  const market = params.get('market') || ''
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

  const onSelectInstrument = useCallback((mkt: string, c: string) => {
    patch({ market: mkt, code: c, window: window || '90' })
  }, [patch, window])

  const onWindowChange = useCallback((w: Window) => {
    patch({ window: w })
  }, [patch])

  const onClearInstrument = useCallback(() => {
    patch({ code: null })
  }, [patch])

  const hasSelection = useMemo(() => Boolean(market && code), [market, code])

  return (
    <div className={styles.page}>
      <div className={styles.scroll}>
        <div className={styles.inner}>
          {hasSelection ? (
            <RadarOverview
              market={market}
              code={code}
              window={window}
              onWindowChange={onWindowChange}
              onBack={onClearInstrument}
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
