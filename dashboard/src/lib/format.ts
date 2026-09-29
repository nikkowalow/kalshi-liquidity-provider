export const num = (v: unknown): number | null =>
  v === null || v === undefined || v === '' ? null : Number(v)

/** YES price: 4 decimals for sub-penny prices, else 2. */
export function px(v: unknown): string {
  const n = num(v)
  if (n === null) return '—'
  const s = n.toFixed(4)
  return s.endsWith('00') ? n.toFixed(2) : s
}

export function usd(v: unknown, digits = 2): string {
  const n = num(v)
  if (n === null) return '—'
  return (n < 0 ? '-$' : '$') + Math.abs(n).toFixed(digits)
}

/** Signed dollars: "+$1.20" / "−$0.40" / "$0.00". */
export function signedUsd(v: unknown, digits = 2): string {
  const n = num(v)
  if (n === null) return '—'
  return `${n > 0 ? '+' : n < 0 ? '−' : ''}$${Math.abs(n).toFixed(digits)}`
}

export const signClass = (v: unknown): string => {
  const n = num(v)
  return n === null || n === 0 ? 'dim' : n > 0 ? 'pos' : 'neg'
}

export function qty(v: unknown): string {
  const n = num(v)
  if (n === null) return '—'
  return Number.isInteger(n) ? String(n) : n.toFixed(2)
}

export function pct(v: unknown): string {
  const n = num(v)
  return n === null ? '—' : `${(n * 100).toFixed(1)}%`
}

export const hms = (ts: number): string =>
  new Date(ts * 1000).toLocaleTimeString([], { hour12: false })

export function duration(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  const h = Math.floor(s / 3600)
  const m = Math.floor((s % 3600) / 60)
  return `${h}h${String(m).padStart(2, '0')}m${String(s % 60).padStart(2, '0')}s`
}
