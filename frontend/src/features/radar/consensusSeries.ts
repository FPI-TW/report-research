/**
 * 「市場共識摘要」兩張點圖的純資料層：把 `brokers[]` 攤成「一家券商一個點」。
 *
 * 這裡**不做任何聚合**——沒有中位數、沒有平均、沒有趨勢線。後端 `radar/compute.py`
 * 算出來的 `target_price.groups[*].median` / `eps.groups[*].median` 刻意不進這條路徑。
 * 每個點都直接對應某一家券商的某一份研報，點得下去、查得回原文。
 *
 * 兩個可比性規則是這個模組存在的理由：
 *  1. **目標價保幣別不換算**（後端擷取層的既有約定）。不同幣別的目標價畫在同一條軸上
 *     只是把 US$ 與 NT$ 的數字並排，那是無意義的比較，所以幣別不符者退成「未納入」列。
 *  2. **EPS 的可比鍵是四元組 `(fiscal_year, period, currency, unit)`**，與後端
 *     `_eps_groups_for_signal` 的分組鍵逐字相同。而 `BrokerSummary.latest_eps_*`
 *     只有**一筆**，是後端 `_select_primary_eps` 依「該份研報自己的 report_date」挑的
 *     ——2025 年底出的報告會挑到 FY25E、2026 年出的挑到 FY26E，所以 `brokers[]` 這一欄
 *     **天生就是混年度的**。不先選定口徑就畫，等於把 FY25E 與 FY26E 疊在同一條軸上。
 */
import type { BrokerSummary } from '../../lib/radarSchemas'
import {
  daysSinceReport, epsPeriodLabel, fmtEps, fmtPrice, STALE_REPORT_DAYS,
} from './radarFormat'

/**
 * 研報新鮮度三態。
 *
 * `unknown` 不可以併進 `recent`：後端 `report_date` 為 NULL 時給的是空字串，
 * 而 `isReportStale` 對解析不出來的日期回 false（「不知道日期不等於零天」）。
 * 那個回退對「要不要顯示過期提示」是安全的，但拿來當「90 天內」的證據就是憑空斷言。
 */
export type Freshness = 'recent' | 'stale' | 'unknown'

export const FRESHNESS_LABEL: Record<Freshness, string> = {
  recent: `${STALE_REPORT_DAYS} 天內`,
  stale: `超過 ${STALE_REPORT_DAYS} 天`,
  unknown: '報告日期不明',
}

/** 兩個點圖與表格共用的資料範圍切換。`recent` 只留能證明在 90 天內的。 */
export type FreshnessScope = 'recent' | 'all'

export function reportFreshness(
  iso: string | null | undefined,
  now: Date = new Date(),
): Freshness {
  const days = daysSinceReport(iso, now)
  if (days == null) return 'unknown'
  return days > STALE_REPORT_DAYS ? 'stale' : 'recent'
}

export function inScope(freshness: Freshness, scope: FreshnessScope): boolean {
  return scope === 'all' || freshness === 'recent'
}

export interface AxisScale {
  min: number
  max: number
  ticks: number[]
}

export interface DotPoint {
  /** 券商 key（`BrokerSummary.broker` 去空白後）。跨兩張圖與詳情面板的唯一識別。 */
  key: string
  broker: string
  value: number
  valueLabel: string
  reportDate: string
  freshness: Freshness
}

/** 有數值、但不在目前口徑內的券商。刻意留在畫面上——消失等於謊稱這家沒給數字。 */
export interface ExcludedRow {
  key: string
  broker: string
  note: string
}

export interface DotSeries {
  points: DotPoint[]
  excluded: ExcludedRow[]
  /** `points` 為空時為 null：沒有資料就不該畫出一條憑空的刻度軸。 */
  scale: AxisScale | null
  /** 完全沒有這個指標的券商數（既不在 points 也不在 excluded）。 */
  missing: number
}

/** EPS 的可比口徑。`key` 與後端 `_eps_group_key` 的四元組同構。 */
export interface EpsBasis {
  key: string
  label: string
  fiscalYear: number | null
  period: string | null
  currency: string | null
  unit: string | null
  count: number
}

export function brokerKey(broker: BrokerSummary): string {
  return broker.broker?.trim() ?? ''
}

export function brokerName(broker: BrokerSummary): string {
  return broker.broker_display?.trim() || brokerKey(broker)
}

/** 「好看的」級距寬度。round=false 取涵蓋整個範圍的下一個級距，true 取最接近的。 */
function niceNum(range: number, round: boolean): number {
  const exponent = Math.floor(Math.log10(range))
  const fraction = range / 10 ** exponent
  let nice: number
  if (round) {
    if (fraction < 1.5) nice = 1
    else if (fraction < 3) nice = 2
    else if (fraction < 7) nice = 5
    else nice = 10
  } else if (fraction <= 1) nice = 1
  else if (fraction <= 2) nice = 2
  else if (fraction <= 5) nice = 5
  else nice = 10
  return nice * 10 ** exponent
}

