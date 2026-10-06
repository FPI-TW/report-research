import { expect, test } from 'vitest'
import { adminUsersSchema, auditPageSchema, meSchema } from './adminSchemas'
import { reviewItemSchema, reviewStateSchema } from './reviewSchemas'

test('me：管理員與免登入開發模式（id 為 null）都解析得了；未知角色拒收', () => {
  expect(meSchema.parse({ id: 'u1', username: 'root', role: 'admin' }).role).toBe('admin')
  expect(meSchema.parse({ id: null, username: 'dev', role: 'admin' }).id).toBeNull()
  expect(() => meSchema.parse({ id: 'u1', username: 'x', role: 'superuser' })).toThrow()
  // scopes 不被 zod 丟掉（「查看內容」靠它決定顯示）；舊後端沒有這鍵時是 undefined
  expect(meSchema.parse({ id: 'u1', username: 'qa', role: 'admin', scopes: ['qa_content.read'] }).scopes)
    .toEqual(['qa_content.read'])
  expect(meSchema.parse({ id: 'u1', username: 'root', role: 'admin' }).scopes).toBeUndefined()
})

test('帳號清單：時間欄位可為 null 或缺鍵', () => {
  const parsed = adminUsersSchema.parse({ items: [{
    id: 'u1', username: 'alice', role: 'user', enabled: false, created_at: '2026-10-01T00:00:00Z',
    last_login_at: null, active_sessions: 0,
  }] })
  expect(parsed.items[0].enabled).toBe(false)
  expect(parsed.items[0].last_seen_at).toBeUndefined()
})

test('稽核：未知 action 照收（不用 enum）、CLI 的 actor 為 null、畸形 detail 退回空物件', () => {
  const parsed = auditPageSchema.parse({
    total: 3, limit: 20, offset: 0, has_more: false, next_offset: null,
    items: [
      { id: 1, actor_user_id: null, actor_username: null, action: 'user.create', target_type: 'user',
        target_id: 'u1', detail: { username: 'alice', via: 'cli' }, created_at: '2026-10-05T00:00:00Z' },
      { id: 2, actor_user_id: 'u9', actor_username: 'root', action: 'user.rename_future', target_type: 'user',
        target_id: 'u1', detail: {}, created_at: null },
      { id: 3, actor_user_id: 'u9', actor_username: 'root', action: 'review.update', target_type: 'review',
        target_id: 'x', detail: 'not-an-object', created_at: null },
    ],
  })
  expect(parsed.items.map(i => i.action)).toEqual(['user.create', 'user.rename_future', 'review.update'])
  expect(parsed.items[2].detail).toEqual({})
})

test('待複核：reviewer 與 asker_code 不被 zod 丟掉；原文與帳號名即使送來也不留下', () => {
  const item = reviewItemSchema.parse({
    qa_id: 'q1', reviewer: 'root', asker_code: '3fa9c2d1', question: '機密', asked_by: 'alice',
  })
  expect(item.reviewer).toBe('root')
  expect(item.asker_code).toBe('3fa9c2d1')
  expect(item).not.toHaveProperty('question')
  expect(item).not.toHaveProperty('asked_by')
  const legacy = reviewItemSchema.parse({ qa_id: 'q2', reviewer: null, asker_code: null })
  expect(legacy.reviewer).toBeNull()
  expect(reviewItemSchema.parse({ qa_id: 'q3' }).asker_code).toBeUndefined()
  const state = reviewStateSchema.parse({
    kind: 'feedback', subject_id: 'q1', status: 'resolved', note: '', verification: 'passed',
    updated_at: '2026-10-05T00:00:00Z', reviewer: 'root',
  })
  expect(state.reviewer).toBe('root')
})
