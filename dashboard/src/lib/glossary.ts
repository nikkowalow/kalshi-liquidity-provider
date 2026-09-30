// Hover explanations for every label on the dashboard.
// Elements opt in with data-help="<key>"; <HelpLayer> shows the matching entry.

export type HelpEntry = [title: string, body: string, footnote?: string | null]

export const HELP: Record<string, HelpEntry> = {
  // top bar
  "bar:status": ["Run status", "RUNNING: the bot is updating its state right now. STALE: no update for 5+ seconds (it may have crashed or be stuck). ENDED: stopped normally. HALTED: stopped by a risk limit (see the Halt box)."],
  "bar:mode": ["Mode", "LIVE: real orders are being placed. DRY-RUN: the bot only computes and logs what it would do; nothing is sent to Kalshi."],
  "bar:follow": ["Follow latest", "Automatically switch to the newest bot run when one starts. Untick to stay on the run you picked."],

  // KPIs
  "kpi:Balance": ["Balance", "Cash in your Kalshi account, as reported by Kalshi. Refreshed every 15 seconds."],
  "kpi:Capital in use": ["Capital in use", "Money the bot has tied up: what it paid for its positions, plus the cash Kalshi holds for its open orders. Sell orders against contracts you already own don't count, since they need no extra cash.",
    "Compared against max_capital. BINDING means the cap is being hit and new orders are shrunk or skipped to stay under it."],
  "kpi:Session P&L": ["Session P&L", "Profit or loss since the bot last started (this session), in the markets the bot trades. Open positions are valued at the current mid price, and fees are subtracted.",
    "Mostly unrealized: it moves with prices until you sell or the market settles. The bot stops itself if this falls below -max_session_loss."],
  "kpi:Est. rewards": ["Estimated rewards", "The bot's estimate of the liquidity-program rewards earned, totalled across every session of this journal (restarts don't reset it); the line underneath shows this session alone. Once a second it replays Kalshi's scoring on the live order book, using your orders' real place in the queue.",
    "An estimate, not Kalshi's number. Kalshi pays after each program period ends, and doesn't pay amounts under $1."],
  "kpi:Reward rate": ["Reward rate", "Estimated rewards earned over roughly the last 10 minutes, as dollars per hour. The small line underneath projects that rate over a full day."],
  "kpi:Exposure": ["Exposure", "The total you paid for the positions you currently hold (their cost basis)."],
  "kpi:Resting": ["Resting orders", "How many of the bot's orders are sitting on the order book right now, waiting to be filled."],
  "kpi:Fills": ["Fills", "How many times one of the bot's orders traded, across all sessions. Rejected means Kalshi refused an order, usually because it would have traded immediately (the bot only posts orders that rest on the book)."],
  "kpi:Requotes": ["Requotes", "How many times the bot recalculated its quotes. It does this whenever an order book, one of its orders, or its position changes, and every few seconds anyway."],
  "kpi:Feed": ["Feed", "WS UP: the live market-data connection to Kalshi is working. If it drops, the bot can't see prices, so it pulls its orders until the connection is back. The second line says whether Kalshi trading is open."],
  "kpi:Halt": ["Halt", "A risk limit stopped the bot and it cancelled all its orders. The reason is shown. Restart the bot to resume."],

  // panels & charts
  "panel:markets": ["Markets", "One row per market the bot is quoting, then (dimmed, tagged PAST) markets it quoted earlier. Every price is in YES terms: 0.30 means $0.30 per contract, and a contract pays $1 if its outcome happens."],
  "panel:performance": ["Performance", "How this run is going over time, sampled every 10 seconds. Hover a chart to see exact values."],
  "chart:rewards": ["Est. rewards earned", "Running total of the bot's estimated program rewards this run. The steeper the line, the faster you're earning."],
  "chart:pnl": ["Session P&L", "Trading profit or loss over time, valued at mid prices (not including rewards). Dips usually mean you were filled and the price then moved against you. The dashed line is $0."],
  "chart:capital": ["Capital in use", "Money tied up in positions plus open orders, over time. The red dashed line is your max_capital budget."],
  "panel:blotter": ["Blotter", "Everything the bot did, newest first: orders placed, cancelled, shrunk, or rejected, fills, and quote changes. Use the buttons to filter by type and the box to filter by ticker."],
  "panel:orders": ["Resting orders", "The bot's orders sitting on the order book right now."],
  "panel:fills": ["Fills", "Trades that happened against the bot's orders this run."],
  "panel:log": ["Log", "The bot's own log messages. Yellow is a warning and red is an error. The level buttons filter what's shown."],
  "panel:selection": ["Market selection history", "Each time the bot re-ranked markets (every 10–15 minutes), which ones it chose and why."],
  "panel:config": ["Run config", "The full settings this run started with (from config/*.yaml plus command-line flags)."],

  // market columns
  "col:Ticker": ["Ticker", "Kalshi's ID for the market. Hover a ticker to see which outcome it's about."],
  "col:Bid": ["Bid", "The highest price anyone (including the bot) is currently offering to pay for YES."],
  "col:Ask": ["Ask", "The lowest price anyone is currently willing to sell YES for. Kalshi doesn't list asks directly: it's $1 minus the best NO bid."],
  "col:Sprd": ["Spread", "Ask minus bid, in cents. Narrow means the market agrees on a price. Wide means uncertainty, and more risk that a fill is a bad trade."],
  "col:Sz b/a": ["Size bid / ask", "How many contracts are waiting at the best bid and at the best ask. Bigger numbers mean more competition for rewards at those prices."],
  "col:Our YES bid": ["Our YES bid", "The bot's buy order for YES, shown as contracts@price, plus the reason it chose that price. Hover the reason for details.",
    "This order earns the YES side of the reward."],
  "col:Our YES ask (NO bid)": ["Our YES ask (NO bid)", "The bot's sell order for YES. On Kalshi, selling YES at 0.60 is the same as bidding for NO at 0.40, so it sits in the NO order book.",
    "This order earns the NO side of the reward."],
  "col:Pos": ["Position", "Contracts you hold. Positive means YES contracts and negative means NO. Each pays $1 if its side wins, and $0 if not."],
  "col:Cost": ["Cost", "What you paid for your current position in this market."],
  "col:Realized": ["Realized P&L", "Profit or loss already locked in from positions you've closed in this market, as reported by Kalshi (not limited to this run)."],
  "col:Prog $/d": ["Program $/day", "How much this market's liquidity program pays out per day, split among everyone providing qualifying orders. It's the whole pie, not your slice."],
  "col:Target": ["Target size", "The program's order-book depth that counts. Only the first N contracts of depth on each side (walking down from the best price) earn anything. If either side has less than this, that second pays nobody."],
  "col:Share Y/N": ["Share YES / NO", "Your estimated share of the reward on the YES side and on the NO side, for the current book.",
    "Your $/day is roughly Program $/day × (YES share + NO share) ÷ 2."],
  "col:Earned": ["Earned", "The bot's estimate of rewards earned in this market, across all sessions."],
  "col:$/h": ["$/hour", "Your recent reward rate in this market, over roughly the last 10 minutes."],
  "col:Paying": ["Paying", "The share of scored seconds in which your orders earned something. Below 100% means some seconds paid nothing: a side of the book was under Target, your order was too deep in the queue, or you had no order resting."],
  "col:Flags": ["Flags", "Anything unusual about this market right now. Hover a flag for details."],

  // flags
  "flag:PAST": ["PAST", "The bot isn't quoting this market now. It's here because the bot earned (or tried to earn) rewards in it earlier, in this session or a previous one. Earned and snapshot counts are totals across all sessions."],
  "chip:past": ["Past markets", "Show or hide markets the bot quoted earlier but isn't quoting now."],
  "bar:session": ["Session", "How many times the bot has been started into this journal. Stopping and restarting the bot continues the same history: rewards, fills and past markets carry over. SINCE is when the first session started."],
  "flag:ok": ["ok", "Nothing unusual: the bot is quoting normally."],
  "flag:BLIND": ["BLIND", "The bot lost its trusted view of this order book (disconnected, or a missed update). It has pulled its orders here until a fresh copy of the book arrives."],
  "flag:PAUSED": ["PAUSED", "Quoting is paused for risk.cooldown_seconds, for one of two reasons. Too many contracts filled here in a short time (risk.fill_burst_contracts), which often means someone better informed is trading against you. Or the price moved sharply (risk.max_mid_move within mid_move_window_seconds), which usually means news."],
  "flag:CLOSING": ["CLOSING", "The market closes soon (within risk.close_buffer_seconds). The bot stops quoting, because prices can jump on last-minute news."],
  "flag:REDUCE-ONLY": ["REDUCE-ONLY", "The bot will only place orders that shrink your position here. Either the market dropped out of the selection while you still held a position, or a capital or exposure limit was reached."],

  // blotter
  "blot:Type": ["Type", "What happened. Hover any value in this column for details."],
  "blot:Side": ["Side", "BID means buying YES. ASK means selling YES, which is the same as buying NO."],
  "blot:Price": ["Price", "Order price in YES terms. For an ASK, the NO price is $1 minus this."],
  "blot:Qty": ["Qty", "Number of contracts."],
  "blot:Info": ["Info", "Order ID, the rejection reason, fill details, or for a quote: both sides, your position, and the estimated $/day."],
  "type:place": ["PLACE", "The bot put a new order on the book."],
  "type:cancel": ["CANCEL", "The bot took an order off the book, usually to move it to a new price or because the market is paused or closing."],
  "type:decrease": ["DECREASE", "The bot made an order smaller without moving it, which keeps its place in the queue."],
  "type:reject": ["REJECT", "Kalshi refused the order. The usual reason is post-only: the price moved and the order would have traded immediately, which the bot never allows. It retries on the next update."],
  "type:fill": ["FILL", "Someone traded against one of the bot's orders."],
  "type:quote": ["QUOTE", "The bot's desired quotes changed: the prices and sizes it now wants on each side, and why. Always shown, including in dry-run."],

  // resting orders & fills
  "ord:Side": ["Side", "BID means an order to buy YES. ASK means an order to sell YES, which sits in the NO book."],
  "ord:Price": ["Price", "Price in YES terms."],
  "ord:Qty": ["Qty", "Contracts still waiting to fill."],
  "ord:Order": ["Order", "Start of Kalshi's order ID."],
  "ord:Rank": ["Rank / target", "Where the first contract of this order sits in its side of the book: #1 is the very front. It counts every contract from other traders at better prices, plus those ahead of it at the same price. The number after the slash is the program's Target Size: only the first that many contracts on a side earn rewards.",
    "Green IN: the whole order is inside Target Size. Yellow PARTIAL: part of it is. Red OUT: none of it earns right now. Refreshed every few seconds."],
  "ord:Better": ["Better px", "Contracts from other traders at better prices on this side. They're all ahead of us regardless of time."],
  "ord:Queue": ["At level", "Contracts ahead of us at our own price, as reported by Kalshi's queue-position endpoint (time priority). 'back?' means the bot hasn't fetched it yet and assumes we're at the back of the line."],
  "ord:Credit": ["Full credit", "YES: the order is at or above the program's reference price, so it earns full credit. DISC: it's below the reference price, so its credit is discounted by the discount factor for each tick below."],
  "ord:Age": ["Age", "How long ago the bot placed (or first saw) this order. Older orders have moved up the queue as orders ahead of them filled or cancelled."],
  "col:Rank Y/N": ["Rank YES / NO", "Queue rank of our best order on the YES side and on the NO side, out of the program's Target Size. Green means fully inside the top N contracts, which is where rewards are paid. Hover a badge for details."],
  "rank:in": ["Inside Target Size", "The whole order is within the first Target Size contracts on its side, so it counts toward rewards every second, as long as both sides of the market reach Target Size."],
  "rank:partial": ["Partly inside", "Only part of this order is within the first Target Size contracts on its side. The rest earns nothing until orders ahead fill or cancel."],
  "rank:out": ["Outside Target Size", "More than Target Size contracts sit ahead of this order, so it earns no reward right now. It will move up as orders ahead of it fill or cancel."],
  "rank:unknown": ["Unknown", "No program data or live book for this market yet."],
  "panel:tape": ["Tape", "A scrolling feed of the bot's latest activity: fills (magenta), orders placed, cancelled, or shrunk, and warnings. Hover to pause it."],
  "fill:Side": ["Side", "BID means you bought YES. ASK means you sold YES, or bought NO."],
  "fill:Price": ["Price", "Trade price in YES terms."],
  "fill:Qty": ["Qty", "Contracts traded."],
  "fill:Pos after": ["Position after", "Your position in that market right after this fill. Positive means YES and negative means NO."],
  "fill:TAKER": ["TAKER", "This fill took liquidity rather than resting on the book. The bot is set up to avoid this. Takers pay higher fees and earn no rewards."],

  // selection
  "sel:Est $/d": ["Estimated $/day", "When the market was chosen, the bot's estimate of what its quotes would earn per day, if the book stayed the same and competitors didn't react."],
  "sel:Closes": ["Closes", "When the market stops trading."],

  // log levels
  "level:DEBUG": ["DEBUG", "Very detailed messages. Only produced when the bot runs with -v."],
  "level:INFO": ["INFO", "Normal activity."],
  "level:WARNING": ["WARNING", "Something noteworthy that the bot handled, such as a pause, a budget limit, or a reconnect."],
  "level:ERROR": ["ERROR", "Something failed. The bot usually retries."],
  "level:CRITICAL": ["CRITICAL", "Serious: a halt, or the bot couldn't cancel its orders. Check it now."],
}