/** 浮點累加會漂（0.1 + 0.2 = 0.30000000000000004），刻度值一律收斂到 12 位有效數字。 */
function tidy(value: number): number {
  return Number(value.toPrecision(12))
}

/**
 * Wilkinson-lite 的「好看刻度」：`[205, 210, 280, 478]` → 200/300/400/500。
 *
 * 刻意**不從 0 起算**。目標價的絕對值遠大於彼此的差距（205 vs 478），從 0 起算會把
 * 所有點擠進右半段、券商之間的分歧完全看不出來——而分歧正是這張圖唯一要講的事。
 * 代價是視覺上的距離不等比於數值比例，所以每個點都標了數字、且軸有刻度。
 */
export function niceScale(values: number[], maxTicks = 5): AxisScale | null {
  if (!values.length) return null

  let lo = Math.min(...values)
  let hi = Math.max(...values)
  if (lo === hi) {
    // 單一資料點沒有「範圍」可言。給一個對稱的假範圍讓它落在正中央，
    // 而不是讓 (value - min) / (max - min) 變成 0/0。
    const pad = lo === 0 ? 1 : Math.abs(lo) * 0.1
    lo -= pad
    hi += pad
  }

  const step = niceNum(niceNum(hi - lo, false) / Math.max(1, maxTicks - 1), true)
  const min = tidy(Math.floor(lo / step) * step)
  const max = tidy(Math.ceil(hi / step) * step)

  const ticks: number[] = []
  const count = Math.round((max - min) / step)
  for (let i = 0; i <= count; i += 1) ticks.push(tidy(min + i * step))

  return { min, max, ticks }
}

/** 數值 → 軸上的百分比位置。落在軸外時夾回兩端（不該發生，但夾住比溢出好讀）。 */
export function axisPosition(value: number, scale: AxisScale): number {
  const span = scale.max - scale.min
  if (span <= 0) return 50
  return Math.min(100, Math.max(0, ((value - scale.min) / span) * 100))
}

function byValueThenName(a: DotPoint, b: DotPoint): number {
  return a.value - b.value || a.broker.localeCompare(b.broker, 'zh-Hant')
}

/**
 * 主要幣別：優先用後端共識給的 `primary_currency`，否則取券商之間最常見的那一個。
 *
 * 眾數相同時取字典序最小者，只是為了讓輸出穩定——換一次窗期就換一種幣別當基準
 * 會讓同一檔標的在兩次載入之間講不同的話。
 */
export function pickTargetCurrency(
  brokers: BrokerSummary[],
  preferred?: string | null,
): string | null {
  const tally = new Map<string, number>()
  for (const broker of brokers) {
    if (broker.latest_target_price == null) continue
    const currency = broker.latest_target_currency?.trim()
    if (!currency) continue
    tally.set(currency, (tally.get(currency) ?? 0) + 1)
  }
  if (preferred && tally.has(preferred)) return preferred
  let best: string | null = null
  let bestCount = 0
  for (const [currency, count] of [...tally].sort((a, b) => a[0].localeCompare(b[0]))) {
    if (count > bestCount) {
      best = currency
      bestCount = count
    }
  }
  return best
}

export function buildTargetSeries(
  brokers: BrokerSummary[],
  currency: string | null,
  now?: Date,
): DotSeries {
  const points: DotPoint[] = []
  const excluded: ExcludedRow[] = []
  let missing = 0

  for (const broker of brokers) {
    const key = brokerKey(broker)
    if (!key) continue
    if (broker.latest_target_price == null) {
      missing += 1
      continue
    }
    const own = broker.latest_target_currency?.trim() || null
    if (own !== currency) {
      excluded.push({
        key,
        broker: brokerName(broker),
        // 未標示幣別不等於「就是主要幣別」——擷取層允許有價無幣別，猜一個等於捏造。
        note: `${own ?? '未標示幣別'}，未納入目前口徑`,
      })
      continue
    }
    points.push({
      key,
      broker: brokerName(broker),
      value: broker.latest_target_price,
      valueLabel: fmtPrice(broker.latest_target_price, currency),
      reportDate: broker.latest_report_date,
      freshness: reportFreshness(broker.latest_report_date, now),
    })
  }

  points.sort(byValueThenName)
  excluded.sort((a, b) => a.broker.localeCompare(b.broker, 'zh-Hant'))
  return { points, excluded, scale: niceScale(points.map(p => p.value)), missing }
}

/**
 * 券商自己那一筆 EPS 的可比鍵。**只此一處**——先前詳情面板另外組了一份一模一樣的字串，
 * 而欄位順序或分隔符任一邊改動都不會有東西報錯，只會讓面板與圖對同一家券商講不同的話。
 */
