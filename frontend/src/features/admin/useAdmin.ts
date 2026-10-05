import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { jsonBody, requestJSON } from '../../lib/api'
import {
  adminUserSchema, adminUsersSchema, auditPageSchema, forceLogoutSchema,
  type AdminUser, type AuditPage, type Role,
} from '../../lib/adminSchemas'

export const USERS_KEY = ['admin', 'users'] as const
export const AUDIT_KEY = ['admin', 'audit'] as const
export const AUDIT_PAGE_SIZE = 20

/** 帳號清單。錯誤一律走 requestJSON：403／409 的 detail 要能原樣顯示。 */
export function useAdminUsers() {
  return useQuery<AdminUser[]>({
    queryKey: USERS_KEY,
    queryFn: async () => (await requestJSON('/api/admin/users', adminUsersSchema, { cache: 'no-store' })).items,
    retry: false,
  })
}

/** 管理操作稽核，一頁 AUDIT_PAGE_SIZE 筆；翻頁時保留上一頁，避免表格閃空。 */
export function useAdminAudit(offset: number) {
  return useQuery<AuditPage>({
    queryKey: [...AUDIT_KEY, offset],
    queryFn: () => requestJSON(
      `/api/admin/audit?limit=${AUDIT_PAGE_SIZE}&offset=${offset}`, auditPageSchema, { cache: 'no-store' },
    ),
    placeholderData: keepPreviousData,
    retry: false,
  })
}

const userPath = (id: string) => `/api/admin/users/${encodeURIComponent(id)}`

/**
 * 帳號管理的四種寫入。每一種成功後都重抓帳號清單與稽核紀錄：後端在同一筆交易裡
 * 改資料並寫稽核，畫面兩邊要一起反映（停用會讓有效 session 歸零、重設密碼也是）。
 */
export function useAdminActions() {
  const client = useQueryClient()
  const refresh = async () => {
    await Promise.all([
      client.invalidateQueries({ queryKey: USERS_KEY }),
      client.invalidateQueries({ queryKey: AUDIT_KEY }),
    ])
  }
  const create = useMutation({
    mutationFn: (body: { username: string; password: string; role: Role }) =>
      requestJSON('/api/admin/users', adminUserSchema, jsonBody('POST', body)),
    onSuccess: refresh,
  })
  const update = useMutation({
    mutationFn: ({ id, ...patch }: { id: string; role?: Role; enabled?: boolean }) =>
      requestJSON(userPath(id), adminUserSchema, jsonBody('PATCH', patch)),
    onSuccess: refresh,
  })
  const resetPassword = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) =>
      requestJSON(`${userPath(id)}/password`, adminUserSchema, jsonBody('POST', { password })),
    onSuccess: refresh,
  })
  const forceLogout = useMutation({
    mutationFn: (id: string) => requestJSON(`${userPath(id)}/logout`, forceLogoutSchema, jsonBody('POST')),
    onSuccess: refresh,
  })
  return { create, update, resetPassword, forceLogout }
}
