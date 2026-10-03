import { type ReactNode, useEffect, useState } from 'react'
import { apiUrl } from '../lib/api'
import { num, pct, qty, signClass, signedUsd, usd } from '../lib/format'
import { Stamp } from './Stamp'
import { Ticker } from './Ticker'

/** What selection judged a market on (Candidate.figures in strategy/selection.py). */
interface Figures {
  est_daily_reward?: number
  fill_checked?: boolean
  fills_per_day?: number | null
  fill_events_per_day?: number | null
  fill_cost_per_day?: number | null
  net_daily_reward?: number
  unpaid_bonus?: number
  rank_multiplier?: number
  rank_score?: number
  earned?: number
  earning_days_left?: number
  est_period_payout?: number
  size?: number
  capital_needed?: number
  reward_per_day?: number
  target_size?: number
  competition?: string | null
}

/** A selection entry: a ``markets`` journal event's market, or a deselect's ``selected``. */
type Picked = Figures & { ticker: string; held?: boolean; est_fills_per_day?: number | null }

/** GET /api/deselect/{ticker}?before=ts (see _deselect_record in src/kalshi_lp/api.py). */
type Why =
  | {
      recorded: true
      ts: number
      ticker: string
      stage: string
      reason: string
      figures?: Figures
      rival?: Picked
      position?: number
      reduce_only?: boolean
      selected: Picked[]
    }
  | {
      recorded: false
      ticker: string
      last: Picked & { ts: number }
      replaced_by: { ts: number; markets: Picked[] } | null
    }

const STAGES: { [stage: string]: string } = {
  program: 'no reward program',
  eligibility: 'not eligible',
  book: 'order book',
  payout: "can't reach the payout minimum",
  fill_risk: 'fill risk',
  diversify: 'one market per series',
  outranked: 'outranked',
  capital: 'out of budget',
  paused: 'paused',
  selected: 'still selected',
  unknown: 'unknown',
}

async function fetchRecord(ticker: string, ts: number): Promise<Why | null | { error: string }> {
  try {
    const res = await fetch(apiUrl(`/api/deselect/${encodeURIComponent(ticker)}?before=${ts}`))
    const data = await res.json()
    return res.ok ? (data as Why | null) : { error: data?.error ?? res.statusText }
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) }
  }
}

const dollars = (v: unknown, digits = 2) => (num(v) === null ? '—' : usd(v, digits))

function Field({ label, children, cls }: { label: string; children: ReactNode; cls?: string }) {
  return (
    <div className="md-field">
      <span className="md-label">{label}</span>
      <span className={cls}>{children}</span>
    </div>
  )
}

/** The numbers selection weighed for one market. */
function FigureFields({ f }: { f: Figures }) {
  const fillShare =
    num(f.fill_cost_per_day) !== null && num(f.est_daily_reward)
      ? (f.fill_cost_per_day ?? 0) / (f.est_daily_reward ?? 1)
      : null
  return (
    <>
      <Field label="est reward / day" cls="pos">
        {dollars(f.est_daily_reward)}
      </Field>
      {f.fill_checked === false ? (
        <Field label="fill risk">
          <span className="dim">not checked</span>
        </Field>
      ) : (
        <>
          <Field label="expected fills / day">
            {num(f.fills_per_day) === null ? '—' : `${qty(f.fills_per_day)} contracts`}
            {num(f.fill_events_per_day) !== null && (
              <span className="dim"> · {(f.fill_events_per_day ?? 0).toFixed(2)} sweeps</span>
            )}
          </Field>
          <Field label="expected fill cost / day" cls="neg">
            {dollars(f.fill_cost_per_day)}
            {fillShare !== null && <span className="dim"> · {pct(fillShare)} of the reward</span>}
          </Field>
        </>
      )}
      <Field label="net / day" cls={signClass(f.net_daily_reward)}>
        {num(f.net_daily_reward) === null ? '—' : signedUsd(f.net_daily_reward)}
      </Field>
      {!!f.unpaid_bonus && <Field label="unpaid-earnings bonus / day">{dollars(f.unpaid_bonus)}</Field>}
      {num(f.rank_multiplier) !== null && f.rank_multiplier !== 1 && (
        <Field label="rank multiplier">×{f.rank_multiplier?.toFixed(2)}</Field>
      )}
      <Field label="rank score / day">{dollars(f.rank_score)}</Field>
      <Field label="earned this period">{dollars(f.earned, 4)}</Field>
      <Field label="earning days left">{num(f.earning_days_left)?.toFixed(2) ?? '—'}</Field>
      <Field label="projected this period" cls={(f.est_period_payout ?? 0) >= 1 ? 'pos' : 'neg'}>
        {dollars(f.est_period_payout)}
      </Field>
      <Field label="size per side">{qty(f.size)}</Field>
      <Field label="capital needed">{dollars(f.capital_needed)}</Field>
      <Field label="program / day">{dollars(f.reward_per_day)}</Field>
      <Field label="target size">{qty(f.target_size)}</Field>
      {f.competition && <Field label="competition">{f.competition}</Field>}
    </>
  )
}

