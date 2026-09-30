import { usd } from '../lib/format'
import type { MarketRow, RunState } from '../types'
import { Flash } from './Flash'
import { Empty, Panel } from './Panel'
import { Ticker } from './Ticker'

/** Where the money comes from: each market's share of the current total $/h. */
export function EarningsMix({ state }: { state: RunState | null }) {
  const markets: MarketRow[] = state?.markets ?? []
  const earning = markets.filter((m) => m.rate_per_hour > 0).sort((a, b) => b.rate_per_hour - a.rate_per_hour)
  const total = earning.reduce((sum, m) => sum + m.rate_per_hour, 0)
  return (
    <Panel
      title="Earnings mix"
      help="panel:mix"
      note={
        total > 0 ? (
          <>
            <span className="rec">●</span> {earning.length} market{earning.length === 1 ? '' : 's'} earning ·
            total{' '}
            <b className="pos">
              <Flash value={total}>{usd(total, 3)}/h</Flash>
            </b>{' '}
            · {usd(total * 24)}/day at this pace
          </>
        ) : (
          ''
        )
      }
      height="sm"
    >
      {earning.length === 0 ? (
        <Empty>no market is earning right now (rates cover the last ~10 minutes)</Empty>
      ) : (
        <div className="mix">
          {earning.map((m, i) => {
            const share = total > 0 ? m.rate_per_hour / total : 0
            return (
              <div key={m.ticker} className={`mix-row${i === 0 ? ' leader' : ''}`}>
                <span className="mix-rank">#{i + 1}</span>
                <span className="mix-ticker">
                  <Ticker value={m.ticker} help={`ticker:${m.ticker}|${m.title}`} />
                </span>
                <span className="mix-track" data-help="mix:bar">
                  <span className="mix-bar" style={{ width: `${(share * 100).toFixed(2)}%` }} />
                </span>
                <span className="mix-pct">
                  <Flash value={Math.round(share * 1000)}>{(share * 100).toFixed(1)}%</Flash>
                </span>
                <span className="mix-rate">
                  <Flash value={m.rate_per_hour}>{usd(m.rate_per_hour, 3)}/h</Flash>
                </span>
              </div>
            )
          })}
        </div>
      )}
    </Panel>
  )
}
