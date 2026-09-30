import { qty, signClass, usd } from '../lib/format'
import type { Num } from '../types'

interface Risk {
  est_daily_reward?: Num
  est_fills_per_day?: Num
  fill_cost_per_day?: Num
  net_daily_reward?: Num
}

/** Expected fills per day: green when no recent sweep would reach us, red when they eat half the reward. */
export function FillsCell({ r }: { r: Risk }) {
  const fills = r.est_fills_per_day
  if (fills == null) return <span className="dim">—</span>
  const cost = r.fill_cost_per_day ?? 0
  const est = r.est_daily_reward ?? 0
  const cls = fills === 0 ? 'pos' : est > 0 && cost >= est / 2 ? 'neg' : 'yl'
  return (
    <span className={cls} title={`fills cost ~${usd(cost)}/day`}>
      {fills === 0 ? '0' : qty(fills)}
    </span>
  )
}

/** Net reward per hour (reward minus expected fill cost), what selection ranks by. */
export function NetCell({ r }: { r: Risk }) {
  const net = r.net_daily_reward
  if (net == null) return <span className="dim">—</span>
  return (
    <span className={signClass(net)} title={`${usd(net)}/day`}>
      {usd(net / 24, 3)}
    </span>
  )
}
