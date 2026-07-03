import { Link } from 'react-router'
import { Icon } from '../primitives/Icon'
import { NavItem } from './NavItem'
import { ConversationList } from './ConversationList'
import { AccountMenu } from './AccountMenu'
import styles from './SideRail.module.css'

interface SideRailProps {
  collapsed: boolean
  onToggle: () => void
}

export function SideRail({ collapsed, onToggle }: SideRailProps) {
  if (collapsed) {
    return (
      <div className={styles.mini}>
        <Link to="/search" title="廷豐智能研報" className={styles.glyph}>廷</Link>
        <button type="button" onClick={onToggle} title="展開側欄" className={styles.toggleMini}>
          <Icon name="panel" size={17} />
        </button>
        <nav className={styles.miniNav}>
          <NavItem to="/search" icon="search" label="檢索" variant="mini" />
          <NavItem to="/ask" icon="messages" label="問答" variant="mini" />
          <NavItem to="/monitor" icon="activity" label="監控" variant="mini" />
        </nav>
        <div className={styles.spacer} />
        <AccountMenu variant="mini" />
      </div>
    )
  }
  return (
    <div className={styles.full}>
      <div className={styles.header}>
        <span className={styles.glyphSmall}>廷</span>
        <span className={styles.title}>廷豐智能研報</span>
        <button type="button" onClick={onToggle} title="收合側欄" className={styles.toggleFull}>
          <Icon name="panel" size={17} />
        </button>
      </div>
      <nav className={styles.fullNav}>
        <NavItem to="/search" icon="search" label="檢索" variant="row" />
        <NavItem to="/ask" icon="messages" label="問答" variant="row" />
        <NavItem to="/monitor" icon="activity" label="監控" variant="row" />
      </nav>
      <div className={styles.divider} />
      <ConversationList />
      <AccountMenu variant="row" />
    </div>
  )
}
