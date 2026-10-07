import { useEffect, useMemo, useState } from 'react'
import { type BalanceChange, fetchBalanceChanges } from '../lib/balance'
import { signClass, signedUsd, usd } from '../lib/format'
import { Stamp } from './Stamp'
import { Ticker } from './Ticker'

type Change = BalanceChange

const KINDS: Change['kind'][] = ['reward', 'fill', 'settlement', 'deposit', 'withdrawal', 'unexplained']
const KIND_CLASS: Record<Change['kind'], string> = {
  reward: 'pos',
  fill: 'mg',
  settlement: 'yl',
  deposit: 'bid',
  withdrawal: 'ask',
  unexplained: 'neg',
}

/** Every change to the account balance, with when it happened (click the Balance tile). */
export function BalanceHistory({ onClose }: { onClose: () => void }) {
  const [changes, setChanges] = useState<Change[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [shown, setShown] = useState<Set<Change['kind']>>(() => new Set(KINDS))

  useEffect(() => {
    let alive = true
    void fetchBalanceChanges().then((r) => {
      if (!alive) return
      if ('error' in r) setError(r.error)
      else setChanges(r)
    })
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => {
      alive = false
      window.removeEventListener('keydown', onKey)
    }
  }, [onClose])

  const totals = useMemo(() => {
    const t = new Map<Change['kind'], { n: number; sum: number }>()
    for (const c of changes ?? []) {
      const cur = t.get(c.kind) ?? { n: 0, sum: 0 }
      t.set(c.kind, { n: cur.n + 1, sum: cur.sum + c.amount })
    }
    return t
  }, [changes])
  const rows = (changes ?? []).filter((c) => shown.has(c.kind))

  return (
    <div className="md-backdrop" onClick={onClose}>
      <div className="md st" role="dialog" aria-modal="true" aria-label="balance history" onClick={(e) => e.stopPropagation()}>
        <header className="md-head">
          <div>
            <span className="md-title">BALANCE HISTORY</span>
            <span className="dim">
              {changes ? `${changes.length} changes since the journal began` : error ? '' : 'loading…'}
            </span>
          </div>
          <div className="md-actions">
            <button type="button" className="chip" onClick={onClose} aria-label="close">
              ✕ esc
            </button>
          </div>
        </header>
        {error ? (
          <div className="empty">couldn&apos;t load the balance history: {error}</div>
        ) : (
          <>
            <div className="bh-sum">
              {KINDS.filter((k) => totals.has(k)).map((k) => {
                const t = totals.get(k)!
                const on = shown.has(k)
                return (
                  <button
                    key={k}
                    type="button"
                    className={`chip${on ? ' on' : ''}`}
                    data-help={`balance:${k}`}
                    onClick={() =>
                      setShown((s) => {
                        const next = new Set(s)
                        if (on) next.delete(k)
                        else next.add(k)
                        return next
                      })
                    }
                  >
                    <span className={KIND_CLASS[k]}>{k}</span> {t.n} · {signedUsd(t.sum)}
                  </button>
                )
              })}
            </div>
            <div className="st-fields">
              <table>
                <thead>
                  <tr>
                    <th className="l">Time</th>
                    <th className="l">What</th>
                    <th className="l">Market</th>
                    <th>Amount</th>
                    <th>Balance after</th>
                    <th className="l">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((c, i) => (
                    <tr key={`${c.ts}-${i}`}>
                      <td className="l dim">
                        <Stamp ts={c.ts} date />
                      </td>
                      <td className={`l ${KIND_CLASS[c.kind]}`}>{c.kind}</td>
                      <td className="l">{c.ticker ? <Ticker value={c.ticker} /> : <span className="dim">—</span>}</td>
                      <td className={signClass(c.amount)}>{signedUsd(c.amount, 4)}</td>
                      <td>{c.balance_after == null ? '—' : usd(c.balance_after)}</td>
                      <td className="l dim">{c.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {changes && rows.length === 0 && <div className="empty">nothing to show</div>}
            </div>
            <footer className="st-foot">
              <span className="dim">
                fills, settlements, deposits and withdrawals: Kalshi&apos;s records (exact times) · rewards:
                balance jumps those records don&apos;t explain, timed from the bot&apos;s 10-second balance
                readings
              </span>
            </footer>
          </>
        )}
      </div>
    </div>
  )
}
