import { useState } from 'react'
import { SecurityDialog } from '../../features/account/SecurityDialog'
import { Icon } from '../primitives/Icon'
import styles from './RequireAdmin.module.css'

/**
 * 管理員 TOTP 強制（後端 `ADMIN_MFA_REQUIRED`，預設關；只有部署時設成開才會用到）：還沒開兩步驟驗證的管理員進管理後台時，
 * `AdminShell` 用這一頁取代導覽與內容——此時每一支管理 API 都會回 403 `mfa_enrollment_required`。
 *
 * 設定流程沿用帳號選單的「帳號安全」（`SecurityDialog`，打 `/api/me/totp*`，這些端點不受強制影響）；
 * 啟用成功後它會重新取 `/api/me`，`mfa_enrollment_required` 變成 false，後台就恢復正常。
 * 這只是顯示層：擋人的是後端 `web/authz.py` 的 `require_admin`。
 */
export function AdminMfaRequired() {
  const [open, setOpen] = useState(false)
  return (
    <div className={styles.wrap}>
      <div className={styles.card} role="alert">
        <Icon name="shield" size={28} />
        <h1 className={styles.title}>請先開啟兩步驟驗證</h1>
        <p className={styles.body}>
          管理後台要求所有管理員開啟兩步驟驗證（TOTP）。開啟之前，帳號管理、待複核、維運等管理功能都無法使用；
          研報平台的一般功能不受影響。
        </p>
        <p className={styles.body}>準備好手機上的驗證器 App（例如 Google Authenticator），依畫面輸入一次驗證碼即可完成。</p>
        <button type="button" className={styles.action} onClick={() => setOpen(true)}>設定兩步驟驗證</button>
      </div>
      <SecurityDialog open={open} onClose={() => setOpen(false)} />
    </div>
  )
}
