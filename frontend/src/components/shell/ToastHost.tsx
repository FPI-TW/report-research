import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { Check } from 'lucide-react'
import { useToast } from '../../lib/toast'
import styles from './ToastHost.module.css'

/**
 * 提示訊息的落點：底部置中的石墨膠囊。
 * 置中用 left/right＋margin auto，不用 translateX(-50%)——進出場動畫的 transform 會把它蓋掉，
 * 膠囊就整個往右偏（交接包「實測抓到的坑」第 2 條）。
 */
export function ToastHost() {
  const toast = useToast()
  const reduced = useReducedMotion()
  return (
    <div className={styles.host} role="status" aria-live="polite">
      <AnimatePresence>
        {toast && (
          <motion.div
            key={toast.id}
            className={styles.toast}
            initial={reduced ? { opacity: 0 } : { opacity: 0, y: 16, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={reduced ? { opacity: 0 } : { opacity: 0, y: 6, scale: 0.98, transition: { duration: 0.26, ease: [0.4, 0, 0.2, 1] } }}
            transition={{ duration: 0.45, ease: [0.32, 0.72, 0, 1] }}
          >
            {toast.ok && <Check size={14} strokeWidth={2.6} aria-hidden className={styles.ok} />}
            {toast.message}
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  )
}
