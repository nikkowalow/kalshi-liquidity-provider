import { Blotter } from './components/Blotter'
import { Charts } from './components/Charts'
import { Controls } from './components/Controls'
import { HelpLayer } from './components/HelpLayer'
import { Kpis } from './components/Kpis'
import { MarketDetail } from './components/MarketDetail'
import { MarketsTable } from './components/MarketsTable'
import { OrdersAndFills, RunConfig, Selections } from './components/SidePanels'
import { Scanner } from './components/Scanner'
import { Tape } from './components/Tape'
import { TopBar } from './components/TopBar'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { num } from './lib/format'
import { scaleJournal, scaleSamples, scaleState } from './lib/scale'
import { useJournal } from './lib/useJournal'
import { OpenMarketContext } from './lib/openMarket'
import { OpenDeselectContext } from './lib/openDeselect'
import { DeselectDetail } from './components/DeselectDetail'
import type { MarketRow } from './types'
import { useMultiplier } from './lib/useMultiplier'

export default function App() {
  const raw = useJournal()
  const { runId, link } = raw
  const [multiplier, setMultiplier] = useMultiplier()
  // Everything below sees the projected numbers; the multiplier is display-only.
  const { state, rewardFactor } = useMemo(
    () => scaleState(raw.state, multiplier),
    [raw.state, multiplier],
  )
  // The journal only changes when events arrive; keep it (and its lists) stable between
  // snapshots so the panels built from it can skip the once-a-second re-render.
  const journalFactor = Math.round(rewardFactor * 100) / 100
  const journal = useMemo(
    () => scaleJournal(raw.journal, multiplier, journalFactor),
    [raw.journal, multiplier, journalFactor],
  )
  useEffect(() => {
    document.body.classList.toggle('simulated', multiplier !== 1) // projected numbers turn yellow
  }, [multiplier])
  // Per-second history of the totals while this page is open (5-minute charts, sparklines).
  const live = useMemo(
    () => scaleSamples(raw.live, multiplier, rewardFactor),
    [raw.live, multiplier, rewardFactor],
  )
  const configText = state?.config ? JSON.stringify(state.config, null, 2) : null
  // The market whose popup is open (looked up in each snapshot, so it stays live).
  const [selected, setSelected] = useState<string | null>(null)
  const closeDetail = useCallback(() => setSelected(null), [])
  // A "market no longer selected" cancel whose why-popup is open.
  const [deselect, setDeselect] = useState<{ ticker: string; ts: number } | null>(null)
  const openDeselect = useCallback((ticker: string, ts: number) => setDeselect({ ticker, ts }), [])
  const closeDeselect = useCallback(() => setDeselect(null), [])
  // Any ticker opens the popup; one the bot never tracked gets a bare row (history still shows).
  const detail = selected ? (state?.markets.find((m) => m.ticker === selected) ?? untracked(selected)) : undefined
  const payoutMinimum =
    num((state?.config as { selection?: { payout_minimum?: unknown } } | undefined)?.selection?.payout_minimum) ?? 1
  const rejects = useMemo(
    () => journal.orders.filter((o) => o.action === 'reject').length,
    [journal.orders],
  )

  return (
    <OpenMarketContext.Provider value={setSelected}>
    <OpenDeselectContext.Provider value={openDeselect}>
      <TopBar
        runId={runId}
        link={link}
        state={state}
        multiplier={multiplier}
        onMultiplier={setMultiplier}
        rewardFactor={rewardFactor}
        controls={
          <Controls
            controls={raw.controls}
            live={link === 'live'}
            heldSince={raw.state?.held_since ?? null}
            maxCapital={num(raw.state?.totals?.max_capital)} // the real budget, not multiplied
          />
        }
      />
      <Kpis state={state} fills={journal.fills.length} rejects={rejects} />
      <Tape fills={journal.fills} orders={journal.orders} />
      <div className="grid">
        <MarketsTable
          markets={state?.markets ?? []}
          orders={state?.orders ?? []}
          minimum={payoutMinimum}
          onSelect={setSelected}
        />
        <Charts journal={journal} state={state} live={live} />
        <Scanner scan={raw.scan} />
        <Blotter orders={journal.orders} fills={journal.fills} quotes={journal.quotes} />
        <OrdersAndFills orders={state?.orders ?? []} journal={journal} />
        <Selections selections={journal.selections} />
        <RunConfig
          start={journal.start}
          end={journal.end}
          configText={configText}
          stateSession={state?.session}
        />
      </div>
      <div className="foot">
        kalshi-lp · live from the bot over a WebSocket (/api/ws; HTTP endpoints listed at /api) ·
        reward figures are the bot&apos;s own estimate of Kalshi&apos;s scoring, not a
        statement · hover any underlined label for an explanation
      </div>
      {detail && (
        <MarketDetail
          market={detail}
          orders={(state?.orders ?? []).filter((o) => o.ticker === detail.ticker)}
          journal={journal}
          payoutMinimum={payoutMinimum}
          onClose={closeDetail}
        />
      )}
      {deselect && <DeselectDetail ticker={deselect.ticker} ts={deselect.ts} onClose={closeDeselect} />}
      <HelpLayer />
    </OpenDeselectContext.Provider>
    </OpenMarketContext.Provider>
  )
}

/** A market the bot has no row for (e.g. from the scanner): just the ticker, nothing earned. */
function untracked(ticker: string): MarketRow {
  return {
    ticker,
    title: 'not tracked by the bot',
    inactive: true,
    close_time: null,
    reduce_only: false,
    paused: false,
    near_close: false,
    healthy: true,
    book: null,
    position: 0,
    exposure: 0,
    realized_pnl: 0,
    fees: 0,
    quotes: {},
    reward: { per_day: 0, target_size: null, discount_factor: null },
    earned: 0,
    rate_per_hour: 0,
    avg_score: 0,
    snapshots: 0,
    paying_snapshots: 0,
  }
}
