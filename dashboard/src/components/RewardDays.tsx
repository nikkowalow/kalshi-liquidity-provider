import { type CSSProperties, memo, useEffect, useMemo, useState } from 'react'
import { type BalanceChange, fetchBalanceChanges } from '../lib/balance'
import { usd } from '../lib/format'
import { rewardDays } from '../lib/rewardDays'

const REFRESH_MS = 5 * 60_000 // payouts land a few times a day; the bot caches this call too

/** A row of days, shaded by the rewards Kalshi paid (oldest left, today right). */
export const RewardDays = memo(function RewardDays() {
  const [changes, setChanges] = useState<BalanceChange[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    const load = () =>
      void fetchBalanceChanges().then((r) => {
        if (!alive) return
        if ('error' in r) setError(r.error)
        else {
          setChanges(r)
          setError(null)
        }
      })
    load()
    const timer = window.setInterval(load, REFRESH_MS)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])
  const days = useMemo(() => rewardDays(changes ?? []), [changes])
  const best = Math.max(...days.map((d) => d.paid), 0)
  const total = days.reduce((sum, d) => sum + d.paid, 0)
  return (
    <div className="reward-days" data-help="panel:reward-days">
      <span className="rd-label">
        REWARDS PAID/DAY
        <i>{changes ? `${usd(total)} total` : error ? 'unavailable' : 'loading…'}</i>
      </span>
      <div className="rd-row">
        {error && !changes && <span className="dim rd-msg">{error}</span>}
        {days.map((d) => {
          // Strength: share of the best day (sqrt, so small days still show).
          const strength = best > 0 ? Math.sqrt(d.paid / best) : 0
          return (
            <div
              key={d.key}
              className={`rd-day${d.paid > 0 ? '' : ' zero'}${d.today ? ' today' : ''}${strength > 0.6 ? ' hot' : ''}`}
              style={{ '--rd': strength.toFixed(3) } as CSSProperties}
              title={`${d.label}${d.today ? ' (today, so far)' : ''}: ${usd(d.paid, 2)} paid by Kalshi in ${d.payouts} payout${d.payouts === 1 ? '' : 's'}`}
            >
              <span className="rd-date">{d.label}</span>
              <span className="rd-amt">{usd(d.paid)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
})
