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

/**
 * 左側欄：迷你（60）↔ 完整（272）兩態疊放於同一容器，容器寬度過渡＋兩層淡入淡出交叉切換，
 * 收合／展開因此有平滑動畫（而非兩棵 DOM 直接抽換造成的瞬跳）。
 * 非當前態的層 aria-hidden＋inert：移出無障礙樹與 tab 序、且不可互動。
 */
export function SideRail({ collapsed, onToggle }: SideRailProps) {
  return (
    <div className={styles.rail} data-collapsed={collapsed}>
      <div className={styles.full} aria-hidden={collapsed} inert={collapsed}>
        {/* 品牌 logo 置左（與收合態 logo 同座標），收合鈕置右上 */}
        <div className={styles.header}>
          <span className={styles.glyphSmall}><BrandLogo size={30} alt="" /></span>
          <span className={styles.title}>廷豐智能研報</span>
          <button type="button" onClick={onToggle} title="收合側欄" aria-label="收合側欄" className={styles.toggleFull}>
            <Icon name="panel" size={22} />
          </button>
        </div>
        <nav className={styles.fullNav}>
          <NavItem to="/search" icon="search" label="檢索" variant="row" />
          <NavItem to="/ask" icon="messages" label="問答" variant="row" />
          <NavItem to="/radar" icon="compass" label="觀點雷達" variant="row" />
          <NavItem to="/monitor" icon="activity" label="監控" variant="row" />
        </nav>
        <div className={styles.divider} />
        <ConversationList />
        <AccountMenu variant="row" />
      </div>
      <div className={styles.mini} aria-hidden={!collapsed} inert={!collapsed}>
        {/* 品牌標記與展開鈕同格：預設顯示 logo，hover 換成展開圖示，點擊展開（仿 ChatGPT） */}
        <button type="button" onClick={onToggle} title="展開側欄" aria-label="展開側欄" className={styles.brandToggle}>
          <BrandLogo size={30} className={styles.brandLogo} alt="" />
          <Icon name="panel" size={22} className={styles.brandExpand} />
        </button>
        <nav className={styles.miniNav}>
          <NavItem to="/search" icon="search" label="檢索" variant="mini" />
          <NavItem to="/ask" icon="messages" label="問答" variant="mini" />
          <NavItem to="/radar" icon="compass" label="觀點雷達" variant="mini" />
          <NavItem to="/monitor" icon="activity" label="監控" variant="mini" />
        </nav>
        <div className={styles.spacer} />
        <AccountMenu variant="mini" />
      </div>
    </div>
  )
}
