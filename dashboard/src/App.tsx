import { Blotter } from './components/Blotter'
import { Charts } from './components/Charts'
import { EarningsMix } from './components/EarningsMix'
import { HelpLayer } from './components/HelpLayer'
import { Kpis } from './components/Kpis'
import { MarketsTable } from './components/MarketsTable'
import { LogPanel, OrdersAndFills, RunConfig, Selections } from './components/SidePanels'
import { Tape } from './components/Tape'
import { TopBar } from './components/TopBar'
import { useEffect, useMemo } from 'react'
import { scaleSamples, scaleView } from './lib/scale'
import { useJournal } from './lib/useJournal'
import { useMultiplier } from './lib/useMultiplier'

export default function App() {
  const raw = useJournal()
  const { runs, runId, chooseRun, follow, setFollow, connected } = raw
  const [multiplier, setMultiplier] = useMultiplier()
  // Everything below sees the projected numbers; the multiplier is display-only.
  const { state, journal, rewardFactor } = useMemo(
    () => scaleView(raw.state, raw.journal, multiplier),
    [raw.state, raw.journal, multiplier],
  )
  useEffect(() => {
    document.body.classList.toggle('simulated', multiplier !== 1) // projected numbers turn yellow
  }, [multiplier])
  // Per-second history of the totals while this page is open (5-minute charts, sparklines).
  const live = useMemo(
    () => scaleSamples(raw.live, multiplier, rewardFactor),
    [raw.live, multiplier, rewardFactor],
  )
  const rejects = journal.orders.filter((o) => o.action === 'reject').length

  return (
    <>
      <TopBar
        runs={runs}
        runId={runId}
        onChooseRun={chooseRun}
        follow={follow}
        onFollow={setFollow}
        state={state}
        serverUp={connected}
        multiplier={multiplier}
        onMultiplier={setMultiplier}
        rewardFactor={rewardFactor}
      />
      <Kpis state={state} fills={journal.fills.length} rejects={rejects} live={live} />
      <Tape journal={journal} />
      <div className="grid">
        <MarketsTable markets={state?.markets ?? []} orders={state?.orders ?? []} />
        <Charts journal={journal} state={state} live={live} />
        <EarningsMix state={state} />
        <Blotter journal={journal} />
        <OrdersAndFills orders={state?.orders ?? []} journal={journal} />
        <LogPanel journal={journal} />
        <Selections journal={journal} />
        <RunConfig journal={journal} state={state} />
      </div>
      <div className="foot">
        kalshi-lp · reads runs/&lt;run&gt;/state.json + events.jsonl via dashboard/server.py · polls
        every 1s · reward figures are the bot&apos;s own estimate of Kalshi&apos;s scoring, not a
        statement · hover any underlined label for an explanation
      </div>
      <HelpLayer />
    </>
  )
}
