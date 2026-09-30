import { relative, useNow } from '../lib/clock'
import { hms } from '../lib/format'

const dateTime = (ts: number) =>
  new Date(ts * 1000).toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })

/** A timestamp plus a live "12s ago" (or "in 3h 05m") that ticks every second.
 *  ``date``: show the date too (for times more than a day away). */
export function Stamp({ ts, date = false, onlyAgo = false }: { ts: number; date?: boolean; onlyAgo?: boolean }) {
  const now = useNow()
  const age = now / 1000 - ts
  const fresh = age > -5 && age < 10
  return (
    <span className={`stamp${fresh ? ' fresh' : ''}`} title={new Date(ts * 1000).toLocaleString()}>
      {!onlyAgo && <>{date ? dateTime(ts) : hms(ts)} </>}
      <span className="ago">{relative(ts, now)}</span>
    </span>
  )
}
