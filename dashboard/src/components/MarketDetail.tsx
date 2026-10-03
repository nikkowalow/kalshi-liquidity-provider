import { type ReactNode, useEffect, useMemo, useState } from 'react'
import { apiUrl } from '../lib/api'
import type { Journal } from '../lib/useJournal'
import { num, pct, px, qty, sideClass, signClass, signedUsd, usd } from '../lib/format'
import { span, useNow } from '../lib/clock'
import { periodView } from '../lib/period'
import type { FillEvent, LegQuote, MarketRow, OrderEvent, OrderRow } from '../types'
import { CompetitionTag } from './CompetitionTag'
import { RankBadge } from './SidePanels'
import { Stamp } from './Stamp'
import { Ticker } from './Ticker'

/** Every order action in a market, from the bot's whole journal (GET /api/history/<ticker>). */
async function fetchHistory(ticker: string): Promise<OrderEvent[] | null> {
  try {
    const res = await fetch(apiUrl(`/api/history/${encodeURIComponent(ticker)}`))
    return res.ok ? ((await res.json()) as OrderEvent[]) : null
  } catch {
    return null
  }
}

const orderKey = (o: OrderEvent) => `${o.ts}|${o.order_id ?? ''}|${o.action}|${o.side}|${o.price}`

/** Kalshi's page for the market's series (it lists the current event and its markets). */
const kalshiUrl = (ticker: string) =>
  `https://kalshi.com/markets/${ticker.split('-')[0].toLowerCase()}`

const isoTs = (iso: string | null | undefined) => (iso ? Date.parse(iso) / 1000 : null)

function Field({ label, children, cls }: { label: string; children: ReactNode; cls?: string }) {
  return (
    <div className="md-field">
      <span className="md-label">{label}</span>
      <span className={cls}>{children}</span>
    </div>
  )
}

function Card({ title, children, wide }: { title: string; children: ReactNode; wide?: boolean }) {
  return (
    <section className={`md-card${wide ? ' wide' : ''}`}>
      <h3>{title}</h3>
      {children}
    </section>
  )
}

/** Progress toward Kalshi's minimum payout: nothing is paid out below it. */
function PayoutBar({ earned, minimum }: { earned: number; minimum: number }) {
  const done = earned >= minimum
  const share = Math.min(earned / minimum, 1)
  return (
    <div className="md-progress" data-help="detail:minimum">
      <div className={`md-progress-fill${done ? ' done' : ''}`} style={{ width: `${share * 100}%` }} />
      <span>
        {done
          ? `past the ${usd(minimum)} minimum: pays out`
          : `${usd(earned, 2)} of ${usd(minimum)} minimum · ${usd(minimum - earned, 2)} to go`}
      </span>
    </div>
  )
}

function LegLine({ name, q, cls }: { name: string; q?: LegQuote; cls: 'bid' | 'ask' }) {
  return (
    <tr>
      <td className={`l ${cls}`}>{name}</td>
      <td className={cls}>{q?.price == null ? '—' : px(q.price)}</td>
      <td className={cls}>{qty(q?.size)}</td>
      <td>{q?.share == null ? '—' : pct(q.share)}</td>
      <td className="l dim">{q?.reason ?? '—'}</td>
    </tr>
  )
}

