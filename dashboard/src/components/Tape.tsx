import { useMemo } from 'react'
import type { Journal } from '../lib/useJournal'
import { hms, px, qty } from '../lib/format'

interface TapeItem {
  key: string
  ts: number
  cls: string
  text: string
}

const KIND_CLASS: Record<string, string> = {
  place: 'cy',
  cancel: 'dim',
  decrease: 'yl',
  reject: 'neg',
}

/** Scrolling ticker of the latest bot activity: fills, orders, and warnings. */
export function Tape({ journal }: { journal: Journal }) {
  const items = useMemo(() => {
    const out: TapeItem[] = []
    for (const f of journal.fills.slice(-15)) {
      out.push({
        key: `f${f.ts}${f.order_id}`,
        ts: f.ts,
        cls: 'mg',
        text: `FILL ${f.ticker} ${(f.side ?? '').toUpperCase()} ${qty(f.count)}@${px(f.price)}`,
      })
    }
    for (const o of journal.orders.slice(-30)) {
      out.push({
        key: `o${o.ts}${o.order_id ?? o.ticker}${o.action}`,
        ts: o.ts,
        cls: KIND_CLASS[o.action] ?? '',
        text: `${o.action.toUpperCase()} ${o.ticker} ${o.side.toUpperCase()} ${qty(o.size)}@${px(o.price)}`,
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
      <span key={`${copy}${i.key}`} className={`tape-item ${i.cls}`}>
        <span className="dim">{hms(i.ts)}</span> {i.text}
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
