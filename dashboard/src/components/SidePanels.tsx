import { useState } from 'react'
import type { Journal } from '../lib/useJournal'
import { px, qty, sideClass, usd } from '../lib/format'
import { toggled } from '../lib/sets'
import { type Accessors, sortRows, useSort } from '../lib/sort'
import { useFreshKeys } from '../lib/useFreshKeys'
import type { FillEvent, MarketsEvent, OrderRow, RunState } from '../types'
import { competitionRoom } from '../lib/competition'
import { CompetitionTag } from './CompetitionTag'
import { FillsCell, NetCell } from './FillRiskCells'
import { span } from '../lib/clock'
import { Flash } from './Flash'
import { Chips, Empty, Panel, PanelHeader } from './Panel'
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
  const fillSort = useSort('fills')
  const byTicker = [...orders].sort((a, b) => a.ticker.localeCompare(b.ticker) || a.side.localeCompare(b.side))
  const sorted = sortRows(byTicker, ORDER_ACCESSORS, orderSort.state)
  const fills = sortRows([...journal.fills].reverse(), FILL_ACCESSORS, fillSort.state).slice(0, 300)
  const freshOrders = useFreshKeys(sorted.map((o) => o.order_id))
  const fillKey = (f: (typeof fills)[number]) => `${f.ts}-${f.order_id}-${f.count}`
  const freshFills = useFreshKeys(fills.map(fillKey))
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
      <PanelHeader title="Fills" help="panel:fills" note={String(journal.fills.length)} />
      <div className="body h-sm">
        {fills.length === 0 ? (
          <Empty>no fills yet</Empty>
        ) : (
          <table>
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
              </tr>
            </thead>
            <tbody>
              {fills.map((f) => (
                <tr key={fillKey(f)} className={freshFills.has(fillKey(f)) ? 'row-fill' : undefined}>
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
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </section>
  )
}

const LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const
type Level = (typeof LEVELS)[number]

export function LogPanel({ journal }: { journal: Journal }) {
  const [levels, setLevels] = useState<ReadonlySet<Level>>(
    () => new Set<Level>(['INFO', 'WARNING', 'ERROR', 'CRITICAL']),
  )
  const lines = journal.logs
    .filter((l) => levels.has(l.level as Level))
    .slice(-1000)
    .reverse()
  const key = (l: (typeof lines)[number]) => `${l.ts}|${l.msg}`
  const fresh = useFreshKeys(lines.map(key))
  return (
    <Panel
      title="Log"
      help="panel:log"
      note={
        <>
          {lines.length} · tailing <span className="cursor">█</span>
        </>
      }
      span={7}
      height="md"
      tools={<Chips options={LEVELS} selected={levels} onToggle={(l) => setLevels((s) => toggled(s, l))} helpPrefix="level" />}
    >
      {lines.length === 0 ? (
        <Empty>nothing logged yet</Empty>
      ) : (
        <table className="log">
          <tbody>
            {lines.map((l, i) => (
              <tr key={`${key(l)}-${i}`} className={`${l.level}${fresh.has(key(l)) ? ' row-new' : ''}`}>
                <td className="dim">
                  <Stamp ts={l.ts} />
                </td>
                <td>{l.level.slice(0, 4)}</td>
                <td className="dim">{l.logger.replace('kalshi_lp.', '')}</td>
                <td>{l.msg}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}

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

export function Selections({ journal }: { journal: Journal }) {
  const sorter = useSort('selections')
  const selections = [...journal.selections].reverse()
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
}

export function RunConfig({ journal, state }: { journal: Journal; state: RunState | null }) {
  const { start, end } = journal
  const config = state?.config ?? start?.config // older journals only have it in run_start
  const session = state?.session ?? start?.session
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
      <pre>{config ? JSON.stringify(config, null, 2) : '—'}</pre>
    </Panel>
  )
}
