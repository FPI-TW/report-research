import type { Transition, Variants } from 'motion/react'

/** 收束過衝 spring（完成勾/圖示落定用；對齊 --tf-ease-emphasized 的手感）。 */
export const springPop: Transition = { type: 'spring', stiffness: 480, damping: 26 }

/**
 * JS 端動效 token 單一真相，對齊 styles/tokens.css 的 --tf-ease-out / --tf-dur-* / --tf-stagger。
 * 秒為單位（Motion 慣例）；tokens.css 為 ms，兩邊需一致更動。
 */
export const TF_EASE_OUT: [number, number, number, number] = [0.22, 1, 0.36, 1]

export const TF_DUR = {
  d1: 0.12,
  d2: 0.18,
  d3: 0.24,
  d4: 0.34,
} as const

/** 清單進場的每項延遲與封頂項數（＝--tf-stagger / --tf-stagger-cap）。 */
export const TF_STAGGER = 0.04
export const TF_STAGGER_CAP = 8

/** 全域預設 transition：--tf-dur-3 + --tf-ease-out。 */
export const tfTransition: Transition = { duration: TF_DUR.d3, ease: TF_EASE_OUT }

/** reduced-motion 下的即時 transition：直接呈現終態、無位移（複刻舊 hook 契約與測試 determinism）。 */
export const tfInstant: Transition = { duration: 0 }

/** 進場淡入＋上浮（原 .tf-reveal / @keyframes tf-up 的 Motion 版）。 */
export const revealVariants: Variants = {
  hidden: { opacity: 0, y: 6 },
  visible: { opacity: 1, y: 0 },
}

/** 進場 transition：reduced 走即時；否則 --tf-dur-3 + --tf-ease-out，delay 依 index 封頂 stagger。 */
export function revealTransition(reduced: boolean | null, index = 0): Transition {
  if (reduced) return tfInstant
  return {
    duration: TF_DUR.d3,
    ease: TF_EASE_OUT,
    delay: Math.min(index, TF_STAGGER_CAP) * TF_STAGGER,
  }
}

/** 進場變體：up＝淡入上浮（預設）、fade＝純淡入（密集文字/表格）、scale＝淡入微縮放。 */
export type RevealVariant = 'up' | 'fade' | 'scale'
export function revealVariantsFor(variant: RevealVariant): Variants {
  if (variant === 'fade') return { hidden: { opacity: 0 }, visible: { opacity: 1 } }
  if (variant === 'scale') return { hidden: { opacity: 0, scale: 0.98 }, visible: { opacity: 1, scale: 1 } }
  return revealVariants
}

/**
 * 全站互動手勢 prop bundle（直接 spread 到 motion 元件，統一 hover/press 手感）。
 * scale/y 皆位移類，MotionConfig reducedMotion="user" 會於減少動態時自動停用，免逐一 guard。
 */
export const tapPress = { whileTap: { scale: 0.96 } } as const
/** 較含蓄的按壓（寬版/連結型控制）。 */
export const tapPressSoft = { whileTap: { scale: 0.97 } } as const
/** 卡片/浮起型：hover 上抬、press 微縮。 */
export const hoverLift = { whileHover: { y: -2 }, whileTap: { scale: 0.98 } } as const

/** stagger 容器：父層 visible 時依 staggerChildren 逐一觸發子項 revealVariants。 */
export const staggerContainer: Variants = {
  hidden: {},
  visible: { transition: { staggerChildren: TF_STAGGER } },
}
