import type { OpsRemoteHost } from '../../../lib/generated/adminApi'

export type RemoteState = OpsRemoteHost['state']
export type RemoteAlert = OpsRemoteHost['reasons'][number]
type RemoteSync = NonNullable<OpsRemoteHost['sync']>

// 後端的狀態詞彙在 app/services/host_beacon.py（STATES／ALERT_STATES），經產生的 client 是嚴格 enum；
// 這裡的 Record 以它為鍵，後端加了狀態而這裡沒補，tsc 會擋下來。

export const REMOTE_STATE_LABELS: Record<RemoteState, string> = {
  ok: '正常',
  disabled: '未設定',
  unknown: '判斷不出來',
  missing: '沒有信標',
  invalid: '信標看不懂',
  stale: '主機失聯',
  timer_inactive: '排程停了',
  sync_stalled: '同步停擺',
  source_failed: '來源讀取失敗',
  push_disabled: '推送沒開',
  push_failed: '推送失敗',
  round_aborted: '上一輪被中止',
}

/** 每一條告警成立時，這台（收檔那端）實際受到的影響與先看哪裡。處置細節在 docs/production_resilience.md「來源主機信標」。 */
export const REMOTE_ALERT_HINTS: Record<RemoteAlert, string> = {
  missing: 'R2 上沒有這台主機的信標：它從沒發布過，或兩邊設定的名稱不一致。',
  invalid: '信標的格式這一版讀不懂，多半是兩台主機的程式版本差太多。',
  stale: '那台主機太久沒有回報：停機、排程器沒在跑，或它連不到 R2。這段期間不會有新研報推上來。',
  timer_inactive: '那台的 sync 排程（report-mark-sync.timer）沒有在跑，不會再抓 NAS 的新檔。',
  sync_stalled: '那台的 sync 太久沒有走到來源段的結論：可能一直被上一輪的鎖擋住，或 unit 起不來。',
  source_failed: '那台最近一輪掛不上 NAS 或 rsync 失敗，抓不到新檔。',
  push_disabled: '那台有在抓 NAS 的新檔，但沒開推送（SYNC_INBOX_PUSH）：什麼都不會傳到這裡。',
  push_failed: '那台最近一輪推送到 R2 失敗，或還有沒補傳的批次：那些檔在那台有、這裡沒有，要手動補傳。',
  round_aborted: '那台最近一輪 sync 被中止（關機、斷電）：可能有檔已落地卻沒被列進推送清單，要人工重放。',
}

export const FEED_RESULT_LABELS: Record<NonNullable<RemoteSync['feed_result']>, string> = {
  ok: '成功',
  mount_failed: 'NAS 掛載失敗',
  rsync_failed: 'rsync 失敗',
}

export const PUSH_LABELS: Record<NonNullable<RemoteSync['push']>, string> = {
  off: '沒開',
  none: '本輪沒有新檔',
  ok: '成功',
  failed: '失敗',
}

export const ROUND_STATE_LABELS: Record<RemoteSync['round_state'], string> = {
  running: '執行中',
  done: '已完成',
  failed: '失敗結束',
  signalled: '被訊號中止',
  aborted: '被中止',
  unknown: '不明',
}

export const SOURCE_LABELS: Record<RemoteSync['source'], string> = {
  nas: 'NAS（rsync）',
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
  if (m < 1) return '不到 1 分鐘前'
  if (m < 60) return `${m} 分鐘前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小時 ${m % 60} 分前`
  return `${Math.floor(h / 24)} 天 ${h % 24} 小時前`
}
