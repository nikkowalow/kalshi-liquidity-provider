import type { MarketRow } from '../types'

const isoTs = (iso: string | null | undefined) => (iso ? Date.parse(iso) / 1000 : null)

export interface PeriodView {
  end: number | null // unix seconds the current program period ends
  earned: number // earned in this period so far (all-time on bots without per-period data)
  earningEnd: number | null // when earning stops: period end or market close, whichever is first
  projected: number | null // earned this period + estimate for the rest of it
}

/**
 * The market's current program period, as the bot's $1 / $2 payout filters see it. Kalshi
 * pays each period on its own (nothing under $1), so earlier periods' earnings don't count.
 * ``now``: unix seconds.
 */
export function periodView(m: MarketRow, now: number): PeriodView {
  const end = isoTs(m.reward.period_end)
  const close = isoTs(m.close_time)
  const current = m.periods?.find((p) => p.end !== null && p.end === m.reward.period_end)
  const earned = m.periods ? (current?.earned ?? 0) : m.earned
  const earningEnd = end === null ? null : close === null ? end : Math.min(end, close)
  const days = earningEnd === null ? null : Math.max(earningEnd - now, 0) / 86_400
  const projected = m.est_daily_reward != null && days !== null ? earned + m.est_daily_reward * days : null
  return { end, earned, earningEnd, projected }
}
