import type { BrokerSummary, EpsGroup } from '../../lib/radarSchemas'

/** 券商 key（`BrokerSummary.broker` 去空白）。點圖、券商表與網址的 `broker` 共用這一個識別。 */
export function brokerKey(broker: BrokerSummary): string {
  return broker.broker?.trim() ?? ''
}

export function brokerName(broker: BrokerSummary): string {
  return broker.broker_display?.trim() || brokerKey(broker)
}

/**
 * EPS 的可比鍵 `(fiscal_year, period, currency, unit)`，與後端 `_eps_groups_for_signal` 的分組鍵
 * 同一組欄位。券商最新那一筆 EPS 是依該份研報自己的日期挑的年度，所以各家天生混年度；
 * 只有鍵與共識那一組相同的值，才能放在「EPS 中位數」旁邊比。
 */
export function epsBasisKey(broker: BrokerSummary): string {
  return [
    broker.latest_eps_fy ?? '',
    broker.latest_eps_period ?? '',
    broker.latest_eps_currency ?? '',
    broker.latest_eps_unit ?? '',
  ].join('|')
}

export function epsGroupKey(group: EpsGroup): string {
  return [group.fiscal_year ?? '', group.period ?? '', group.currency ?? '', group.unit ?? ''].join('|')
}
