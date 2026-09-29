import { useMemo, useState } from 'react'
import type { Journal } from '../lib/useJournal'
import { hms, px, qty, usd } from '../lib/format'
import { toggled } from '../lib/sets'
import { useFreshKeys } from '../lib/useFreshKeys'
import type { LegQuote } from '../types'
import { Chips, Empty, Panel } from './Panel'

const KINDS = ['place', 'cancel', 'decrease', 'reject', 'fill', 'quote'] as const
type Kind = (typeof KINDS)[number]

const KIND_CLASS: Record<Kind, string> = {
  place: 'cy',
  cancel: 'dim',
  decrease: 'yl',
  reject: 'neg',
  fill: 'mg',
  quote: 'am',
}

interface BlotterRow {
  id: string
  ts: number
  kind: Kind
  ticker: string
  side: string
  price: number | null
  size: number | null
  info: string
}

const legText = (q: LegQuote) =>
  q.price === null ? `— (${q.reason})` : `${qty(q.size)}@${px(q.price)}`

function rows(journal: Journal): BlotterRow[] {
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
      info: o.action === 'reject' ? (o.error ?? '') : (o.order_id ?? '').slice(0, 12),
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
    }),
  )
  return out.sort((a, b) => b.ts - a.ts)
}

export function Blotter({ journal }: { journal: Journal }) {
  const [kinds, setKinds] = useState<ReadonlySet<Kind>>(() => new Set(KINDS))
  const [query, setQuery] = useState('')
  const all = useMemo(() => rows(journal), [journal])
  const q = query.trim().toUpperCase()
  const shown = all.filter((r) => kinds.has(r.kind) && (!q || r.ticker.toUpperCase().includes(q))).slice(0, 800)
  const fresh = useFreshKeys(all.map((r) => r.id))

  return (
    <Panel
      title="Blotter"
      help="panel:blotter"
      note={`${shown.length} shown`}
      span={7}
      height="lg"
      tools={
        <>
          <Chips options={KINDS} selected={kinds} onToggle={(k) => setKinds((s) => toggled(s, k))} helpPrefix="type" />
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
        <table>
          <thead>
            <tr>
              <th className="l">Time</th>
              <th className="l" data-help="blot:Type">
                Type
              </th>
              <th className="l">Ticker</th>
              <th className="l" data-help="blot:Side">
                Side
              </th>
              <th data-help="blot:Price">Price</th>
              <th data-help="blot:Qty">Qty</th>
              <th className="l" data-help="blot:Info">
                Info
              </th>
            </tr>
          </thead>
          <tbody>
            {shown.map((r) => (
              <tr key={r.id} className={fresh.has(r.id) ? (r.kind === 'fill' ? 'row-fill' : 'row-new') : undefined}>
                <td className="l dim">{hms(r.ts)}</td>
                <td className={`l ${KIND_CLASS[r.kind]}`} data-help={`type:${r.kind}`}>
                  {r.kind.toUpperCase()}
                </td>
                <td className="l">{r.ticker}</td>
                <td className="l">{r.side.toUpperCase()}</td>
                <td>{r.price === null ? '' : px(r.price)}</td>
                <td>{r.size === null ? '' : qty(r.size)}</td>
                <td className="l dim">{r.info}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Panel>
  )
}
