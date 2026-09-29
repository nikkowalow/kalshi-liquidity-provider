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
├── cli.py                 # `klp` entry point: check / markets / status / run / cancel
├── config.py              # typed settings (YAML + env credentials)
├── log.py
├── core/                  # exchange-agnostic domain types
│   ├── types.py           #   Side, Leg, Quote, Decimal formatting
│   ├── pricing.py         #   per-market price grids (sub-penny tick bands)
│   └── orderbook.py       #   YES/NO bid ladders, own-order stripping
├── exchange/              # Kalshi Trade API v2
│   ├── auth.py            #   RSA-PSS / Ed25519 request signing
│   ├── rate_limit.py      #   token buckets matching Kalshi's read/write budgets
│   ├── client.py          #   async REST client (orders, books, portfolio, programs)
│   ├── models.py          #   typed API payloads
│   └── errors.py
├── strategy/              # pure pricing logic, no I/O (except selection)
│   ├── rewards.py         #   model of the incentive program's scoring rules
│   ├── quoting.py         #   quote engine: placement, skew, edge, limits
│   └── selection.py       #   rank markets by estimated reward
└── engine/                # order lifecycle and control loop
    ├── reconciler.py      #   desired quotes vs resting orders -> minimal actions
    ├── executor.py        #   batched cancels / decreases / creates, dry-run
    ├── risk.py            #   limits, breakers, kill switch
    └── bot.py             #   the main loop
config/                    # demo.yaml, prod.yaml
tests/                     # unit tests + end-to-end cycles against a fake exchange
```

## The loop

Every `interval_seconds` (2s by default):

1. Check exchange status (every 30s) and re-rank markets (every 10–15 min).
2. Take a snapshot in four reads: the bot's resting orders, positions, balance, and every
   quoted market's book in one batched call.
3. Update risk: session P&L, exposure, fill bursts.
4. Per market: remove the bot's own orders from the book (so it never chases itself), compute
   the quotes, and diff them against what's resting. Orders that are already right are left
   alone to keep queue priority, oversized orders are shrunk in place, and everything else is
   cancelled and replaced.
5. Send the batched plan: cancels, then decreases, then creates.

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

- **REST polling.** Reaction time is about one loop interval. Moving books and fills onto the
  WebSocket feed (`orderbook_delta`, `fill`) would cut adverse selection a lot, and is the most
  valuable next step.
- **Reward model is an estimate.** It uses the current book as if it lasted all day and assumes
  our orders join the back of the queue at their price level.
- **Fees are not modeled in pricing.** Check each series' maker fees; they come out of spread
  capture.
- **Session P&L** counts P&L since startup in the markets the bot trades, marked at mid. It
  subtracts fees on top of realized P&L, so it errs toward showing a bigger loss.
- **Volume ranking** in fallback mode scans the first `candidate_pool` open markets, not the whole
  exchange.
