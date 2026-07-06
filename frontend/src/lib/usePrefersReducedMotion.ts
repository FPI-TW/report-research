import { useMediaQuery } from './useMediaQuery'

/** 全站 JS 動效的單一 reduced-motion 來源；CSS 動效由 tokens.css 的全域 blanket 歸零。 */
export function usePrefersReducedMotion(): boolean {
  return useMediaQuery('(prefers-reduced-motion: reduce)')
}
