# kalshi-lp

A market-making bot built to earn rewards from Kalshi's
[Liquidity Incentive Program](https://help.kalshi.com/en/articles/13823851-liquidity-incentive-program).
It rests two-sided post-only quotes in the markets with the richest rewards, sizes and places them
according to the program's scoring rules, and manages inventory and risk.

> **Read this first.** The rewards are real but so is the risk. Resting orders get filled, and they
> tend to get filled right before the price moves against you (adverse selection). Rewards only
> turn into profit if they exceed those losses and fees. Run in demo, then run in production with
> small size, and watch the P&L before scaling up.

## How it makes money

Kalshi pays a daily reward pool per eligible market to traders whose resting orders sit near the
top of the book. Once per second, at a random moment, Kalshi snapshots the book:

- The **YES bid** book and the **NO bid** book are scored separately.
- Walking down from the best bid, orders qualify until the side's depth reaches the program's
  **Target Size**. If a side has less depth than Target Size, that snapshot pays nobody.
- The **reference price** is the level where cumulative depth first reaches Target Size / 5.
  Orders at or above it get full credit. Orders `n` ticks below it are discounted by
  `discount_factor^n`.
- Your share of each side is your score over the side's total. You are paid your share of the
  period's snapshot scores.

The default placement (`placement: reward`) runs that scoring model over every price from the
reference price up to the best bid and picks the **deepest price whose reward share is within
`share_tolerance` (10%) of the best achievable**. Deeper orders are less likely to be filled, so
this gives up a little reward for a lot less risk. It does not simply bid *at* the reference
price: if the queue ahead already fills the Target Size, an order at the back of that queue
earns nothing.

A YES `ask` rests on the NO book (a YES ask at 0.52 is a NO bid at 0.48), so one bid plus one ask
per market covers both reward sides.

## Estimating rewards before you trade

`klp rewards` pulls every active liquidity program and samples the order books several times.
For each order size, it prices the quotes the bot would actually post and scores them with the
program's rules. It needs no API key.

```bash
.venv/bin/klp rewards -c config/prod.yaml                      # real production programs
.venv/bin/klp rewards -c config/prod.yaml --sizes 10,25,50 --top 40 --samples 5
```

```
TICKER                          PROGRAM  TARGET     BOOK    QUOTE  SHARE   @10    @50   @100   @250
                                  $/day           bid/ask  bid/ask        est. $/day at each order size
KXAAAGASM-26SEP30-4.44           830.05    1000  .14/.15  .11/.16   2.5%  20.81  94.72 196.26 413.63
```

Read these as upper bounds. They assume competitors don't respond, that you join the back of each
queue, and they leave out losses from fills. Each order size is posted on **both** sides, so
check the capital it ties up.

While the bot runs, a **live earnings tracker** replays Kalshi's scoring once per second, at a
random moment, on the live book. It uses your orders' real queue positions (from Kalshi's
queue-position endpoint) and credits `score / 2 × reward_per_day / 86,400` per snapshot, which
is exactly what the program formula pays for the same book. Every minute it logs estimated
earnings and the current $/hour, per market. In dry-run, it scores the quotes the bot *would*
post.

## Quick start (demo)

**1. Get demo API keys.** Create an account at <https://demo.kalshi.co> (mock funds). Under
Account & security → API keys, create a key and save the private key file.

**2. Install.**

```bash
make install                 # creates .venv and installs with dev tools
cp .env.example .env
mkdir -p secrets && mv ~/Downloads/<your-key>.key secrets/demo.key
# edit .env: set KALSHI_DEMO_KEY_ID
```

**3. Verify, preview, dry-run, then go live on demo.**

```bash
.venv/bin/klp check                # credentials + connectivity + balance
.venv/bin/klp markets              # which markets would be quoted, estimated $/day
.venv/bin/klp run                  # dry-run: logs the quotes it would place, sends nothing
.venv/bin/klp run --duration 60    # ...and stop after 60 seconds
.venv/bin/klp run --live           # places real orders on demo (mock money)
.venv/bin/klp status               # positions and the bot's resting orders
.venv/bin/klp cancel               # cancel the bot's orders
```

