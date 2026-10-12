import type { ReactNode } from 'react'
import { Link } from 'react-router'
import { Skeleton } from '../primitives/Skeleton'
import { Icon } from '../primitives/Icon'
import { useMe } from '../../lib/useMe'
import styles from './RequireAdmin.module.css'

/**
 * 管理頁（/admin/*）的顯示層守門。
 *
 * **這不是授權。** 它只決定要不要把管理頁渲染出來、要不要讓頁面去打 `/api/admin/*`；
 * 真正擋人的是後端 `web/authz.py` 的 `require_admin`——一般使用者就算繞過這裡（改前端、
 * 直接打 API）也只會拿到 403。這裡存在的理由是體驗：非管理員點進來看到一句清楚的
 * 「需要管理員權限」，而不是一頁打滿 403 的空表格。
 *
 * 身分還沒回來時只畫骨架、不渲染 children：children 一掛上就會開始取數，非管理員的
 * 瀏覽器不該把那些請求送出去。
 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const me = useMe()
  if (me.isPending) {
    return (
      <div className={styles.wrap} aria-busy="true" aria-label="確認權限中">
        <Skeleton width={220} height={26} />
        <Skeleton width="100%" height={160} />
      </div>
    )
  }
  if (me.data?.role !== 'admin') return <AdminForbidden />
  return <>{children}</>
}

export function AdminForbidden() {
  return (
    <div className={styles.wrap}>
      <div className={styles.card} role="alert">
        <Icon name="shield" size={28} />
        <h1 className={styles.title}>需要管理員權限</h1>
        <p className={styles.body}>這一頁只開放給管理員。若你需要帳號或審核相關的協助，請聯絡管理員。</p>
        <Link to="/search" className={styles.back}>回到檢索</Link>
      </div>
    </div>
  )
}
