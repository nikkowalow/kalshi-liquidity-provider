import { useEffect, useState, type RefObject } from 'react'

export const ROW_HEIGHT = 18 // px; tables using this pin their rows to it (see .vt in index.css)
const OVERSCAN = 12

export interface VirtualWindow {
  start: number // first row index to render
  end: number // one past the last
  padTop: number // px of spacer above the rendered rows
  padBottom: number // px below
}

/**
 * Render only the rows scrolled into view (plus a margin) of a long table.
 *
 * The blotter and log can hold thousands of rows, each with a live
 * timestamp; drawing just the visible ~30 keeps every update cheap.
 * Spacer rows keep the scrollbar the size of the whole table.
 */
export function useVirtualRows(scroller: RefObject<HTMLElement | null>, count: number): VirtualWindow {
  const [view, setView] = useState({ top: 0, height: 600 })
  useEffect(() => {
    const el = scroller.current
    if (!el) return
    let frame = 0
    const measure = () => {
      frame = 0
      setView((v) =>
        v.top === el.scrollTop && v.height === el.clientHeight
          ? v
          : { top: el.scrollTop, height: el.clientHeight },
      )
    }
    const onScroll = () => {
      if (!frame) frame = requestAnimationFrame(measure)
    }
    measure()
    el.addEventListener('scroll', onScroll, { passive: true })
    const observer = new ResizeObserver(onScroll)
    observer.observe(el)
    return () => {
      el.removeEventListener('scroll', onScroll)
      observer.disconnect()
      if (frame) cancelAnimationFrame(frame)
    }
  }, [scroller])
  const start = Math.max(0, Math.floor(view.top / ROW_HEIGHT) - OVERSCAN)
  const end = Math.min(count, Math.ceil((view.top + view.height) / ROW_HEIGHT) + OVERSCAN)
  return {
    start,
    end,
    padTop: start * ROW_HEIGHT,
    padBottom: Math.max(0, (count - end) * ROW_HEIGHT),
  }
}
