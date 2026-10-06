import { memo, useCallback, useMemo, useState } from 'react'
import { type Accessors, sortRows, useSort } from '../lib/sort'
import { pct, px, qty, signClass, usd } from '../lib/format'
import { activeCount, botFilters, loadFilters, passes, saveFilters, type ScanFilters } from '../lib/scanFilters'
import type { ScanReport, ScanRow } from '../types'
import { CompetitionTag } from './CompetitionTag'
import { Empty, Panel } from './Panel'
import { ScanFilterBar } from './ScanFilterBar'
import { SortTh } from './SortTh'
import { Stamp } from './Stamp'
import { Ticker } from './Ticker'

const COLUMNS: [label: string, align?: 'l'][] = [
  ['#'],
  ['Ticker', 'l'],
  ['$/day'],
  ['$/h'],
  ['Net $/d'],
  ['Fills/d'],
  ['Return/d'],
  ['Capital'],
  ['Period $'],
  ['Left'],
  ['Vol 24h'],
  ['Resolves'],
  ['Prog $/d'],
  ['Target'],
  ['Bid'],
  ['Ask'],
  ['Sprd'],
  ['Our bid / ask', 'l'],
  ['Share Y/N'],
  ['Comp', 'l'],
  ['Status', 'l'],
]
const TEXT_COLUMNS = new Set(['Ticker', 'Our bid / ask', 'Comp', 'Status'])
const COMP_ORDER = { low: 0, medium: 1, high: 2 } as const

const ACCESSORS: Accessors<ScanRow> = {
  '#': (r) => r.rank,
  Ticker: (r) => r.ticker,
  '$/day': (r) => r.est_daily,
  '$/h': (r) => r.est_hourly,
  'Net $/d': (r) => r.net_daily,
  'Fills/d': (r) => r.fills_per_day,
  'Return/d': (r) => r.return_daily,
  Capital: (r) => r.capital,
  'Period $': (r) => r.period_payout,
  Left: (r) => r.days_left,
  'Vol 24h': (r) => r.volume_24h,
  Resolves: (r) => r.hours_to_resolve,
  'Prog $/d': (r) => r.program_per_day,
  Target: (r) => r.target_size,
  Bid: (r) => r.bid,
  Ask: (r) => r.ask,
  Sprd: (r) => r.spread,
  'Our bid / ask': (r) => r.yes_price,
  'Share Y/N': (r) => (r.yes_share ?? 0) + (r.no_share ?? 0),
  Comp: (r) => COMP_ORDER[r.competition],
  Status: (r) => (r.trading ? 'a' : r.skip ? `c ${r.skip}` : 'b'), // trading, candidates, skipped
}

/** Days left, in the unit that reads best: "7m", "5.2h", "3.4d". */
function timeLeft(days: number): string {
  if (days < 1 / 24) return `${Math.round(days * 1440)}m`
  if (days < 1) return `${(days * 24).toFixed(1)}h`
  return `${days.toFixed(1)}d`
}

function Status({ r }: { r: ScanRow }) {
  if (r.trading) {
    return (
      <span className="tag pos" data-help="scan:trading">
        TRADING
      </span>
    )
  }
  if (r.skip) {
    return (
      <span className="yl" data-help="scan:skip">
        {r.skip}
      </span>
    )
  }
  return (
    <span className="dim" data-help="scan:candidate">
      candidate
    </span>
  )
}

