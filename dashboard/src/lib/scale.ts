/** "What if I ran N× the capital?" — a display-only projection of the run.
 *
 * Money and contract quantities (balance, capital, exposure, P&L, order and
 * fill sizes, positions) scale linearly: N× the size on the same orders.
 *
 * Rewards do NOT scale linearly. Kalshi pays each side's share of the
 * qualifying book, share = ours / (ours + others). With others unchanged,
 * sizing ours up N× gives
 *
 *     share' = N·s / (N·s + 1 − s)
 *
 * so a 2% share becomes ~17% at 10×, not 20%, and a 50% share can never
 * exceed 100%. Each market's rewards are scaled by its (share'_yes + share'_no)
 * / (share_yes + share_no), using the current quote shares. Markets without
 * a current share (past ones) use the earnings-weighted average factor.
 *
 * Expected fills and their cost (from the selection's fill-risk replay) scale
 * N x: bigger orders catch more of each sweep. Not modelled: extra capital
 * also buys more markets; bigger orders may push past a program's Target
 * Size; competitors react.
 */
import type { Journal } from './useJournal'
import type { LegQuote, MarketRow, MetricsEvent, RunState, Sample, Totals } from '../types'

type N = number | null | undefined

const lin = <T extends N>(v: T, n: number): T => (typeof v === 'number' ? (v * n) as T : v)

/** Scale a quantity that may arrive as a string (fills send fixed-point strings). */
function linStr<T extends string | number | null | undefined>(v: T, n: number): T {
  if (typeof v === 'number') return (v * n) as T
  if (typeof v === 'string' && v !== '' && !Number.isNaN(Number(v))) return String(Number(v) * n) as T
  return v
}

export function scaledShare(s: N, n: number): number | null {
  if (typeof s !== 'number') return null
  if (s <= 0) return 0
  if (s >= 1) return 1
  return (n * s) / (n * s + 1 - s)
}

/** Reward multiplier for one market from its current per-side shares, or null if unknown. */
export function rewardFactor(yes: N, no: N, n: number): number | null {
  const before = (yes ?? 0) + (no ?? 0)
  if (yes == null && no == null) return null
  if (before <= 0) return null
  return ((scaledShare(yes, n) ?? 0) + (scaledShare(no, n) ?? 0)) / before
}

function scaleLeg(q: LegQuote | undefined, n: number): LegQuote | undefined {
  if (!q) return q
  return { ...q, size: lin(q.size, n), share: scaledShare(q.share, n) }
}

interface Factors {
  byTicker: Map<string, number>
  overall: number // earnings-weighted, for past markets and run totals
}

function factors(markets: MarketRow[], n: number): Factors {
  const byTicker = new Map<string, number>()
  let weighted = 0
  let weight = 0
  let plain = 0
  let count = 0
  for (const m of markets) {
    const f = rewardFactor(m.quotes.yes?.share, m.quotes.no?.share, n)
    if (f === null) continue
    byTicker.set(m.ticker, f)
    weighted += f * (m.earned || 0)
    weight += m.earned || 0
    plain += f
    count += 1
  }
  // No share data at all: the linear upper bound is the only honest fallback left.
  const overall = weight > 0 ? weighted / weight : count ? plain / count : n
  return { byTicker, overall }
}

interface FillRiskFields {
  est_daily_reward?: N
  est_fills_per_day?: N
  fill_cost_per_day?: N
  net_daily_reward?: N
}

/** Fill risk at N x size: fills and their cost grow ~N x (bigger orders catch more of each
 * sweep); the reward grows by the share factor ``rf``; net is recomputed from both. */
function scaleFillRisk<T extends FillRiskFields>(r: T, n: number, rf: number): T {
  const est = lin(r.est_daily_reward, rf)
  const cost = lin(r.fill_cost_per_day, n)
  return {
    ...r,
    est_daily_reward: est,
    est_fills_per_day: lin(r.est_fills_per_day, n),
    fill_cost_per_day: cost,
    net_daily_reward:
      typeof r.net_daily_reward === 'number' ? (est ?? 0) - (cost ?? 0) : r.net_daily_reward,
  }
}

function scaleTotals<T extends Partial<Totals>>(t: T, n: number, rf: number): T {
  return {
    ...t,
    balance: lin(t.balance, n),
    capital_in_use: lin(t.capital_in_use, n),
    max_capital: lin(t.max_capital, n),
    session_pnl: lin(t.session_pnl, n),
    exposure: lin(t.exposure, n),
    rewards_earned: lin(t.rewards_earned, rf),
    rewards_session: lin(t.rewards_session, rf),
    rewards_per_hour: lin(t.rewards_per_hour, rf),
  }
}

/** Per-second totals history under the multiplier (rewards by the current overall factor). */
export function scaleSamples(samples: Sample[], n: number, rf: number): Sample[] {
  if (n === 1) return samples
  return samples.map((s) => ({ t: s.t, totals: scaleTotals(s.totals, n, rf) }))
}

export interface Scaled {
  state: RunState | null
  journal: Journal
  rewardFactor: number // overall reward multiplier actually applied
}

export function scaleView(state: RunState | null, journal: Journal, n: number): Scaled {
  if (n === 1 || !state) return { state, journal, rewardFactor: 1 }
  const f = factors(state.markets, n)
  const factorOf = (ticker: string) => f.byTicker.get(ticker) ?? f.overall

  let rate = 0
  let rateBefore = 0
  const markets = state.markets.map((m): MarketRow => {
    const rf = factorOf(m.ticker)
    rate += (m.rate_per_hour || 0) * rf
    rateBefore += m.rate_per_hour || 0
    return {
      ...scaleFillRisk(m, n, rf),
      position: m.position * n,
      exposure: m.exposure * n,
      realized_pnl: m.realized_pnl * n,
      fees: m.fees * n,
      earned: m.earned * rf,
      rate_per_hour: m.rate_per_hour * rf,
      quotes: { yes: scaleLeg(m.quotes.yes, n), no: scaleLeg(m.quotes.no, n) },
    }
  })
  const rateFactor = rateBefore > 0 ? rate / rateBefore : f.overall

  const scaledState: RunState = {
    ...state,
    totals: {
      ...scaleTotals(state.totals, n, f.overall),
      rewards_per_hour: lin(state.totals.rewards_per_hour, rateFactor),
    },
    markets,
    orders: state.orders.map((o) => ({ ...o, size: o.size * n })),
  }

  const scaledJournal: Journal = {
    ...journal,
    orders: journal.orders.map((o) => ({ ...o, size: lin(o.size, n) })),
    fills: journal.fills.map((fl) => ({
      ...fl,
      count: linStr(fl.count, n),
      post_position: linStr(fl.post_position, n),
    })),
    quotes: journal.quotes.map((q) => {
      const rf = rewardFactor(q.yes.share, q.no.share, n) ?? factorOf(q.ticker)
      return {
        ...q,
        position: q.position * n,
        yes: scaleLeg(q.yes, n) as LegQuote,
        no: scaleLeg(q.no, n) as LegQuote,
        est_daily_reward: q.est_daily_reward * rf,
      }
    }),
    metrics: journal.metrics.map((m) => scaleTotals(m, n, f.overall) as MetricsEvent),
    selections: journal.selections.map((s) => ({
      ...s,
      markets: s.markets.map((m) => {
        const scaled = scaleFillRisk(m, n, factorOf(m.ticker))
        return { ...scaled, est_daily_reward: scaled.est_daily_reward ?? m.est_daily_reward }
      }),
    })),
  }
  return { state: scaledState, journal: scaledJournal, rewardFactor: f.overall }
}
