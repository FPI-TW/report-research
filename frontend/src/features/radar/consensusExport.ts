import type { BrokerSummary, EpsGroup } from '../../lib/radarSchemas'
import { brokerName, epsBasisKey, epsGroupKey } from './brokerKeys'
import { RATING_DISPLAY } from './radarFormat'

/**
 * 「各券商最新觀點」→ TSV（貼進 Excel／Google Sheets 會自動分欄）。
 *
 * 目的地是試算表，所以**數值保持原始數字**，不是畫面上的「1,568」字串——那種字串貼進
 * Excel 是文字，不能加總也不能排序。畫面靠位置與小字表達的語意拆成獨立欄位：幣別、EPS 的
 * 年度／期間／單位各一欄，外加「納入目標價共識」「與 EPS 共識同口徑」。
 *
 * 那兩欄不能省：共識只算主要幣別與同一個 EPS 口徑，各家的值不一定在裡面。只貼數字的話，
 * 收到的人會把不同幣別、不同年度的數字放在同一欄比較。EPS 那欄只說「這一格的值與共識同口徑」，
 * 不說這家券商有沒有被算進 EPS 共識——同一份研報可能另外報了共識那個年度，這裡只拿到一筆。
 */
const HEADERS = [
  '券商', '評等', '目標價', '目標價幣別', '納入目標價共識',
  'EPS', 'EPS 幣別', 'EPS 年度', 'EPS 期間', 'EPS 單位', '與 EPS 共識同口徑',
  '報告日期', '期間內',
] as const

/** 儲存格內的 tab／換行會讓整列錯位。 */
function cell(v: string | number | null | undefined): string {
  if (v == null) return ''
  return String(v).replace(/[\t\r\n]+/g, ' ').trim()
}

function yesNo(applicable: boolean, included: boolean): string {
  return applicable ? (included ? '是' : '否') : ''
}

export function brokerTableTsv(
  brokers: readonly BrokerSummary[],
  targetCurrency: string | null,
  eps: EpsGroup | null,
): string {
  const epsKey = eps ? epsGroupKey(eps) : null
  const rows = brokers.map(b => {
    const hasTarget = b.latest_target_price != null
    const hasEps = b.latest_eps_value != null
    const ownCurrency = b.latest_target_currency?.trim() || null
    return [
      brokerName(b),
      RATING_DISPLAY[b.latest_rating],
      hasTarget ? b.latest_target_price : null,
      hasTarget ? ownCurrency : null,
      // 期間外的券商不在共識裡，即使幣別相同。
      yesNo(hasTarget, !b.stale && ownCurrency === targetCurrency),
      hasEps ? b.latest_eps_value : null,
      hasEps ? b.latest_eps_currency : null,
      hasEps ? b.latest_eps_fy : null,
      hasEps ? b.latest_eps_period : null,
      hasEps ? b.latest_eps_unit : null,
      yesNo(hasEps, epsKey != null && epsBasisKey(b) === epsKey),
      b.latest_report_date,
      b.stale ? '否' : '是',
    ].map(cell).join('\t')
  })
  return [HEADERS.join('\t'), ...rows].join('\n')
}
