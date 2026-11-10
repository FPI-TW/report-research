import type { OpsRemoteHost } from '../../../lib/generated/adminApi'
import { t } from '@/i18n'

export type RemoteState = OpsRemoteHost['state']
export type RemoteAlert = OpsRemoteHost['reasons'][number]
type RemoteSync = NonNullable<OpsRemoteHost['sync']>

// 後端的狀態詞彙在 app/services/host_beacon.py（STATES／ALERT_STATES），經產生的 client 是嚴格 enum；
// 這裡的 Record 以它為鍵，後端加了狀態而這裡沒補，tsc 會擋下來。

export const REMOTE_STATE_LABELS: Record<RemoteState, string> = {
  get ok() { return t('admin.ops.remoteHostLabels.ok') },
  get disabled() { return t('admin.ops.remoteHostLabels.notConfigured') },
  get unknown() { return t('admin.ops.remoteHostLabels.undetermined') },
  get missing() { return t('admin.ops.remoteHostLabels.noBeacon') },
  get invalid() { return t('admin.ops.remoteHostLabels.beaconUnreadable') },
  get stale() { return t('admin.ops.remoteHostLabels.hostLost') },
  get timer_inactive() { return t('admin.ops.remoteHostLabels.scheduleStopped') },
  get sync_stalled() { return t('admin.ops.remoteHostLabels.syncStalled') },
  get source_failed() { return t('admin.ops.remoteHostLabels.sourceReadFailed') },
  get push_disabled() { return t('admin.ops.remoteHostLabels.pushOff') },
  get push_failed() { return t('admin.ops.remoteHostLabels.pushFailed') },
  get round_aborted() { return t('admin.ops.remoteHostLabels.lastRoundAborted') },
}

/** 每一條告警成立時，這台（收檔那端）實際受到的影響與先看哪裡。處置細節在 docs/production_resilience.md「來源主機信標」。 */
export const REMOTE_ALERT_HINTS: Record<RemoteAlert, string> = {
  get missing() { return t('admin.ops.remoteHostLabels.noBeaconForThisHost') },
  get invalid() { return t('admin.ops.remoteHostLabels.thisVersionCanTRead') },
  get stale() { return t('admin.ops.remoteHostLabels.thatHostHasnTReported') },
  get timer_inactive() { return t('admin.ops.remoteHostLabels.thatHostSSyncSchedule') },
  get sync_stalled() { return t('admin.ops.remoteHostLabels.thatHostSSyncHasn') },
  get source_failed() { return t('admin.ops.remoteHostLabels.thatHostSLastRound') },
  get push_disabled() { return t('admin.ops.remoteHostLabels.thatHostIsFetchingNew') },
  get push_failed() { return t('admin.ops.remoteHostLabels.thatHostSLastPush') },
  get round_aborted() { return t('admin.ops.remoteHostLabels.thatHostSLastSync') },
}

export const FEED_RESULT_LABELS: Record<NonNullable<RemoteSync['feed_result']>, string> = {
  get ok() { return t('admin.ops.remoteHostLabels.succeeded') },
  get mount_failed() { return t('admin.ops.remoteHostLabels.nasMountFailed') },
  get rsync_failed() { return t('admin.ops.remoteHostLabels.rsyncFailed') },
}

export const PUSH_LABELS: Record<NonNullable<RemoteSync['push']>, string> = {
  get off() { return t('admin.ops.remoteHostLabels.off') },
  get none() { return t('admin.ops.remoteHostLabels.noNewFilesThisRound') },
  get ok() { return t('admin.ops.remoteHostLabels.succeeded') },
  get failed() { return t('admin.ops.remoteHostLabels.failed') },
}

export const ROUND_STATE_LABELS: Record<RemoteSync['round_state'], string> = {
  get running() { return t('admin.ops.remoteHostLabels.running') },
  get done() { return t('admin.ops.remoteHostLabels.completed') },
  get failed() { return t('admin.ops.remoteHostLabels.endedWithFailure') },
  get signalled() { return t('admin.ops.remoteHostLabels.killedBySignal') },
  get aborted() { return t('admin.ops.remoteHostLabels.aborted') },
  get unknown() { return t('admin.ops.remoteHostLabels.unknown') },
}

export const SOURCE_LABELS: Record<RemoteSync['source'], string> = {
  get nas() { return t('admin.ops.remoteHostLabels.nasRsync') },
  'r2-inbox': 'R2 inbox',
}

export type RemoteTone = 'ok' | 'alert' | 'unknown'

export function remoteTone(state: RemoteState): RemoteTone {
  if (state === 'ok') return 'ok'
  return state === 'unknown' || state === 'disabled' ? 'unknown' : 'alert'
}

/** 秒數 → 「3 分鐘前」「2 小時 5 分前」「1 天 3 小時前」。 */
export function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return '—'
  const m = Math.floor(seconds / 60)
  if (m < 1) return t('admin.ops.remoteHostLabels.lessThan1MinuteAgo')
  if (m < 60) return t('admin.ops.remoteHostLabels.minAgo', { m })
  const h = Math.floor(m / 60)
  if (h < 24) return t('admin.ops.remoteHostLabels.hMinAgo', { h, v: m % 60 })
  return t('admin.ops.remoteHostLabels.daysHAgo', { floor: Math.floor(h / 24), v: h % 24 })
}