// Why the bot chose a quote's price. Matched by prefix since some include numbers.
const REASONS: [prefix: string, title: string, body: string][] = [
  ["thin book", "Thin book (no cushion)", "Fewer than quoting.min_cushion contracts (capped at Target Size / 5, the full-credit depth) from other traders are resting on this side, so there's nothing ahead of us to absorb a sell-off. The bot doesn't quote here until the book fills in."],
  ["reward(kept)", "Kept in place", "This order is still earning nearly as much as the best new price would, so the bot left it where it is. Moving an order sends it to the back of the queue."],
  ["reward", "Reward-optimized", "The bot tried each price between the best bid and the deepest price that still gets full credit, and picked the deepest (least likely to be filled) whose reward share is within 10% of the best."],
  ["join(thin)", "Join (thin book)", "The book is too thin to compute the program's reference price, so the bot joined the best bid."],
  ["join", "Join", "Placed at the current best bid (placement: join)."],
  ["improve", "Improve", "Placed one tick better than the best bid (placement: improve). More likely to be filled."],
  ["one-sided", "One-sided book", "Nobody is bidding on this side, so the bot priced off the other side of the book with a safety margin (one_sided_edge)."],
  ["position limit", "Position limit", "You hold the maximum position on this side (max_position_per_market), or the bot is in reduce-only mode, so it won't buy more."],
  ["price", "Below price floor", "The price the bot wanted is under quoting.min_price. The bot skips long-shot prices, where one fill can swing P&L a lot relative to the reward."],
  ["self-cross", "Self-cross", "Both of the bot's quotes would have traded with each other, so this side was dropped."],
  ["empty book", "Empty book", "Nobody is quoting either side, so there's no price to anchor to. The bot doesn't quote."],
]

function reasonHelp(reason: string): HelpEntry {
  const hit = REASONS.find(([prefix]) => reason.startsWith(prefix))
  const extra = reason.includes('+uncross')
    ? "Also moved back one tick so the bot's bid and ask don't trade with each other."
    : null
  return hit ? [hit[1], hit[2], extra] : ['Reason', reason]
}

export function helpFor(key: string): HelpEntry | null {
  if (HELP[key]) return HELP[key]
  const [kind, ...rest] = key.split(':')
  const value = rest.join(':')
  if (kind === 'reason') return reasonHelp(value)
  if (kind === 'ticker') {
    const [ticker, title] = value.split('|')
    return [ticker, title || '(no description)', 'Prices in this row are for YES on this outcome.']
  }
  return null
}
