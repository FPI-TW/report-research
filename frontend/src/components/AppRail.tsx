import { Link, NavLink } from 'react-router'
import { AccountMenu } from './AccountMenu'
import { NAV_LINKS } from './navLinks'
import classes from './AppRail.module.css'

/** 桌機左側窄軌：品牌 glyph + 主導覽（圖示+小字）+ 底部帳號選單。 */
export function AppRail() {
  return (
    <div className={classes.rail}>
      <Link to="/search" className={classes.brand} aria-label="廷豐智能研報">
        廷
      </Link>
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
        <AccountMenu position="right-end" />
      </div>
    </div>
  )
}
