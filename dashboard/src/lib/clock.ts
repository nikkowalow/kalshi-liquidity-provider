import { useSyncExternalStore } from 'react'

/** One shared 1-second clock for every "12s ago" on the page.
 *
 * Components that call useNow() re-render once a second; nothing else does,
 * so a table with hundreds of timestamps doesn't re-render its rows.
 */
let now = Date.now()
const listeners = new Set<() => void>()
let timer: ReturnType<typeof setInterval> | undefined

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (timer === undefined) {
    timer = setInterval(() => {
      now = Date.now()
      for (const l of listeners) l()
    }, 1000)
  }
  return () => {
    listeners.delete(listener)
    if (!listeners.size && timer !== undefined) {
      clearInterval(timer)
      timer = undefined
    }
  }
}

/** Current time in ms, updated every second. */
export function useNow(): number {
  return useSyncExternalStore(subscribe, () => now)
}

/** "12s", "4m 05s", "2h 07m", "3d 4h" */
export function span(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${String(s % 60).padStart(2, '0')}s`
  if (s < 86_400) return `${Math.floor(s / 3600)}h ${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}m`
  return `${Math.floor(s / 86_400)}d ${Math.floor((s % 86_400) / 3600)}h`
}

/** "12s ago" for the past, "in 3h 05m" for the future. ``ts`` in unix seconds. */
export function relative(ts: number, nowMs: number): string {
  const diff = nowMs / 1000 - ts
  // The page clock ticks once a second, so a brand-new event can be a moment "ahead" of it.
  if (diff < 1 && diff > -5) return 'just now'
  return diff >= 0 ? `${span(diff)} ago` : `in ${span(-diff)}`
}
