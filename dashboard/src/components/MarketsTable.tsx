import { memo, useState } from 'react'
import { type Accessors, sortRows, useSort } from '../lib/sort'
import { num, pct, px, qty, signClass, signedUsd, usd } from '../lib/format'
import { useFreshKeys } from '../lib/useFreshKeys'
import { span, useNow } from '../lib/clock'
import { periodView } from '../lib/period'
import type { LegQuote, MarketRow, OrderRow } from '../types'
import { competitionRoom } from '../lib/competition'
import { CompetitionTag } from './CompetitionTag'
import { FillsCell, NetCell } from './FillRiskCells'
import { Flash } from './Flash'
import { Empty, Panel } from './Panel'
import { RankBadge } from './SidePanels'
import { SortTh } from './SortTh'
import { Ticker } from './Ticker'

const COLUMNS: [label: string, align?: 'l'][] = [
  ['Ticker', 'l'],
  ['To $1', 'l'],
  ['Bid'],
  ['Ask'],
  ['Sprd'],
  ['Sz b/a'],
  ['Our YES bid', 'l'],
  ['Our YES ask (NO bid)', 'l'],
  ['Cost'],
  ['Realized'],
  ['Prog $/d'],
  ['Target'],
  ['Comp', 'l'],
  ['Fills/d'],
  ['Net $/h'],
  ['Share Y/N'],
  ['Q pos Y', 'l'],
  ['Q pos N', 'l'],
  ['Earned'],
  ['$/h'],
  ['$/d'],
  ['$/period'],
  ['Period left'],
  ['Mix', 'l'],
  ['Paying'],
  ['Flags', 'l'],
]
const TEXT_COLUMNS = new Set(['Ticker', 'Flags'])

/** Most severe first when sorted descending. */
function flagRank(m: MarketRow): number {
  if (m.inactive) return 0
  if (!m.healthy) return 5
  if (m.paused) return 4
  if (m.near_close) return 3
  if (m.reduce_only) return 2
  return 1
}

const add = (a: number | null | undefined, b: number | null | undefined) =>
  a == null && b == null ? null : (a ?? 0) + (b ?? 0)

function accessors(ordersByTicker: Map<string, OrderRow[]>): Accessors<MarketRow> {
  const bestRank = (leg: 'yes' | 'no') => (m: MarketRow) => {
    const o = sideRank(ordersByTicker.get(m.ticker) ?? [], leg)
    return o?.ahead_total == null ? null : o.ahead_total + 1
  }
  return {
    Ticker: (m) => m.ticker,
    'To $1': (m) => periodView(m, Date.now() / 1000).earned,
    Bid: (m) => m.book?.bid,
    Ask: (m) => m.book?.ask,
    Sprd: (m) => (m.book?.bid != null && m.book.ask != null ? m.book.ask - m.book.bid : null),
    'Sz b/a': (m) => add(m.book?.bid_size, m.book?.ask_size),
    'Our YES bid': (m) => m.quotes.yes?.price,
    'Our YES ask (NO bid)': (m) => m.quotes.no?.price,
    Cost: (m) => m.exposure,
    Realized: (m) => m.realized_pnl,
    'Prog $/d': (m) => m.reward.per_day,
    Target: (m) => m.reward.target_size,
    Comp: (m) => competitionRoom(m.competition),
    'Fills/d': (m) => m.est_fills_per_day,
    'Net $/h': (m) => m.net_daily_reward,
    'Share Y/N': (m) => add(m.quotes.yes?.share, m.quotes.no?.share),
    'Q pos Y': bestRank('yes'),
    'Q pos N': bestRank('no'),
    Earned: (m) => m.earned,
    '$/h': (m) => m.rate_per_hour,
    '$/d': (m) => m.rate_per_hour,
    '$/period': (m) => periodView(m, Date.now() / 1000).projected,
    'Period left': (m) => periodView(m, Date.now() / 1000).end,
    Mix: (m) => m.rate_per_hour,
    Paying: (m) => (m.snapshots ? m.paying_snapshots / m.snapshots : null),
    Flags: flagRank,
  }
}

