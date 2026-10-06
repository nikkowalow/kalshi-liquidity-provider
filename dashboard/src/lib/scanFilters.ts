import type { ScanRow } from '../types'

/** The market scanner's what-if filters. Number fields are kept as typed text: blank = off. */
export interface ScanFilters {
  search: string // ticker, series or title contains
  minVolume: string // contracts traded in the last 24h
  minMid: string // YES mid, 0-1
  maxMid: string
  maxSpreadCents: string
  minHoursToResolve: string
  minProgramHoursLeft: string
  minProgramDays: string // program period length
  minDaily: string // est. $/day
  minNet: string // net $/day after fill costs
  minPeriodPayout: string
  minReturnPct: string // $/day per $ locked, %
  maxFillsPerDay: string
  maxFillCostPct: string // fill cost as % of the reward
  minTradesReplayed: string
  competition: { low: boolean; medium: boolean; high: boolean }
  hideDataRelease: boolean
  hideExcludedSeries: boolean
  hideSkipped: boolean // the bot's own skip reasons
  onlyTrading: boolean
}

export const NO_FILTERS: ScanFilters = {
  search: '',
  minVolume: '',
  minMid: '',
  maxMid: '',
  maxSpreadCents: '',
  minHoursToResolve: '',
  minProgramHoursLeft: '',
  minProgramDays: '',
  minDaily: '',
  minNet: '',
  minPeriodPayout: '',
  minReturnPct: '',
  maxFillsPerDay: '',
  maxFillCostPct: '',
  minTradesReplayed: '',
  competition: { low: true, medium: true, high: true },
  hideDataRelease: false,
  hideExcludedSeries: false,
  hideSkipped: false,
  onlyTrading: false,
}

type Selection = Record<string, unknown> | undefined

// Config values arrive as numbers, or as strings for decimals ("0.08"); null/absent = off.
const text = (v: unknown, scale = 1): string => {
  const n = typeof v === 'number' ? v : typeof v === 'string' && v.trim() ? Number(v) : NaN
  return Number.isFinite(n) ? String(+(n * scale).toFixed(4)) : ''
}

/**
 * The bot's own selection settings as scanner filters (what the bot would allow).
 * Leaves out min_period_payout: the scanner's payouts are at 10 contracts/side, far
 * below the bot's size, so the bot's $ threshold would hide nearly everything.
 */
export function botFilters(sel: Selection): ScanFilters {
  const s = sel ?? {}
  return {
    ...NO_FILTERS,
    minVolume: text(s.min_volume_24h),
    minMid: text(s.min_mid_price),
    maxMid: text(s.max_mid_price),
    maxSpreadCents: text(s.max_spread, 100),
    minHoursToResolve: text(s.min_seconds_to_close, 1 / 3600),
    minProgramHoursLeft: text(s.min_program_seconds_left, 1 / 3600),
    minProgramDays: text(s.min_program_period_days),
    maxFillsPerDay: text(s.max_fills_per_day),
    maxFillCostPct: text(s.max_fill_cost_share, 100),
    minTradesReplayed: text(s.min_fill_risk_trades),
    hideDataRelease: s.exclude_data_releases !== false,
    hideExcludedSeries: true,
  }
}

const num = (s: string): number | null => {
  const t = s.trim()
  if (!t) return null
  const n = Number(t)
  return Number.isFinite(n) ? n : null
}

/** How many filters are switched on (for the toolbar badge). */
export function activeCount(f: ScanFilters): number {
  let n = 0
  for (const [k, v] of Object.entries(f)) {
    if (typeof v === 'string' && k !== 'search' && num(v) !== null) n++
    else if (typeof v === 'boolean' && v) n++
  }
  if (f.search.trim()) n++
  if (!(f.competition.low && f.competition.medium && f.competition.high)) n++
  return n
}

/**
 * Whether ``r`` passes every filter that's on. A filter on a value the row doesn't have
 * (e.g. fills/day when its trades weren't checked) fails it: unknown isn't a pass.
 */
export function passes(r: ScanRow, f: ScanFilters): boolean {
  const atLeast = (v: number | null | undefined, min: string) => {
    const m = num(min)
    return m === null || (v != null && v >= m)
  }
  const atMost = (v: number | null | undefined, max: string) => {
    const m = num(max)
    return m === null || (v != null && v <= m)
  }
  const q = f.search.trim().toLowerCase()
  if (q && !`${r.ticker} ${r.series ?? ''} ${r.title}`.toLowerCase().includes(q)) return false
  if (!f.competition[r.competition]) return false
  if (f.hideDataRelease && r.data_release) return false
  if (f.hideExcludedSeries && r.excluded_series) return false
  if (f.hideSkipped && r.skip) return false
  if (f.onlyTrading && !r.trading) return false
  const costShare =
    r.fill_cost_per_day != null && r.est_daily > 0 ? (r.fill_cost_per_day / r.est_daily) * 100 : null
  return (
    atLeast(r.volume_24h, f.minVolume) &&
    atLeast(r.mid, f.minMid) &&
    atMost(r.mid, f.maxMid) &&
    atMost(r.spread == null ? null : r.spread * 100, f.maxSpreadCents) &&
    atLeast(r.hours_to_resolve, f.minHoursToResolve) &&
    atLeast(r.program_hours_left, f.minProgramHoursLeft) &&
    atLeast(r.program_period_days, f.minProgramDays) &&
    atLeast(r.est_daily, f.minDaily) &&
    atLeast(r.net_daily, f.minNet) &&
    atLeast(r.period_payout, f.minPeriodPayout) &&
    atLeast(r.return_daily == null ? null : r.return_daily * 100, f.minReturnPct) &&
    atMost(r.fills_per_day, f.maxFillsPerDay) &&
    atMost(costShare, f.maxFillCostPct) &&
    atLeast(r.trades_replayed, f.minTradesReplayed)
  )
}

const STORAGE_KEY = 'klp-scan-filters'

export function loadFilters(): ScanFilters {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const saved = JSON.parse(raw) as Partial<ScanFilters>
      return { ...NO_FILTERS, ...saved, competition: { ...NO_FILTERS.competition, ...saved.competition } }
    }
  } catch {
    // storage unavailable or corrupt: start with no filters
  }
  return NO_FILTERS
}

export function saveFilters(f: ScanFilters): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(f))
  } catch {
    // not persisted; the filters still work for this page view
  }
}
