import type { ApiClientEntitlements } from '../../../lib/generated/adminApi'
import { MARKET_ORDER, marketLabel } from '../../../lib/meta'
import type { AdminApiClient, ApiClientScope } from '../useAdminApiClients'

/** 錯誤物件 → 給人看的訊息。後端 400／404／409 的 detail 由 requestJSON 放進 message，原樣顯示。 */
export function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

export const SCOPE_LABELS: Record<ApiClientScope, string> = {
  search: '語意檢索（search）',
  'report.file': '研報原檔連結（report.file）',
}
export const SCOPE_ORDER = Object.keys(SCOPE_LABELS) as ApiClientScope[]

// 選填維度：留空＝不限。值是語料裡的原字串（券商名、報告類型、商品類型代碼），逐字比對。
export const OPTIONAL_DIMS = [
  { key: 'source', label: '券商', example: '元大、凱基' },
  { key: 'report_type', label: '報告類型', example: '個股報告、產業報告' },
  { key: 'instrument_type', label: '商品類型', example: 'equity、etf' },
] as const
export type OptionalDim = (typeof OPTIONAL_DIMS)[number]['key']
export type DimText = Record<OptionalDim, string>

// 與後端 app/services/api_clients.py 的範圍相同；這裡只是提早提示，後端仍會再驗一次。
export const RATE_RANGE = [1, 6000] as const
export const QUOTA_RANGE = [1, 1_000_000] as const
export const NAME_MAX = 100
export const NOTE_MAX = 1000

/** 「元大、凱基, 永豐」→ ['元大', '凱基', '永豐']；頓號、逗號、換行都算分隔，空白值丟掉。 */
export function splitValues(text: string): string[] {
  return [...new Set(text.split(/[、,，\n]/).map(s => s.trim()).filter(Boolean))]
}

export function dimTextOf(e: ApiClientEntitlements | undefined): DimText {
  return {
    source: (e?.source ?? []).join('、'),
    report_type: (e?.report_type ?? []).join('、'),
    instrument_type: (e?.instrument_type ?? []).join('、'),
  }
}

export function entitlementsOf(markets: readonly string[], dims: DimText): ApiClientEntitlements {
  const out: ApiClientEntitlements = { market: MARKET_ORDER.filter(m => markets.includes(m)) }
  for (const { key } of OPTIONAL_DIMS) {
    const values = splitValues(dims[key])
    if (values.length) out[key] = values
  }
  return out
}

export const sameList = (a: readonly string[] | null | undefined, b: readonly string[] | null | undefined) =>
  [...(a ?? [])].sort().join('\n') === [...(b ?? [])].sort().join('\n')

export function sameEntitlements(a: ApiClientEntitlements, b: ApiClientEntitlements): boolean {
  return sameList(a.market, b.market) && OPTIONAL_DIMS.every(({ key }) => sameList(a[key], b[key]))
}

/** 每分鐘限流與每日額度的前端檢查；通過回 null。 */
export function limitsError(rate: number, quota: number): string | null {
  if (!Number.isInteger(rate) || rate < RATE_RANGE[0] || rate > RATE_RANGE[1]) {
    return `每分鐘限流需為 ${RATE_RANGE[0]}–${RATE_RANGE[1]} 的整數`
  }
  if (!Number.isInteger(quota) || quota < QUOTA_RANGE[0] || quota > QUOTA_RANGE[1]) {
    return `每日額度需為 ${QUOTA_RANGE[0]}–${QUOTA_RANGE[1].toLocaleString()} 的整數`
  }
  return null
}

/** 授權的市場（中文名）。市場必選，所以一定有內容。 */
export function marketsText(e: ApiClientEntitlements): string {
  return e.market.map(marketLabel).join('、')
}

/** 選填維度的摘要：有設定的列出來，都沒設定就是不限。 */
export function dimsText(e: ApiClientEntitlements): string {
  const parts: string[] = []
  for (const { key, label } of OPTIONAL_DIMS) {
    const values = e[key]
    if (values?.length) parts.push(`${label}：${values.join('、')}`)
  }
  return parts.length ? parts.join('；') : '券商、報告類型、商品類型不限'
}

/** 清單與詳情顯示的金鑰前綴。完整金鑰只在建立與輪替時出現一次，其他地方都只有前綴。 */
export const keyPrefixText = (c: Pick<AdminApiClient, 'key_prefix'>) => `rmk_${c.key_prefix}_…`
