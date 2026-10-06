import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { requestJSON } from '../../lib/api'
import { auditPageSchema, type AuditPage, type Role } from '../../lib/adminSchemas'
import { adminApi, type UserItem } from '../../lib/generated/adminApi'

export type AdminUser = UserItem
export type GrantableScope = NonNullable<UserItem['scopes']>[number]

export const USERS_KEY = ['admin', 'users'] as const
export const AUDIT_KEY = ['admin', 'audit'] as const
export const AUDIT_PAGE_SIZE = 20

/** 帳號清單（產生的 adminApi）。錯誤一律走 requestJSON：403／409 的 detail 與 code 要能原樣顯示。 */
export function useAdminUsers() {
  return useQuery<AdminUser[]>({
    queryKey: USERS_KEY,
    queryFn: async () => (await adminApi.listUsers()).items,
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

/**
 * 帳號管理的寫入。每一種成功後都重抓帳號清單與稽核紀錄：後端在同一筆交易裡
 * 改資料並寫稽核，畫面兩邊要一起反映（停用會讓有效 session 歸零、重設密碼也是）。
 *
 * 需要已提升權限的（權限調整、刪除、重設 TOTP）由呼叫端以 `useElevationGate().guard` 包住
 * `mutateAsync`：收到 `elevation_required` 時彈出驗證框，驗證後自動重試。
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
    mutationFn: (body: { username: string; password: string; role: Role }) => adminApi.createUser(body),
    onSuccess: refresh,
  })
  const update = useMutation({
    mutationFn: ({ id, ...patch }: { id: string; role?: Role; enabled?: boolean }) => adminApi.updateUser(id, patch),
    onSuccess: refresh,
  })
  const resetPassword = useMutation({
    mutationFn: ({ id, password }: { id: string; password: string }) => adminApi.resetPassword(id, { password }),
    onSuccess: refresh,
  })
  const forceLogout = useMutation({
    mutationFn: (id: string) => adminApi.forceLogout(id),
    onSuccess: refresh,
  })
  const setPrivileges = useMutation({
    mutationFn: ({ id, ...body }: { id: string; is_super?: boolean; scopes?: GrantableScope[] }) =>
      adminApi.setPrivileges(id, body),
    onSuccess: refresh,
  })
  const requestDeletion = useMutation({ mutationFn: (id: string) => adminApi.requestDeletion(id), onSuccess: refresh })
  const cancelDeletion = useMutation({ mutationFn: (id: string) => adminApi.cancelDeletion(id), onSuccess: refresh })
  const resetTotp = useMutation({ mutationFn: (id: string) => adminApi.resetTotp(id), onSuccess: refresh })
  return { create, update, resetPassword, forceLogout, setPrivileges, requestDeletion, cancelDeletion, resetTotp }
}
