import { useEffect, useRef, useState } from 'react'
import { helpFor, type HelpEntry } from '../lib/glossary'

/**
 * One floating explanation box for the whole page. Any element with
 * data-help="<key>" shows the glossary entry for that key on hover.
 * Delegated listeners mean tables can re-render every second without
 * losing the tooltip.
 */
export function HelpLayer() {
  const [entry, setEntry] = useState<HelpEntry | null>(null)
  const box = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let key: string | undefined
    let frame = 0
    let mouse = { x: 0, y: 0 }
    const place = () => {
      frame = 0
      const el = box.current
      if (!el || key === undefined) return // hidden: don't touch layout at all
      const pad = 14
      let x = mouse.x + pad
      let y = mouse.y + pad
      if (x + el.offsetWidth > window.innerWidth - 4) x = mouse.x - el.offsetWidth - pad
      if (y + el.offsetHeight > window.innerHeight - 4) y = mouse.y - el.offsetHeight - pad
      el.style.left = `${Math.max(4, x)}px`
      el.style.top = `${Math.max(4, y)}px`
    }
    const over = (ev: MouseEvent) => {
      const next = (ev.target as Element | null)?.closest<HTMLElement>('[data-help]')?.dataset.help
      if (next === key) return // same label: nothing to update
      key = next
      setEntry(next ? helpFor(next) : null)
    }
    const move = (ev: MouseEvent) => {
      mouse = { x: ev.clientX, y: ev.clientY }
      if (key !== undefined && !frame) frame = requestAnimationFrame(place) // once per frame
    }
    const leave = () => {
      key = undefined
      setEntry(null)
    }
    document.addEventListener('mouseover', over, { passive: true })
    document.addEventListener('mousemove', move, { passive: true })
    document.documentElement.addEventListener('mouseleave', leave)
    return () => {
      document.removeEventListener('mouseover', over)
      document.removeEventListener('mousemove', move)
      document.documentElement.removeEventListener('mouseleave', leave)
      if (frame) cancelAnimationFrame(frame)
    }
  }, [])

  return (
    <div id="help" ref={box} style={{ display: entry ? 'block' : 'none' }}>
      {entry && (
        <>
          <b>{entry[0]}</b>
          {entry[1]}
          {entry[2] && <i>{entry[2]}</i>}
        </>
      )}
    </div>
  )
}
