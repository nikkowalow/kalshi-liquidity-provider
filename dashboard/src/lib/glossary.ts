// Hover explanations for every label on the dashboard.
// Elements opt in with data-help="<key>"; <HelpLayer> shows the matching entry.

export type HelpEntry = [title: string, body: string, footnote?: string | null]

export const HELP: Record<string, HelpEntry> = {
  // top bar
  "bar:status": ["Run status", "RUNNING: the bot is updating its state right now. STALE: no update for 5+ seconds (it may have crashed or be stuck). ENDED: stopped normally. HALTED: stopped by a risk limit (see the Halt box)."],
  "bar:mode": ["Mode", "LIVE: real orders are being placed. DRY-RUN: the bot only computes and logs what it would do; nothing is sent to Kalshi."],
  "bar:run": ["Journal", "The history this bot is writing to: runs/<env>-<mode>, e.g. prod-live. Every restart continues it."],
  "ctl:pause": ["Pause (two clicks)", "Cancels every bot order at once and quotes nothing until you press RESUME. The bot keeps running: it watches the books, scans markets, and still closes any position a fill leaves (flatten_on_fill). Rewards stop while paused. A restart starts quoting again."],
  "ctl:resume": ["Resume", "Quote again, starting right away."],
  "ctl:held": ["Paused from the dashboard", "You paused quoting here. No orders rest on the exchange; press RESUME to quote again."],
  "ctl:rescan": ["Rescan now", "Re-rank markets now instead of waiting for the next scheduled scan (loop.reselect_interval_seconds). The result shows in the log and the markets table within a few seconds."],
  "ctl:flatten": ["Flatten (two clicks)", "Close every position in the markets the bot has traded, now, with immediate-or-cancel orders that cross the book (paying the spread and taker fee). Retries a few times, reaching a little further each time. Quoting carries on afterwards; PAUSE first for a full exit. Positions you opened yourself in other markets are left alone."],
  "ctl:budget": ["Capital budget", "The real risk.max_capital: position cost plus cash in resting orders. Changing it re-ranks markets and resizes quotes at once. It lasts until the bot restarts; to keep it, set risk.max_capital in the config. Not the C× multiplier, which only changes what the dashboard displays."],
  "ctl:settings": ["Settings", "Every strategy and risk setting in the bot's YAML config, with what each one does. Save writes the file (only the changed lines; comments are kept, and the old version is saved as .bak). The bot reads it on restart: 'save & restart' does both in a few seconds, and open positions carry over. The environment, live/dry-run, account and dashboard server settings are left to the file."],
  "ctl:stop": ["Stop the bot (two clicks)", "Shuts the bot down as Ctrl-C would: cancels its orders (open positions stay, and the next start picks them up), writes the final snapshot, and exits. Start it again from the terminal, or use settings → save & restart to restart without leaving the page."],
  "bar:link": ["Connection to the bot", "The dashboard gets its data straight from the running bot, over a WebSocket (the bot serves it: klp run, port 8050 by default). While the bot is stopped the page keeps showing what it last had and reconnects by itself as soon as the bot is back."],

  // KPIs
  "kpi:Balance": ["Balance", "Cash in your Kalshi account, as reported by Kalshi. Refreshed every 15 seconds."],
  "kpi:Capital in use": ["Capital in use", "Money the bot has tied up: what it paid for its positions, plus the cash Kalshi holds for its open orders. Sell orders against contracts you already own don't count, since they need no extra cash.",
    "Compared against max_capital. BINDING means the cap is being hit and new orders are shrunk or skipped to stay under it."],
  "kpi:Session P&L": ["Session P&L", "Profit or loss since the bot last started (this session), in the markets the bot trades. Open positions are valued at the current mid price, and fees are subtracted.",
    "Mostly unrealized: it moves with prices until you sell or the market settles. The bot stops itself if this falls below -max_session_loss."],
  "kpi:Est. rewards": ["Estimated rewards", "The bot's estimate of the liquidity-program rewards earned, totalled across every session of this journal (restarts don't reset it); the line underneath shows this session alone. Once a second it replays Kalshi's scoring on the live order book, using your orders' real place in the queue. Between updates the counter keeps running at your current reward rate.",
    "An estimate, not Kalshi's number. Kalshi pays after each program period ends, and doesn't pay amounts under $1."],
  "kpi:Rewards paid": ["Rewards paid (actual)", "What Kalshi has actually paid you from the liquidity programs since this journal began. Kalshi's API doesn't list reward payouts, so the bot works them out: every 5 minutes it takes the change in your balance and removes everything Kalshi does list (fills, fees, settlements, deposits, withdrawals). What's left is payouts. The percentage compares it with the bot's estimate; the gap is mostly periods that ended under the $1 minimum, and payouts still to come.",
    "Counts every unexplained credit to the account, so a promotion or bonus from Kalshi would show up here too. Not shown in dry runs (needs your account)."],
  "flag:UNWIND": ["Unwinding (passive exit)", "An order filled and the bot is getting out of the position with a resting exit order at the entry price, instead of selling into the book a sweep just emptied. It crosses the book anyway if the market moves against the position (others offer it below the entry by risk.unwind_stop), after risk.unwind_seconds, or near the close. The market's quotes stay off until it's flat."],
  "detail:paid": ["Paid by Kalshi", "Kalshi pays each program period separately after it ends. The bot matches every payout it finds to the periods that just ended, in proportion to its estimate for each, so this per-market figure is a best-effort split of the real total. Earnings from before per-period tracking aren't matched to a market."],
  "detail:projected": ["Projected this period", "What this market should earn in the current program period: what it has earned in the period so far, plus the estimate at selection ($/day) for the time left until the period ends or the market closes, whichever comes first. Earnings from earlier periods aren't included: Kalshi pays each period on its own, and pays nothing for one under $1. This is the number the bot's $1 / $2 payout filters use."],
  "detail:fillchance": ["Chance of a fill", "The chance at least one sweep reaches our quotes before this period's earning time runs out. The bot replays the market's last 24 hours of public trades against the quotes it posts: sweeps that would have eaten the queue in front of us count as fills. Treating those as random events at that daily rate, P(at least one) = 1 - e^(-expected fills). Sudden jumps on news or data releases rarely show in the replay, so treat this as a floor."],
  "detail:net": ["Expected net this period", "Projected rewards for the period (earned so far + the estimate for the rest) minus the expected cost of the fills still to come (taker fee to exit + the adverse move, risk.adverse_move per contract). Fills already taken this period aren't subtracted: see the fill history for those."],
  "detail:minimum": ["$1 minimum", "Kalshi pays nothing for a period that earned under $1. The bar shows the bot's estimate for the current period against that minimum."],
  "kpi:Reward rate": ["Reward rate", "Estimated rewards earned over roughly the last 10 minutes, as dollars per hour. The small line underneath projects that rate over a full day."],
  "panel:trades": ["Fill history", "Every position the bot has held, from its first fill to the fill that made it flat. Entry and exit are average prices in the held side's terms (a NO bought at 40c shows 0.40). P&L is the cash those fills moved, fees included. A position with no closing fill is still open, or was settled by Kalshi (settlements aren't fills)."],
  "trade:How": ["Exit", "How the closing fills traded: passive = our resting exit order was filled (no crossing), crossed = we sold into the book (taker), mixed = some of each."],
  "trade:Per": ["P&L per contract", "The trade's P&L divided by the most contracts held, in cents."],
  "kpi:Daily reward rate": ["Daily reward rate", "The hourly reward rate (estimated rewards over roughly the last 10 minutes) projected over 24 hours. An estimate: Kalshi pays nothing for a market's period that earns under $1."],
  "kpi:Exposure": ["Exposure", "The total you paid for the positions you currently hold (their cost basis)."],
  "kpi:Resting": ["Resting orders", "How many of the bot's orders are sitting on the order book right now, waiting to be filled."],
  "kpi:Fills": ["Fills", "How many times one of the bot's orders traded, across all sessions. Rejected means Kalshi refused an order, usually because it would have traded immediately (the bot only posts orders that rest on the book)."],
  "kpi:Requotes": ["Requotes", "How many times the bot recalculated its quotes. It does this whenever an order book, one of its orders, or its position changes, and every few seconds anyway."],
  "kpi:Feed": ["Feed", "WS UP: the live market-data connection to Kalshi is working. If it drops, the bot can't see prices, so it pulls its orders until the connection is back. The second line says whether Kalshi trading is open."],
  "kpi:Halt": ["Halt", "A risk limit stopped the bot and it cancelled all its orders. The reason is shown. Restart the bot to resume."],

  // panels & charts
  "panel:markets": ["Markets", "One row per market the bot is quoting, then (dimmed, tagged PAST) markets it quoted earlier. Every price is in YES terms: 0.30 means $0.30 per contract, and a contract pays $1 if its outcome happens."],
  "panel:performance": ["Performance", "How this run is going over time. 5M/15M/1H windows scroll live with a sample every second (collected while this page is open); 1D/ALL show the journal history, sampled every 10 seconds, across every session. The small ▲/▼ figure is the change over the window. Hover a chart to see exact values."],
  "chart:rewards": ["Est. rewards earned", "Running total of the bot's estimated program rewards across every session. The steeper the line, the faster you're earning."],
  "chart:pnl": ["Session P&L", "Trading profit or loss over time, valued at mid prices (not including rewards). Dips usually mean you were filled and the price then moved against you. The dashed line is $0."],
  "chart:capital": ["Capital in use", "Money tied up in positions plus open orders, over time. The red dashed line is your max_capital budget."],
  "panel:blotter": ["Blotter", "Everything the bot did, newest first: orders placed, cancelled, shrunk, or rejected, fills, and quote changes. Use the buttons to filter by type and the box to filter by ticker."],
  "panel:orders": ["Resting orders", "The bot's orders sitting on the order book right now."],
  "panel:fills": ["Fills", "Trades that happened against the bot's orders this run."],
  "panel:log": ["Log", "The bot's own log messages. Yellow is a warning and red is an error. The level buttons filter what's shown."],
  "panel:selection": ["Market selection history", "Each time the bot's market scan (every minute) changed which markets it quotes: the markets it chose, best $/h first."],
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
  "col:$/d": ["$/day", "The $/hour rate projected over 24 hours. Kalshi pays nothing for a period under $1."],
  "col:$/period": ["$/period", "What this market should earn in the current program period: earned in it so far, plus the estimate at selection for the time left (until the period ends or the market closes). Green once it clears Kalshi's $1 minimum, red below it: Kalshi pays nothing for a period under $1. Hover for what's earned so far."],
  "col:Period left": ["Period left", "Time until the current program period ends and Kalshi pays it out. ⚑: the market closes before then, so earning stops at the close."],
  "col:Mix": ["Earnings mix", "The percentage is this market's share of your total $/hour across all markets right now. The bar is scaled to the top earner: its bar is full, and the others are relative to it."],
  "col:Paying": ["Paying", "The share of scored seconds in which your orders earned something. Below 100% means some seconds paid nothing: a side of the book was under Target, your order was too deep in the queue, or you had no order resting."],
  "col:Flags": ["Flags", "Anything unusual about this market right now. Hover a flag for details."],

  // flags
  "flag:PAST": ["PAST", "The bot isn't quoting this market now. It's here because the bot earned (or tried to earn) rewards in it earlier, in this session or a previous one. Earned and snapshot counts are totals across all sessions."],
  "panel:scanner": ["Market scanner", "Every market with an active liquidity program, estimated at the same size, 10 contracts per side, and ranked by $/day: which markets pay best. Research only: nothing here is traded, and the bot keeps choosing its markets with its own size and filters. Each estimate quotes the market the bot's way (its placement, offset, cushion and Target Size rules, but no loss cap, so every market is compared at the full 10), at the back of the queue, as if the current book held all day. Rescanned every few minutes (scanner.interval_seconds); the multiplier doesn't apply here."],
  "chip:scan-skipped": ["Hide filtered", "Hide markets the bot's own selection would skip anyway (excluded series, closing soon, spread or price outside the filters, or paying under Kalshi's $1 minimum this period)."],
  "scan:#": ["Rank", "Position by estimated $/day at 10 contracts per side, 1 = best."],
  "scan:Ticker": ["Ticker", "Kalshi's ID for the market. Hover for its title; click to copy."],
  "scan:$/day": ["Estimated $/day", "Reward per day for 10 contracts on each side, quoted the bot's way: your share of each side's first Target Size contracts x the program's daily reward, if the book stayed as it is now. Before any fill costs."],
  "scan:$/h": ["Estimated $/hour", "$/day divided by 24."],
  "scan:Net $/d": ["Net $/day", "Estimated reward minus the expected cost of getting filled, replayed from the market's recent public trades (each fill costs the taker fee to exit plus the price move that swept us). What the market would actually make."],
  "scan:Fills/d": ["Expected fills per day", "How many of the 10-lot quotes' contracts recent sweeps would have filled, per day (selection.trade_lookback_hours of trades). 0 means no recent sweep reached our price."],
  "scan:Return/d": ["Return per day", "$/day divided by the cash the two quotes lock: how hard each dollar of capital works here. The best use of a small budget ranks high on this, not just on $/day."],
  "scan:Capital": ["Capital", "Cash the 10-contract YES bid and NO bid would lock (10 x each leg's price)."],
  "scan:Period $": ["Period payout", "$/day x days left: what this would pay before the program period ends or the market closes. Kalshi pays nothing under $1 (grey)."],
  "scan:Left": ["Time left", "Until the program period ends or the market closes, whichever is first. Markets with minutes left (the 15-minute FX, metals and crypto series) show a huge $/day but pay little before they close."],
  "scan:Prog $/d": ["Program $/day", "The whole program's daily reward, shared among everyone quoting within Target Size."],
  "scan:Target": ["Target Size", "Only this many contracts on each side, from the best price down, earn rewards."],
  "scan:Bid": ["Best YES bid", "The live book's best YES bid."],
  "scan:Ask": ["Best YES ask", "The live book's best YES ask (= 1 − best NO bid)."],
  "scan:Sprd": ["Spread", "Ask − bid, in cents."],
  "scan:Our bid / ask": ["Where we'd quote", "The prices the scan's 10-lot quotes would rest at, in YES terms: the YES bid, and the NO bid shown as a YES ask."],
  "scan:Share Y/N": ["Reward share", "Estimated share of each side's rewards at those prices, joining the back of the queue."],
  "scan:Comp": ["Competition", "How crowded the rewarded depth is (see the Markets table's Comp column)."],
  "scan:Status": ["Status", "TRADING: the bot quotes this market now. candidate: passes the bot's filters, but ranked below the markets it picked (at its own size and budget). Otherwise, why the bot's selection would skip it."],
  "scan:trading": ["Trading", "The bot is quoting this market right now."],
  "scan:candidate": ["Candidate", "Passes the bot's filters; the bot picked higher-paying markets for its budget and slots (selection.max_markets), at its own size."],
  "scan:skip": ["Filtered out", "The bot's selection would skip this market whatever it pays: see the reason."],
  "chip:past": ["Past markets", "Show or hide markets the bot quoted earlier but isn't quoting now."],
  "blot:Reason": ["Reason", "Why the bot did it. reprice a -> b: the target price moved (the part in brackets is the strategy's reason and your estimated reward share there). new quote: nothing was resting on that side. stop quoting bid/ask: that side is switched off (e.g. position limit, thin book). shrink: fewer contracts wanted. top up a -> b: add an order at the same price to reach the wanted size, after a partial fill or when the size grew (e.g. a bigger budget); the resting order keeps its place in the queue. market paused / closes within / not selected / feed disconnected / shutting down: the whole market was pulled. shrunk ... to fit max_capital: the budget cut the order. Hover a cell for the full text."],
  "mix:bar": ["Share of total $/h", "Width = this market's $/hour divided by your total $/hour across all markets."],
  "chip:window": ["Chart window", "How much time the charts show. 5M, 15M and 1H scroll with time and use a sample every second (collected while this page is open; older points come from the journal every 10s). 1D and ALL show the journal history across every session."],
  "bar:rx": ["Receiving", "The dashboard is receiving live snapshots from the bot, pushed over its WebSocket. The LED blinks each time one arrives, about once a second; orders and fills show up the moment they happen."],
  "col:Fills/d": ["Expected fills per day", "How many of our contracts would have been filled per day, replaying the market's recent public trades (selection.trade_lookback_hours) against the quotes the bot posts. A fill comes from a sweep: a burst of selling bigger than the queue in front of us, too fast to dodge. Green: no recent sweep would have reached us. Red: the fills would cost at least half the reward. Hover a number for the daily cost. Updated by the market scan every minute."],
  "col:Net $/h": ["Net $/hour", "Estimated reward per hour, at the size the bot quotes, minus the expected cost of getting filled (each fill costs the taker fee to exit plus the price move that swept us). The bot scans every minute (loop.reselect_interval_seconds) and keeps its capital in the markets with the highest net $/h; a better market takes a slot once it pays selection.incumbent_bonus (10%) more. Hover for the daily figure. — means the market's trades weren't checked (e.g. it was only kept for a pause)."],
  "col:Comp": ["Competition", "How crowded the rewarded depth is. Only the first Target Size contracts on each side earn, so competitors don't shrink the reward pool; they squeeze it toward the top of the book. The number is the room: how many cents below the best bid other traders' orders fill up Target Size (on the tighter side). Plenty of room lets the bot rest deep, where fills are rare, and still earn. Shown for information: selection ranks purely by estimated $/day unless selection.competition_weight is set above 0. Kalshi's site shows a similar High/Medium/Low label, but the API doesn't provide it, so this is the bot's own measure."],
  "comp:low": ["Low competition", "Others' orders only reach Target Size more than 3¢ below the best bid (∞: they don't reach it at all). The bot can rest well below the top and still earn."],
  "comp:medium": ["Medium competition", "Others' orders reach Target Size 2–3¢ below the best bid."],
  "comp:high": ["High competition", "Others' orders fill Target Size within 1¢ of the best bid. To earn anything the bot must quote at the very top, where fills happen, and any better-priced order pushes it out of the counted depth."],
  "bar:multiplier": ["Capital multiplier (display only)", "Shows what this run would look like with N times the capital: every order, position, balance, capital and P&L figure is multiplied by N. Nothing changes in the bot. Rewards are NOT simply multiplied: your share of each book is ours / (ours + others), so N times the size gives share N·s / (N·s + 1 − s), a gain that shrinks as your share grows. 'rewards ×' is the resulting overall reward multiplier. Expected fills and their cost (Fills/d, Net $/d) are scaled N times, since bigger orders catch more of each sweep. Not modelled: more capital would also let the bot quote more markets; bigger orders may reach past a program's Target Size; other traders react."],
  "bar:session": ["Session", "How many times the bot has been started into this journal. Stopping and restarting the bot continues the same history: rewards, fills and past markets carry over. SINCE is when the first session started."],
  "flag:ok": ["ok", "Nothing unusual: the bot is quoting normally."],
  "flag:BLIND": ["BLIND", "The bot lost its trusted view of this order book (disconnected, or a missed update). It has pulled its orders here until a fresh copy of the book arrives."],
  "flag:PAUSED": ["PAUSED", "Quoting is paused for risk.cooldown_seconds, for one of two reasons. Too many contracts filled here in a short time (risk.fill_burst_contracts), which often means someone better informed is trading against you. Or the price moved sharply (risk.max_mid_move within mid_move_window_seconds), which usually means news."],
  "flag:CLOSING": ["CLOSING", "The market closes soon (within risk.close_buffer_seconds). The bot stops quoting, because prices can jump on last-minute news."],
  "type:exit": ["Exit", "An immediate-or-cancel order that crossed the book to close a filled position (risk.flatten_on_fill). It trades right away as a taker (paying the spread and taker fee) or cancels; it never rests."],
  "flag:FLATTENING": ["Flattening", "An order here was filled and the bot is closing the position right away (risk.flatten_on_fill): its quotes are pulled and an immediate-or-cancel order crosses the book to sell (or buy back) the contracts. It retries every few seconds until the position is zero."],
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
  "col:Q pos Y": ["Queue position, YES", "Queue rank of our best YES bid, out of the program's Target Size. Green means fully inside the top N contracts, which is where rewards are paid. Hover a badge for details."],
  "col:Q pos N": ["Queue position, NO", "Queue rank of our best NO bid (our YES ask), out of the program's Target Size. Green means fully inside the top N contracts, which is where rewards are paid. Hover a badge for details."],
  "rank:in": ["Inside Target Size", "The whole order is within the first Target Size contracts on its side, so it counts toward rewards every second, as long as both sides of the market reach Target Size."],
  "rank:partial": ["Partly inside", "Only part of this order is within the first Target Size contracts on its side. The rest earns nothing until orders ahead fill or cancel."],
  "rank:out": ["Outside Target Size", "More than Target Size contracts sit ahead of this order, so it earns no reward right now. On its next requote (within seconds) the bot moves it up to the lowest price that gets it back inside, as long as that stays within its safety limits (cushion, distance from mid, never crossing); if no such price exists, it pulls the order, since it would only carry fill risk."],
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
  ["reward+into target", "Moved into Target Size", "The bot's price (after offset_ticks / skew) sat just past the first Target Size contracts, where an order earns nothing, so it climbed the fewest ticks needed to get the whole order counted, never past its safety limits (cushion, distance from mid, no crossing)."],
  ["reward(kept: in target)", "Kept: counts by queue priority", "A new order couldn't get inside Target Size at any safe price, but this resting order still counts because it joined its price level earlier (orders that came later are behind it). Moving it would lose that spot."],
  ["outside Target Size", "Outside Target Size", "Even at the highest safe price (cushion, distance from mid, no crossing), a new order here would sit behind more than Target Size contracts and earn nothing, so this side isn't quoted."],
  ["reward(kept)", "Kept in place", "This order is still earning nearly as much as the best new price would, so the bot left it where it is. Moving an order sends it to the back of the queue."],
  ["reward", "Reward-optimized", "The bot tried each price between the best bid and the deepest price that still gets full credit, and picked the deepest (least likely to be filled) whose reward share is within 10% of the best."],
  ["join(thin)", "Join (thin book)", "The book is too thin to compute the program's reference price, so the bot joined the best bid."],
  ["join", "Join", "Placed at the current best bid (placement: join)."],
  ["improve", "Improve", "Placed one tick better than the best bid (placement: improve). More likely to be filled."],
  ["one-sided", "One-sided book", "Nobody is bidding on this side, so the bot priced off the other side of the book with a safety margin (one_sided_edge)."],
  ["loss cap", "Loss cap", "Even one contract here could lose more than quoting.max_loss_per_fill if the market jumped all the way against it, so this side isn't quoted."],
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
  if (kind === 'paused') {
    const [reason, left] = value.split('|')
    const secs = Math.max(0, Math.round(Number(left) || 0))
    const when = secs >= 60 ? `${Math.floor(secs / 60)}m ${secs % 60}s` : `${secs}s`
    return [
      'Paused',
      `Why: ${reason || 'a risk limit'}. Resumes in ${when}.`,
      'Its orders are pulled meanwhile. With loop.reselect_on_pause the bot looks for a better market to take this slot; if none passes the filters, this market keeps the slot and resumes after the pause.',
    ]
  }
  if (kind === 'ticker') {
    const [ticker, title] = value.split('|')
    return [ticker, title || '(no description)', 'Prices in this row are for YES on this outcome.']
  }
  return null
}