/** Both bid ladders side by side: YES bids, and NO bids shown as YES asks (1 - price). */
function Ladder({ m, orders }: { m: MarketRow; orders: OrderRow[] }) {
  const b = m.book
  if (!b) return <div className="empty">no live book (market not quoted now)</div>
  if (!b.yes_levels || !b.no_levels) {
    return (
      <div className="empty">
        top of book only: {px(b.bid)} x {qty(b.bid_size)} / {px(b.ask)} x {qty(b.ask_size)} · restart
        the bot for the full ladder
      </div>
    )
  }
  const ours = (leg: 'yes' | 'no', price: number) =>
    orders
      .filter((o) => o.leg === leg && Math.abs(o.leg_price - price) < 1e-9)
      .reduce((n, o) => n + o.size, 0)
  const rows = Math.max(b.yes_levels.length, b.no_levels.length)
  return (
    <table className="md-ladder">
      <thead>
        <tr>
          <th className="l">ours</th>
          <th>size</th>
          <th>YES bid</th>
          <th>YES ask</th>
          <th>size</th>
          <th className="l">ours</th>
        </tr>
      </thead>
      <tbody>
        {Array.from({ length: rows }, (_, i) => {
          const y = b.yes_levels?.[i]
          const n = b.no_levels?.[i]
          const yOurs = y ? ours('yes', y[0]) : 0
          const nOurs = n ? ours('no', n[0]) : 0
          return (
            <tr key={i}>
              <td className="l bid">{yOurs ? `◀ ${qty(yOurs)}` : ''}</td>
              <td className={`bid${yOurs ? ' md-mine' : ''}`}>{y ? qty(y[1]) : ''}</td>
              <td className={`bid${yOurs ? ' md-mine' : ''}`}>{y ? px(y[0]) : ''}</td>
              <td className={`ask${nOurs ? ' md-mine' : ''}`}>{n ? px(1 - n[0]) : ''}</td>
              <td className={`ask${nOurs ? ' md-mine' : ''}`}>{n ? qty(n[1]) : ''}</td>
              <td className="l ask">{nOurs ? `${qty(nOurs)} ▶` : ''}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

function QueueTable({ orders }: { orders: OrderRow[] }) {
  if (!orders.length) return <div className="empty">no resting orders</div>
  return (
    <table>
      <thead>
        <tr>
          <th className="l">Side</th>
          <th>Price</th>
          <th>Qty</th>
          <th className="l">Rank / target</th>
          <th>Better px</th>
          <th>At level</th>
          <th>Full credit</th>
          <th>Age</th>
        </tr>
      </thead>
      <tbody>
        {orders.map((o) => (
          <tr key={o.order_id}>
            <td className={`l ${sideClass(o.side)}`}>
              {o.side === 'bid' ? 'YES BID' : 'NO BID'}
            </td>
            <td className={sideClass(o.side)}>{px(o.price)}</td>
            <td className={sideClass(o.side)}>{qty(o.size)}</td>
            <td className="l">
              <RankBadge o={o} />
            </td>
            <td className="dim">{qty(o.ahead_better)}</td>
            <td className="dim">{o.queue_ahead === null ? 'back?' : qty(o.queue_ahead)}</td>
            <td>
              {o.full_credit === null ? (
                <span className="dim">—</span>
              ) : o.full_credit ? (
                <span className="pos">YES</span>
              ) : (
                <span className="yl">DISC</span>
              )}
            </td>
            <td className="dim">{span(o.age)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Cash flow of the fills we have: what they made or lost once each position closed. */
function fillCash(fills: FillEvent[]): number {
  return fills.reduce((sum, f) => {
    const p = num(f.price) ?? 0
    const n = Number(f.count)
    const fee = num(f.fee) ?? 0
    return sum + (f.side === 'bid' ? -p * n : p * n) - fee
  }, 0)
}

function FillsTable({ fills }: { fills: FillEvent[] }) {
  if (!fills.length) return <div className="empty">no fills in this market</div>
  return (
    <table>
      <thead>
        <tr>
          <th className="l">Time</th>
          <th className="l">Side</th>
          <th>Price</th>
          <th>Qty</th>
          <th>Fee</th>
          <th>Pos after</th>
        </tr>
      </thead>
      <tbody>
        {fills.map((f) => (
          <tr key={`${f.ts}-${f.order_id}-${f.count}`}>
            <td className="l dim">
              <Stamp ts={f.ts} date />
            </td>
            <td className={`l ${sideClass(f.side)}`}>
              {(f.side ?? '').toUpperCase()}
              {f.is_taker && <span className="tag neg">TAKER</span>}
            </td>
            <td className={sideClass(f.side)}>{px(f.price)}</td>
            <td className={sideClass(f.side)}>{qty(f.count)}</td>
            <td className="dim">{usd(f.fee, 3)}</td>
            <td>{qty(f.post_position)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function OrderHistory({ orders }: { orders: OrderEvent[] }) {
  if (!orders.length) return <div className="empty">no order activity in the loaded history</div>
  return (
    <table>
      <thead>
        <tr>
          <th className="l">Time</th>
          <th className="l">Action</th>
          <th className="l">Side</th>
          <th>Price</th>
          <th>Qty</th>
          <th className="l">Why</th>
        </tr>
      </thead>
      <tbody>
        {orders.map((o, i) => (
          <tr key={`${o.ts}-${o.order_id ?? i}-${o.action}`}>
            <td className="l dim">
              <Stamp ts={o.ts} />
            </td>
            <td className={`l k-${o.action}`}>{o.action.toUpperCase()}</td>
            <td className={`l ${sideClass(o.side)}`}>{o.side.toUpperCase()}</td>
            <td className={sideClass(o.side)}>{px(o.price)}</td>
            <td className={sideClass(o.side)}>{qty(o.size)}</td>
            <td className="l dim md-why" title={o.error ?? o.reason}>
              {o.error ?? o.reason ?? ''}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

const STATUS_CLASS: Record<string, string> = { running: 'yl', 'awaiting payout': 'mg', 'paid out': 'pos' }

/** Kalshi's payout status for this market: the running period, and the last one that ended. */
function payoutStatus(periods: NonNullable<MarketRow['periods']>): string {
  const ended = periods.find((p) => p.status && p.status !== 'running')
  const running = periods.some((p) => p.status === 'running')
  const now = running ? 'this period: running, pays after it ends' : 'no running period tracked'
  return ended ? `${now} · last period: ${ended.status}` : now
}

function PeriodsTable({
  periods,
  minimum,
  current,
}: {
  periods: NonNullable<MarketRow['periods']>
  minimum: number
  current: string | null
}) {
  if (!periods.length) return <div className="empty">no tracked periods yet</div>
  return (
    <table>
      <thead>
        <tr>
          <th className="l">Period ends</th>
          <th>Earned (est.)</th>
          <th className="l">vs $1 minimum</th>
          <th>Paid (actual, matched)</th>
          <th className="l">Kalshi</th>
        </tr>
      </thead>
      <tbody>
        {periods.map((p) => {
          const end = isoTs(p.end)
          const earned = num(p.earned)
          const live = p.end !== null && p.end === current
          return (
            <tr key={p.end ?? 'legacy'}>
              <td className="l">
                {end === null ? <span className="dim">before per-period tracking</span> : <Stamp ts={end} date />}
                {live && <span className="tag yl">CURRENT</span>}
              </td>
              <td>{usd(earned, 4)}</td>
              <td className="l">
                {p.end === null ? (
                  <span className="dim">several periods</span>
                ) : earned !== null && earned >= minimum ? (
                  <span className="pos">reached</span>
                ) : (
                  <span className={live ? 'yl' : 'neg'}>{live ? 'in progress' : 'under: not paid'}</span>
                )}
              </td>
              <td className={p.paid ? 'pos' : 'dim'}>{p.paid == null ? '—' : usd(p.paid, 4)}</td>
              <td className={`l ${STATUS_CLASS[p.status ?? ''] ?? 'dim'}`}>{p.status ?? '—'}</td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

export function MarketDetail({
  market: m,
  orders,
  journal,
  payoutMinimum,
  onClose,
}: {
  market: MarketRow
  orders: OrderRow[]
  journal: Journal
  payoutMinimum: number
  onClose: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const fills = useMemo(
    () => journal.fills.filter((f) => f.ticker === m.ticker).reverse(),
    [journal.fills, m.ticker],
  )
  // The market's whole history from the journal, plus whatever arrives while the popup is open.
  const [full, setFull] = useState<OrderEvent[] | null>(null)
  useEffect(() => {
    let alive = true
    void fetchHistory(m.ticker).then((h) => alive && setFull(h ?? []))
    return () => {
      alive = false
    }
  }, [m.ticker])
  const history = useMemo(() => {
    const live = journal.orders.filter((o) => o.ticker === m.ticker)
    const seen = new Set((full ?? []).map(orderKey))
    return [...(full ?? []), ...live.filter((o) => !seen.has(orderKey(o)))].sort((a, b) => b.ts - a.ts)
  }, [full, journal.orders, m.ticker])
  const quoteChanges = useMemo(
    () => journal.quotes.filter((q) => q.ticker === m.ticker).length,
    [journal.quotes, m.ticker],
  )

  const now = useNow() / 1000
  const r = m.reward
  const periodEnd = isoTs(r.period_end)
  const periodStart = isoTs(r.period_start)
  const close = isoTs(m.close_time)
  const daysLeft = periodEnd === null ? null : Math.max(periodEnd - now, 0) / 86400
  const paying = m.snapshots ? m.paying_snapshots / m.snapshots : null
  const periods = m.periods ?? []
  const pv = periodView(m, now)
  const { earned: periodEarned, projected } = pv
  const flags = [
    m.inactive && 'not quoted now (past market)',
    !m.healthy && 'book not trusted (blind)',
    m.paused && `paused: ${m.pause_reason ?? 'risk limit'}`,
    m.unwind ? `exit resting at ${px(m.unwind.price)}` : m.flattening && 'closing out a filled position',
    m.near_close && 'closing soon: quotes pulled',
    m.reduce_only && 'reduce-only',
  ].filter(Boolean) as string[]

  return (
    <div className="md-backdrop" onClick={onClose}>
      <div className="md" role="dialog" aria-modal="true" aria-label={m.ticker} onClick={(e) => e.stopPropagation()}>
        <header className="md-head">
          <div>
            <Ticker value={m.ticker} />
            <span className="md-title">{m.title}</span>
            {flags.map((f) => (
              <span key={f} className="tag yl">
                {f}
              </span>
            ))}
          </div>
          <div className="md-actions">
            <a href={kalshiUrl(m.ticker)} target="_blank" rel="noreferrer">
              open on Kalshi ↗
            </a>
            <button type="button" className="chip" onClick={onClose} aria-label="close">
              ✕ esc
            </button>
          </div>
        </header>

        <div className="md-grid">
          <Card title="Our rewards">
            <PayoutBar earned={periodEarned} minimum={payoutMinimum} />
            <Field label="Paid by Kalshi (actual)" cls="pos">
              <span data-help="detail:paid">{m.paid == null ? 'nothing matched yet' : usd(m.paid, 4)}</span>
            </Field>
            <Field label="This period (est.)">{usd(periodEarned, 4)}</Field>
            <Field label="Earned all-time (est.)">{usd(m.earned, 4)}</Field>
            <Field label="Earning now">{usd(m.rate_per_hour, 3)}/h</Field>
            <Field label="Est. at selection">{m.est_daily_reward == null ? '—' : `${usd(m.est_daily_reward)}/day`}</Field>
            <Field label="Fill cost (est.)">
              {m.fill_cost_per_day == null ? '—' : `${usd(m.fill_cost_per_day)}/day · ${qty(m.est_fills_per_day)} fills/day`}
            </Field>
            <Field label="Net (est.)" cls={signClass(m.net_daily_reward)}>
              {m.net_daily_reward == null ? '—' : `${usd(m.net_daily_reward)}/day`}
            </Field>
            <Field label="Projected this period">
              <span data-help="detail:projected">{projected === null ? '—' : usd(projected)}</span>
            </Field>
            <Field label="Time scoring">
              {paying === null ? '—' : `${pct(paying)} of ${qty(m.snapshots)} snapshots`}
            </Field>
            <Field label="Avg score">{m.avg_score.toFixed(4)} / 2</Field>
          </Card>

          <Card title="Fill risk (rest of this period)">
            <Field label="Chance of a fill">
              <span data-help="detail:fillchance" className={pv.fillChance === null ? 'dim' : pv.fillChance >= 0.5 ? 'neg' : pv.fillChance >= 0.15 ? 'yl' : 'pos'}>
                {pv.fillChance === null ? '—' : pct(pv.fillChance)}
              </span>
            </Field>
            <Field label="Expected fills">
              {pv.fillEvents === null ? '—' : `${pv.fillEvents.toFixed(2)} · ${qty(Math.round((pv.fillContracts ?? 0) * 10) / 10)} contracts`}
            </Field>
            <Field label="Expected fill loss" cls={pv.fillLoss ? 'neg' : 'dim'}>
              {pv.fillLoss === null ? '—' : usd(pv.fillLoss)}
            </Field>
            <Field label="Rewards this period (proj.)">{projected === null ? '—' : usd(projected)}</Field>
            <Field label="Expected net this period" cls={signClass(pv.net)}>
              <span data-help="detail:net">{pv.net === null ? '—' : signedUsd(pv.net)}</span>
            </Field>
            <Field label="Time left">{pv.daysLeft === null ? '—' : span(pv.daysLeft * 86_400)}</Field>
            <div className="md-sub">
              {pv.fillBasis === 'live'
                ? 'from our resting orders now (real size and queue spot), refreshed each minute'
                : pv.fillBasis === 'planned'
                  ? "from the quote the bot planned at selection (no resting orders now)"
                  : 'no estimate yet'}
              {' · '}replays recent trades, counting on half the queue in front of us; sudden jumps
              (news, data releases) still aren&apos;t in it
            </div>
          </Card>

          <Card title="Reward program">
            <Field label="Total payout (period)" cls="yl">
              {r.period_reward == null ? '—' : usd(r.period_reward)}
            </Field>
            <Field label="Per day" cls="yl">
              {usd(r.per_day)}
            </Field>
            <Field label="Started">
              {periodStart === null ? '—' : <Stamp ts={periodStart} date />}
            </Field>
            <Field label="Ends">{periodEnd === null ? '—' : <Stamp ts={periodEnd} date />}</Field>
            <Field label="Days left">{daysLeft === null ? '—' : daysLeft.toFixed(2)}</Field>
            <Field label="Target size">{qty(r.target_size)} contracts</Field>
            <Field label="Discount factor">{r.discount_factor == null ? '—' : r.discount_factor}</Field>
            <Field label="Max per account">
              {r.max_reward_per_account == null ? 'no cap' : `${usd(r.max_reward_per_account)} / period`}
            </Field>
            <Field label="Paid out">
              <span data-help="detail:paidout">{payoutStatus(periods)}</span>
            </Field>
            <Field label="Competition">
              <CompetitionTag c={m.competition} />
            </Field>
            <Field label="Market closes">{close === null ? '—' : <Stamp ts={close} date />}</Field>
          </Card>

          <Card title="Position">
            <Field label="Position" cls={signClass(m.position)}>
              {m.position > 0 ? '+' : ''}
              {qty(m.position)} {m.position > 0 ? 'YES' : m.position < 0 ? 'NO' : ''}
            </Field>
            <Field label="Cost">{usd(m.exposure)}</Field>
            <Field label="Realized P&L" cls={signClass(m.realized_pnl)}>
              {signedUsd(m.realized_pnl)}
            </Field>
            <Field label="Fees paid">{usd(m.fees, 3)}</Field>
            <Field label="Fill cash flow (loaded)" cls={signClass(fillCash(fills))}>
              {signedUsd(fillCash(fills))} · {fills.length} fills
            </Field>
            <Field label="Size per side">{m.size == null ? '—' : `${qty(m.size)} contracts`}</Field>
            {m.unwind && (
              <>
                <h4>Exit order (passive)</h4>
                <Field label="Resting" cls={sideClass(m.unwind.side)}>
                  {m.unwind.side === 'ask' ? 'sell YES' : 'sell NO (YES bid)'} {qty(m.unwind.size)} @{' '}
                  {px(m.unwind.price)}
                </Field>
                <Field label="Entry">{px(m.unwind.entry)}</Field>
                <Field label="Crosses the book in">
                  {m.unwind.seconds_left == null ? '—' : span(m.unwind.seconds_left)}
                </Field>
                <div className="md-sub">{m.unwind.why}</div>
              </>
            )}
            <h4>Our quotes</h4>
            <table>
              <thead>
                <tr>
                  <th className="l">Leg</th>
                  <th>Price</th>
                  <th>Size</th>
                  <th>Share</th>
                  <th className="l">Why</th>
                </tr>
              </thead>
              <tbody>
                <LegLine name="YES bid" q={m.quotes.yes} cls="bid" />
                <LegLine name="NO bid" q={m.quotes.no} cls="ask" />
              </tbody>
            </table>
          </Card>

          <Card title="Order book">
            <div className="md-sub">
              mid {px(m.book?.mid)} · spread{' '}
              {m.book?.bid != null && m.book.ask != null ? `${((m.book.ask - m.book.bid) * 100).toFixed(1)}¢` : '—'}{' '}
              · depth {qty(m.book?.yes_depth)} / {qty(m.book?.no_depth)}
            </div>
            <Ladder m={m} orders={orders} />
          </Card>

          <Card title={`Program periods · ${periods.length}`} wide>
            <PeriodsTable periods={periods} minimum={payoutMinimum} current={r.period_end ?? null} />
          </Card>

          <Card title={`Queue · ${orders.length} resting`} wide>
            <QueueTable orders={orders} />
          </Card>

          <Card title={`Fills · ${fills.length}`} wide>
            <FillsTable fills={fills} />
          </Card>

          <Card
            title={`Order history · ${full === null ? 'loading all…' : `${history.length} actions`} · ${quoteChanges} quote changes`}
            wide
          >
            <OrderHistory orders={history} />
          </Card>
        </div>
      </div>
    </div>
  )
}
