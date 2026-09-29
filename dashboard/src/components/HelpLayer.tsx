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
    const over = (ev: MouseEvent) => {
      const el = (ev.target as Element | null)?.closest<HTMLElement>('[data-help]')
      setEntry(el?.dataset.help ? helpFor(el.dataset.help) : null)
    }
    const move = (ev: MouseEvent) => {
      const el = box.current
      if (!el) return
      const pad = 14
      let x = ev.clientX + pad
      let y = ev.clientY + pad
      if (x + el.offsetWidth > window.innerWidth - 4) x = ev.clientX - el.offsetWidth - pad
      if (y + el.offsetHeight > window.innerHeight - 4) y = ev.clientY - el.offsetHeight - pad
      el.style.left = `${Math.max(4, x)}px`
      el.style.top = `${Math.max(4, y)}px`
    }
    const leave = () => setEntry(null)
    document.addEventListener('mouseover', over)
    document.addEventListener('mousemove', move)
    document.documentElement.addEventListener('mouseleave', leave)
    return () => {
      document.removeEventListener('mouseover', over)
      document.removeEventListener('mousemove', move)
      document.documentElement.removeEventListener('mouseleave', leave)
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