/** Our quote; why the bot priced it there (or isn't quoting) is in the hover help. */
function QuoteCell({ q, cls }: { q?: LegQuote; cls: 'bid' | 'ask' }) {
  if (!q) return <span className="dim">—</span>
  if (q.price === null) {
    return (
      <span className="dim help-u" data-help={`reason:${q.reason}`}>
        —
      </span>
    )
  }
  return (
    <span className={cls} data-help={`reason:${q.reason}`}>
      <Flash value={`${q.size}@${q.price}`}>
        {qty(q.size)}@{px(q.price)}
      </Flash>
    </span>
  )
}

/** $/period and time left in it. Ticks on its own each second; the row doesn't re-render. */
function PeriodCells({ m }: { m: MarketRow }) {
  const now = useNow() / 1000
  const p = periodView(m, now)
  const left = p.end === null ? null : p.end - now
  const closesFirst = p.earningEnd !== null && p.end !== null && p.earningEnd < p.end
  return (
    <>
      <td
        className={p.projected === null ? 'dim' : p.projected >= 1 ? 'pos' : 'neg'}
        title={`earned this period ${usd(p.earned, 4)}`}
      >
        {p.projected === null ? '—' : usd(p.projected)}
      </td>
      <td className="dim" title={closesFirst ? 'the market closes before the period ends' : undefined}>
        {left === null ? '—' : left <= 0 ? 'ended' : span(left)}
        {closesFirst && <span className="yl"> ⚑</span>}
      </td>
    </>
  )
}

/** This market's share of the total $/h; the bar is scaled so the top earner fills it. */
function MixBar({ share, fill }: { share: number | null; fill: number }) {
  if (share === null) return <span className="dim">—</span>
  return (
    <span className="mini-mix" data-help="mix:bar">
      <span className="mini-mix-track">
        <span className="mini-mix-bar" style={{ width: `${(fill * 100).toFixed(1)}%` }} />
      </span>
      <span className="mini-mix-pct">{(share * 100).toFixed(0)}%</span>
    </span>
  )
}

/** This period's earnings toward Kalshi's payout minimum: grey until it's reached, then green. */
function ThresholdBar({ m, minimum }: { m: MarketRow; minimum: number }) {
  const earned = periodView(m, useNow() / 1000).earned
  const done = earned >= minimum
  const fill = minimum > 0 ? Math.min(earned / minimum, 1) : 1
  return (
    <span
      className={`mini-mix${done ? '' : ' to-go'}`}
      title={`${usd(earned, 2)} of ${usd(minimum)} this period${done ? ': pays out' : ` · ${usd(minimum - earned, 2)} to go`}`}
    >
      <span className="mini-mix-track">
        <span className="mini-mix-bar" style={{ width: `${(fill * 100).toFixed(1)}%` }} />
      </span>
      <span className="mini-mix-pct">{(fill * 100).toFixed(0)}%</span>
    </span>
  )
}

function Flags({ m }: { m: MarketRow }) {
  if (m.inactive) {
    return (
      <span className="tag dim" data-help="flag:PAST">
        PAST
      </span>
    )
  }
  const flags: [string, string, string?][] = []
  if (!m.healthy) flags.push(['BLIND', 'neg'])
  if (m.unwind) flags.push([`UNWIND @${px(m.unwind.price)}`, 'mg', 'flag:UNWIND'])
  else if (m.flattening) flags.push(['FLATTENING', 'mg'])
  if (m.paused) {
    const left = Math.round(m.pause_left ?? 0)
    const label = `PAUSED ${left >= 60 ? `${Math.ceil(left / 60)}m` : `${left}s`}`
    flags.push([label, 'yl', `paused:${m.pause_reason ?? ''}|${left}`])
  }
  if (m.near_close) flags.push(['CLOSING', 'yl'])
  if (m.reduce_only) flags.push(['REDUCE-ONLY', 'mg'])
  if (!flags.length) {
    return (
      <span className="dim" data-help="flag:ok">
        ok
      </span>
    )
  }
  return (
    <>
      {flags.map(([name, cls, help]) => (
        <span key={name} className={`tag ${cls}`} data-help={help ?? `flag:${name}`}>
          {name}
        </span>
      ))}
    </>
  )
}

