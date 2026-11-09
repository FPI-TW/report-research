import type { LampTone } from '../../../components/console'
import type { AdminPageKey } from '../../../components/shell/adminNav'
import type { OpsDependencyGraph, OpsDependencyNode, OpsServiceListResponse } from '../../../lib/generated/adminApi'
import type { AdminSignals, PageSignal } from '../adminSignals'
import { needsAttention } from '../ops/opsLabels'
import { healthLabel } from '../ops/services/health'
import type { SourceState } from '../useAdminSignals'
import { t } from '@/i18n'

export interface Tile { key: AdminPageKey; label: string; value: string; sub: string; tone: LampTone }

/** 還沒讀到、讀不到時的磚：不講「正常」。 */
function unknownTile(key: AdminPageKey, label: string, state: SourceState): Tile {
  return { key, label, value: '—', sub: state === 'error' ? t('admin.overview.tiles.unavailable') : t('admin.overview.tiles.loading'), tone: 'idle' }
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
        key: 'services', label: t('admin.overview.tiles.services'), tone: bad.length ? 'bad' : 'ok',
        value: t('admin.overview.tiles.text', { v: services.items.length - bad.length, length: services.items.length }),
        sub: bad.length ? t('admin.overview.tiles.needAttention', { length: bad.length, map: bad.map(s => s.name).join(t('admin.overview.tiles.text2')) }) : t('admin.overview.tiles.noFailingServices'),
      })
    } else if (signals.services) {
      tiles.push({ key: 'services', label: t('admin.overview.tiles.services'), tone: 'warn', value: t('admin.overview.tiles.unavailable'), sub: signals.services.reason ?? '' })
    } else {
      tiles.push(unknownTile('services', t('admin.overview.tiles.services'), sources.services))
    }
  }
  if (sources.pipeline !== 'off') {
    const s = signals.pipeline
    tiles.push(sources.pipeline !== 'ready' ? unknownTile('pipeline', t('admin.overview.tiles.pipeline'), sources.pipeline) : {
      key: 'pipeline', label: t('admin.overview.tiles.pipeline'), tone: tone(s),
      value: s?.lamp === 'bad' ? t('admin.overview.tiles.errors') : s?.lamp === 'warn' ? t('admin.overview.tiles.warning') : t('admin.overview.tiles.ok'),
      sub: s?.reason ?? t('admin.overview.tiles.noScheduleFailuresInThe'),
    })
  }
  if (sources.incidents !== 'off') {
    const s = signals.incidents
    tiles.push(sources.incidents !== 'ready' ? unknownTile('incidents', t('admin.overview.tiles.incidents'), sources.incidents) : {
      key: 'incidents', label: t('admin.overview.tiles.incidents'), tone: tone(s),
      value: s?.count ? t('admin.overview.tiles.open', { count: s.count }) : t('admin.overview.tiles.0Open'),
      sub: s?.detail ?? t('admin.overview.tiles.noOpenIncidents'),
    })
  }
  if (sources.security !== 'off') {
    const s = signals.security
    tiles.push(sources.security !== 'ready' ? unknownTile('security', t('admin.overview.tiles.security'), sources.security) : {
      key: 'security', label: t('admin.overview.tiles.security'), tone: tone(s),
      value: s?.lamp ? t('admin.overview.tiles.alerting') : t('admin.overview.tiles.ok'),
      sub: s?.reason ?? t('admin.overview.tiles.noAlertsFiring'),
    })
  }
  if (sources.dataHealth !== 'off') {
    const s = signals.dataHealth
    tiles.push(sources.dataHealth !== 'ready' ? unknownTile('dataHealth', t('admin.overview.tiles.dataHealth'), sources.dataHealth) : {
      key: 'dataHealth', label: t('admin.overview.tiles.dataHealth'), tone: tone(s),
      value: s?.lamp === 'bad' ? t('admin.overview.tiles.errors2') : s?.lamp === 'warn' ? t('admin.overview.tiles.warning') : t('admin.overview.tiles.ok'),
      sub: s?.detail ?? (s?.lamp ? '' : t('admin.overview.tiles.freshnessAuditAndR2Reconciliation')),
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
  if (!q.data) return unknownTile('dependencies', t('admin.overview.tiles.externalDependencies'), q.isError ? 'error' : 'pending')
  const ext = externalNodes(q.data)
  const names = (list: OpsDependencyNode[]) => list.map(n => n.name).join(t('admin.overview.tiles.text2'))
  const down = ext.filter(n => n.health === 'down')
  const degraded = ext.filter(n => n.health === 'degraded')
  const unknown = ext.filter(n => n.health === 'unknown')
  const unmonitored = unknown.filter(n => healthLabel(n) === '未監控')
  const sub = ext.length === 0 ? t('admin.overview.tiles.noExternalDependenciesRegistered')
    : down.length ? t('admin.overview.tiles.down', { down: names(down) })
      : degraded.length ? t('admin.overview.tiles.degraded', { degraded: names(degraded) })
        : unknown.length === 0 ? t('admin.overview.tiles.allOk')
          : unmonitored.length === unknown.length ? t('admin.overview.tiles.unmonitored', { unknown: names(unknown) }) : t('admin.overview.tiles.unknown', { unknown: names(unknown) })
  return {
    key: 'dependencies', label: t('admin.overview.tiles.externalDependencies'),
    value: t('admin.overview.tiles.text3', { length: ext.filter(n => n.health === 'ok').length, length2: ext.length }), sub,
    tone: down.length ? 'bad' : degraded.length ? 'warn' : ext.length > 0 && unknown.length === 0 ? 'ok' : 'idle',
  }
}