/** The markets selection picked instead, with the same numbers. */
function PickedTable({ rows, heldColumn }: { rows: Picked[]; heldColumn: boolean }) {
  if (!rows.length) return <div className="empty">nothing: no market passed the filters</div>
  return (
    <table>
      <thead>
        <tr>
          <th className="l">#</th>
          <th className="l">Market</th>
          {heldColumn && <th className="l">Held</th>}
          <th>Est $/d</th>
          <th>Fill cost $/d</th>
          <th>Net $/d</th>
          <th>Rank $/d</th>
          <th>This period</th>
          <th>Capital</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((m, i) => (
          <tr key={m.ticker}>
            <td className="l dim">{i + 1}</td>
            <td className="l">
              <Ticker value={m.ticker} />
            </td>
            {heldColumn && <td className="l dim">{m.held ? 'yes' : 'new'}</td>}
            <td className="pos">{dollars(m.est_daily_reward)}</td>
            <td className="neg">{dollars(m.fill_cost_per_day)}</td>
            <td className={signClass(m.net_daily_reward)}>
              {num(m.net_daily_reward) === null ? '—' : signedUsd(m.net_daily_reward)}
            </td>
            <td>{dollars(m.rank_score)}</td>
            <td>{dollars(m.est_period_payout)}</td>
            <td>{dollars(m.capital_needed)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Recorded({ r }: { r: Extract<Why, { recorded: true }> }) {
  return (
    <div className="md-grid">
      <section className="md-card wide">
        <h3>Why it was dropped · {STAGES[r.stage] ?? r.stage}</h3>
        <div className="dsl-reason">{r.reason}</div>
        {!!r.position && (
          <div className="md-sub">
            It still held {qty(r.position)} contracts, so it stayed{' '}
            {r.reduce_only ? 'in reduce-only mode (quoting only the side that works the position down)' : 'tracked'}.
          </div>
        )}
      </section>
      {r.figures && (
        <section className="md-card">
          <h3>Its numbers at that scan</h3>
          <FigureFields f={r.figures} />
        </section>
      )}
      {r.rival && (
        <section className="md-card">
          <h3>
            Took the series slot: <Ticker value={r.rival.ticker} />
          </h3>
          <FigureFields f={r.rival} />
        </section>
      )}
      <section className="md-card wide">
        <h3>What the bot picked instead ({r.selected.length})</h3>
        <PickedTable rows={r.selected} heldColumn />
      </section>
    </div>
  )
}

function Reconstructed({ r }: { r: Extract<Why, { recorded: false }> }) {
  const last = r.last
  return (
    <div className="md-grid">
      <section className="md-card wide">
        <h3>Why it was dropped · not recorded</h3>
        <div className="dsl-reason">
          The bot left this market before it recorded its reasons. These are the numbers the selections had: the
          last time it was picked, and the selection that replaced it.
        </div>
      </section>
      <section className="md-card">
        <h3>
          Last picked <Stamp ts={last.ts} date />
        </h3>
        <FigureFields f={{ ...last, fills_per_day: last.est_fills_per_day ?? last.fills_per_day }} />
      </section>
      <section className="md-card wide">
        <h3>
          {r.replaced_by ? (
            <>
              The selection that dropped it <Stamp ts={r.replaced_by.ts} date /> ({r.replaced_by.markets.length})
            </>
          ) : (
            'No later selection found'
          )}
        </h3>
        {r.replaced_by && <PickedTable rows={r.replaced_by.markets} heldColumn={false} />}
      </section>
    </div>
  )
}

/** Why the bot left a market: opened from a "market no longer selected" cancel. */
export function DeselectDetail({ ticker, ts, onClose }: { ticker: string; ts: number; onClose: () => void }) {
  const [record, setRecord] = useState<Why | null | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let alive = true
    void fetchRecord(ticker, ts).then((r) => {
      if (!alive) return
      if (r && 'error' in r) setError(r.error)
      else setRecord(r)
    })
    // Capture phase, so Escape closes only this popup and not the market popup under it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return
      e.stopImmediatePropagation()
      onClose()
    }
    window.addEventListener('keydown', onKey, true)
    return () => {
      alive = false
      window.removeEventListener('keydown', onKey, true)
    }
  }, [ticker, ts, onClose])

  return (
    <div className="md-backdrop" onClick={onClose}>
      <div
        className="md st dsl"
        role="dialog"
        aria-modal="true"
        aria-label="why the bot left this market"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="md-head">
          <div>
            <span className="md-title">WHY THE BOT LEFT</span>
            <Ticker value={ticker} />{' '}
            <span className="dim">
              {record && record.recorded ? (
                <>
                  at the scan <Stamp ts={record.ts} date />
                </>
              ) : null}
            </span>
          </div>
          <div className="md-actions">
            <button type="button" className="chip" onClick={onClose} aria-label="close">
              ✕ esc
            </button>
          </div>
        </header>
        <div className="st-fields">
          {error ? (
            <div className="empty">couldn&apos;t load the reason: {error}</div>
          ) : record === undefined ? (
            <div className="empty">loading…</div>
          ) : record === null ? (
            <div className="empty">no record of the bot selecting this market before then</div>
          ) : record.recorded ? (
            <Recorded r={record} />
          ) : (
            <Reconstructed r={record} />
          )}
        </div>
      </div>
    </div>
  )
}
