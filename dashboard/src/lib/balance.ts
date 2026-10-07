import { apiUrl } from './api'

/** GET /api/balance (see src/kalshi_lp/engine/balance_history.py). */
export interface BalanceChange {
  ts: number
  kind: 'fill' | 'settlement' | 'deposit' | 'withdrawal' | 'reward' | 'unexplained'
  ticker: string | null
  amount: number
  balance_after?: number
  detail: string
}

/** Every balance change since the journal began, newest first (rewards are Kalshi's payouts). */
export async function fetchBalanceChanges(): Promise<BalanceChange[] | { error: string }> {
  try {
    const res = await fetch(apiUrl('/api/balance'))
    const data = await res.json()
    return res.ok ? (data as BalanceChange[]) : { error: data.error ?? res.statusText }
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) }
  }
}
