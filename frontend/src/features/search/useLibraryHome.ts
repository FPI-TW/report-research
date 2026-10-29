import { useQuery, type UseQueryResult } from '@tanstack/react-query'
import { getLatestBrief } from '../../lib/briefApi'
import { getRatingChanges } from '../../lib/libraryApi'
import { MARKET_ORDER } from '../../lib/meta'
import { getInstrumentRadar } from '../../lib/radarApi'
import type { Market } from '../../lib/radarSchemas'
import { getReadingDoc } from '../../lib/readingApi'
import type { ReadingDoc } from '../../lib/readingSchemas'
import type { ReportRow } from '../../lib/schemas'

/** 首頁「最新評等變動」列數（交接包：4 列）。 */
export const RATING_CHANGE_ROWS = 4

const HOME_STALE_MS = 5 * 60_000

function isMarket(v: string | null | undefined): v is Market {
  return !!v && (MARKET_ORDER as readonly string[]).includes(v)
}

/**
 * 頭條的「券商共識列」講哪一檔：優先用這篇研報自己擷取到的訊號（代號與市場都經過驗證），
 * 沒有訊號才退回第一個個股標的配研報市場——那一檔查不到共識（404）時整列不顯示。
 */
export function headlineInstrument(
  doc: ReadingDoc | undefined,
  row: Pick<ReportRow, 'market' | 'stock_targets'> | undefined,
): { market: Market; code: string } | null {
  const sig = doc?.signals.find(s => isMarket(s.market) && s.instrument_code)
  if (sig && isMarket(sig.market)) return { market: sig.market, code: sig.instrument_code }
  const code = row?.stock_targets?.[0]
  if (code && isMarket(row?.market)) return { market: row.market, code }
  return null
}

/** 已經有結果（成功或失敗），或根本不會抓。 */
function settled(q: UseQueryResult<unknown>): boolean {
  return q.status !== 'pending' || q.fetchStatus === 'idle'
}

/**
 * 首頁 bento 的附屬資料：頭條的重點摘錄與券商共識、最新評等變動、今日簡報。
 *
 * 每一份都是附屬內容，失敗就不顯示那一塊（fail-open），所以一律 `retry: false`——
 * 出廠的三次重試會讓整片首頁在骨架上多等好幾秒。查詢鍵與閱讀頁、券商共識頁、每日簡報頁
 * 相同，點進去時直接命中快取。
 */
export function useLibraryHome(head: ReportRow | undefined) {
  const hash = head?.file_hash ?? ''
  const doc = useQuery({
    queryKey: ['reading-doc', hash],
    queryFn: () => getReadingDoc(hash),
    enabled: Boolean(hash),
    staleTime: HOME_STALE_MS,
    retry: false,
  })
  const instrument = settled(doc) ? headlineInstrument(doc.data, head) : null
  const consensus = useQuery({
    queryKey: ['radar-overview', instrument?.market ?? '', instrument?.code ?? '', '90'],
    queryFn: ({ signal }) => getInstrumentRadar(
      instrument?.code as string, instrument?.market as Market, '90', { signal },
    ),
    enabled: instrument !== null,
    staleTime: HOME_STALE_MS,
    retry: false,
  })
  const changes = useQuery({
    queryKey: ['radar-rating-changes', RATING_CHANGE_ROWS],
    queryFn: () => getRatingChanges(RATING_CHANGE_ROWS),
    staleTime: HOME_STALE_MS,
    retry: false,
  })
  const brief = useQuery({
    queryKey: ['brief', 'latest'],
    queryFn: getLatestBrief,
    staleTime: HOME_STALE_MS,
    retry: false,
  })

  // 四張卡一起進場（交接包：骨架 → 依序 stagger），不讓先到的那張先跳出來、後到的再把版面撐開。
  const ready = !!head && settled(doc) && settled(consensus) && settled(changes) && settled(brief)
  return { ready, doc, consensus, changes, brief }
}
