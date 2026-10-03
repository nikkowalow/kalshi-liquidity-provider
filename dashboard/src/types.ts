// Shapes of the bot's run journal (runs/<id>/state.json and events.jsonl).
// Decimals arrive as JSON numbers; missing values as null.

export type Num = number | null

export interface Totals {
  balance: Num
  capital_in_use: Num
  max_capital: Num
  session_pnl: Num
  exposure: Num
  rewards_earned: Num // all runs in this journal
  rewards_session?: Num // since the bot last started
  rewards_per_hour: Num
  rewards_paid?: Num // Kalshi's actual payouts, from balance reconciliation (null until checked)
  requotes: Num
  fills?: Num // all runs in this journal
  resting_orders: Num
}

export interface Book {
  bid: Num
  ask: Num
  mid: Num
  bid_size: Num
  ask_size: Num
  yes_depth: Num
  no_depth: Num
  // Best levels of each bid ladder, [leg price, size] best first (newer bots only).
  yes_levels?: [number, number][]
  no_levels?: [number, number][]
}

export interface LegQuote {
  side: 'bid' | 'ask' | null
  price: Num
  size: Num
  reason: string
  share: Num
}

export interface CompetitionInfo {
  level: 'low' | 'medium' | 'high'
  room: Num // dollars below the best bid where others fill Target Size; null = they don't
}

export interface MarketRow {
  ticker: string
  title: string
  inactive?: boolean // not quoted now; only in the history of earlier markets
  last_earned_at?: number | null
  close_time: string | null
  reduce_only: boolean
  paused: boolean
  pause_reason?: string | null // why it's paused, e.g. "18 contracts filled in 300s"
  pause_left?: number // seconds until it resumes
  flattening?: boolean // holding a position the bot is closing out
  // A passive exit resting now (risk.exit_mode: passive); null when crossing or flat.
  unwind?: {
    side: 'bid' | 'ask'
    price: number
    size: number
    entry: Num // average YES entry price
    seconds_left: Num // until it crosses the book anyway
    why: string
  } | null
  size?: Num // auto-sized contracts per side, when quoting.auto_size is on
  near_close: boolean
  healthy: boolean
  book: Book | null
  position: number
  exposure: number
  realized_pnl: number
  fees: number
  quotes: { yes?: LegQuote; no?: LegQuote }
  event_ticker?: string | null
  reward: {
    per_day: number
    target_size: Num
    discount_factor: Num
    period_reward?: Num // dollars the program pays over its whole period (all participants)
    period_start?: string | null
    period_end?: string | null
    max_reward_per_account?: Num // dollars one account can earn per period; null: no cap
  }
  competition?: CompetitionInfo | null
  // From the latest selection (see src/kalshi_lp/strategy/fill_risk.py); null if not checked.
  est_daily_reward?: Num // estimated reward $/day at selection
  est_fills_per_day?: Num // our contracts recent sweeps would have filled, per day
  est_fill_events_per_day?: Num // sweeps that would have reached our quotes, per day
  // The same, for the orders resting now (real size and queue spot); null without resting orders.
  live_fills_per_day?: Num
  live_fill_events_per_day?: Num
  live_fill_cost_per_day?: Num
  fill_cost_per_day?: Num // what those fills cost (taker fee to exit + adverse move)
  net_daily_reward?: Num // est_daily_reward - fill_cost_per_day
  earned: number
  paid?: Num // Kalshi payouts matched to this market (best-effort split of the real total)
  // Per program period, newest first. status: Kalshi's (running / awaiting payout / paid out).
  periods?: { end: string | null; earned: Num; paid: Num; status?: string | null }[]
  rate_per_hour: number
  avg_score: number
  snapshots: number
  paying_snapshots: number
}

export interface OrderRow {
  order_id: string
  ticker: string
  side: 'bid' | 'ask'
  price: number
  size: number
  // Queue standing (see src/kalshi_lp/engine/queue.py)
  leg: 'yes' | 'no'
  leg_price: number
  queue_ahead: Num // Kalshi's figure: contracts ahead at our price level
  ahead_better: Num // others' contracts at better prices on our side
  ahead_total: Num // everything ahead of us on our side
  target_size: Num
  in_target: 'in' | 'partial' | 'out' | 'unknown'
  full_credit: boolean | null
  age: number
}

export interface RunState {
  run_id: string
  session?: number // restarts of the bot into this journal, 1-based
  first_started_at?: number
  config?: unknown
  status: string
  environment: string
  mode: string
  started_at: number
  updated_at: number
  ws_connected: boolean
  trading_active: boolean
  globally_paused: boolean
  held_since?: number | null // paused from the dashboard (unix seconds)
  halt_reason: string
  budget_binding: boolean
  totals: Totals
  markets: MarketRow[]
  orders: OrderRow[]
}

