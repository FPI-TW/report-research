import type { Transition, Variants } from 'motion/react'

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
