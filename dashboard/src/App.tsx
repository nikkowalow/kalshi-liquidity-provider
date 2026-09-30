import { Blotter } from './components/Blotter'
import { Charts } from './components/Charts'
import { HelpLayer } from './components/HelpLayer'
import { Kpis } from './components/Kpis'
import { MarketsTable } from './components/MarketsTable'
import { LogPanel, OrdersAndFills, RunConfig, Selections } from './components/SidePanels'
import { Tape } from './components/Tape'
import { TopBar } from './components/TopBar'
import { useJournal } from './lib/useJournal'

export default function App() {
  const { runs, runId, chooseRun, follow, setFollow, state, journal, connected } = useJournal()
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
      />
      <Kpis state={state} fills={journal.fills.length} rejects={rejects} />
      <Tape journal={journal} />
      <div className="grid">
        <MarketsTable markets={state?.markets ?? []} orders={state?.orders ?? []} />
        <Charts journal={journal} state={state} />
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