/** Best-ranked resting order per side, for the at-a-glance rank column. */
function sideRank(orders: OrderRow[], leg: 'yes' | 'no') {
  const mine = orders.filter((o) => o.leg === leg && o.ahead_total !== null)
  return mine.sort((a, b) => (a.ahead_total ?? 0) - (b.ahead_total ?? 0))[0]
}

interface RowProps {
  m: MarketRow
  orders: OrderRow[]
  fresh: boolean
  share: number | null // of the total $/h across markets (null: not earning)
  fill: number // bar length: this market's $/h over the top earner's
  minimum: number // Kalshi's payout minimum per period
  sig: string // everything the row shows; unchanged signature = skip re-rendering it
  onSelect: (ticker: string) => void // stable (a state setter): left out of the memo check
}

const Row = memo(
  RowView,
  (a, b) =>
    a.sig === b.sig &&
    a.fresh === b.fresh &&
    a.share === b.share &&
    a.fill === b.fill &&
    a.minimum === b.minimum,
)

function RowView({ m, orders, fresh, share, fill, minimum, onSelect }: RowProps) {
  const b = m.book
  const spread = b && b.bid !== null && b.ask !== null ? b.ask - b.bid : null
  const { yes, no } = m.quotes
  const yesShare = yes?.share != null ? pct(yes.share) : '—'
  const noShare = no?.share != null ? pct(no.share) : '—'
  const paying = m.snapshots ? m.paying_snapshots / m.snapshots : null
  const yesRank = sideRank(orders, 'yes')
  const noRank = sideRank(orders, 'no')
  return (
    <tr
      onClick={() => onSelect(m.ticker)}
      title="click for everything about this market"
      className={
        ['clickable', fresh ? 'row-new' : '', m.inactive ? 'row-past' : '', m.rate_per_hour > 0 ? 'earning' : '']
          .join(' ')
          .trim() || undefined
      }
    >
      <td className="l">
        <Ticker value={m.ticker} help={`ticker:${m.ticker}|${m.title}`} />
      </td>
      <td className="l">
        <ThresholdBar m={m} minimum={minimum} />
      </td>
      <td className="bid">
        <Flash value={b?.bid ?? null}>{px(b?.bid)}</Flash>
      </td>
      <td className="ask">
        <Flash value={b?.ask ?? null}>{px(b?.ask)}</Flash>
      </td>
      <td>{spread === null ? '—' : `${(spread * 100).toFixed(1)}¢`}</td>
      <td>
        <Flash value={b?.bid_size ?? null}>
          <span className="bid">{qty(b?.bid_size)}</span>
        </Flash>
        <span className="dim">/</span>
        <Flash value={b?.ask_size ?? null}>
          <span className="ask">{qty(b?.ask_size)}</span>
        </Flash>
      </td>
      <td className="l">
        <QuoteCell q={yes} cls="bid" />
      </td>
      <td className="l">
        <QuoteCell q={no} cls="ask" />
      </td>
      <td>{usd(m.exposure)}</td>
      <td className={signClass(m.realized_pnl)}>{signedUsd(m.realized_pnl)}</td>
      <td className="yl">{usd(m.reward.per_day)}</td>
      <td className="dim">{qty(m.reward.target_size)}</td>
      <td className="l">
        <CompetitionTag c={m.competition} />
      </td>
      <td>
        <FillsCell r={m} />
      </td>
      <td>
        <NetCell r={m} />
      </td>
      <td>
        <Flash value={yesShare}>
          <span className="bid">{yesShare}</span>
        </Flash>
        <span className="dim">/</span>
        <Flash value={noShare}>
          <span className="ask">{noShare}</span>
        </Flash>
      </td>
      <td className="l">{yesRank ? <RankBadge o={yesRank} /> : <span className="dim">—</span>}</td>
      <td className="l">{noRank ? <RankBadge o={noRank} /> : <span className="dim">—</span>}</td>
      <td className="pos">
        <Flash value={m.earned}>{usd(m.earned, 4)}</Flash>
      </td>
      <td>
        <Flash value={m.rate_per_hour}>{usd(m.rate_per_hour, 3)}</Flash>
      </td>
      <td>{usd(m.rate_per_hour * 24)}</td>
      <PeriodCells m={m} />
      <td className="l">
        <MixBar share={share} fill={fill} />
      </td>
      <td className="dim">{paying === null ? '—' : pct(num(paying))}</td>
      <td className="l">
        <Flags m={m} />
      </td>
    </tr>
  )
}

