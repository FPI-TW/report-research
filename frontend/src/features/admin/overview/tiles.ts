import type { LampTone } from '../../../components/console'
import type { AdminPageKey } from '../../../components/shell/adminNav'
import type { OpsDependencyGraph, OpsDependencyNode, OpsServiceListResponse } from '../../../lib/generated/adminApi'
import type { AdminSignals, PageSignal } from '../adminSignals'
import { needsAttention } from '../ops/opsLabels'
import { healthLabel } from '../ops/services/health'
import type { SourceState } from '../useAdminSignals'

export interface Tile { key: AdminPageKey; label: string; value: string; sub: string; tone: LampTone }

/** 還沒讀到、讀不到時的磚：不講「正常」。 */
function unknownTile(key: AdminPageKey, label: string, state: SourceState): Tile {
  return { key, label, value: '—', sub: state === 'error' ? '讀不到' : '讀取中', tone: 'idle' }
}

/**
 * 狀態磚（服務、管線、事件、安全、資料健康）。燈號與原因一律取自 `useAdminSignals`——和導覽同一組判準，
 * 這裡只負責把它寫成一格；沒有 scope 的來源（off）不出磚。
 */
export function buildTiles(
  signals: AdminSignals, sources: Record<string, SourceState>, services: OpsServiceListResponse | undefined,
): Tile[] {
  const tiles: Tile[] = []
  const tone = (s: PageSignal | undefined): LampTone => s?.lamp ?? 'ok'

  if (sources.services !== 'off') {
    if (services) {
      const bad = services.items.filter(needsAttention)
      tiles.push({
        key: 'services', label: '服務', tone: bad.length ? 'bad' : 'ok',
        value: `${services.items.length - bad.length}／${services.items.length}`,
        sub: bad.length ? `${bad.length} 個需要處理：${bad.map(s => s.name).join('、')}` : '沒有失敗的服務',
      })
    } else if (signals.services) {
      tiles.push({ key: 'services', label: '服務', tone: 'warn', value: '讀不到', sub: signals.services.reason ?? '' })
    } else {
      tiles.push(unknownTile('services', '服務', sources.services))
    }
  }
  if (sources.pipeline !== 'off') {
    const s = signals.pipeline
    tiles.push(sources.pipeline !== 'ready' ? unknownTile('pipeline', '管線', sources.pipeline) : {
      key: 'pipeline', label: '管線', tone: tone(s),
      value: s?.lamp === 'bad' ? '有異常' : s?.lamp === 'warn' ? '注意' : '正常',
      sub: s?.reason ?? '近 24 小時沒有排程失敗',
    })
  }
  if (sources.incidents !== 'off') {
    const s = signals.incidents
    tiles.push(sources.incidents !== 'ready' ? unknownTile('incidents', '事件', sources.incidents) : {
      key: 'incidents', label: '事件', tone: tone(s),
      value: s?.count ? `${s.count} 進行中` : '0 進行中',
      sub: s?.detail ?? '沒有進行中的事件',
    })
  }
  if (sources.security !== 'off') {
    const s = signals.security
    tiles.push(sources.security !== 'ready' ? unknownTile('security', '安全', sources.security) : {
      key: 'security', label: '安全', tone: tone(s),
      value: s?.lamp ? '告警中' : '正常',
      sub: s?.reason ?? '沒有觸發中的告警',
    })
  }
  if (sources.dataHealth !== 'off') {
    const s = signals.dataHealth
    tiles.push(sources.dataHealth !== 'ready' ? unknownTile('dataHealth', '資料健康', sources.dataHealth) : {
      key: 'dataHealth', label: '資料健康', tone: tone(s),
      value: s?.lamp === 'bad' ? '異常' : s?.lamp === 'warn' ? '注意' : '正常',
      sub: s?.detail ?? (s?.lamp ? '' : '新鮮度、稽核、R2 對帳都正常'),
    })
  }
  return tiles
}

export const externalNodes = (graph: OpsDependencyGraph): OpsDependencyNode[] => graph.nodes.filter(n => n.kind === 'external')

/**
 * 外部依賴磚：正常／總數。健康判讀是後端算好的（探針最後一次的結果），這裡只數；
 * 故障紅、降級橘，其餘用中性色——沒有探針的「未監控」與狀態不明都不是正常，也不是異常。
 */
export function externalTile(q: { data: OpsDependencyGraph | undefined; isError: boolean }): Tile {
  if (!q.data) return unknownTile('dependencies', '外部依賴', q.isError ? 'error' : 'pending')
  const ext = externalNodes(q.data)
  const names = (list: OpsDependencyNode[]) => list.map(n => n.name).join('、')
  const down = ext.filter(n => n.health === 'down')
  const degraded = ext.filter(n => n.health === 'degraded')
  const unknown = ext.filter(n => n.health === 'unknown')
  const unmonitored = unknown.filter(n => healthLabel(n) === '未監控')
  const sub = ext.length === 0 ? '沒有登記外部依賴'
    : down.length ? `${names(down)} 故障`
      : degraded.length ? `${names(degraded)} 降級`
        : unknown.length === 0 ? '都正常'
          : unmonitored.length === unknown.length ? `${names(unknown)} 未監控` : `${names(unknown)} 狀態不明`
  return {
    key: 'dependencies', label: '外部依賴',
    value: `${ext.filter(n => n.health === 'ok').length}／${ext.length}`, sub,
    tone: down.length ? 'bad' : degraded.length ? 'warn' : ext.length > 0 && unknown.length === 0 ? 'ok' : 'idle',
  }
}
