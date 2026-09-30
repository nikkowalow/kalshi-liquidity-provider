import { useState } from 'react'
import type { Journal } from '../lib/useJournal'
import { hms, px, qty, usd } from '../lib/format'
import { toggled } from '../lib/sets'
import { useFreshKeys } from '../lib/useFreshKeys'
import type { OrderRow, RunState } from '../types'
import { Flash } from './Flash'
import { Chips, Empty, Panel, PanelHeader } from './Panel'
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

export function OrdersAndFills({ orders, journal }: { orders: OrderRow[]; journal: Journal }) {
  const sorted = [...orders].sort((a, b) => a.ticker.localeCompare(b.ticker) || a.side.localeCompare(b.side))
  const fills = [...journal.fills].reverse().slice(0, 300)
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
                <th className="l">Ticker</th>
                <th className="l" data-help="ord:Side">
                  Side
                </th>
                <th data-help="ord:Price">Price</th>
                <th data-help="ord:Qty">Qty</th>
                <th className="l" data-help="ord:Rank">
                  Rank / target
                </th>
                <th data-help="ord:Better">Better px</th>
                <th data-help="ord:Queue">At level</th>
                <th data-help="ord:Credit">Full credit</th>
                <th data-help="ord:Age">Age</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map((o) => (
                <tr key={o.order_id} className={freshOrders.has(o.order_id) ? 'row-new' : undefined}>
                  <td className="l">
                    <Ticker value={o.ticker} />
                  </td>
                  <td className={`l ${o.side === 'bid' ? 'cy' : 'mg'}`}>{o.side === 'bid' ? 'BID' : 'ASK'}</td>
                  <td>{px(o.price)}</td>
                  <td>
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
                  <td className="dim">{Math.floor(o.age)}s</td>
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
                <th className="l">Time</th>
                <th className="l">Ticker</th>
                <th className="l" data-help="fill:Side">
                  Side
                </th>
                <th data-help="fill:Price">Price</th>
                <th data-help="fill:Qty">Qty</th>
                <th data-help="fill:Pos after">Pos after</th>
              </tr>
            </thead>
            <tbody>
              {fills.map((f) => (
                <tr key={fillKey(f)} className={freshFills.has(fillKey(f)) ? 'row-fill' : undefined}>
                  <td className="l dim">{hms(f.ts)}</td>
                  <td className="l">
                    <Ticker value={f.ticker} />
                  </td>
                  <td className={`l ${f.side === 'bid' ? 'cy' : 'mg'}`}>
                    {(f.side ?? '').toUpperCase()}
                    {f.is_taker && (
                      <span className="tag neg" data-help="fill:TAKER">
                        TAKER
                      </span>
                    )}
                  </td>
                  <td>{px(f.price)}</td>
                  <td>{qty(f.count)}</td>
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
      note={String(lines.length)}
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
                <td className="dim">{hms(l.ts)}</td>
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

export function Selections({ journal }: { journal: Journal }) {
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
                <th className="l" colSpan={2}>
                  {hms(s.ts)} · {s.markets.length} markets
                  {s.reduce_only.length ? ` · reduce-only: ${s.reduce_only.join(', ')}` : ''}
                </th>
                <th data-help="col:Prog $/d">Prog $/d</th>
                <th data-help="col:Target">Target</th>
                <th data-help="sel:Est $/d">Est $/d</th>
                <th className="l" data-help="sel:Closes">
                  Closes
                </th>
              </tr>
            </thead>
            <tbody>
              {s.markets.map((m) => (
                <tr key={m.ticker}>
                  <td className="l am">
                    <Ticker value={m.ticker} />
                  </td>
                  <td className="l dim">{m.title.slice(0, 28)}</td>
                  <td className="yl">{usd(m.reward_per_day)}</td>
                  <td>{qty(m.target_size)}</td>
                  <td className="pos">{usd(m.est_daily_reward)}</td>
                  <td className="l dim">
                    {m.close_time ? new Date(m.close_time).toLocaleString([], { hour12: false }) : '—'}
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
  const note = [
    session != null ? `session ${session}` : '',
    start ? `${start.environment} · ${start.mode} · ${start.api_url}` : '',
    end ? `ENDED ${hms(end.ts)} (${end.reason})` : '',
  ]
    .filter(Boolean)
    .join(' · ')
  return (
    <Panel title="Run config" help="panel:config" note={note} height="sm">
      <pre>{config ? JSON.stringify(config, null, 2) : '—'}</pre>
    </Panel>
  )
}
