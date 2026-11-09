/**
 * SSO 登入（`/api/me` 的 `auth_method === 'sso'`）的帳號沒有本地密碼可以重新驗證：權限提升改成回公司 IdP
 * 重新登入（後端 `GET /login/oidc?intent=elevate`，`web/routers/oidc_login.py`）。IdP 導回原頁之後，
 * `/api/me` 的 `elevated_until` 就是新的提升視窗；原本要做的操作需要再按一次。
 */
export function isSsoSession(authMethod: string | null | undefined): boolean {
  return authMethod === 'sso'
}

export function ssoElevateUrl(here: { pathname: string; search: string } = window.location): string {
  return '/login/oidc?intent=elevate&next=' + encodeURIComponent(here.pathname + here.search)
}

export function startSsoElevation(): void {
  window.location.assign(ssoElevateUrl())
}
