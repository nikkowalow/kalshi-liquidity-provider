import type { BalanceChange } from './balance'

export interface Day {
  key: string // local date, YYYY-MM-DD
  label: string // "Oct 5"
  paid: number // rewards Kalshi paid that day
  payouts: number // how many payouts
  today: boolean
}

const MAX_DAYS = 45

function dayKey(ts: number): string {
  const d = new Date(ts * 1000)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

/**
 * Rewards Kalshi actually paid per local calendar day: the balance history's reward
 * payouts (the same ones the Balance tile's history lists), from the journal's first
 * balance reading to today. Days without a payout count 0.
 */
export function rewardDays(changes: BalanceChange[], now = Date.now() / 1000): Day[] {
  if (!changes.length) return []
  const byDay = new Map<string, { paid: number; payouts: number }>()
  for (const c of changes) {
    if (c.kind !== 'reward') continue
    const k = dayKey(c.ts)
    const d = byDay.get(k) ?? { paid: 0, payouts: 0 }
    d.paid += c.amount
    d.payouts += 1
    byDay.set(k, d)
  }
  const first = Math.min(...changes.map((c) => c.ts))
  const days: Day[] = []
  const cursor = new Date(first * 1000)
  cursor.setHours(12, 0, 0, 0) // noon: DST changes can't skip or repeat a date
  const todayKey = dayKey(now)
  for (let k = dayKey(cursor.getTime() / 1000); k <= todayKey; k = dayKey(cursor.getTime() / 1000)) {
    const d = byDay.get(k)
    days.push({
      key: k,
      label: cursor.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }),
      paid: d?.paid ?? 0,
      payouts: d?.payouts ?? 0,
      today: k === todayKey,
    })
    cursor.setDate(cursor.getDate() + 1)
  }
  return days.slice(-MAX_DAYS)
}
