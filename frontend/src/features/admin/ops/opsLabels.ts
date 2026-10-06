import { ApiError } from '../../../lib/api'
import type { OpsServiceStatus } from '../../../lib/generated/adminApi'

export type Tier = OpsServiceStatus['tier']
export type Summary = OpsServiceStatus['summary']

export const TIERS: readonly Tier[] = ['critical', 'important', 'supporting']

export const TIER_LABELS: Record<Tier, string> = {
  critical: '核心',
  important: '重要',
  supporting: '輔助',
}

export const TIER_HINTS: Record<Tier, string> = {
  critical: '壞了使用者馬上感覺得到',
  important: '停了不會立刻出事，但資料或告警會開始落後',
  supporting: '稽核、對帳、觀測',
}

export const SUMMARY_LABELS: Record<Summary, string> = {
  running: '運作中',
  idle: '閒置',
  failed: '失敗',
  transitioning: '轉換中',
  not_found: '找不到',
  unknown: '未知',
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
    return a ? (sub ? `${a}／${sub}` : a) : '—'
  }
  if (s.container) {
    const { status, health } = s.container
    return status ? (health ? `${status}（${health}）` : status) : '—'
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
  if (h > 0) return `${h} 小時 ${m} 分`
  if (m > 0) return `${m} 分 ${s % 60} 秒`
  return `${s} 秒`
}

// ── 事件（/api/admin/incidents，P5 的 DB 投影）──────────────────────────────

export const INCIDENT_STATUS_LABELS: Record<'firing' | 'resolved' | 'lost', string> = {
  firing: '進行中',
  resolved: '已恢復',
  lost: '結束不明',
}

export const INCIDENT_STATUS_HINTS: Record<'firing' | 'resolved' | 'lost', string> = {
  firing: '還沒收到「已恢復」；最新狀態以 Slack 告警為準',
  resolved: '主機上的事件偵測已送出「已恢復」',
  lost: '沒收到「已恢復」，但同一個元件之後又開了新事件——它必定已經結束，只是不知道何時',
}

export const EVENT_ACTION_LABELS: Record<'FIRING' | 'REMINDER' | 'ESCALATED' | 'RESOLVED', string> = {
  FIRING: '開始',
  REMINDER: '提醒',
  ESCALATED: '升級',
  RESOLVED: '已恢復',
}

/** P5 各實例的元件名（deploy/systemd/*incident.service 的 INCIDENT_COMPONENT／INCIDENT_MONITOR_COMPONENT）。 */
export const INCIDENT_COMPONENTS: { value: string; label: string }[] = [
  { value: 'web', label: 'Web（本機 /healthz）' },
  { value: 'edge', label: '對外邊緣' },
  { value: 'container', label: '容器' },
  { value: 'host', label: '主機資源' },
  { value: 'linebot', label: 'LINE bot' },
  { value: 'monitor', label: 'Web 探針本身' },
  { value: 'edge_monitor', label: '邊緣探針本身' },
  { value: 'container_monitor', label: '容器探針本身' },
  { value: 'host_monitor', label: '主機探針本身' },
  { value: 'linebot_monitor', label: 'LINE bot 探針本身' },
]

export function componentLabel(component: string): string {
  return INCIDENT_COMPONENTS.find(c => c.value === component)?.label ?? component
}
