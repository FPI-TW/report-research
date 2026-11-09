import { ApiError } from '../../../lib/api'
import type { OpsServiceStatus } from '../../../lib/generated/adminApi'
import { t } from '@/i18n'

export type Tier = OpsServiceStatus['tier']
export type Summary = OpsServiceStatus['summary']

export const TIERS: readonly Tier[] = ['critical', 'important', 'supporting']

export const TIER_LABELS: Record<Tier, string> = {
  get critical() { return t('admin.ops.opsLabels.core') },
  get important() { return t('admin.ops.opsLabels.important') },
  get supporting() { return t('admin.ops.opsLabels.supporting') },
}

export const TIER_HINTS: Record<Tier, string> = {
  get critical() { return t('admin.ops.opsLabels.usersNoticeImmediatelyWhenIt') },
  get important() { return t('admin.ops.opsLabels.nothingBreaksRightAwayWhen') },
  get supporting() { return t('admin.ops.opsLabels.auditReconciliationObservation') },
}

export const SUMMARY_LABELS: Record<Summary, string> = {
  get running() { return t('admin.ops.opsLabels.running') },
  get idle() { return t('admin.ops.opsLabels.idle') },
  get failed() { return t('admin.ops.opsLabels.failed') },
  get transitioning() { return t('admin.ops.opsLabels.transitioning') },
  get not_found() { return t('admin.ops.opsLabels.notFound') },
  get unknown() { return t('admin.ops.opsLabels.unknown') },
}

/** 需要注意：失敗、找不到；核心服務只要不是運作中／轉換中也算（核心層都是常駐服務）。 */
export function needsAttention(s: OpsServiceStatus): boolean {
  if (s.summary === 'failed' || s.summary === 'not_found') return true
  return s.tier === 'critical' && s.summary !== 'running' && s.summary !== 'transitioning'
}

/** 狀態欄：systemd 是 ActiveState／SubState，容器是 status（＋health）。 */
export function stateText(s: OpsServiceStatus): string {
  if (s.systemd) {
    const { active_state: a, sub_state: sub } = s.systemd
    return a ? (sub ? t('admin.ops.opsLabels.text', { a, sub }) : a) : '—'
  }
  if (s.container) {
    const { status, health } = s.container
    return status ? (health ? t('admin.ops.opsLabels.text2', { status, health }) : status) : '—'
  }
  return '—'
}

/** 結果欄：systemd 的 Result，容器的結束碼（只在停著時有意義）。 */
export function resultText(s: OpsServiceStatus): string {
  if (s.systemd) return s.systemd.result ?? '—'
  if (s.container) {
    if (s.container.running) return '—'
    return s.container.exit_code != null ? `exit ${s.container.exit_code}` : '—'
  }
  return '—'
}

/** 最近一次啟動／執行時間。 */
export function lastRunAt(s: OpsServiceStatus): string | null {
  if (s.systemd) return s.systemd.exec_main_start_at ?? s.systemd.active_enter_at ?? null
  if (s.container) return s.container.started_at ?? null
  return null
}

/** 維運代理不可用（503 `ops_agent_unavailable`）：整頁換成降級說明。 */
export function isAgentUnavailable(err: unknown): boolean {
  return err instanceof ApiError && err.code === 'ops_agent_unavailable'
}

/** 秒數 → 「1 小時 5 分」「2 分 30 秒」「8 秒」。 */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—'
  const s = Math.round(seconds)
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  if (h > 0) return t('admin.ops.opsLabels.hMin', { h, m })
  if (m > 0) return t('admin.ops.opsLabels.minS', { m, v: s % 60 })
  return t('admin.ops.opsLabels.s', { s })
}

// ── 事件（/api/admin/incidents，P5 的 DB 投影）──────────────────────────────

export const INCIDENT_STATUS_LABELS: Record<'firing' | 'resolved' | 'lost', string> = {
  get firing() { return t('admin.ops.opsLabels.open') },
  get resolved() { return t('admin.ops.opsLabels.recovered') },
  get lost() { return t('admin.ops.opsLabels.endUnknown') },
}

export const INCIDENT_STATUS_HINTS: Record<'firing' | 'resolved' | 'lost', string> = {
  get firing() { return t('admin.ops.opsLabels.noRecoveredReceivedYetSlack') },
  get resolved() { return t('admin.ops.opsLabels.theHostSIncidentDetection') },
  get lost() { return t('admin.ops.opsLabels.noRecoveredWasReceivedBut') },
}

export const EVENT_ACTION_LABELS: Record<'FIRING' | 'REMINDER' | 'ESCALATED' | 'RESOLVED', string> = {
  get FIRING() { return t('admin.ops.opsLabels.opened') },
  get REMINDER() { return t('admin.ops.opsLabels.reminder') },
  get ESCALATED() { return t('admin.ops.opsLabels.escalated') },
  get RESOLVED() { return t('admin.ops.opsLabels.recovered') },
}

/** P5 各實例的元件名（deploy/systemd/*incident.service 的 INCIDENT_COMPONENT／INCIDENT_MONITOR_COMPONENT）。 */
export const INCIDENT_COMPONENTS: { value: string; label: string }[] = [
  { value: 'web', get label() { return t('admin.ops.opsLabels.webLocalHealthz') } },
  { value: 'edge', get label() { return t('admin.ops.opsLabels.externalEdge') } },
  { value: 'container', get label() { return t('admin.ops.opsLabels.containers') } },
  { value: 'host', get label() { return t('admin.ops.opsLabels.hostResources') } },
  { value: 'linebot', label: 'LINE bot' },
  { value: 'monitor', get label() { return t('admin.ops.opsLabels.webProbeItself') } },
  { value: 'edge_monitor', get label() { return t('admin.ops.opsLabels.edgeProbeItself') } },
  { value: 'container_monitor', get label() { return t('admin.ops.opsLabels.containerProbeItself') } },
  { value: 'host_monitor', get label() { return t('admin.ops.opsLabels.hostProbeItself') } },
  { value: 'linebot_monitor', get label() { return t('admin.ops.opsLabels.lineBotProbeItself') } },
]

export function componentLabel(component: string): string {
  return INCIDENT_COMPONENTS.find(c => c.value === component)?.label ?? component
}
