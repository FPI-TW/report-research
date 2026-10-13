import type { AdminUser, BulkUserAction, GrantableScope } from '../useAdmin'

/** 錯誤物件 → 給人看的訊息。後端 400／409 的 detail 由 requestJSON 放進 message，原樣顯示。 */
export function messageOf(err: unknown): string {
  return err instanceof Error && err.message ? err.message : '操作失敗，請重試'
}

/** 可授予的 scope：以產生的 client（`UserItem.scopes` 的 enum）為準，鍵少一個型別就不過。 */
export const SCOPE_LABELS: Record<GrantableScope, string> = {
  'qa_content.read': '查看問答內容（qa_content.read）',
  'ops.operate': '執行維運操作（ops.operate）',
  'api_clients.manage': '管理 API 用戶端（api_clients.manage）',
}

// 密碼政策與後端 app/services/passwords.py 相同；這裡只是提早提示，後端仍會再驗一次。
export const MIN_PASSWORD = 10

// 帳號規則與後端 app/services/accounts.py 的 `_USERNAME_RE`（`^[\w.@-]{2,64}$`）相同：文字、數字與 . _ @ -。
const USERNAME_RE = /^[\p{L}\p{N}._@-]{2,64}$/u

/** 帳號名稱不合規則時的訊息（文案同後端）；合規回 null。傳入的是已去頭尾空白的值。 */
export function usernameProblem(username: string): string | null {
  return USERNAME_RE.test(username) ? null : '帳號需為 2–64 個字元，只能包含文字、數字與 . _ @ -'
}

export function passwordProblem(password: string): string | null {
  return password.length < MIN_PASSWORD ? `密碼至少 ${MIN_PASSWORD} 個字元` : null
}

export type ConfirmKind = 'disable' | 'demote' | 'logout' | 'delete' | 'resetTotp'

export const CONFIRM_TEXT: Record<ConfirmKind, (u: AdminUser) => { title: string; body: string; label: string }> = {
  disable: u => ({
    title: `停用「${u.username}」？`,
    body: '停用後立即生效：該帳號所有裝置會在下一個動作時被登出，也無法再登入。可以隨時重新啟用（需重新登入）。',
    label: '停用',
  }),
  demote: u => ({
    title: `拿掉「${u.username}」的管理員權限？`,
    body: '改為一般使用者後，對方立即失去帳號管理與待複核的權限。',
    label: '改為一般使用者',
  }),
  logout: u => ({
    title: `強制登出「${u.username}」？`,
    body: '該帳號所有已登入的裝置都會被登出，需要重新輸入密碼。帳號本身不受影響。',
    label: '強制登出',
  }),
  delete: u => ({
    title: `刪除「${u.username}」？`,
    body: '帳號會立即停用並登出所有裝置，24 小時後永久刪除該帳號的問答紀錄、對話與回饋，帳號名稱與登入資料一併清除（操作紀錄保留）。24 小時內可以取消。',
    label: '排程刪除',
  }),
  resetTotp: u => ({
    title: `重設「${u.username}」的兩步驟驗證？`,
    body: '給遺失驗證器的人用：關閉後對方只用密碼就能登入，之後可自行在「帳號安全」重新開啟。',
    label: '重設兩步驟驗證',
  }),
}

export const BULK_ACTIONS = ['disable', 'enable', 'logout'] as const satisfies readonly BulkUserAction[]

export const BULK_TEXT: Record<BulkUserAction, { verb: string; body: string }> = {
  disable: { verb: '停用', body: '停用後立即生效：這些帳號所有裝置都會被登出，也無法再登入。' },
  enable: { verb: '啟用', body: '啟用後可以重新登入（先前被撤銷的 session 不會復活）。排程刪除中的帳號會被略過。' },
  logout: { verb: '強制登出', body: '這些帳號所有已登入的裝置都會被登出，帳號本身不受影響。' },
}
