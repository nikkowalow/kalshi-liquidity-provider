import type { MarketRow } from '../types'

const isoTs = (iso: string | null | undefined) => (iso ? Date.parse(iso) / 1000 : null)

export interface PeriodView {
  end: number | null // unix seconds the current program period ends
  earned: number // earned in this period so far (all-time on bots without per-period data)
  earningEnd: number | null // when earning stops: period end or market close, whichever is first
  projected: number | null // earned this period + estimate for the rest of it
  daysLeft: number | null // earning time left in the period, days
  fillChance: number | null // chance of at least one fill before it ends (0..1)
  fillEvents: number | null // expected fill events in that time
  fillContracts: number | null // expected contracts filled
  fillLoss: number | null // expected cost of those fills, dollars
  net: number | null // projected - fillLoss
  fillBasis: 'live' | 'planned' | null // resting orders now, or the quote selection planned
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
  // Fill risk for the rest of the period, from the bot's replay of recent trades (per day),
  // with fills as a Poisson process: P(at least one) = 1 - e^(-expected fills).
  // Prefer the orders resting now (their real size and queue spot) over the planned quote.
  const live = m.live_fill_cost_per_day != null
  const fillBasis = live ? 'live' : m.fill_cost_per_day != null ? 'planned' : null
  const events = (live ? m.live_fill_events_per_day : m.est_fill_events_per_day) ?? null
  const contracts = (live ? m.live_fills_per_day : m.est_fills_per_day) ?? null
  const cost = (live ? m.live_fill_cost_per_day : m.fill_cost_per_day) ?? null
  const fillEvents = events !== null && days !== null ? events * days : null
  const fillChance = fillEvents === null ? null : 1 - Math.exp(-fillEvents)
  const fillContracts = contracts !== null && days !== null ? contracts * days : null
  const fillLoss = cost !== null && days !== null ? cost * days : null
  const net = projected !== null && fillLoss !== null ? projected - fillLoss : null
  return {
    end,
    earned,
    earningEnd,
    projected,
    daysLeft: days,
    fillChance,
    fillEvents,
    fillContracts,
    fillLoss,
    net,
    fillBasis,
  }
}
