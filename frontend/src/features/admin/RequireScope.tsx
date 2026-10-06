import type { ReactNode } from 'react'
import { useMe } from '../../lib/useMe'
import styles from './Admin.module.css'

/**
 * 管理頁內的 scope 顯示層守門（外層已有 `RequireAdmin`）：沒有該 scope 時不渲染 children、
 * 不送出任何請求，只說明需要哪個權限。**這不是授權**——擋人的是後端 `authz.require_scope`，
 * 這裡只是讓沒有權限的管理員看到一句話而不是一頁 403。
 */
export function RequireScope({ scope, title, children }: { scope: string; title: string; children: ReactNode }) {
  const me = useMe()
  if (me.isPending) return <p className={styles.idle}>確認權限中…</p>
  if (!me.data?.scopes?.includes(scope)) {
    return (
      <div className={styles.page}>
        <div className={styles.inner}>
          <section className={styles.card} role="alert" aria-labelledby="scope-required-title">
            <h1 id="scope-required-title" className={styles.ctitle}>需要「{title}」權限</h1>
            <p className={styles.hint}>
              這一頁需要 <code>{scope}</code> 權限。管理員預設都有；若你的帳號被收回，請聯絡 super admin。
            </p>
          </section>
        </div>
      </div>
    )
  }
  return <>{children}</>
}
