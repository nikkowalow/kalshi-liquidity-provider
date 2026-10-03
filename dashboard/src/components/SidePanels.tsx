import { memo, useMemo, useRef, useState } from 'react'
import type { Journal } from '../lib/useJournal'
import { px, qty, sideClass, signClass, signedUsd, usd } from '../lib/format'
import { type Accessors, sortRows, useSort } from '../lib/sort'
import { useFreshKeys } from '../lib/useFreshKeys'
import { type RoundTrip, roundTrips } from '../lib/roundTrips'
import { useVirtualRows } from '../lib/useVirtualRows'
import type { FillEvent, FillRiskEvent, MarketsEvent, OrderRow, RiskEstimate } from '../types'
import { competitionRoom } from '../lib/competition'
import { CompetitionTag } from './CompetitionTag'
import { FillsCell, NetCell } from './FillRiskCells'
import { span } from '../lib/clock'
import { Flash } from './Flash'
import { Empty, Panel, PanelHeader } from './Panel'
import { PadRow } from './PadRow'
import { SortTh } from './SortTh'
import { Stamp } from './Stamp'
import { Ticker } from './Ticker'

const TARGET_CLASS: Record<OrderRow['in_target'], string> = {
  in: 'tag pos',
  partial: 'tag yl',
  out: 'tag neg',
  unknown: 'tag dim',
}

/** "#121 / 1000": our first contract's position on its side vs the program's Target Size. */
export function RankBadge({ o }: { o: OrderRow }) {
  if (o.ahead_total === null) return <span className="dim">—</span>
  const rank = o.ahead_total + 1
  return (
    <span className={TARGET_CLASS[o.in_target]} data-help={`rank:${o.in_target}`}>
      <Flash value={rank}>#{qty(rank)}</Flash>
      {o.target_size !== null && <span className="dim"> / {qty(o.target_size)}</span>}
    </span>
  )
}

const ORDER_ACCESSORS: Accessors<OrderRow> = {
  Ticker: (o) => o.ticker,
  Side: (o) => o.side,
  Price: (o) => o.price,
  Qty: (o) => o.size,
  Rank: (o) => (o.ahead_total === null ? null : o.ahead_total + 1),
  Better: (o) => o.ahead_better,
  Level: (o) => o.queue_ahead,
  Credit: (o) => o.full_credit,
  Age: (o) => o.age,
}

const FILL_ACCESSORS: Accessors<FillEvent> = {
  Time: (f) => f.ts,
  Ticker: (f) => f.ticker,
  Side: (f) => f.side,
  Price: (f) => (f.price === null ? null : Number(f.price)),
  Qty: (f) => Number(f.count),
  Pos: (f) => (f.post_position === null ? null : Number(f.post_position)),
}