export function MarketsTable({
  markets: all,
  orders,
  minimum,
  onSelect,
}: {
  markets: MarketRow[]
  orders: OrderRow[]
  minimum: number
  onSelect: (ticker: string) => void
}) {
  const [showPast, setShowPast] = useState(true)
  const active = all.filter((m) => !m.inactive).length
  const pastCount = all.length - active
  const sorter = useSort('markets')
  const byTicker = new Map<string, OrderRow[]>()
  for (const o of orders) byTicker.set(o.ticker, [...(byTicker.get(o.ticker) ?? []), o])
  const markets = sortRows(
    showPast ? all : all.filter((m) => !m.inactive),
    accessors(byTicker),
    sorter.state,
  )
  const fresh = useFreshKeys(markets.map((m) => m.ticker))
  const totalRate = all.reduce((sum, m) => sum + (m.rate_per_hour > 0 ? m.rate_per_hour : 0), 0)
  const topRate = Math.max(0, ...all.map((m) => m.rate_per_hour))
  return (
    <Panel
      title="Markets"
      help="panel:markets"
      note={all.length ? `${active} active${pastCount ? ` · ${pastCount} past` : ''}` : ''}
      tools={
        <>
          <span className="dim">
            click a row for details · book = live YES bid/ask · quotes = bot&apos;s desired YES bid / ask · share = est. reward
            share YES/NO{' '}
          </span>
          {pastCount > 0 && (
            <button
              type="button"
              className={`chip${showPast ? ' on' : ''}`}
              data-help="chip:past"
              onClick={() => setShowPast((v) => !v)}
            >
              past markets
            </button>
          )}
        </>
      }
      height="md"
    >
      {markets.length === 0 ? (
        <Empty>no markets yet</Empty>
      ) : (
        <table>
          <thead>
            <tr>
              {COLUMNS.map(([label, align]) => (
                <SortTh
                  key={label}
                  k={label}
                  sorter={sorter}
                  align={align}
                  help={`col:${label}`}
                  text={TEXT_COLUMNS.has(label)}
                >
                  {label}
                </SortTh>
              ))}
            </tr>
          </thead>
          <tbody>
            {markets.map((m) => (
              <Row
                key={m.ticker}
                m={m}
                orders={byTicker.get(m.ticker) ?? []}
                fresh={fresh.has(m.ticker)}
                share={totalRate > 0 && m.rate_per_hour > 0 ? m.rate_per_hour / totalRate : null}
                fill={topRate > 0 ? Math.max(m.rate_per_hour, 0) / topRate : 0}
                minimum={minimum}
                sig={JSON.stringify(m) + JSON.stringify(byTicker.get(m.ticker))}
                onSelect={onSelect}
              />
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
