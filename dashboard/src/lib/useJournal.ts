import { useCallback, useEffect, useRef, useState } from 'react'
import type {
  FillEvent,
  JournalEvent,
  LogEvent,
  MarketsEvent,
  MetricsEvent,
  OrderEvent,
  QuoteEvent,
  RunEndEvent,
  RunInfo,
  RunStartEvent,
  RunState,
} from '../types'

const MAX_EVENTS = 5000
const MAX_METRICS = 20000 // ~2 days at one sample per 10s; older history comes thinned from /metrics
const HISTORY_POINTS = 2000
const TAIL_BYTES = 3_000_000 // first load: only the recent end of a long journal
const POLL_MS = 1000
const RUNS_POLL_MS = 3000

export interface Journal {
  orders: OrderEvent[]
  fills: FillEvent[]
  quotes: QuoteEvent[]
  logs: LogEvent[]
  selections: MarketsEvent[]
  metrics: MetricsEvent[]
  start: RunStartEvent | null
  end: RunEndEvent | null
}

const emptyJournal = (): Journal => ({
  orders: [],
  fills: [],
  quotes: [],
  logs: [],
  selections: [],
  metrics: [],
  start: null,
  end: null,
})

function append<T>(list: T[], item: T, max = MAX_EVENTS): T[] {
  const next = list.length >= max ? list.slice(list.length - max + 1) : list.slice()
  next.push(item)
  return next
}

function ingest(journal: Journal, events: JournalEvent[]): Journal {
  let j = journal
  for (const e of events) {
    switch (e.type) {
      case 'order':
        j = { ...j, orders: append(j.orders, e) }
        break
      case 'fill':
        j = { ...j, fills: append(j.fills, e) }
        break
      case 'quote':
        j = { ...j, quotes: append(j.quotes, e) }
        break
      case 'log':
        j = { ...j, logs: append(j.logs, e) }
        break
      case 'markets':
        j = { ...j, selections: append(j.selections, e) }
        break
      case 'metrics':
        // Skip samples the thinned history already covers.
        if (j.metrics.length && e.ts <= j.metrics[j.metrics.length - 1].ts) break
        j = { ...j, metrics: append(j.metrics, e, MAX_METRICS) }
        break
      case 'ws':
        // Connection changes show up in the log panel.
        j = {
          ...j,
          logs: append(j.logs, {
            type: 'log',
            ts: e.ts,
            level: e.status === 'connected' ? 'INFO' : 'WARNING',
            logger: 'ws',
            msg: `websocket ${e.status}`,
          }),
        }
        break
      case 'run_start':
        j = { ...j, start: e, end: null } // a restart continues the same journal
        break
      case 'run_end':
        j = { ...j, end: e }
        break
    }
  }
  return j
}

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { cache: 'no-store' })
  if (!res.ok) throw new Error(`${url}: ${res.status}`)
  return res.json() as Promise<T>
}

/** Polls the journal server: run list, the selected run's snapshot, and new events. */
export function useJournal() {
  const [runs, setRuns] = useState<RunInfo[]>([])
  const [runId, setRunId] = useState<string | null>(null)
  const [follow, setFollow] = useState(true)
  const [state, setState] = useState<RunState | null>(null)
  const [journal, setJournal] = useState<Journal>(emptyJournal)
  const [connected, setConnected] = useState(true)
  const offset = useRef<number | null>(null) // null: not loaded yet, start from the tail
  const activeRun = useRef<string | null>(null)

  const selectRun = useCallback((id: string | null) => {
    activeRun.current = id
    offset.current = null
    setRunId(id)
    setState(null)
    setJournal(emptyJournal())
  }, [])

  // Run list, and "follow latest".
  useEffect(() => {
    let cancelled = false
    const poll = async () => {
      try {
        const list = await getJSON<RunInfo[]>('/api/runs')
        if (cancelled) return
        setRuns(list)
        setConnected(true)
        const latest = list[0]?.id ?? null
        if (latest && (follow || activeRun.current === null) && latest !== activeRun.current) {
          selectRun(latest)
        }
      } catch {
        if (!cancelled) setConnected(false)
      }
    }
    void poll()
    const timer = setInterval(poll, RUNS_POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [follow, selectRun])

  // Snapshot + incremental events for the selected run.
  useEffect(() => {
    if (!runId) return
    let cancelled = false
    let busy = false
    const poll = async () => {
      if (busy) return
      busy = true
      try {
        const first = offset.current === null
        const [snapshot, batch, history] = await Promise.all([
          getJSON<RunState>(`/api/runs/${runId}/state`),
          getJSON<{ events: JournalEvent[]; offset: number }>(
            first
              ? `/api/runs/${runId}/events?tail=${TAIL_BYTES}`
              : `/api/runs/${runId}/events?offset=${offset.current}`,
          ),
          first
            ? // Optional: an older server has no /metrics; the charts then fill from events.
              getJSON<MetricsEvent[]>(`/api/runs/${runId}/metrics?points=${HISTORY_POINTS}`).catch(
                () => null,
              )
            : Promise.resolve(null),
        ])
        if (cancelled || activeRun.current !== runId) return
        offset.current = batch.offset
        setState(snapshot)
        if (history) setJournal((j) => ({ ...j, metrics: history }))
        if (batch.events.length) setJournal((j) => ingest(j, batch.events))
        setConnected(true)
      } catch {
        if (!cancelled) setConnected(false)
      } finally {
        busy = false
      }
    }
    void poll()
    const timer = setInterval(poll, POLL_MS)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [runId])

  const chooseRun = useCallback(
    (id: string) => {
      setFollow(false)
      selectRun(id)
    },
    [selectRun],
  )

  return { runs, runId, chooseRun, follow, setFollow, state, journal, connected }
}
