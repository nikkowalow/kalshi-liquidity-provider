import { useEffect, useState } from 'react'

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

/** Market ticker that copies itself to the clipboard on click. */
export function Ticker({ value, help }: { value: string; help?: string }) {
  const [state, setState] = useState<'idle' | 'ok' | 'fail'>('idle')
  useEffect(() => {
    if (state === 'idle') return
    const t = setTimeout(() => setState('idle'), 1200)
    return () => clearTimeout(t)
  }, [state])

  return (
    <span
      className={`ticker${state === 'ok' ? ' copied' : ''}`}
      data-help={help}
      title="click to copy"
      role="button"
      tabIndex={0}
      onClick={async () => setState((await copy(value)) ? 'ok' : 'fail')}
      onKeyDown={async (e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          setState((await copy(value)) ? 'ok' : 'fail')
        }
      }}
    >
      {value}
      {state !== 'idle' && <span className="copy-note">{state === 'ok' ? ' ✓ copied' : ' copy failed'}</span>}
    </span>
  )
}
