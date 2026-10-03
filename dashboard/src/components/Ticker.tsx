import { useEffect, useState } from 'react'
import { useOpenMarket } from '../lib/openMarket'

async function copy(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    // Clipboard API needs a secure context; fall back for plain-http LAN access.
    const el = document.createElement('textarea')
    el.value = text
    el.style.position = 'fixed'
    el.style.opacity = '0'
    document.body.appendChild(el)
    el.select()
    const ok = document.execCommand('copy')
    el.remove()
    return ok
  }
}

/**
 * A market ticker. Click: open the market's details (the same popup as the markets table).
 * ⌘/Ctrl/Alt-click: copy it to the clipboard.
 */
export function Ticker({ value, help }: { value: string; help?: string }) {
  const open = useOpenMarket()
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle')
  useEffect(() => {
    if (state === 'idle') return
    const t = setTimeout(() => setState('idle'), 1200)
    return () => clearTimeout(t)
  }, [state])

  const act = async (e: { metaKey: boolean; ctrlKey: boolean; altKey: boolean; stopPropagation: () => void }) => {
    e.stopPropagation() // a click inside a clickable row opens this, not the row's own action
    if (open && !(e.metaKey || e.ctrlKey || e.altKey)) open(value)
    else setState((await copy(value)) ? 'ok' : 'fail')
  }

  return (
    <span
      className={`ticker${state === 'ok' ? ' copied' : ''}`}
      data-help={help}
      title={open ? 'click for details · ⌘/ctrl-click to copy' : 'click to copy'}
      role="button"
      tabIndex={0}
      onClick={(e) => void act(e)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          void act(e)
        }
      }}
    >
      {value}
      {state !== 'idle' && <span className="copy-note">{state === 'ok' ? ' ✓ copied' : ' copy failed'}</span>}
    </span>
  )
}
