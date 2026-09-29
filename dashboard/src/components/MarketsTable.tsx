import { num, pct, px, qty, signClass, signedUsd, usd } from '../lib/format'
import { useFreshKeys } from '../lib/useFreshKeys'
import type { LegQuote, MarketRow, OrderRow } from '../types'
import { Flash } from './Flash'
import { Empty, Panel } from './Panel'
import { RankBadge } from './SidePanels'

const COLUMNS: [label: string, align?: 'l'][] = [
  ['Ticker', 'l'],
  ['Bid'],
  ['Ask'],
  ['Sprd'],
  ['Sz b/a'],
  ['Our YES bid', 'l'],
  ['Our YES ask (NO bid)', 'l'],
  ['Pos'],
  ['Cost'],
  ['Realized'],
  ['Prog $/d'],
  ['Target'],
  ['Share Y/N'],
  ['Rank Y/N', 'l'],
  ['Earned'],
  ['$/h'],
  ['Paying'],
  ['Flags', 'l'],
]

function QuoteCell({ q }: { q?: LegQuote }) {
  if (!q) return <span className="dim">—</span>
  const reason = (
    <span className="dim help-u" data-help={`reason:${q.reason}`}>
      {q.reason}
    </span>
  )
  if (q.price === null) return <span className="dim">— {reason}</span>
  return (
    <>
      <Flash value={`${q.size}@${q.price}`}>
        {qty(q.size)}@{px(q.price)}
      </Flash>{' '}
      {reason}
    </>
  )
}

function Flags({ m }: { m: MarketRow }) {
  const flags: [string, string][] = []
  if (!m.healthy) flags.push(['BLIND', 'neg'])
  if (m.paused) flags.push(['PAUSED', 'yl'])
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
      {flags.map(([name, cls]) => (
        <span key={name} className={`tag ${cls}`} data-help={`flag:${name}`}>
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

function Row({ m, orders, fresh }: { m: MarketRow; orders: OrderRow[]; fresh: boolean }) {
  const b = m.book
  const spread = b && b.bid !== null && b.ask !== null ? b.ask - b.bid : null
  const { yes, no } = m.quotes
  const share = `${yes?.share != null ? pct(yes.share) : '—'}/${no?.share != null ? pct(no.share) : '—'}`
  const paying = m.snapshots ? m.paying_snapshots / m.snapshots : null
  const yesRank = sideRank(orders, 'yes')
  const noRank = sideRank(orders, 'no')
  return (
    <tr className={fresh ? 'row-new' : undefined}>
      <td className="l am" data-help={`ticker:${m.ticker}|${m.title}`}>
        {m.ticker}
      </td>
      <td className="cy">
        <Flash value={b?.bid ?? null}>{px(b?.bid)}</Flash>
      </td>
      <td className="cy">
        <Flash value={b?.ask ?? null}>{px(b?.ask)}</Flash>
      </td>
      <td>{spread === null ? '—' : `${(spread * 100).toFixed(1)}¢`}</td>
      <td className="dim">
        <Flash value={`${b?.bid_size}/${b?.ask_size}`}>
          {qty(b?.bid_size)}/{qty(b?.ask_size)}
        </Flash>
      </td>
      <td className="l">
        <QuoteCell q={yes} />
      </td>
      <td className="l">
        <QuoteCell q={no} />
      </td>
      <td className={signClass(m.position)}>
        <Flash value={m.position}>
          {m.position > 0 ? '+' : ''}
          {qty(m.position)}
        </Flash>
      </td>
      <td>{usd(m.exposure)}</td>
      <td className={signClass(m.realized_pnl)}>{signedUsd(m.realized_pnl)}</td>
      <td className="yl">{usd(m.reward.per_day)}</td>
      <td className="dim">{qty(m.reward.target_size)}</td>
      <td>
        <Flash value={share}>{share}</Flash>
      </td>
      <td className="l">
        {yesRank ? <RankBadge o={yesRank} /> : <span className="dim">—</span>}
        <span className="dim"> · </span>
        {noRank ? <RankBadge o={noRank} /> : <span className="dim">—</span>}
      </td>
      <td className="pos">
        <Flash value={m.earned}>{usd(m.earned, 4)}</Flash>
      </td>
      <td>
        <Flash value={m.rate_per_hour}>{usd(m.rate_per_hour, 3)}</Flash>
      </td>
      <td className="dim">{paying === null ? '—' : pct(num(paying))}</td>
      <td className="l">
        <Flags m={m} />
      </td>
    </tr>
  )
}

export function MarketsTable({ markets, orders }: { markets: MarketRow[]; orders: OrderRow[] }) {
  const fresh = useFreshKeys(markets.map((m) => m.ticker))
  return (
    <Panel
      title="Markets"
      help="panel:markets"
      note={markets.length ? `${markets.length} active` : ''}
      tools={
        <span className="dim">
          book = live YES bid/ask · quotes = bot&apos;s desired YES bid / ask · share = est. reward
          share YES/NO
        </span>
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
                <th key={label} className={align} data-help={`col:${label}`}>
                  {label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {markets.map((m) => (
              <Row
                key={m.ticker}
                m={m}
                orders={orders.filter((o) => o.ticker === m.ticker)}
                fresh={fresh.has(m.ticker)}
              />
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
