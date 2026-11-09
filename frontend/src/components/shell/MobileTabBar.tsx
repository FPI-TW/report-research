import { NavItem } from './NavItem'
import { AccountMenu } from './AccountMenu'
import styles from './MobileTabBar.module.css'
import { t } from '@/i18n'

export function MobileTabBar() {
  return (
    <div className={styles.bar}>
      <NavItem to="/library" icon="search" label={t('shell.mobileTabBar.library')} />
      <NavItem to="/assistant" icon="messages" label={t('shell.mobileTabBar.assistant')} />
      <NavItem to="/consensus" icon="compass" label={t('shell.mobileTabBar.consensus')} />
      <NavItem to="/brief" icon="fileText" label={t('shell.mobileTabBar.dailyBrief')} />
      <div className={styles.account}><AccountMenu variant="mobile" /></div>
    </div>
  )
}
