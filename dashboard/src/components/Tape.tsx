import { memo, useMemo } from 'react'
import type { Journal } from '../lib/useJournal'
import { kindClass, px, qty, sideClass } from '../lib/format'
import { Stamp } from './Stamp'
import { Ticker } from './Ticker'

interface TapeItem {
  key: string
  ts: number
  cls: string
  text: string
  trade?: { ticker: string; side: string; size: string; reason?: string } // colored separately
}

/** Scrolling ticker of the latest bot activity: fills and orders. */
export const Tape = memo(function Tape({ fills, orders }: Pick<Journal, 'fills' | 'orders'>) {
  const items = useMemo(() => {
    const out: TapeItem[] = []
    for (const f of fills.slice(-15)) {
      out.push({
        key: `f${f.ts}${f.order_id}`,
        ts: f.ts,
        cls: kindClass('fill'),
        text: 'FILL',
        trade: { ticker: f.ticker, side: f.side ?? '', size: `${qty(f.count)}@${px(f.price)}` },
      })
    }
    for (const o of orders.slice(-30)) {
      out.push({
        key: `o${o.ts}${o.order_id ?? o.ticker}${o.action}`,
        ts: o.ts,
        cls: kindClass(o.action),
        text: o.action.toUpperCase(),
        trade: { ticker: o.ticker, side: o.side, size: `${qty(o.size)}@${px(o.price)}`, reason: o.reason },
      })
    }
    return out.sort((a, b) => b.ts - a.ts).slice(0, 40)
  }, [fills, orders])

  if (!items.length) return <div className="tape" data-help="panel:tape" />
  const run = (copy: string) =>
    items.map((i) => (
      <span key={`${copy}${i.key}`} className="tape-item">
        <span className="dim">
          <Stamp ts={i.ts} />
        </span>{' '}
        <span className={i.cls}>{i.text}</span>
        {i.trade && (
          <>
            {' '}
            <span className="tk">
              <Ticker value={i.trade.ticker} />
            </span>{' '}
            <span className={sideClass(i.trade.side)}>
              {i.trade.side.toUpperCase()} {i.trade.size}
            </span>
            {i.trade.reason && <span className="dim"> — {i.trade.reason}</span>}
          </>
        )}
      </span>
    ))
  return (
    <div className="tape" data-help="panel:tape">
      {/* Two copies so the loop scrolls seamlessly. Restarts when new activity arrives. */}
      <div className="tape-track" key={items[0].key}>
        {run('a')}
        {run('b')}
      </div>
    </div>
  )
})
