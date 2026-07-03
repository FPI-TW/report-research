import { useEffect, type RefObject } from 'react'

export function useClickOutside(
  ref: RefObject<HTMLElement | null>,
  onOutside: () => void,
): void {
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      const el = ref.current
      if (el && !el.contains(e.target as Node)) onOutside()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [ref, onOutside])
}
