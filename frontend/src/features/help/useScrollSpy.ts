import { useCallback, useEffect, useRef, useState, type RefObject } from 'react'

/** 節標題捲到距頂端這麼近，就算「讀到這一節」。 */
const SPY_OFFSET = 80
/** 點目錄跳過去時，節標題離捲動區頂端留的空間。 */
const JUMP_GAP = 24
/** 平滑捲動期間不跟著捲動更新目錄（否則選取底色會一路滑過中間各節）；`scrollend` 先到就提早解除。 */
const JUMP_LOCK_MS = 1000

/**
 * 節在捲動區內容裡的位置。用 offsetTop 累加而不是 getBoundingClientRect：各節的捲動進場
 * 帶 translateY，還沒進場的節量到的位置會多出位移，跳過去會停在標題下方。
 */
function offsetWithin(node: HTMLElement, container: HTMLElement): number {
  let y = 0
  let n: HTMLElement | null = node
  while (n && n !== container) {
    y += n.offsetTop
    n = n.offsetParent as HTMLElement | null
  }
  if (n === container) return y
  return node.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop
}

function prefersReducedMotion(): boolean {
  return typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

/**
 * 使用說明的目錄 scroll-spy：捲動時找出目前讀到的節，點目錄時只捲這個容器的 scrollTop
 * （不用 scrollIntoView：它會連外層一起捲，交接包「實測抓到的坑」第 1 條）。
 * 捲到底時一律算最後一節，最後幾節矮到捲不上頂端也點得亮。
 */
export function useScrollSpy<T extends string>(
  ids: readonly T[],
  domId: (id: T) => string,
  scrollRef: RefObject<HTMLElement | null>,
) {
  const [active, setActive] = useState<T>(ids[0])
  const locked = useRef(false)
  const releaseRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    let raf = 0
    const compute = () => {
      raf = 0
      if (locked.current) return
      let cur = ids[0]
      for (const id of ids) {
        const sec = document.getElementById(domId(id))
        if (sec && offsetWithin(sec, el) - SPY_OFFSET <= el.scrollTop) cur = id
      }
      if (el.scrollHeight - el.scrollTop - el.clientHeight < 4) cur = ids[ids.length - 1]
      setActive(cur)
    }
    const onScroll = () => {
      if (!raf) raf = requestAnimationFrame(compute)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => {
      el.removeEventListener('scroll', onScroll)
      cancelAnimationFrame(raf)
    }
  }, [ids, domId, scrollRef])

  useEffect(() => () => releaseRef.current?.(), [])

  const jump = useCallback((id: T) => {
    const el = scrollRef.current
    const sec = document.getElementById(domId(id))
    setActive(id)
    if (!el || !sec) return

    releaseRef.current?.()
    locked.current = true
    const release = () => {
      locked.current = false
      clearTimeout(timer)
      el.removeEventListener('scrollend', release)
      releaseRef.current = null
    }
    releaseRef.current = release
    el.addEventListener('scrollend', release)
    const timer = setTimeout(release, JUMP_LOCK_MS)

    const top = Math.max(0, offsetWithin(sec, el) - JUMP_GAP)
    if (typeof el.scrollTo === 'function') el.scrollTo({ top, behavior: prefersReducedMotion() ? 'auto' : 'smooth' })
    else el.scrollTop = top
    // 焦點移到該節標題（鍵盤使用者接著 Tab 就在這一節裡）；preventScroll 免得瀏覽器再捲一次。
    sec.querySelector<HTMLElement>('h2')?.focus({ preventScroll: true })
  }, [domId, scrollRef])

  return { active, jump }
}
