import { useCallback, useState } from 'react'

/** The dashboard's display-only capital multiplier (see lib/scale.ts), remembered in this browser. */
const KEY = 'klp-capital-multiplier'
export const MULTIPLIER_PRESETS = [1, 2, 5, 10, 25, 50, 100] as const

function load(): number {
  try {
    const v = Number(localStorage.getItem(KEY))
    return Number.isFinite(v) && v > 0 ? v : 1
  } catch {
    return 1
  }
}

export function useMultiplier(): [number, (n: number) => void] {
  const [n, setN] = useState(load)
  const set = useCallback((next: number) => {
    const v = Number.isFinite(next) && next > 0 ? next : 1
    setN(v)
    try {
      localStorage.setItem(KEY, String(v))
    } catch {
      // not remembered; still applies to this page view
    }
  }, [])
  return [n, set]
}
