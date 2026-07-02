import { NavLink } from 'react-router'
import { AccountMenu } from './AccountMenu'
import { NAV_LINKS } from './navLinks'
import classes from './MobileTabBar.module.css'

/** 手機底部分頁列：三導覽格 + 第 4 格帳號（點開登出選單）。 */
export function MobileTabBar() {
  return (
    <div className={classes.bar}>
      <nav aria-label="主導覽" className={classes.nav}>
        {NAV_LINKS.map(({ to, label, Icon }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              isActive ? `${classes.item} ${classes.active}` : classes.item
            }
          >
            <Icon size={20} stroke={1.8} aria-hidden />
            <span>{label}</span>
          </NavLink>
        ))}
      </nav>
      <div className={classes.account}>
        <AccountMenu position="top-end" label="帳號" />
      </div>
    </div>
  )
}
