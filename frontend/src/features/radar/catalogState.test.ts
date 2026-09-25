import { describe, expect, it } from 'vitest'
import {
  catalogPatch, DEFAULT_SORT, instrumentHref, parseCatalogState,
  SORT_OPTIONS, STANCE_DISPLAY, STANCE_OPTIONS,
} from './catalogState'
import { BUCKET_TO_STANCE, RATING_BUCKET } from './radarFormat'

describe('parseCatalogState', () => {
  it('讀出 q／sort／stance', () => {
    const s = parseCatalogState(new URLSearchParams('q=台積&sort=reports&stance=bullish'))
    expect(s).toEqual({ q: '台積', sort: 'reports', stance: 'bullish' })
  })

  it('沒有參數時回預設', () => {
    expect(parseCatalogState(new URLSearchParams())).toEqual({
      q: '', sort: DEFAULT_SORT, stance: null,
    })
  })

  /*
   * 非法值靜默退回預設，不是丟錯也不是把整組條件清掉——與 RadarPage 對 market／window
   * 的既有處置一致。有人手改網址時，畫面該還是可用的。
   */
  it('非法 sort／stance 退回預設而不是丟錯', () => {
    const s = parseCatalogState(new URLSearchParams('sort=;DROP&stance=bull'))
    expect(s.sort).toBe(DEFAULT_SORT)
    expect(s.stance).toBeNull()
  })
})

describe('catalogPatch', () => {
  it('預設值一律寫成 null（＝從網址刪掉）', () => {
    // 不這樣做的話，同一個畫面會有兩種網址：一種帶 sort=latest、一種不帶。
    expect(catalogPatch({ sort: DEFAULT_SORT })).toEqual({ sort: null })
    expect(catalogPatch({ stance: null })).toEqual({ stance: null })
    expect(catalogPatch({ q: '   ' })).toEqual({ q: null })
  })

  it('只寫進被指定的鍵', () => {
    expect(catalogPatch({ stance: 'bearish' })).toEqual({ stance: 'bearish' })
  })

  it('非預設值照原樣寫入', () => {
    expect(catalogPatch({ sort: 'brokers', q: ' 2330 ' })).toEqual({
      sort: 'brokers', q: '2330',
    })
  })
})

describe('instrumentHref', () => {
  it('換到另一檔時不帶著上一檔展開的券商', () => {
    const href = instrumentHref(new URLSearchParams('market=TW&code=2330&broker=kgi&q=台'), 'TW', '2317')
    const sp = new URL(href, 'http://x').searchParams
    expect(sp.get('broker')).toBeNull()
    expect(sp.get('code')).toBe('2317')
    expect(sp.get('q')).toBe('台') // 清單篩選照舊一路帶著
  })

  /*
   * 詳情連結以當前網址為基底，篩選條件才會一路帶著；不這樣做的話，從詳情按返回
   * 會回到一個沒有搜尋、沒有排序、沒有立場的清單，而使用者剛剛才設好它們。
   */
  it('保留 q／sort／stance 並補上 market／code／window', () => {
    const href = instrumentHref(
      new URLSearchParams('q=台積&sort=reports&stance=bullish'), 'TW', '2330',
    )
    const sp = new URLSearchParams(href.slice(href.indexOf('?')))
    expect(sp.get('q')).toBe('台積')
    expect(sp.get('sort')).toBe('reports')
    expect(sp.get('stance')).toBe('bullish')
    expect(sp.get('market')).toBe('TW')
    expect(sp.get('code')).toBe('2330')
    expect(sp.get('window')).toBe('90')
  })

  it('已有 window 時不覆蓋', () => {
    const href = instrumentHref(new URLSearchParams('window=180'), 'US', 'AAPL')
    expect(new URLSearchParams(href.slice(href.indexOf('?'))).get('window')).toBe('180')
  })

  it('代碼含斜線時仍是合法查詢字串', () => {
    const href = instrumentHref(new URLSearchParams(), 'US', 'BRK/B')
    expect(new URLSearchParams(href.slice(href.indexOf('?'))).get('code')).toBe('BRK/B')
  })
})

describe('選項與後端 enum 的對應', () => {
  /*
   * 桶界的唯一真相是後端 `scale.rating_bucket()`；前端只負責送參數。這條擋的是
   * 「前端自己造一套簡寫」——送出 `bull` 會被 FastAPI 以 422 擋掉，但那是在使用者
   * 按下去之後才會知道。
   */
  it('立場選項的值全部落在後端 StanceFilter 的三個值裡', () => {
    const allowed = new Set(['bullish', 'neutral', 'bearish'])
    for (const opt of STANCE_OPTIONS) {
      if (opt.value === '') continue
      expect(allowed.has(opt.value), opt.value).toBe(true)
    }
  })

  it('RATING_BUCKET 的每個桶都對得到一個 StanceFilter，且顯示字一致', () => {
    for (const bucket of Object.values(RATING_BUCKET)) {
      const stance = BUCKET_TO_STANCE[bucket]
      expect(stance).toBeDefined()
      const opt = STANCE_OPTIONS.find(o => o.value === stance)
      expect(opt?.label, stance).toBe(STANCE_DISPLAY[stance])
    }
  })

  it('排序選項的值不重複且含預設值', () => {
    const values = SORT_OPTIONS.map(o => o.value)
    expect(new Set(values).size).toBe(values.length)
    expect(values).toContain(DEFAULT_SORT)
  })
})