`Ctrl-C` stops the bot and cancels all of its orders. The `Makefile` wraps each of these
(`make check`, `make markets`, `make demo`, `make demo-live`, ...).

Demo usually has no incentive programs (it had none when this was written). With
`fallback_to_volume: true` (the demo default), the bot then quotes the most-traded markets with a
spread of at most `max_spread`, so you can still exercise the whole order lifecycle. Most demo
markets are placeholder 1¢/99¢ books, which that filter skips. Demo prices and fills are not
representative of production.

## Live dashboard

`klp run` writes a journal to `runs/<env>-<mode>/` (for example `runs/prod-live/`):
`events.jsonl` (orders placed, cancelled, shrunk, or rejected, fills, quote changes, market
selections, and every log line), `metrics.jsonl` (totals every 10 seconds, for the charts) and
`state.json` (a full snapshot, rewritten every second).

The journal is persistent. Stopping and restarting the bot, for a code or config change, say,
appends to the same journal as a new *session*: estimated rewards, per-market earnings, fills
and the markets it was in carry over, so the dashboard shows one continuous history. Use
`klp run --journal NAME` to start a separate history instead. Journals from older versions (one
directory per run) are imported the first time and moved to `runs/_imported/`.

The dashboard reads the journal live:

```bash
make dashboard          # builds the React app, serves it + the journal API on http://127.0.0.1:8050
```

The UI is a React + TypeScript app built with Vite, in `dashboard/src`. To work on it with hot
reload, run `make dashboard-api` in one terminal and `make dashboard-dev` in another, then open
http://localhost:5173 (Vite proxies `/api` to the Python server).

It shows status and KPIs (balance, capital vs. budget, session P&L, estimated rewards and rate);
per-market book, quotes, position, program, reward share, and earnings; charts of rewards, P&L,
and capital over time; a filterable blotter; resting orders and fills; the log; market-selection
history; and the run's config. *Follow latest* shows the most recently updated journal, and
the others (prod-live, demo-dry, ...) stay selectable. The server is standard-library only and listens on localhost only.

## Going to production

```bash
# .env: KALSHI_PROD_KEY_ID and KALSHI_PROD_PRIVATE_KEY_PATH
.venv/bin/klp check   -c config/prod.yaml
.venv/bin/klp markets -c config/prod.yaml
.venv/bin/klp run     -c config/prod.yaml                          # dry-run first
.venv/bin/klp run     -c config/prod.yaml --live --confirm-prod    # real money
```

Demo and production credentials use different environment variables, so a demo config cannot
load production keys. Live production trading needs both `--live` and `--confirm-prod`.

Program eligibility: rewards are for most U.S. Kalshi members. Payouts under $1.00 are not paid,
and a verified SSN is needed above IRS reporting thresholds. See Kalshi's rules for details.

## Project layout

