import { Icon } from '../primitives/Icon'
import { BrandLogo } from '../BrandLogo'
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
        {/* 品牌標記與展開鈕同格：預設顯示 logo，hover 換成展開圖示，點擊展開（仿 ChatGPT） */}
        <button type="button" onClick={onToggle} title="展開側欄" aria-label="展開側欄" className={styles.brandToggle}>
          <BrandLogo size={30} className={styles.brandLogo} alt="" />
          <Icon name="panel" size={18} className={styles.brandExpand} />
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
        <BrandLogo size={26} className={styles.glyphSmall} />
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
