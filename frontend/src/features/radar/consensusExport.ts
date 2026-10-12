import type { BrokerSummary } from '../../lib/radarSchemas'
import {
  brokerName, epsBasisKey, FRESHNESS_LABEL, reportFreshness, type EpsBasis,
} from './consensusSeries'

/**
 * 「各家目標價與 EPS」表格 → TSV（貼進 Excel／Google Sheets 會自動分欄）。
 *
 * 目的地是試算表，所以**數值保持原始數字**，不是畫面上的「NT$1,200」——那種字串貼進
 * Excel 是文字，不能加總也不能排序。畫面靠格式化字串與備註小字表達的語意，這裡拆成
 * 獨立欄位：幣別、EPS 的年度／期間／單位各一欄，外加兩欄「納入目前口徑」。
 *
 * 那兩欄不能省：目標價的口徑是幣別眾數、EPS 的口徑是涵蓋最多的年度，不在口徑內的值
 * 畫面上有小字標明「未納入目前口徑」。只貼數字的話，收到的人會把不同幣別、不同年度的
 * 數字放在同一欄比較——正是畫面刻意避免的事。
 *
 * 純函式；複製走 CopyButton → lib/clipboard.ts。
 */
const HEADERS = [
  '券商', '目標價', '目標價幣別', '納入目標價口徑',
  'EPS', 'EPS 幣別', 'EPS 年度', 'EPS 期間', 'EPS 單位', '納入 EPS 口徑',
  '報告日期', '資料新鮮度',
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
  brokers: BrokerSummary[],
  currency: string | null,
  basis: EpsBasis | null,
): string {
  const rows = brokers.map(b => {
    const hasTarget = b.latest_target_price != null
    const hasEps = b.latest_eps_value != null
    const ownCurrency = b.latest_target_currency?.trim() || null
    return [
      brokerName(b),
      hasTarget ? b.latest_target_price : null,
      hasTarget ? ownCurrency : null,
      // 與表格同一個判準：幣別與目前口徑不同（含未標示幣別）就是未納入。
      yesNo(hasTarget, ownCurrency === currency),
      hasEps ? b.latest_eps_value : null,
      hasEps ? b.latest_eps_currency : null,
      hasEps ? b.latest_eps_fy : null,
      hasEps ? b.latest_eps_period : null,
      hasEps ? b.latest_eps_unit : null,
      yesNo(hasEps, basis != null && epsBasisKey(b) === basis.key),
      b.latest_report_date,
      FRESHNESS_LABEL[reportFreshness(b.latest_report_date)],
    ].map(cell).join('\t')
  })
  return [HEADERS.join('\t'), ...rows].join('\n')
}