```
src/kalshi_lp/
├── cli.py                 # `klp`: check / markets / rewards / status / run / cancel
├── config.py              # typed settings (YAML + env credentials)
├── journal.py             # per-run event log + state snapshot for the dashboard
├── log.py
├── core/                  # exchange-agnostic domain types
│   ├── types.py           #   Side, Leg, Quote, Decimal formatting
│   ├── pricing.py         #   per-market price grids (sub-penny tick bands)
│   └── orderbook.py       #   YES/NO bid ladders, own-order stripping
├── exchange/              # Kalshi Trade API v2
│   ├── auth.py            #   RSA-PSS / Ed25519 request signing
│   ├── rate_limit.py      #   token buckets matching Kalshi's read/write budgets
│   ├── client.py          #   async REST client (orders, books, portfolio, programs)
│   ├── ws.py              #   WebSocket client: signed connect, commands, seq checks, reconnect
│   ├── models.py          #   typed API payloads
│   └── errors.py
├── feed/                  # real-time state from the WebSocket
│   ├── state.py           #   live books, our orders, positions, queue positions
│   └── stream.py          #   channel subscriptions -> state; resync on gaps
├── strategy/              # pricing logic, no I/O except selection/estimates
│   ├── rewards.py         #   the incentive program's scoring rules, queue-aware
│   ├── quoting.py         #   quote engine: placement, stickiness, skew, edge, limits
│   ├── selection.py       #   rank markets by estimated reward
│   └── estimator.py       #   `klp rewards`: $/day by order size across live programs
└── engine/                # order lifecycle and control loops
    ├── reconciler.py      #   desired quotes vs resting orders -> minimal actions
    ├── executor.py        #   batched cancels / decreases / creates, dry-run
    ├── risk.py            #   limits, breakers, kill switch
    ├── reward_tracker.py  #   live per-second reward scoring
    └── bot.py             #   quoting / maintenance / reward loops
config/                    # demo.yaml, prod.yaml
dashboard/                 # live dashboard: React/Vite app (src/) + stdlib API server.py
tests/                     # unit, WebSocket (local server), end-to-end against a fake exchange
```

## How it runs

Market data and account updates stream over the WebSocket: `orderbook_delta` for the quoted
markets, plus `user_orders`, `fill`, and `market_positions`. Three loops share that live state:

- **Quoting** wakes when a book, one of the bot's orders, or a position changes, debounced by
  `requote_min_interval_seconds` (0.25s), and at least every `heartbeat_seconds`. It requotes
  only the markets that changed. For each one it removes the bot's own orders from the book (so
  it never chases itself), computes quotes, and diffs them against what's resting. Orders that
  are already right keep their queue priority, oversized orders are shrunk in place, and the
  rest are cancelled and replaced over REST.
- **Maintenance** checks exchange status and cross-checks orders, positions, and balance over
  REST every 15s, which corrects any drift in the streamed state. It also refreshes queue
  positions, re-ranks markets every 10–15 min, and logs reward estimates.
- **Reward sampling** scores one snapshot per second (see above).

**Sticky quotes.** Moving an order sends it to the back of the queue. So a resting order keeps
its price while its reward share, using its real queue position, stays within `share_tolerance`
of the best new price. The one exception: the bot never holds a price more aggressive than the
one it would choose now.

**Blind means flat.** If the WebSocket disconnects or skips a sequence number, the affected books
are marked untrusted and the bot pulls its quotes there until a fresh snapshot arrives.

## Market selection

| Mode | Picks |
|---|---|
| `incentives` (default) | Markets with active liquidity programs, ranked by estimated $/day for our quote size |
| `volume` | The most-traded open markets (the fallback when no programs are active) |
| `tickers` | Exactly the tickers listed in `selection.tickers` |

Every mode skips markets that close within `min_seconds_to_close`, have a spread wider than
`max_spread` (a wide book has no trustworthy mid), or have a mid outside
`min_mid_price`–`max_mid_price`. At most `max_per_series` markets come from one series, because
markets in a series tend to move together. When a market drops out of the selection while the bot
still holds a position there, the bot keeps quoting it in reduce-only mode until the position is
flat.

**Fill risk** (`fill_risk`, on by default). A resting order can't be made fill-proof. What fills
it is a sweep: a burst of selling that eats every contract queued in front of it faster than the
bot can move. For the best `fill_risk_pool` candidates, the bot replays the last
`trade_lookback_hours` of the market's public trades against the quotes it would post. It groups
prints within `sweep_window_seconds` into sweeps, and counts how much of each sweep would get past
the depth ahead of us. Each such fill costs the taker fee to exit plus `adverse_move`. Markets are
then ranked by **net $/day** (estimated reward minus that cost), and any at or below
`min_net_daily_reward` are dropped. `klp markets` and the dashboard show the fills/day and net
figures.

