import { expect, test } from 'vitest'
import type { AuditEntry } from '../../lib/adminSchemas'
import { actionLabel, auditSummary, authEventLabel } from './auditLabels'

const entry = (over: Partial<AuditEntry>): AuditEntry => ({
  id: 1, actor_user_id: 'u1', actor_username: 'root', action: 'data.export', target_type: 'export',
  target_id: 'users', detail: {}, created_at: '2026-10-06T02:00:00Z', ...over,
} as AuditEntry)

test('CSV 匯出的稽核：種類與筆數，達上限時註明；不顯示 target 代碼', () => {
  expect(actionLabel('data.export')).toBe('匯出 CSV')
  expect(auditSummary(entry({ detail: { kind: 'users', format: 'csv', filters: { limit: 10000 }, row_count: 12, truncated: false } })))
    .toBe('帳號清單・12 筆')
  expect(auditSummary(entry({ target_id: 'reports', detail: { kind: 'reports', row_count: 10000, truncated: true } })))
    .toBe('研報清單・10000 筆（達上限）')
})

test('批次摘要：動作、變更與略過筆數；逐筆那一列註明來自批次', () => {
  expect(actionLabel('report.bulk_visibility')).toBe('批次隱藏／恢復研報')
  expect(actionLabel('user.bulk_action')).toBe('批次帳號操作')
  expect(auditSummary(entry({
    action: 'report.bulk_visibility', target_type: 'bulk', target_id: 'hide',
    detail: { hidden: true, requested: 5, changed: 3, skipped: { not_found: 1, report_is_draft: 1 }, has_reason: true },
  }))).toBe('隱藏・變更 3 筆・略過 2 筆')
  expect(auditSummary(entry({
    action: 'user.bulk_action', target_type: 'bulk', target_id: 'disable',
    detail: { action: 'disable', requested: 2, changed: 2, unchanged: 0, skipped: {}, via: 'web_bulk' },
  }))).toBe('停用・變更 2 筆')
  expect(auditSummary(entry({
    action: 'user.disable', target_type: 'user', target_id: 'u2',
    detail: { username: 'alice', revoked_sessions: 1, via: 'web_bulk' },
  }))).toBe('alice・登出 1 個 session・批次操作')
})

test('Admin v2 預先放好的稽核標籤與登入事件標籤；未知值原樣顯示', () => {
  expect(actionLabel('session.admin_revoke')).toBe('撤銷 session')
  expect(actionLabel('flag.update')).toBe('變更功能旗標')
  expect(actionLabel('quota.update')).toBe('調整配額')
  expect(actionLabel('session.elevate')).toBe('重新驗證（權限提升）')
  expect(actionLabel('something.new')).toBe('something.new')
  expect(authEventLabel('login.failure', 'bad_password')).toBe('登入失敗（密碼錯誤）')
  expect(authEventLabel('login.locked')).toBe('登入被限流')
  expect(authEventLabel('login.weird', 'odd_reason')).toBe('login.weird（odd_reason）')
})
