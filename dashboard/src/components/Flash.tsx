import { useRef, type ReactNode } from 'react'

/**
 * Flashes its content whenever `value` changes: green if a number went up,
 * red if it went down, amber for any other change. Changing the span's key
 * remounts it, which restarts the CSS animation even on rapid changes.
 */
export function Flash({ value, children }: { value: unknown; children?: ReactNode }) {
  const prev = useRef(value)
  const flash = useRef({ n: 0, dir: '' })
  if (!Object.is(prev.current, value)) {
    const before = prev.current
    const dir =
      typeof value === 'number' && typeof before === 'number'
        ? value > before
          ? 'up'
          : 'down'
        : 'chg'
    flash.current = { n: flash.current.n + 1, dir }
    prev.current = value
  }
  const { n, dir } = flash.current
  return (
    <span key={n} className={n ? `fl fl-${dir}` : undefined}>
      {children ?? String(value ?? '—')}
    </span>
  )
}
