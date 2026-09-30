import { useEffect, useState } from 'react'
import { duration } from '../lib/format'
import type { RunInfo, RunState } from '../types'
import { Flash } from './Flash'

const STALE_AFTER_S = 5

function effectiveStatus(state: RunState | null): string {
  if (!state) return '—'
  if (state.status === 'running' && Date.now() / 1000 - state.updated_at > STALE_AFTER_S) {
    return 'stale'
  }
  return state.status
}

function useClock(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])
  return now
}

interface TopBarProps {
  runs: RunInfo[]
  runId: string | null
  onChooseRun: (id: string) => void
  follow: boolean
  onFollow: (follow: boolean) => void
  state: RunState | null
  serverUp: boolean
}

export function TopBar({ runs, runId, onChooseRun, follow, onFollow, state, serverUp }: TopBarProps) {
  const now = useClock()
  const status = effectiveStatus(state)
  const uptime = state ? duration((state.status === 'running' ? now / 1000 : state.updated_at) - state.started_at) : ''

  return (
    <div className="bar">
      <span className="brand">KLP &lt;GO&gt;</span>
      <select value={runId ?? ''} onChange={(e) => onChooseRun(e.target.value)}>
        {runs.length === 0 && <option value="">no runs yet — start the bot</option>}
        {runs.map((r) => (
          <option key={r.id} value={r.id}>
            {r.id} [{r.status}]
          </option>
        ))}
      </select>
      <label data-help="bar:follow">
        <input type="checkbox" checked={follow} onChange={(e) => onFollow(e.target.checked)} /> follow
        latest
      </label>
      <span className={`badge ${status}`} data-help="bar:status">
        <Flash value={state?.updated_at ?? null}>
          <span className={status === 'running' ? 'pulse' : undefined}>●</span>
        </Flash>{' '}
        {status}
      </span>
      <span className={`badge ${state?.mode ?? ''}`} data-help="bar:mode">
        {state?.mode ?? '—'}
      </span>
      {state && <span>ENV {state.environment}</span>}
      {!serverUp && <span className="badge halted">● dashboard server unreachable</span>}
      <span className="spacer" />
      {state?.session != null && (
        <span data-help="bar:session">
          SESSION {state.session}
          {state.first_started_at
            ? ` · SINCE ${new Date(state.first_started_at * 1000).toLocaleString([], {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })}`
            : ''}
        </span>
      )}
      {uptime && <span>UP {uptime}</span>}
      <span>{new Date(now).toLocaleTimeString([], { hour12: false })}</span>
    </div>
  )
}
