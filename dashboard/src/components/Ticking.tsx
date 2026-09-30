import { useEffect, useRef, useState } from 'react'

/**
 * A dollar amount that keeps counting between updates, at the current rate.
 *
 * Rewards accrue every second, but the dashboard hears about them once a
 * second; this extrapolates ``value + rate x elapsed`` (at most a few seconds
 * past the last update) so the digits run continuously. It never counts
 * backwards: a new, slightly lower value waits until the counter catches up.
 */
export function Ticking({
  value,
  perHour,
  at,
  digits = 5,
  prefix = '$',
}: {
  value: number | null
  perHour: number | null
  at: number | null // unix seconds of ``value``
  digits?: number
  prefix?: string
}) {
  const [shown, setShown] = useState(value)
  const floor = useRef(value ?? 0)
  useEffect(() => {
    if (value === null || at === null) return
    const rate = Math.max(perHour ?? 0, 0) / 3600
    // Below the counter by more than a few seconds' overshoot: a real change (another run,
    // the multiplier switched off), not extrapolation jitter. Start from the new value.
    if (value < floor.current - Math.max(rate * 10, 1e-6)) floor.current = value
    let frame = 0
    let last = 0
    const tick = (ms: number) => {
      frame = requestAnimationFrame(tick)
      if (ms - last < 60) return // ~16 fps is plenty for spinning digits
      last = ms
      const elapsed = Math.min(Math.max(Date.now() / 1000 - at, 0), 3)
      const next = Math.max(value + rate * elapsed, floor.current)
      floor.current = next
      setShown(next)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [value, perHour, at])
  if (value === null) return <>—</>
  const v = shown ?? value
  return (
    <span className="ticking">
      {prefix}
      {v.toFixed(digits)}
    </span>
  )
}
