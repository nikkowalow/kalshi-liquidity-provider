import { useMemo } from 'react'
import type { Journal } from '../lib/useJournal'
import { kindClass, px, qty, sideClass } from '../lib/format'
import { Stamp } from './Stamp'

interface TapeItem {
  key: string
  ts: number
  cls: string
  text: string
  trade?: { ticker: string; side: string; size: string; reason?: string } // colored separately
}

/** Scrolling ticker of the latest bot activity: fills, orders, and warnings. */
export function Tape({ journal }: { journal: Journal }) {
  const items = useMemo(() => {
    const out: TapeItem[] = []
    for (const f of journal.fills.slice(-15)) {
      out.push({
        key: `f${f.ts}${f.order_id}`,
        ts: f.ts,
        cls: kindClass('fill'),
        text: 'FILL',
        trade: { ticker: f.ticker, side: f.side ?? '', size: `${qty(f.count)}@${px(f.price)}` },
      })
    }
    for (const o of journal.orders.slice(-30)) {
      out.push({
        key: `o${o.ts}${o.order_id ?? o.ticker}${o.action}`,
        ts: o.ts,
        cls: kindClass(o.action),
        text: o.action.toUpperCase(),
        trade: { ticker: o.ticker, side: o.side, size: `${qty(o.size)}@${px(o.price)}`, reason: o.reason },
      })
    }
    for (const l of journal.logs.slice(-200)) {
      if (l.level === 'WARNING' || l.level === 'ERROR' || l.level === 'CRITICAL') {
        out.push({ key: `l${l.ts}${l.msg}`, ts: l.ts, cls: l.level === 'WARNING' ? 'yl' : 'neg', text: l.msg })
      }
    }
    return out.sort((a, b) => b.ts - a.ts).slice(0, 40)
  }, [journal])

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
            <span className="tk">{i.trade.ticker}</span>{' '}
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
}
