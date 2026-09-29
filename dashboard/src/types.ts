// Shapes of the bot's run journal (runs/<id>/state.json and events.jsonl).
// Decimals arrive as JSON numbers; missing values as null.

export type Num = number | null

export interface Totals {
  balance: Num
  capital_in_use: Num
  max_capital: Num
  session_pnl: Num
  exposure: Num
  rewards_earned: Num
  rewards_per_hour: Num
  requotes: Num
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
}

export interface LegQuote {
  side: 'bid' | 'ask' | null
  price: Num
  size: Num
  reason: string
  share: Num
}

export interface MarketRow {
  ticker: string
  title: string
  close_time: string | null
  reduce_only: boolean
  paused: boolean
  near_close: boolean
  healthy: boolean
  book: Book | null
  position: number
  exposure: number
  realized_pnl: number
  fees: number
  quotes: { yes?: LegQuote; no?: LegQuote }
  reward: { per_day: number; target_size: Num; discount_factor: Num }
  earned: number
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
  status: string
  environment: string
  mode: string
  started_at: number
  updated_at: number
  ws_connected: boolean
  trading_active: boolean
  globally_paused: boolean
  halt_reason: string
  budget_binding: boolean
  totals: Totals
  markets: MarketRow[]
  orders: OrderRow[]
}

export interface RunInfo {
  id: string
  updated_at: number
  status: string
}

interface Base {
  ts: number
}

export interface OrderEvent extends Base {
  type: 'order'
  action: 'place' | 'cancel' | 'decrease' | 'reject'
  ticker: string
  side: string
  price: Num
  size: Num
  order_id?: string
  error?: string
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

export interface QuoteEvent extends Base {
  type: 'quote'
  ticker: string
  position: number
  yes: LegQuote
  no: LegQuote
  est_daily_reward: number
  dry_run: boolean
}

export interface LogEvent extends Base {
  type: 'log'
  level: string
  logger: string
  msg: string
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
  | QuoteEvent
  | LogEvent
  | MarketsEvent
  | MetricsEvent
  | WsEvent
  | RunStartEvent
  | RunEndEvent
