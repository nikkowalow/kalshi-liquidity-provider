import { useCallback, useState } from 'react'

/** Click-to-sort for the dashboard tables.
 *
 * Clicking a header sorts by it (numbers largest first, text A→Z), clicking again
 * reverses, and a third click goes back to the table's default order. Empty
 * values always sort last. The choice is remembered per table in this browser.
 */

export type SortValue = number | string | boolean | null | undefined
export type Dir = 'asc' | 'desc'
export interface SortState {
  key: string | null
  dir: Dir
}
export type Accessors<T> = Record<string, (row: T) => SortValue>

const NONE: SortState = { key: null, dir: 'desc' }

function load(storageKey: string): SortState {
  try {
    const raw = localStorage.getItem(`klp-sort:${storageKey}`)
    if (raw) {
      const s = JSON.parse(raw) as SortState
      if ((s.key === null || typeof s.key === 'string') && (s.dir === 'asc' || s.dir === 'desc')) return s
    }
  } catch {
    // storage unavailable: fall back to the default order
  }
  return NONE
}

function save(storageKey: string, state: SortState) {
  try {
    localStorage.setItem(`klp-sort:${storageKey}`, JSON.stringify(state))
  } catch {
    // not persisted; sorting still works for this page view
  }
}

export interface Sorter {
  state: SortState
  toggle: (key: string, text: boolean) => void
}

export function useSort(storageKey: string): Sorter {
  const [state, setState] = useState<SortState>(() => load(storageKey))
  const toggle = useCallback(
    (key: string, text: boolean) => {
      setState((s) => {
        const first: Dir = text ? 'asc' : 'desc'
        let next: SortState
        if (s.key !== key) next = { key, dir: first }
        else if (s.dir === first) next = { key, dir: first === 'asc' ? 'desc' : 'asc' }
        else next = NONE
        save(storageKey, next)
        return next
      })
    },
    [storageKey],
  )
  return { state, toggle }
}

function compare(a: SortValue, b: SortValue): number {
  if (typeof a === 'string' && typeof b === 'string') return a.localeCompare(b)
  return Number(a) - Number(b)
}

const empty = (v: SortValue) => v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v))

/** Rows in the chosen order (a new array); the input order is kept for ties. */
export function sortRows<T>(rows: readonly T[], accessors: Accessors<T>, state: SortState): T[] {
  const get = state.key ? accessors[state.key] : undefined
  if (!get) return [...rows]
  const sign = state.dir === 'asc' ? 1 : -1
  const keyed = rows.map((row) => ({ row, v: get(row) }))
  keyed.sort((x, y) => {
    const ex = empty(x.v)
    const ey = empty(y.v)
    if (ex || ey) return ex === ey ? 0 : ex ? 1 : -1 // empties last in both directions
    return sign * compare(x.v, y.v)
  })
  return keyed.map((k) => k.row)
}
