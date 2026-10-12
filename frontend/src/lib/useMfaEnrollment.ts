import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError } from './api'

/** 後端 `web/authz.py` 的 `MFA_ENROLLMENT_REQUIRED`：管理員必須先開 TOTP 才能用管理功能。 */
export const MFA_ENROLLMENT_CODE = 'mfa_enrollment_required'

export function isMfaEnrollmentError(err: unknown): boolean {
  return err instanceof ApiError && err.status === 403 && err.code === MFA_ENROLLMENT_CODE
}

/**
 * 管理後台任何一支 API 回 403 `mfa_enrollment_required` 時，重新取 `/api/me`。
 *
 * 要不要顯示 TOTP 設定由 `/api/me` 的 `mfa_enrollment_required` 決定（與後端同一個判斷函式）；
 * 這裡只是「政策在使用中途被打開、或 TOTP 被管理員重設」時讓那個值立刻刷新，而不是等 staleTime 過期。
 */
export function useMfaEnrollmentSignal(): void {
  const client = useQueryClient()
  useEffect(() => {
    const refresh = () => { void client.invalidateQueries({ queryKey: ['me'] }) }
    const unsubQueries = client.getQueryCache().subscribe(event => {
      if (event.type === 'updated' && event.query.queryKey[0] !== 'me' && isMfaEnrollmentError(event.query.state.error)) refresh()
    })
    const unsubMutations = client.getMutationCache().subscribe(event => {
      if (event.type === 'updated' && isMfaEnrollmentError(event.mutation?.state.error)) refresh()
    })
    return () => { unsubQueries(); unsubMutations() }
  }, [client])
}