export function OrdersAndFills({ orders, journal }: { orders: OrderRow[]; journal: Journal }) {
  const orderSort = useSort('orders')
  const byTicker = [...orders].sort((a, b) => a.ticker.localeCompare(b.ticker) || a.side.localeCompare(b.side))
  const sorted = sortRows(byTicker, ORDER_ACCESSORS, orderSort.state)
  const freshOrders = useFreshKeys(sorted.map((o) => o.order_id))
  const inTop = orders.filter((o) => o.in_target === 'in').length
  return (
    <section className="panel span-5">
      <PanelHeader
        title="Resting orders"
        help="panel:orders"
        note={orders.length ? `${orders.length} · ${inTop} fully inside Target Size` : '0'}
      />
      <div className="body h-sm">
        {sorted.length === 0 ? (
          <Empty>none</Empty>
        ) : (
          <table>
            <thead>
              <tr>
                <SortTh k="Ticker" sorter={orderSort} align="l" text>
                  Ticker
                </SortTh>
                <SortTh k="Side" sorter={orderSort} align="l" help="ord:Side" text>
                  Side
                </SortTh>
                <SortTh k="Price" sorter={orderSort} help="ord:Price">
                  Price
                </SortTh>
                <SortTh k="Qty" sorter={orderSort} help="ord:Qty">
                  Qty
                </SortTh>
                <SortTh k="Rank" sorter={orderSort} align="l" help="ord:Rank">
                  Rank / target
                </SortTh>
                <SortTh k="Better" sorter={orderSort} help="ord:Better">
                  Better px
                </SortTh>
                <SortTh k="Level" sorter={orderSort} help="ord:Queue">
                  At level
                </SortTh>
                <SortTh k="Credit" sorter={orderSort} help="ord:Credit">
                  Full credit
                </SortTh>
                <SortTh k="Age" sorter={orderSort} help="ord:Age">
                  Age
                </SortTh>
              </tr>
            </thead>
            <tbody>
              {sorted.map((o) => (
                <tr key={o.order_id} className={freshOrders.has(o.order_id) ? 'row-new' : undefined}>
                  <td className="l">
                    <Ticker value={o.ticker} />
                  </td>
                  <td className={`l ${sideClass(o.side)}`}>{o.side === 'bid' ? 'BID' : 'ASK'}</td>
                  <td className={sideClass(o.side)}>{px(o.price)}</td>
                  <td className={sideClass(o.side)}>
                    <Flash value={o.size}>{qty(o.size)}</Flash>
                  </td>
                  <td className="l">
                    <RankBadge o={o} />
                  </td>
                  <td className="dim">
                    <Flash value={o.ahead_better}>{qty(o.ahead_better)}</Flash>
                  </td>
                  <td className="dim">
                    <Flash value={o.queue_ahead}>{o.queue_ahead === null ? 'back?' : qty(o.queue_ahead)}</Flash>
                  </td>
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
        )}
      </div>
      <FillsBlock fills={journal.fills} risks={journal.fillRisks} />
    </section>
  )
}

const fillKey = (f: FillEvent) => `${f.ts}-${f.order_id}-${f.count}`

/** Fills only change when one arrives: memoized, and only visible rows are drawn. */
const FillsBlock = memo(function FillsBlock({
  fills,
  risks,
}: {
  fills: FillEvent[]
  risks: FillRiskEvent[]
}) {
  const [tab, setTab] = useState<'recent' | 'history'>('recent')
  const trips = useMemo(() => roundTrips(fills), [fills])
  const closed = trips.filter((t) => t.pnl !== null)
  const total = closed.reduce((sum, t) => sum + (t.pnl ?? 0), 0)
  const tools = (
    <>
      <button type="button" className={`chip${tab === 'recent' ? ' on' : ''}`} onClick={() => setTab('recent')}>
        fills
      </button>
      <button
        type="button"
        className={`chip${tab === 'history' ? ' on' : ''}`}
        data-help="panel:trades"
        onClick={() => setTab('history')}
      >
        history
      </button>
    </>
  )
  const note =
    tab === 'recent' ? (
      String(fills.length)
    ) : (
      <>
        {trips.length} trades · {closed.length} closed ·{' '}
        <span className={signClass(total)}>{signedUsd(total)}</span>
      </>
    )
  return (
    <>
      <PanelHeader title="Fills" help="panel:fills" note={note} tools={tools} />
      {tab === 'recent' ? <RecentFills fills={fills} risks={risks} /> : <TradeHistory trips={trips} />}
    </>
  )
})

/** Each fill's fill-risk record: the one for its order, recorded within a minute of it. */
function riskFor(f: FillEvent, byOrder: Map<string, FillRiskEvent[]>): FillRiskEvent | undefined {
  const records = byOrder.get(`${f.ticker}|${f.order_id}`) ?? []
  return records.find((r) => Math.abs(r.fill_ts - f.ts) < 60) ?? records[0]
}

const dailyChance = (e: RiskEstimate | null | undefined) =>
  e?.events_per_day == null ? null : 1 - Math.exp(-e.events_per_day)

function RiskCell({ e }: { e: RiskEstimate | null | undefined }) {
  const chance = dailyChance(e)
  if (!e || chance === null) return <td className="dim">—</td>
  const title = [
    `${(e.events_per_day ?? 0).toFixed(2)} fill events/day · ${qty(e.fills_per_day)} contracts/day · ${usd(e.cost_per_day)}/day`,
    `order size ${qty(e.size)} · ${e.basis === 'live' ? 'live: the orders resting then' : 'the quote planned at selection'}`,
    `estimated ${new Date(e.ts * 1000).toLocaleString()}`,
    e.approx ? 'approx: old estimate, from contracts/day ÷ order size (a lower bound)' : '',
  ]
    .filter(Boolean)
    .join('\n')
  return (
    <td className={chance >= 0.5 ? 'neg' : chance >= 0.15 ? 'yl' : 'pos'} title={title}>
      {(chance * 100).toFixed(1)}%{e.approx ? '≈' : ''}
    </td>
  )
}

function RecentFills({ fills: all, risks }: { fills: FillEvent[]; risks: FillRiskEvent[] }) {
  const byOrder = useMemo(() => {
    const m = new Map<string, FillRiskEvent[]>()
    for (const r of risks) {
      const k = `${r.ticker}|${r.order_id}`
      m.set(k, [...(m.get(k) ?? []), r])
    }
    return m
  }, [risks])
  const fillSort = useSort('fills')
  const fills = useMemo(
    () => sortRows([...all].reverse(), FILL_ACCESSORS, fillSort.state),
    [all, fillSort.state],
  )
  const keys = useMemo(() => fills.map(fillKey), [fills])
  const freshFills = useFreshKeys(keys)
  const body = useRef<HTMLDivElement>(null)
  const win = useVirtualRows(body, fills.length)
  return (
    <div className="body h-sm" ref={body}>
      {fills.length === 0 ? (
        <Empty>no fills yet</Empty>
      ) : (
        <table className="vt">
          <thead>
            <tr>
              <SortTh k="Time" sorter={fillSort} align="l">
                Time
              </SortTh>
              <SortTh k="Ticker" sorter={fillSort} align="l" text>
                Ticker
              </SortTh>
              <SortTh k="Side" sorter={fillSort} align="l" help="fill:Side" text>
                Side
              </SortTh>
              <SortTh k="Price" sorter={fillSort} help="fill:Price">
                Price
              </SortTh>
              <SortTh k="Qty" sorter={fillSort} help="fill:Qty">
                Qty
              </SortTh>
              <SortTh k="Pos" sorter={fillSort} help="fill:Pos after">
                Pos after
              </SortTh>
              <th data-help="fill:risk entry">Risk @entry</th>
              <th data-help="fill:risk fill">Risk @fill</th>
            </tr>
          </thead>
          <tbody>
            <PadRow height={win.padTop} cols={8} />
            {fills.slice(win.start, win.end).map((f) => (
              <FillLine key={fillKey(f)} f={f} risk={riskFor(f, byOrder)} fresh={freshFills.has(fillKey(f))} />
            ))}
            <PadRow height={win.padBottom} cols={8} />
          </tbody>
        </table>
      )}
    </div>
  )
}

const TRIP_ACCESSORS: Accessors<RoundTrip> = {
  Opened: (t) => t.opened,
  Closed: (t) => t.closed,
  Ticker: (t) => t.ticker,
  Held: (t) => t.held,
  Qty: (t) => t.size,
  Entry: (t) => t.entry,
  Exit: (t) => t.exit,
  Held_for: (t) => (t.closed === null ? null : t.closed - t.opened),
  Fees: (t) => t.fees,
  PnL: (t) => t.pnl,
  Per: (t) => (t.pnl === null || !t.size ? null : t.pnl / t.size),
  How: (t) => t.exitHow,
}

const EXIT_CLASS = { passive: 'pos', crossed: 'neg', mixed: 'yl' } as const

/** One row per position: what we paid, what we got, and what it made or lost. */
function TradeHistory({ trips: all }: { trips: RoundTrip[] }) {
  const sorter = useSort('trades')
  const trips = useMemo(() => sortRows(all, TRIP_ACCESSORS, sorter.state), [all, sorter.state])
  if (!trips.length) {
    return (
      <div className="body h-sm">
        <Empty>no trades yet</Empty>
      </div>
    )
  }
  const th = (k: string, label: string, align?: 'l', text?: boolean) => (
    <SortTh k={k} sorter={sorter} align={align} text={text} help={`trade:${k}`}>
      {label}
    </SortTh>
  )
  return (
    <div className="body h-sm">
      <table>
        <thead>
          <tr>
            {th('Opened', 'Opened', 'l')}
            {th('Closed', 'Closed', 'l')}
            {th('Ticker', 'Ticker', 'l', true)}
            {th('Held', 'Held', 'l', true)}
            {th('Qty', 'Qty')}
            {th('Entry', 'Entry')}
            {th('Exit', 'Exit')}
            {th('Held_for', 'Held for')}
            {th('Fees', 'Fees')}
            {th('PnL', 'P&L')}
            {th('Per', 'Per ctr')}
            {th('How', 'Exit', 'l', true)}
          </tr>
        </thead>
        <tbody>
          {trips.map((t) => (
            <tr key={t.key}>
              <td className="l dim">
                <Stamp ts={t.opened} date />
              </td>
              <td className="l dim">
                {t.closed === null ? <span className="yl">open / settled</span> : <Stamp ts={t.closed} date />}
              </td>
              <td className="l">
                <Ticker value={t.ticker} />
              </td>
              <td className={`l ${t.held === 'YES' ? 'bid' : 'ask'}`}>
                {t.held}
                {!t.entryMaker && (
                  <span className="tag neg" data-help="fill:TAKER">
                    TAKER
                  </span>
                )}
              </td>
              <td>{qty(t.size)}</td>
              <td>{px(t.entry)}</td>
              <td>{t.exit === null ? '—' : px(t.exit)}</td>
              <td className="dim">{t.closed === null ? '—' : span(t.closed - t.opened)}</td>
              <td className="dim">{usd(t.fees, 3)}</td>
              <td className={signClass(t.pnl)}>{t.pnl === null ? '—' : signedUsd(t.pnl)}</td>
              <td className={signClass(t.pnl)}>
                {t.pnl === null || !t.size ? '—' : `${((t.pnl / t.size) * 100).toFixed(1)}¢`}
              </td>
              <td className={`l ${t.exitHow ? EXIT_CLASS[t.exitHow] : 'dim'}`}>{t.exitHow ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

const FillLine = memo(function FillLine({
  f,
  risk,
  fresh,
}: {
  f: FillEvent
  risk: FillRiskEvent | undefined
  fresh: boolean
}) {
  return (
    <tr className={fresh ? 'row-fill' : undefined}>
      <td className="l dim">
        <Stamp ts={f.ts} />
      </td>
      <td className="l">
        <Ticker value={f.ticker} />
      </td>
      <td className={`l ${sideClass(f.side)}`}>
        {(f.side ?? '').toUpperCase()}
        {f.is_taker && (
          <span className="tag neg" data-help="fill:TAKER">
            TAKER
          </span>
        )}
      </td>
      <td className={sideClass(f.side)}>{px(f.price)}</td>
      <td className={sideClass(f.side)}>{qty(f.count)}</td>
      <td>{qty(f.post_position)}</td>
      {f.is_taker ? (
        <td className="dim" colSpan={2}>
          exit
        </td>
      ) : (
        <>
          <RiskCell e={risk?.entry} />
          <RiskCell e={risk?.at_fill} />
        </>
      )}
    </tr>
  )
})

type Selected = MarketsEvent['markets'][number]
const SELECTION_ACCESSORS: Accessors<Selected> = {
  Ticker: (m) => m.ticker,
  Prog: (m) => m.reward_per_day,
  Target: (m) => m.target_size,
  Est: (m) => m.est_daily_reward,
  Comp: (m) => competitionRoom(m.competition),
  Fills: (m) => m.est_fills_per_day,
  Net: (m) => m.net_daily_reward,
  Closes: (m) => (m.close_time ? Date.parse(m.close_time) : null),
}

export const Selections = memo(function Selections({
  selections: all,
}: {
  selections: MarketsEvent[]
}) {
  const sorter = useSort('selections')
  // The newest 50 selections (each is a table); older history stays in the journal file.
  const selections = useMemo(() => all.slice(-50).reverse(), [all])
  const fresh = useFreshKeys(selections.map((s) => String(s.ts)))
  return (
    <Panel
      title="Market selection history"
      help="panel:selection"
      note={`${selections.length} selections`}
      span={5}
      height="md"
    >
      {selections.length === 0 ? (
        <Empty>no selections yet</Empty>
      ) : (
        selections.map((s) => (
          <table key={s.ts} className={fresh.has(String(s.ts)) ? 'row-new' : undefined}>
            <thead>
              <tr>
                <SortTh k="Ticker" sorter={sorter} align="l" colSpan={2} text>
                  <Stamp ts={s.ts} /> · {s.markets.length} markets
                  {s.reduce_only.length ? ` · reduce-only: ${s.reduce_only.join(', ')}` : ''}
                </SortTh>
                <SortTh k="Prog" sorter={sorter} help="col:Prog $/d">
                  Prog $/d
                </SortTh>
                <SortTh k="Target" sorter={sorter} help="col:Target">
                  Target
                </SortTh>
                <SortTh k="Est" sorter={sorter} help="sel:Est $/d">
                  Est $/d
                </SortTh>
                <SortTh k="Comp" sorter={sorter} align="l" help="col:Comp">
                  Comp
                </SortTh>
                <SortTh k="Fills" sorter={sorter} help="col:Fills/d">
                  Fills/d
                </SortTh>
                <SortTh k="Net" sorter={sorter} help="col:Net $/h">
                  Net $/h
                </SortTh>
                <SortTh k="Closes" sorter={sorter} align="l" help="sel:Closes">
                  Closes
                </SortTh>
              </tr>
            </thead>
            <tbody>
              {sortRows(s.markets, SELECTION_ACCESSORS, sorter.state).map((m) => (
                <tr key={m.ticker}>
                  <td className="l">
                    <Ticker value={m.ticker} />
                  </td>
                  <td className="l dim">{m.title.slice(0, 28)}</td>
                  <td className="yl">{usd(m.reward_per_day)}</td>
                  <td>{qty(m.target_size)}</td>
                  <td className="pos">{usd(m.est_daily_reward)}</td>
                  <td className="l">
                    <CompetitionTag c={m.competition} />
                  </td>
                  <td>
                    <FillsCell r={m} />
                  </td>
                  <td>
                    <NetCell r={m} />
                  </td>
                  <td className="l dim">
                    {m.close_time ? <Stamp ts={Date.parse(m.close_time) / 1000} date /> : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))
      )}
    </Panel>
  )
})

export const RunConfig = memo(function RunConfig({
  start,
  end,
  configText,
  stateSession,
}: {
  start: Journal['start']
  end: Journal['end']
  configText: string | null // the live snapshot's config as JSON (a string: stable between polls)
  stateSession: number | undefined
}) {
  // Older journals only have the config in run_start.
  const text = configText ?? (start?.config ? JSON.stringify(start.config, null, 2) : null)
  const session = stateSession ?? start?.session
  const note = (
    <>
      {[session != null ? `session ${session}` : '', start ? `${start.environment} · ${start.mode} · ${start.api_url}` : '']
        .filter(Boolean)
        .join(' · ')}
      {start && (
        <>
          {' · started '}
          <Stamp ts={start.ts} onlyAgo />
        </>
      )}
      {end && (
        <>
          {' · ENDED '}
          <Stamp ts={end.ts} /> ({end.reason})
        </>
      )}
    </>
  )
  return (
    <Panel title="Run config" help="panel:config" note={note} height="sm">
      <pre>{text ?? '—'}</pre>
    </Panel>
  )
})
