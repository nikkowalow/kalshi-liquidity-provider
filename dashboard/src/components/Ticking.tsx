import { useLayoutEffect, useRef } from 'react'

/**
 * A dollar amount that keeps counting between updates, at the current rate.
 *
 * Rewards accrue every second, but the dashboard hears about them once a
 * second; this extrapolates ``value + rate x elapsed`` (at most a few seconds
 * past the last update) so the digits run continuously. It never counts
 * backwards: a new, slightly lower value waits until the counter catches up.
 *
 * The animation writes the text node directly (no React re-render per frame).
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
  const el = useRef<HTMLSpanElement>(null)
  const floor = useRef(value ?? 0)
  useLayoutEffect(() => {
    if (value === null || at === null) return
    const rate = Math.max(perHour ?? 0, 0) / 3600
    // Below the counter by more than a few seconds' overshoot: a real change (another run,
    // the multiplier switched off), not extrapolation jitter. Start from the new value.
    if (value < floor.current - Math.max(rate * 10, 1e-6)) floor.current = value
    const draw = () => {
      const elapsed = Math.min(Math.max(Date.now() / 1000 - at, 0), 3)
      const next = Math.max(value + rate * elapsed, floor.current)
      floor.current = next
      if (el.current) el.current.textContent = prefix + next.toFixed(digits)
    }
    draw() // before paint: React never renders the number itself, so it can't flicker
    let frame = 0
    let last = 0
    const tick = (ms: number) => {
      frame = requestAnimationFrame(tick)
      if (ms - last < 60) return // ~16 fps is plenty for spinning digits
      last = ms
      draw()
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [value, perHour, at, digits, prefix])
  if (value === null) return <>—</>
  return <span className="ticking" ref={el} />
}
