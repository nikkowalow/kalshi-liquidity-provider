import { useEffect, useState } from 'react'
import { wsUrl } from './api'
import type {
  ControlInfo,
  FillEvent,
  FillRiskEvent,
  JournalEvent,
  LogEvent,
  MarketsEvent,
  MetricsEvent,
  OrderEvent,
  QuoteEvent,
  RunEndEvent,
  RunStartEvent,
  RunState,
  Sample,
  ScanReport,
  ServerMessage,
} from '../types'

const MAX_EVENTS = 5000
const LIVE_SECONDS = 3600 // per-second totals kept for the short chart windows
const MAX_METRICS = 20000 // ~2 days at one sample per 10s; older history comes thinned in the hello
const HISTORY_POINTS = 2000
const FLUSH_MS = 100 // messages arriving within this window are applied in one render
const RETRY_MS = [500, 1000, 2000, 5000] // reconnect backoff while the bot is down

/** The link to the bot: waiting for the first hello, streaming, or lost (reconnecting). */
export type Link = 'connecting' | 'live' | 'offline'

export interface Journal {
  orders: OrderEvent[]
  fills: FillEvent[]
  fillRisks: FillRiskEvent[] // fill-risk record of each fill (when the market was entered, at the fill)
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
  fillRisks: [],
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
      case 'fill_risk':
        j = { ...j, fillRisks: append(j.fillRisks, e) }
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

/** Add a snapshot's totals to the per-second history (skipping repeats, keeping an hour). */
function appendSample(prev: Sample[], snapshot: RunState): Sample[] {
  if (!snapshot.totals) return prev
  const sample = { t: snapshot.updated_at, totals: snapshot.totals }
  const last = prev.at(-1)
  if (last && last.t >= sample.t) return prev
  const cutoff = sample.t - LIVE_SECONDS
  const kept = prev.length && prev[0].t < cutoff ? prev.filter((s) => s.t >= cutoff) : prev
  return [...kept, sample]
}

/**
 * The running bot, live: one WebSocket to its API (/api/ws, see src/kalshi_lp/api.py).
 *
 * The bot greets each connection with its latest snapshot, recent events and the
 * totals history, then pushes every event and snapshot as it happens. While the
 * bot is stopped the page keeps what it last had and reconnects on its own; a
 * restarted bot's hello replaces everything.
 */
export function useJournal() {
  const [runId, setRunId] = useState<string | null>(null)
  const [state, setState] = useState<RunState | null>(null)
  const [journal, setJournal] = useState<Journal>(emptyJournal)
  // Totals from every snapshot while the page is open: per-second detail for short charts.
  const [live, setLive] = useState<Sample[]>([])
  const [link, setLink] = useState<Link>('connecting')
  const [controls, setControls] = useState<ControlInfo | null>(null)
  // The market scanner's latest report (every few minutes; research only, never traded on).
  const [scan, setScan] = useState<ScanReport | null>(null)

  useEffect(() => {
    let socket: WebSocket | null = null
    let stopped = false
    let attempt = 0
    let retry: ReturnType<typeof setTimeout> | undefined
    let flushTimer: ReturnType<typeof setTimeout> | undefined
    let pending: JournalEvent[] = []
    let snapshot: RunState | null = null

    const flush = () => {
      flushTimer = undefined
      const events = pending
      const snap = snapshot
      pending = []
      snapshot = null
      if (events.length) setJournal((j) => ingest(j, events))
      if (snap) {
        setState(snap)
        setLive((prev) => appendSample(prev, snap))
      }
    }
    const schedule = () => {
      if (flushTimer === undefined) flushTimer = setTimeout(flush, FLUSH_MS)
    }

    const onMessage = (ev: MessageEvent<string>) => {
      const msg = JSON.parse(ev.data) as ServerMessage
      switch (msg.channel) {
        case 'hello':
          pending = []
          snapshot = null
          attempt = 0
          setRunId(msg.run_id)
          setControls(msg.controls ?? null) // a new token every time the bot starts
          setScan(msg.scan ?? null)
          setJournal(ingest({ ...emptyJournal(), metrics: msg.metrics }, msg.events))
          if (msg.state) {
            const first = msg.state
            setState(first)
            setLive((prev) => appendSample(prev, first))
          }
          setLink('live')
          break
        case 'events':
          for (const e of msg.data) pending.push(e)
          schedule()
          break
        case 'state':
          snapshot = msg.data
          schedule()
          break
        case 'scan':
          setScan(msg.data)
          break
      }
    }

    const connect = () => {
      const ws = new WebSocket(wsUrl(`/api/ws?points=${HISTORY_POINTS}`))
      socket = ws
      ws.onmessage = onMessage
      ws.onclose = () => {
        if (stopped) return
        clearTimeout(flushTimer)
        flush() // whatever arrived just before the bot went away (e.g. its final snapshot)
        setLink('offline')
        retry = setTimeout(connect, RETRY_MS[Math.min(attempt++, RETRY_MS.length - 1)])
      }
    }
    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      clearTimeout(flushTimer)
      socket?.close()
    }
  }, [])

  return { runId, state, journal, live, link, controls, scan }
}