/** One snapshot's totals, collected once a second while the dashboard is open. */
export interface Sample {
  t: number // unix seconds
  totals: Totals
}

/** The control buttons' credentials; null when the bot has them switched off (api.controls). */
export interface ControlInfo {
  token: string
  actions: string[]
}

/** One market in the scanner's report (src/kalshi_lp/strategy/scanner.py). */
export interface ScanRow {
  rank: number // by est_daily, 1 = best
  ticker: string
  title: string
  close_time: string | null
  program_per_day: number
  target_size: number
  bid: Num // live YES book
  ask: Num
  spread: Num
  yes_price: Num // where the scan's YES bid would rest (leg terms)
  no_price: Num // and its NO bid
  yes_share: Num
  no_share: Num
  est_daily: number // estimated reward, $/day, at `size` contracts per side
  est_hourly: number
  capital: number // cash those quotes lock
  return_daily: Num // est_daily / capital
  days_left: number // until the program period ends or the market closes
  period_payout: number // est_daily x days_left
  competition: 'low' | 'medium' | 'high'
  competition_room?: Num
  fills_per_day: Num // null: trades not checked
  fill_cost_per_day: Num
  net_daily: Num
  trading: boolean // the bot quotes it now
  skip: string | null // why the bot's filters would pass on it
}

export interface ScanReport {
  scanned_at: number
  seconds: number // how long the scan took
  size: number // contracts per side every estimate assumes
  interval: number // seconds between scans
  programs: number
  markets: number // rewarded markets scanned
  earning: number // of those, how many earn anything at this size
  rows: ScanRow[] // the best, by est_daily
}

/** Messages from the bot's WebSocket (/api/ws; see src/kalshi_lp/api.py). */
export type ServerMessage =
  | {
      channel: 'hello'
      run_id: string
      state: RunState | null
      events: JournalEvent[]
      metrics: MetricsEvent[]
      scan?: ScanReport | null
      controls?: ControlInfo | null
    }
  | { channel: 'events'; data: JournalEvent[] }
  | { channel: 'state'; data: RunState }
  | { channel: 'scan'; data: ScanReport }

interface Base {
  ts: number
}

export interface OrderEvent extends Base {
  type: 'order'
  action: 'place' | 'cancel' | 'decrease' | 'reject' | 'exit'
  filled?: Num // exits: contracts actually traded
  ticker: string
  side: string
  price: Num
  size: Num
  order_id?: string
  error?: string
  reason?: string // why the bot did it (older journals lack it)
}

export interface FillEvent extends Base {
  type: 'fill'
  ticker: string
  order_id: string | null
  side: string | null
  price: Num | string
  count: string
  is_taker: boolean
  fee: string | null
  post_position: string | null
}

/** One fill-risk estimate (see src/kalshi_lp/engine/fill_records.py). */
export interface RiskEstimate {
  ts: number // when it was made
  basis: 'selection' | 'live' // the planned quote at selection, or the orders resting then
  events_per_day: Num // fill events a day: daily fill chance = 1 - e^(-this)
  fills_per_day: Num // contracts a day
  cost_per_day: Num
  size: Num
  approx: boolean // events derived from contracts / size (old selections): a lower bound
}

/** The fill risk a fill's market had when the bot entered it and right before the fill. */
export interface FillRiskEvent extends Base {
  type: 'fill_risk'
  fill_ts: number
  order_id: string | null
  ticker: string
  entry: RiskEstimate | null
  at_fill: RiskEstimate | null
  backfilled: boolean // reconstructed from the journal's selections, not recorded live
}

export interface QuoteEvent extends Base {
  type: 'quote'
  ticker: string
  position: number
  yes: LegQuote
  no: LegQuote
  est_daily_reward: number
  dry_run: boolean
}

export interface MarketsEvent extends Base {
  type: 'markets'
  markets: {
    ticker: string
    title: string
    reward_per_day: number
    target_size: Num
    discount_factor: Num
    est_daily_reward: number
    close_time: string | null
    competition?: CompetitionInfo | null
    est_fills_per_day?: Num
    fill_cost_per_day?: Num
    net_daily_reward?: Num
  }[]
  reduce_only: string[]
}

export type MetricsEvent = Base & { type: 'metrics' } & Totals

export interface WsEvent extends Base {
  type: 'ws'
  status: string
}

export interface RunStartEvent extends Base {
  type: 'run_start'
  session?: number
  environment: string
  mode: string
  api_url: string
  config: unknown
}

export interface RunEndEvent extends Base {
  type: 'run_end'
  halted: boolean
  reason: string
}

export type JournalEvent =
  | OrderEvent
  | FillEvent
  | FillRiskEvent
  | QuoteEvent
  | MarketsEvent
  | MetricsEvent
  | WsEvent
  | RunStartEvent
  | RunEndEvent
