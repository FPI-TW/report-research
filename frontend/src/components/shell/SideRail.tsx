import { motion } from 'motion/react'
import { Icon } from '../primitives/Icon'
import { Pressable } from '../primitives/Pressable'
import { BrandLogo } from '../BrandLogo'
import { NavItem } from './NavItem'
import { ConversationList } from './ConversationList'
import { AccountMenu } from './AccountMenu'
import { springHover } from '../../lib/motionTokens'
import styles from './SideRail.module.css'

// 收合態品牌標記：hover 時 logo 淡出縮小、展開圖示淡入放大（原 CSS crossfade 改由 Motion variants 驅動）
const brandLogoVariants = { rest: { opacity: 1, scale: 1 }, hover: { opacity: 0, scale: 0.6 } }
const brandExpandVariants = { rest: { opacity: 0, scale: 0.6 }, hover: { opacity: 1, scale: 1 } }

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
          <Pressable onClick={onToggle} title="收合側欄" aria-label="收合側欄" className={styles.toggleFull} hoverScale={1.1}>
            <Icon name="panel" size={22} />
          </Pressable>
        </div>
        <nav className={styles.fullNav}>
          <NavItem to="/search" icon="search" label="檢索" variant="row" />
          <NavItem to="/ask" icon="messages" label="問答" variant="row" />
          <NavItem to="/radar" icon="compass" label="觀點" variant="row" />
          <NavItem to="/brief" icon="fileText" label="簡報" variant="row" />
          <NavItem to="/monitor" icon="activity" label="監控" variant="row" />
        </nav>
        <div className={styles.divider} />
        <ConversationList />
        <AccountMenu variant="row" />
      </div>
      <div className={styles.mini} aria-hidden={!collapsed} inert={!collapsed}>
        {/* 品牌標記與展開鈕同格：預設顯示 logo，hover crossfade 成展開圖示，點擊展開（仿 ChatGPT）。
            crossfade 由 Motion variants 驅動（rest↔hover），置中偏移交給 Motion x/y。 */}
        <motion.button
          type="button"
          onClick={onToggle}
          title="展開側欄"
          aria-label="展開側欄"
          className={styles.brandToggle}
          initial="rest"
          animate="rest"
          whileHover="hover"
          whileTap={{ scale: 0.94 }}
          transition={springHover}
        >
          <motion.span className={styles.brandLogo} style={{ x: '-50%', y: '-50%' }} variants={brandLogoVariants}>
            <BrandLogo size={30} alt="" />
          </motion.span>
          <motion.span className={styles.brandExpand} style={{ x: '-50%', y: '-50%' }} variants={brandExpandVariants}>
            <Icon name="panel" size={22} />
          </motion.span>
        </motion.button>
        <nav className={styles.miniNav}>
          <NavItem to="/search" icon="search" label="檢索" variant="mini" />
          <NavItem to="/ask" icon="messages" label="問答" variant="mini" />
          <NavItem to="/radar" icon="compass" label="觀點" variant="mini" />
          <NavItem to="/brief" icon="fileText" label="簡報" variant="mini" />
          <NavItem to="/monitor" icon="activity" label="監控" variant="mini" />
        </nav>
        <div className={styles.spacer} />
        <AccountMenu variant="mini" />
      </div>
    </div>
  )
}
