import type { LampTone } from '../../../../components/console'
import type { OpsDependencyNode, OpsServiceStatus } from '../../../../lib/generated/adminApi'
import { needsAttention } from '../opsLabels'

/**
 * 服務的燈號：只有異常會亮。失敗、找不到＝紅；核心服務沒在跑（`needsAttention` 的另一半）＝橘；
 * 排程服務兩次執行之間的「閒置」是正常，用中性色。
 */
export function serviceTone(s: OpsServiceStatus): LampTone {
  if (s.summary === 'failed' || s.summary === 'not_found') return 'bad'
  if (needsAttention(s)) return 'warn'
  if (s.summary === 'running') return 'ok'
  if (s.summary === 'transitioning') return 'info'
  return 'idle'
}

type Health = OpsDependencyNode['health']

const HEALTH_TONE: Record<Health, LampTone> = { ok: 'ok', degraded: 'warn', down: 'bad', unknown: 'idle' }
const HEALTH_LABELS: Record<Health, string> = { ok: '正常', degraded: '降級', down: '故障', unknown: '不明' }

export const NODE_KIND_LABELS: Record<OpsDependencyNode['kind'], string> = {
  systemd: 'systemd',
  container: '容器',
  external: '外部',
}

/** 依賴圖節點的燈號（健康判讀由後端算好，這裡只對應顏色）。 */
export function nodeTone(n: Pick<OpsDependencyNode, 'health'>): LampTone {
  return HEALTH_TONE[n.health]
}

/** 外部依賴沒有探針時，「不明」其實是「未監控」——標籤講清楚，不讓人以為壞了。 */
export function healthLabel(n: OpsDependencyNode): string {
  if (n.health === 'unknown' && n.kind === 'external' && !n.probe) return '未監控'
  return HEALTH_LABELS[n.health]
}

/** 服務詳情抽屜的網址（服務頁的 `?service=<name>`）；依賴圖、總覽都連到這裡。 */
export function serviceHref(name: string): string {
  return `/admin/operations/services?service=${encodeURIComponent(name)}`
}

/** 日誌頁預先選好這個服務。 */
export function logsHref(name: string): string {
  return `/admin/operations/logs?service=${encodeURIComponent(name)}`
}
