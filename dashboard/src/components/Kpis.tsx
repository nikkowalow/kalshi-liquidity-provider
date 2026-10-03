import type { ReactNode } from 'react'
import { num, qty, signClass, signedUsd, usd } from '../lib/format'
import type { RunState } from '../types'
import { Flash } from './Flash'
import { Ticking } from './Ticking'

function Kpi({
  label,
  value,
  raw,
  sub,
  meter,
}: {
  label: string
  value: ReactNode
  raw?: unknown // underlying value; a change makes the tile flash
  sub?: ReactNode
  meter?: number | null
}) {
  return (
    <div className="kpi">
      <div className="k" data-help={`kpi:${label}`}>
        {label}
      </div>
      <div className="v">
        <Flash value={raw === undefined ? value : raw}>{value}</Flash>
      </div>
      <div className="s">{sub}</div>
      {meter != null && (
        <div className={`meter${meter > 0.9 ? ' hot' : ''}`}>
          <i style={{ width: `${Math.min(100, meter * 100).toFixed(0)}%` }} />
        </div>
      )}
    </div>
  )
}

export function Kpis({
  state,
  fills,
  rejects,
}: {
  state: RunState | null
  fills: number
  rejects: number
}) {
  const t = state?.totals
  const cap = num(t?.capital_in_use)
  const max = num(t?.max_capital)
  const rate = num(t?.rewards_per_hour)
  return (
    <div className="kpis">
      <Kpi label="Balance" value={usd(t?.balance)} raw={num(t?.balance)} />
      <Kpi
        label="Capital in use"
        value={usd(cap)}
        raw={cap}
        sub={max ? `of ${usd(max)} budget${state?.budget_binding ? ' · BINDING' : ''}` : 'no budget set'}
        meter={max && cap !== null ? cap / max : null}
      />
      <Kpi
        label="Session P&L"
        value={<span className={signClass(t?.session_pnl)}>{signedUsd(t?.session_pnl)}</span>}
        raw={num(t?.session_pnl)}
        sub="mark-to-market, this session"
      />
      <Kpi
        label="Est. rewards"
        value={
          <Ticking
            value={num(t?.rewards_earned)}
            perHour={num(t?.rewards_per_hour)}
            at={state?.updated_at ?? null}
          />
        }
        raw={num(t?.rewards_earned)}
        sub={
          t?.rewards_session != null
            ? `${usd(t.rewards_session, 4)} this session · all ${state?.session ?? 1} sessions`
            : "bot's estimate"
        }
      />
      <Kpi
        label="Rewards paid"
        value={<span className="pos">{usd(t?.rewards_paid)}</span>}
        raw={num(t?.rewards_paid)}
        sub={
          t?.rewards_paid == null
            ? 'checking Kalshi…'
            : num(t.rewards_earned)
              ? `actual · ${((num(t.rewards_paid)! / num(t.rewards_earned)!) * 100).toFixed(0)}% of est.`
              : 'actual, from Kalshi'
        }
      />
      <Kpi
        label="Reward rate"
        value={`${usd(rate, 3)}/h`}
        raw={rate}
        sub="last ~10 minutes"
      />
      <Kpi
        label="Daily reward rate"
        value={rate === null ? '—' : `${usd(rate * 24)}/day`}
        raw={rate}
        sub="reward rate x 24h"
      />
      <Kpi label="Exposure" value={usd(t?.exposure)} raw={num(t?.exposure)} sub="position cost" />
      <Kpi label="Resting" value={qty(t?.resting_orders)} raw={num(t?.resting_orders)} sub="bot orders" />
      <Kpi
        label="Fills"
        value={num(t?.fills) ?? fills}
        raw={num(t?.fills) ?? fills}
        sub={`all sessions · ${rejects} recent rejects`}
      />
      <Kpi label="Requotes" value={qty(t?.requotes)} raw={num(t?.requotes)} />
      <Kpi
        label="Feed"
        raw={state ? `${state.ws_connected}|${state.trading_active}|${state.globally_paused}` : null}
        value={
          state ? (
            state.ws_connected ? <span className="pos">WS UP</span> : <span className="neg">WS DOWN</span>
          ) : (
            '—'
          )
        }
        sub={
          state
            ? `${state.trading_active ? 'exchange open' : 'EXCHANGE CLOSED'}${state.globally_paused ? ' · PAUSED' : ''}`
            : ''
        }
      />
      {state?.halt_reason && (
        <Kpi label="Halt" raw={state.halt_reason} value={<span className="neg">HALTED</span>} sub={state.halt_reason} />
      )}
    </div>
  )
}