**Best $/h, continuously.** The bot rescans every `reselect_interval_seconds` without pausing its
quoting (the program and market list is reused for `catalog_refresh_seconds`; order books and
trades are fresh each scan). With `quoting.auto_size`, rewards are estimated at the size the bot
will really quote, and the capital goes to the highest net $/h markets first, each at `max_size`,
until `max_capital` is used. Spreading the budget over more markets would only average in worse
ones. A better market takes a slot once it pays `incumbent_bonus` (10%) more; smaller gaps are
estimate noise. With `protect_unpaid`, a market holding unpaid earnings below Kalshi's $1 minimum
counts what leaving would forfeit.

## Quoting

Each leg (YES bid, NO bid) is priced the same way in its own terms:

| Step | Setting | Effect |
|---|---|---|
| Base price | `placement`, `share_tolerance` | `reward` (default, see above), `join` the best bid, or `improve` it by one tick |
| Back off | `offset_ticks` | Extra ticks behind; trades reward for safety |
| Inventory skew | `skew_per_contract`, `max_skew` | When long, lower the bid and the ask so the bot buys less and sells sooner |
| Fair-value guard | `min_edge` | Never quote closer than this to the mid |
| No crossing | always | Never at or through the opposite best; orders are also `post_only` |
| Price band | `min_price`, `max_price` | Skip long-shot tails |
| Size | `size`, `max_position_per_market` | Capped so a fill can't breach the position limit |

## Risk controls

| Control | Setting | Action |
|---|---|---|
| **Capital budget** | `max_capital` | One dollar cap on position cost plus cash locked in resting orders. New orders are shrunk or skipped to fit. Fills only convert locked cash into position cost, so the total can't grow past the cap. Orders selling contracts you already hold are free |
| Position limit | `max_position_per_market` | Stop quoting the side that adds to the position |
| Exposure / balance | `max_total_exposure`, `min_balance` | Reduce-only mode |
| Fill-burst breaker | `fill_burst_contracts` in `fill_burst_window_seconds` | Pause that market for `cooldown_seconds` |
| Close buffer | `close_buffer_seconds` | Pull quotes before a market closes |
| Error breaker | `max_consecutive_errors` | Pull all quotes and pause |
| Session loss | `max_session_loss` | Cancel everything and exit (code 2) |
| Exchange order group | `order_group_contracts_limit` | Kalshi cancels all bot orders if more than N contracts match in 15s |
| Exchange pause | always | Orders set `cancel_order_on_pause`; the bot pulls quotes while trading is halted |
| Shutdown | always | SIGINT/SIGTERM cancel the bot's orders; stale bot orders are cleaned up on startup |

The bot only touches orders whose `client_order_id` starts with its prefix (`klp-`), so orders you
place by hand are left alone. `klp cancel --all` is the one exception: it cancels every order on
the account.

## Development

```bash
make test        # pytest
make lint        # ruff
make typecheck   # mypy --strict
make fmt
```

## Known limitations and next steps

- **Reward estimates are estimates.** The live tracker samples at its own random instant, not
  Kalshi's, and refreshes queue positions every 10s. Kalshi's docs don't say whether a queue
  position counts contracts at better prices, so the tracker reads it as "within the price level,"
  capped at that level's size. If it actually includes better prices, the tracker undercounts.
  `klp rewards` also assumes competitors don't react.
- **Orders go over REST.** Market data is streamed, but orders are placed with REST calls
  (tens of ms). Kalshi's FIX gateway would be faster.
- **Fees are not modeled in pricing.** Check each series' maker fees; they come out of spread
  capture.
- **Session P&L** counts P&L since startup in the markets the bot trades, marked at mid. It
  subtracts fees on top of realized P&L, so it errs toward showing a bigger loss.
- **Volume ranking** in fallback mode scans up to `candidate_pool` open markets (25,000 by
  default).
