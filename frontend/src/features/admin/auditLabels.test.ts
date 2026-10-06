import { expect, test } from 'vitest'
import type { AuditEntry } from '../../lib/adminSchemas'
import { actionLabel, auditSummary } from './auditLabels'

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
