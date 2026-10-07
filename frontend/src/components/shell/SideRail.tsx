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
 * 左側欄（仿 ChatGPT）：圖示軌（60）常駐，展開時在它右邊多出一欄歷史對話面板（212）。
 * 兩者合計 272 ＝ 舊版完整側欄的寬度——雷達表格等處的最小寬度是照「側欄 272」量的，不可加寬。
 * 導覽與帳號只在圖示軌出現一次，面板只放站名、收合鈕與歷史對話。
 * 面板收合時寬度過渡到 0（平滑展開／收合）；aria-hidden＋inert 讓它移出無障礙樹與 tab 序。
 */
export function SideRail({ collapsed, onToggle }: SideRailProps) {
  return (
    <div className={styles.sidebar} data-collapsed={collapsed}>
      <div className={styles.rail}>
        {collapsed ? (
          // 品牌標記與展開鈕同格：預設顯示 logo，hover crossfade 成展開圖示，點擊展開。
          // crossfade 由 Motion variants 驅動（rest↔hover），置中偏移交給 Motion x/y。
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
        ) : (
          // 展開時收合鈕在面板標頭，這裡只留 logo，免得同一畫面出現兩顆收合鈕。
          <span className={styles.brandStatic}><BrandLogo size={30} alt="" /></span>
        )}
        <nav className={styles.nav}>
          <NavItem to="/search" icon="search" label="檢索" variant="mini" />
          <NavItem to="/ask" icon="messages" label="問答" variant="mini" />
          <NavItem to="/radar" icon="compass" label="觀點" variant="mini" />
          <NavItem to="/brief" icon="fileText" label="簡報" variant="mini" />
          <NavItem to="/monitor" icon="activity" label="監控" variant="mini" />
        </nav>
        <div className={styles.spacer} />
        <AccountMenu variant="mini" />
      </div>
      <div className={styles.panel} aria-hidden={collapsed} inert={collapsed}>
        <div className={styles.panelInner}>
          <div className={styles.header}>
            <span className={styles.title}>廷豐智能研報</span>
            <Pressable onClick={onToggle} title="收合側欄" aria-label="收合側欄" className={styles.toggleFull} hoverScale={1.1}>
              <Icon name="panel" size={22} />
            </Pressable>
          </div>
          <ConversationList />
        </div>
      </div>
    </div>
  )
}
