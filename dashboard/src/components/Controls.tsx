import { type FormEvent, memo, useEffect, useState } from 'react'
import { sendControl, type Action } from '../lib/control'
import { usd } from '../lib/format'
import type { ControlInfo } from '../types'
import { Stamp } from './Stamp'

const ARM_MS = 4000 // a two-click button stays armed this long
const NOTE_MS = 8000 // how long the bot's answer stays up

interface Note {
  ok: boolean
  text: string
}

interface ControlsProps {
  controls: ControlInfo | null // null: the bot has its buttons switched off
  live: boolean // connected to the bot right now
  heldSince: number | null // paused from here (unix seconds)
  maxCapital: number | null // the real budget, never the display multiplier's
}

/**
 * The bot's buttons: pause/resume, rescan, flatten, budget, stop.
 *
 * Anything that pulls orders, trades or stops the bot takes two clicks: the first
 * arms it ("confirm flatten?"), the second, within 4 seconds, sends it. The bot's
 * one-line answer shows next to the buttons.
 */
export const Controls = memo(function Controls({ controls, live, heldSince, maxCapital }: ControlsProps) {
  const [armed, setArmed] = useState<Action | null>(null)
  const [busy, setBusy] = useState<Action | null>(null)
  const [note, setNote] = useState<Note | null>(null)
  const [draft, setDraft] = useState<string | null>(null) // budget being edited

  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(null), ARM_MS)
    return () => clearTimeout(t)
  }, [armed])
  useEffect(() => {
    if (!note) return
    const t = setTimeout(() => setNote(null), NOTE_MS)
    return () => clearTimeout(t)
  }, [note])

  if (!controls) return null
  const off = !live || busy !== null

  const run = async (action: Action, body?: Record<string, unknown>) => {
    setArmed(null)
    setBusy(action)
    setNote(null)
    try {
      setNote({ ok: true, text: await sendControl(controls.token, action, body) })
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(null)
    }
  }
  const twoClick = (action: Action) => () => {
    if (armed === action) void run(action)
    else setArmed(action)
  }
  const label = (action: Action, text: string) =>
    busy === action ? `${text}…` : armed === action ? `confirm ${text}?` : text
  const cls = (action: Action, tone: string) => `${tone}${armed === action ? ' armed' : ''}`

  const submitBudget = (e: FormEvent) => {
    e.preventDefault()
    const value = (draft ?? '').trim()
    if (!(Number(value) > 0)) {
      setNote({ ok: false, text: 'enter a dollar amount above 0' })
      return
    }
    setDraft(null)
    void run('budget', { max_capital: value })
  }

  return (
    <span className="ctl">
      {heldSince != null && (
        <span className="badge held" data-help="ctl:held">
          ❚❚ PAUSED <Stamp ts={heldSince} onlyAgo />
        </span>
      )}
      {heldSince != null ? (
        <button type="button" className="go" disabled={off} onClick={() => void run('resume')} data-help="ctl:resume">
          {label('resume', '▶ resume')}
        </button>
      ) : (
        <button type="button" className={cls('pause', 'warn')} disabled={off} onClick={twoClick('pause')} data-help="ctl:pause">
          {label('pause', '❚❚ pause')}
        </button>
      )}
      <button type="button" disabled={off} onClick={() => void run('rescan')} data-help="ctl:rescan">
        {label('rescan', '↻ rescan')}
      </button>
      <button type="button" className={cls('flatten', 'warn')} disabled={off} onClick={twoClick('flatten')} data-help="ctl:flatten">
        {label('flatten', '⇲ flatten')}
      </button>
      {draft !== null ? (
        <form className="ctl-budget" onSubmit={submitBudget}>
          $
          <input
            autoFocus
            type="number"
            min={1}
            step={1}
            value={draft}
            aria-label="max capital in dollars"
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Escape') setDraft(null)
            }}
          />
          <button type="submit" className="go" disabled={off}>
            set
          </button>
          <button type="button" onClick={() => setDraft(null)} aria-label="cancel">
            ×
          </button>
        </form>
      ) : (
        <button
          type="button"
          disabled={off}
          onClick={() => setDraft(maxCapital != null ? String(maxCapital) : '')}
          data-help="ctl:budget"
        >
          {label('budget', `budget ${maxCapital != null ? usd(maxCapital) : 'none'}`)}
        </button>
      )}
      <button type="button" className={cls('stop', 'danger')} disabled={off} onClick={twoClick('stop')} data-help="ctl:stop">
        {label('stop', '⏻ stop')}
      </button>
      {note && (
        <span className={`ctl-note ${note.ok ? 'ok' : 'err'}`} role="status">
          {note.ok ? '✓' : '✗'} {note.text}
        </span>
      )}
    </span>
  )
})