const Row = memo(function Row({ r }: { r: ScanRow }) {
  // The scan's quotes in YES terms: its YES bid, and its NO bid as a YES ask.
  const ask = r.no_price === null ? null : 1 - r.no_price
  return (
    <tr className={r.trading ? 'earning' : undefined}>
      <td className="dim">{r.rank}</td>
      <td className="l">
        <Ticker value={r.ticker} help={`ticker:${r.ticker}|${r.title}`} />
      </td>
      <td className="pos">{usd(r.est_daily)}</td>
      <td>{usd(r.est_hourly, 3)}</td>
      <td className={signClass(r.net_daily)}>{usd(r.net_daily)}</td>
      <td className={r.fills_per_day ? 'neg' : 'dim'}>
        {r.fills_per_day === null ? '—' : r.fills_per_day.toFixed(1)}
      </td>
      <td>{r.return_daily === null ? '—' : pct(r.return_daily)}</td>
      <td>{usd(r.capital)}</td>
      <td className={r.period_payout < 1 ? 'dim' : undefined}>{usd(r.period_payout)}</td>
      <td className="dim">{timeLeft(r.days_left)}</td>
      <td>{r.volume_24h == null ? '—' : qty(r.volume_24h)}</td>
      <td className="dim">{r.hours_to_resolve == null ? '—' : timeLeft(r.hours_to_resolve / 24)}</td>
      <td className="yl">{usd(r.program_per_day)}</td>
      <td className="dim">{qty(r.target_size)}</td>
      <td className="bid">{px(r.bid)}</td>
      <td className="ask">{px(r.ask)}</td>
      <td>{r.spread === null ? '—' : `${(r.spread * 100).toFixed(1)}¢`}</td>
      <td className="l">
        <span className="bid">{px(r.yes_price)}</span>
        <span className="dim"> / </span>
        <span className="ask">{px(ask)}</span>
      </td>
      <td>
        <span className="bid">{r.yes_share === null ? '—' : pct(r.yes_share)}</span>
        <span className="dim">/</span>
        <span className="ask">{r.no_share === null ? '—' : pct(r.no_share)}</span>
      </td>
      <td className="l">
        <CompetitionTag c={{ level: r.competition, room: r.competition_room ?? null }} />
      </td>
      <td className="l">
        <Status r={r} />
      </td>
    </tr>
  )
})

/**
 * The market scanner: every rewarded market's estimated $/day at a fixed size
 * (10 contracts per side), best first. Research only: the bot never trades
 * because of it. Refreshed every few minutes; the actual numbers, not the
 * capital multiplier's projection.
 */
export const Scanner = memo(function Scanner({
  scan,
  selection,
}: {
  scan: ScanReport | null
  selection?: Record<string, unknown> // the bot's selection config, for the "bot's filters" preset
}) {
  const sorter = useSort('scanner')
  const [filters, setFiltersState] = useState<ScanFilters>(loadFilters)
  const [showFilters, setShowFilters] = useState(false)
  const setFilters = useCallback((f: ScanFilters) => {
    setFiltersState(f)
    saveFilters(f)
  }, [])
  const botPreset = useMemo(
    () => (selection ? () => setFilters(botFilters(selection)) : null),
    [selection, setFilters],
  )
  const rows = useMemo(() => {
    const all = scan?.rows ?? []
    return sortRows(
      all.filter((r) => passes(r, filters)),
      ACCESSORS,
      sorter.state,
    )
  }, [scan, filters, sorter.state])
  const active = activeCount(filters)
  const trading = scan ? scan.rows.filter((r) => r.trading).length : 0

  return (
    <Panel
      title={`Market scanner · top ${scan?.rows.length ?? 100} by $/day`}
      help="panel:scanner"
      note={
        scan ? (
          <>
            {`${qty(scan.size)} contracts/side · ${scan.earning} of ${scan.markets} rewarded markets earn · ${trading} traded now · scanned `}
            <Stamp ts={scan.scanned_at} onlyAgo />
            {` (${scan.seconds.toFixed(0)}s) · every ${Math.round(scan.interval / 60)}m`}
          </>
        ) : (
          'first scan runs ~20s after the bot starts'
        )
      }
      tools={
        <button
          type="button"
          className={`chip${showFilters || active ? ' on' : ''}`}
          data-help="chip:scan-filters"
          onClick={() => setShowFilters((v) => !v)}
        >
          filters{active ? ` (${active}) · ${rows.length} left` : ''}
        </button>
      }
      height="lg"
    >
      {showFilters && scan && (
        <ScanFilterBar
          filters={filters}
          onChange={setFilters}
          onBotPreset={botPreset}
          shown={rows.length}
          total={scan.rows.length}
        />
      )}
      {!scan ? (
        <Empty>no scan yet</Empty>
      ) : scan.rows.length === 0 ? (
        <Empty>no rewarded market earns anything at {qty(scan.size)} contracts right now</Empty>
      ) : rows.length === 0 ? (
        <Empty>no market passes these filters ({active} on)</Empty>
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
                  help={`scan:${label}`}
                  text={TEXT_COLUMNS.has(label)}
                >
                  {label}
                </SortTh>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Row key={r.ticker} r={r} />
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
})