export function epsBasisKey(broker: BrokerSummary): string {
  return [
    broker.latest_eps_fy ?? '',
    broker.latest_eps_period ?? '',
    broker.latest_eps_currency ?? '',
    broker.latest_eps_unit ?? '',
  ].join('|')
}

/** 券商自己那一筆 EPS 的期間標籤（`FY26E` / `FY26E 1H`）。 */
export function epsBasisLabel(broker: BrokerSummary): string {
  return epsPeriodLabel(broker.latest_eps_fy, broker.latest_eps_period) || '未標示期間'
}

/**
 * 「不在目前口徑」的說明句。**點圖、表格與詳情面板共用這一支。**
 *
 * 三處先前各寫一份，其中表格那份寫死「未納入目前期間」——於是幣別不同（期間相同）的
 * 券商，在圖上被說成「口徑不同」、在表格裡被說成「期間不同」，而它的期間明明一樣。
 * 更糟的是期間相同時，`FY26E，未納入目前期間` 會與正上方的欄名 `FY26E EPS` 直接打架。
 */
export function epsExclusionNote(broker: BrokerSummary, basis: EpsBasis | null): string {
  const sameSpan = (broker.latest_eps_fy ?? null) === (basis?.fiscalYear ?? null)
    && (broker.latest_eps_period ?? null) === (basis?.period ?? null)
  // 期間相同時印期間標籤只會製造「FY26E，未納入目前 FY26E」這種自相矛盾的句子，
  // 改印真正造成不可比的那一項（幣別／每股口徑）。
  const label = sameSpan
    ? [broker.latest_eps_currency, broker.latest_eps_unit].filter(Boolean).join(' · ') || '不同口徑'
    : epsBasisLabel(broker)
  return `${label}，未納入目前${sameSpan ? '口徑' : '期間'}`
}

/**
 * 有哪些 EPS 口徑可選，依「涵蓋券商數」由多到少排（同數再取較新的財年）。
 *
 * 預設選最多人給的那一個：可比的家數愈多，這張圖能講的話就愈完整。
 * 標籤只印財年與期間；同名（例如兩組都叫 FY26E 但幣別不同）時才補幣別／口徑去歧義
 * ——平常不印是因為 `unit` 一律被後端補成 `per_share`，攤給讀者看只是洩漏欄位值。
 */
export function epsBases(brokers: BrokerSummary[]): EpsBasis[] {
  const map = new Map<string, EpsBasis>()
  for (const broker of brokers) {
    if (!brokerKey(broker) || broker.latest_eps_value == null) continue
    const key = epsBasisKey(broker)
    const found = map.get(key)
    if (found) {
      found.count += 1
      continue
    }
    map.set(key, {
      key,
      label: epsBasisLabel(broker),
      fiscalYear: broker.latest_eps_fy ?? null,
      period: broker.latest_eps_period ?? null,
      currency: broker.latest_eps_currency ?? null,
      unit: broker.latest_eps_unit ?? null,
      count: 1,
    })
  }

  const bases = [...map.values()].sort((a, b) => (
    b.count - a.count
    || (b.fiscalYear ?? -Infinity) - (a.fiscalYear ?? -Infinity)
    || a.key.localeCompare(b.key)
  ))

  const seen = new Map<string, number>()
  for (const basis of bases) seen.set(basis.label, (seen.get(basis.label) ?? 0) + 1)
  return bases.map(basis => (
    (seen.get(basis.label) ?? 0) > 1
      ? { ...basis, label: [basis.label, basis.currency, basis.unit].filter(Boolean).join(' · ') }
      : basis
  ))
}

export function buildEpsSeries(
  brokers: BrokerSummary[],
  basis: EpsBasis | null,
  now?: Date,
): DotSeries {
  const points: DotPoint[] = []
  const excluded: ExcludedRow[] = []
  let missing = 0

  for (const broker of brokers) {
    const key = brokerKey(broker)
    if (!key) continue
    if (broker.latest_eps_value == null) {
      missing += 1
      continue
    }
    if (!basis || epsBasisKey(broker) !== basis.key) {
      excluded.push({ key, broker: brokerName(broker), note: epsExclusionNote(broker, basis) })
      continue
    }
    points.push({
      key,
      broker: brokerName(broker),
      value: broker.latest_eps_value,
      valueLabel: fmtEps(
        broker.latest_eps_value,
        broker.latest_eps_currency,
        null,
        null,
        broker.latest_eps_unit,
      ),
      reportDate: broker.latest_report_date,
      freshness: reportFreshness(broker.latest_report_date, now),
    })
  }

  points.sort(byValueThenName)
  excluded.sort((a, b) => a.broker.localeCompare(b.broker, 'zh-Hant'))
  return { points, excluded, scale: niceScale(points.map(p => p.value)), missing }
}
