import { memo, useMemo, useRef, useState } from 'react'
import type { Journal } from '../lib/useJournal'
import { kindClass, px, qty, sideClass, usd } from '../lib/format'
import { toggled } from '../lib/sets'
import { type Accessors, sortRows, useSort } from '../lib/sort'
import { useFreshKeys } from '../lib/useFreshKeys'
import { useVirtualRows } from '../lib/useVirtualRows'
import type { LegQuote } from '../types'
import { Chips, Empty, Panel } from './Panel'
import { SortTh } from './SortTh'
import { PadRow } from './PadRow'
import { Stamp } from './Stamp'
import { Ticker } from './Ticker'

const KINDS = ['place', 'cancel', 'decrease', 'exit', 'reject', 'fill', 'quote'] as const
type Kind = (typeof KINDS)[number]

interface BlotterRow {
  id: string
  ts: number
  kind: Kind
  ticker: string
  side: string
  price: number | null
  size: number | null
  info: string
  reason: string
  quote?: { bid: string; ask: string; rest: string } // quote rows: legs colored separately
}

const legText = (q: LegQuote) =>
  q.price === null ? `— (${q.reason})` : `${qty(q.size)}@${px(q.price)}`

type Lists = Pick<Journal, 'orders' | 'fills' | 'quotes'>

function rows(journal: Lists): BlotterRow[] {
  const out: BlotterRow[] = []
  journal.orders.forEach((o, i) =>
    out.push({
      id: `o${i}`,
      ts: o.ts,
      kind: o.action,
      ticker: o.ticker,
      side: o.side,
      price: o.price,
      size: o.size,
      info:
        o.action === 'reject'
          ? (o.error ?? '')
          : o.action === 'exit'
            ? `filled ${qty(o.filled)} of ${qty(o.size)} (immediate-or-cancel)`
            : (o.order_id ?? '').slice(0, 12),
      reason: o.reason ?? '',
    }),
  )
  journal.fills.forEach((f, i) =>
    out.push({
      id: `f${i}`,
      ts: f.ts,
      kind: 'fill',
      ticker: f.ticker,
      side: f.side ?? '',
      price: Number(f.price),
      size: Number(f.count),
      info: `${f.is_taker ? 'TAKER' : 'maker'} · fee ${f.fee ?? '?'} · pos→${f.post_position ?? '?'}`,
      reason: '',
    }),
  )
  journal.quotes.forEach((q, i) =>
    out.push({
      id: `q${i}`,
      ts: q.ts,
      kind: 'quote',
      ticker: q.ticker,
      side: '',
      price: null,
      size: null,
      info: `bid ${legText(q.yes)} · ask ${legText(q.no)} · pos ${qty(q.position)} · est ${usd(q.est_daily_reward)}/d`,
      reason: `bid: ${q.yes.reason} · ask: ${q.no.reason}`,
      quote: {
        bid: legText(q.yes),
        ask: legText(q.no),
        rest: ` · pos ${qty(q.position)} · est ${usd(q.est_daily_reward)}/d`,
      },
    }),
  )
  return out.sort((a, b) => b.ts - a.ts)
}

const ACCESSORS: Accessors<BlotterRow> = {
  Time: (r) => r.ts,
  Type: (r) => r.kind,
  Ticker: (r) => r.ticker,
  Side: (r) => r.side || null,
  Price: (r) => r.price,
  Qty: (r) => r.size,
  Reason: (r) => r.reason || null,
  Info: (r) => r.info || null,
}

const BlotterLine = memo(function BlotterLine({ r, fresh }: { r: BlotterRow; fresh: boolean }) {
  return (
    <tr className={fresh ? (r.kind === 'fill' ? 'row-fill' : 'row-new') : undefined}>
      <td className="l dim">
        <Stamp ts={r.ts} />
      </td>
      <td className={`l ${kindClass(r.kind)}`} data-help={`type:${r.kind}`}>
        {r.kind.toUpperCase()}
      </td>
      <td className="l">
        <Ticker value={r.ticker} />
      </td>
      <td className={`l ${sideClass(r.side)}`}>{r.side.toUpperCase()}</td>
      <td className={sideClass(r.side)}>{r.price === null ? '' : px(r.price)}</td>
      <td className={sideClass(r.side)}>{r.size === null ? '' : qty(r.size)}</td>
      <td className="l reason" title={r.reason}>
        {r.reason}
      </td>
      <td className="l dim">
        {r.quote ? (
          <>
            bid <span className="bid">{r.quote.bid}</span> · ask <span className="ask">{r.quote.ask}</span>
            {r.quote.rest}
          </>
        ) : (
          r.info
        )}
      </td>
    </tr>
  )
})

/** Only re-renders when the journal (new events), a filter, the sort or the scroll changes. */
export const Blotter = memo(function Blotter({ orders, fills, quotes }: Lists) {
  const sorter = useSort('blotter')
  const [kinds, setKinds] = useState<ReadonlySet<Kind>>(() => new Set(KINDS))
  const [query, setQuery] = useState('')
  const all = useMemo(() => rows({ orders, fills, quotes }), [orders, fills, quotes])
  const ids = useMemo(() => all.map((r) => r.id), [all])
  const q = query.trim().toUpperCase()
  const shown = useMemo(
    () =>
      sortRows(
        all.filter((r) => kinds.has(r.kind) && (!q || r.ticker.toUpperCase().includes(q))),
        ACCESSORS,
        sorter.state,
      ),
    [all, kinds, q, sorter.state],
  )
  const fresh = useFreshKeys(ids)
  const body = useRef<HTMLDivElement>(null)
  const win = useVirtualRows(body, shown.length)

  return (
    <Panel
      title="Blotter"
      help="panel:blotter"
      note={`${shown.length} shown`}
      span={7}
      height="lg"
      bodyRef={body}
      tools={
        <>
          <Chips options={KINDS} selected={kinds} onToggle={(k) => setKinds((s) => toggled(s, k))} helpPrefix="type" colorClass={kindClass} />
          <input
            className="filter"
            placeholder="filter ticker…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </>
      }
    >
      {shown.length === 0 ? (
        <Empty>no activity yet</Empty>
      ) : (
        <table className="vt">
          <thead>
            <tr>
              <SortTh k="Time" sorter={sorter} align="l">
                Time
              </SortTh>
              <SortTh k="Type" sorter={sorter} align="l" help="blot:Type" text>
                Type
              </SortTh>
              <SortTh k="Ticker" sorter={sorter} align="l" text>
                Ticker
              </SortTh>
              <SortTh k="Side" sorter={sorter} align="l" help="blot:Side" text>
                Side
              </SortTh>
              <SortTh k="Price" sorter={sorter} help="blot:Price">
                Price
              </SortTh>
              <SortTh k="Qty" sorter={sorter} help="blot:Qty">
                Qty
              </SortTh>
              <SortTh k="Reason" sorter={sorter} align="l" help="blot:Reason" text>
                Reason
              </SortTh>
              <SortTh k="Info" sorter={sorter} align="l" help="blot:Info" text>
                Info
              </SortTh>
            </tr>
          </thead>
          <tbody>
            <PadRow height={win.padTop} cols={8} />
            {shown.slice(win.start, win.end).map((r) => (
              <BlotterLine key={r.id} r={r} fresh={fresh.has(r.id)} />
            ))}
            <PadRow height={win.padBottom} cols={8} />
          </tbody>
        </table>
      )}
    </Panel>
  )
})
