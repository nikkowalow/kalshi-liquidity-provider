import { useEffect, useRef, useState } from 'react'
import { apiBase, apiUrl, switchBot } from '../lib/api'

interface Bot {
  run_id: string
  url: string
  online: boolean | null // null: still checking
}

async function listBots(): Promise<Bot[]> {
  const known = new Map<string, Bot>()
  // The bot this page shows lists every bot registered in its runs folder.
  try {
    const res = await fetch(apiUrl('/api/bots'))
    if (res.ok) {
      for (const b of (await res.json()) as { run_id: string; url: string }[]) {
        known.set(b.url, { run_id: b.run_id, url: b.url, online: null })
      }
    }
  } catch {
    // that bot is down: still offer the one that served this page
  }
  if (!known.has(location.origin)) known.set(location.origin, { run_id: 'this page’s bot', url: location.origin, online: null })
  const bots = [...known.values()]
  await Promise.all(
    bots.map(async (b) => {
      try {
        b.online = (await fetch(`${b.url}/api/health`, { signal: AbortSignal.timeout(1500) })).ok
      } catch {
        b.online = false
      }
    }),
  )
  return bots.sort((a, b) => a.run_id.localeCompare(b.run_id))
}

/**
 * The run name in the top bar. Click: list the bots running here (prod, demo, ...) and show
 * another one's data. Only this page switches; every bot keeps running.
 */
export function BotPicker({ runId }: { runId: string | null }) {
  const [open, setOpen] = useState(false)
  const [bots, setBots] = useState<Bot[] | null>(null)
  const box = useRef<HTMLSpanElement>(null)
  const current = apiBase() || location.origin

  useEffect(() => {
    if (!open) return
    let alive = true
    void listBots().then((b) => alive && setBots(b))
    const away = (e: MouseEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false)
    }
    window.addEventListener('mousedown', away)
    return () => {
      alive = false
      window.removeEventListener('mousedown', away)
    }
  }, [open])

  return (
    <span className="bot-picker" ref={box}>
      <button type="button" className="bot-current" data-help="bar:run" onClick={() => setOpen((o) => !o)}>
        {runId ?? '—'} ▾
      </button>
      {open && (
        <span className="bot-menu" role="menu">
          {bots === null ? (
            <span className="bot-item dim">looking for bots…</span>
          ) : (
            bots.map((b) => {
              const here = b.url === current
              return (
                <button
                  key={b.url}
                  type="button"
                  role="menuitem"
                  className={`bot-item${here ? ' on' : ''}`}
                  disabled={here || b.online === false}
                  onClick={() => switchBot(b.url)}
                >
                  <span className={b.online ? 'pos' : 'neg'}>●</span> {b.run_id}
                  <span className="dim"> {b.url.replace(/^https?:\/\//, '')}</span>
                  {here && <span className="dim"> · showing</span>}
                  {b.online === false && <span className="dim"> · offline</span>}
                </button>
              )
            })
          )}
          <span className="bot-item dim">start another: klp run -c config/demo.yaml --live</span>
        </span>
      )}
    </span>
  )
}
