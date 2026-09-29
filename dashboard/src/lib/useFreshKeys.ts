import { useRef } from 'react'

const FRESH_MS = 1600

/**
 * Which row keys appeared recently, so new rows can flash in.
 *
 * Rows present on the first render aren't "new" (no flash storm when the
 * page loads). A key stays fresh for FRESH_MS; the dashboard re-renders
 * every second, which clears the highlight.
 */
export function useFreshKeys(keys: readonly string[]): ReadonlySet<string> {
  const firstSeen = useRef<Map<string, number> | null>(null)
  const now = Date.now()
  if (firstSeen.current === null) {
    firstSeen.current = new Map(keys.map((k) => [k, 0]))
    return new Set()
  }
  const seen = firstSeen.current
  const fresh = new Set<string>()
  for (const key of keys) {
    let t = seen.get(key)
    if (t === undefined) {
      t = now
      seen.set(key, t)
    }
    if (now - t < FRESH_MS) fresh.add(key)
  }
  if (seen.size > 20_000) {
    const live = new Set(keys)
    for (const key of seen.keys()) if (!live.has(key)) seen.delete(key)
  }
  return fresh
}
