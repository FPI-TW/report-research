import { Icon } from '../primitives/Icon'
import styles from './RequireAdmin.module.css'

/**
 * 管理員 TOTP 強制（後端 `ADMIN_MFA_REQUIRED`）對 SSO 登入的管理員：這次公司帳號登入時 IdP 沒有表明做過多重驗證，
 * 每一支管理 API 都會回 403 `sso_mfa_required`。本系統的 TOTP 不算數（SSO 登入不檢查它），所以不顯示 TOTP 設定，
 * 改請對方以經過多重驗證的公司帳號重新登入（判準在後端 `app/services/oidc.py` 的 `is_mfa`）。
 * 這只是顯示層：擋人的是後端 `web/authz.py` 的 `require_admin`。
 */
export function AdminSsoMfaRequired() {
  const relogin = () => {
    window.location.assign('/login/oidc?next=' + encodeURIComponent(window.location.pathname + window.location.search))
  }
  return (
    <div className={styles.wrap}>
      <div className={styles.card} role="alert">
        <Icon name="shield" size={28} />
        <h1 className={styles.title}>需要經過多重驗證的公司帳號登入</h1>
        <p className={styles.body}>
          管理後台要求管理員以多重驗證登入。這次的公司帳號登入沒有經過多重驗證，帳號管理、待複核、維運等管理功能都無法使用；
          研報平台的一般功能不受影響。
        </p>
        <p className={styles.body}>重新登入時請完成公司帳號的多重驗證；若登入畫面沒有要求，請洽資訊單位確認公司帳號的多重驗證設定。</p>
        <button type="button" className={styles.action} onClick={relogin}>以公司帳號重新登入</button>
      </div>
    </div>
  )
}
