import type { FillEvent } from '../types'

/**
 * One position from open to flat: the fills that built it and the fills that closed it.
 *
 * Prices are in the held side's terms (a long NO bought at 0.40 shows entry 0.40, not the
 * YES price 0.60). P&L is the cash the fills moved, fees included, so it's exact once the
 * position is flat; an open (or settled) one has no exit price and no P&L here.
 */
export interface RoundTrip {
  key: string
  ticker: string
  held: 'YES' | 'NO'
  size: number // most contracts held at once
  opened: number // unix seconds of the first fill
  closed: number | null // of the fill that made it flat; null while open (or settled)
  entry: number // average price paid, held-side terms
  exit: number | null // average price received, held-side terms
  fees: number
  pnl: number | null // dollars, fees included; null until flat
  entryMaker: boolean // every entry fill rested (maker)
  exitHow: 'passive' | 'crossed' | 'mixed' | null // how the closing fills traded
  fills: number
}

interface Building {
  ticker: string
  sign: 1 | -1 // +1 long YES, -1 long NO
  opened: number
  size: number
  inQty: number
  inCost: number // held-side dollars paid
  outQty: number
  outValue: number // held-side dollars received
  fees: number
  cash: number // YES-terms cash, fees included
  entryMaker: boolean
  exitMaker: number
  exitTaker: number
  fills: number
}

const num = (v: unknown): number => (v === null || v === undefined || v === '' ? 0 : Number(v))

function finish(b: Building, closed: number | null): RoundTrip {
  const flat = closed !== null
  return {
    key: `${b.ticker}-${b.opened}`,
    ticker: b.ticker,
    held: b.sign > 0 ? 'YES' : 'NO',
    size: b.size,
    opened: b.opened,
    closed,
    entry: b.inQty ? b.inCost / b.inQty : 0,
    exit: b.outQty ? b.outValue / b.outQty : null,
    fees: b.fees,
    // Flat: YES-terms cash is the P&L (the $1 per NO contract Kalshi charges, then pays back,
    // nets out once the NO is sold).
    pnl: flat ? b.cash : null,
    entryMaker: b.entryMaker,
    exitHow: !b.exitMaker && !b.exitTaker ? null : !b.exitTaker ? 'passive' : !b.exitMaker ? 'crossed' : 'mixed',
    fills: b.fills,
  }
}

/** Group fills (any order) into round trips, newest first. */
export function roundTrips(fills: readonly FillEvent[]): RoundTrip[] {
  const out: RoundTrip[] = []
  const open = new Map<string, Building>()
  const position = new Map<string, number>()
  for (const f of [...fills].sort((a, b) => a.ts - b.ts)) {
    const count = num(f.count)
    if (!count) continue
    const yes = num(f.price)
    const delta = f.side === 'bid' ? count : -count
    const before = position.get(f.ticker) ?? 0
    const after = f.post_position != null ? num(f.post_position) : before + delta
    position.set(f.ticker, after)
    const fee = num(f.fee)
    let b = open.get(f.ticker)
    if (!b || before === 0) {
      b = {
        ticker: f.ticker,
        sign: delta > 0 ? 1 : -1,
        opened: f.ts,
        size: 0,
        inQty: 0,
        inCost: 0,
        outQty: 0,
        outValue: 0,
        fees: 0,
        cash: 0,
        entryMaker: true,
        exitMaker: 0,
        exitTaker: 0,
        fills: 0,
      }
      open.set(f.ticker, b)
    }
    const heldPrice = b.sign > 0 ? yes : 1 - yes
    const adding = Math.sign(delta) === b.sign
    // A fill that goes through zero closes this trip and opens the next with the rest.
    const closing = adding ? 0 : Math.min(count, Math.abs(before))
    const opening = count - closing
    b.fills += 1
    b.fees += fee
    b.cash += (f.side === 'bid' ? -yes : yes) * (adding ? count : closing)
    if (adding) {
      b.inQty += count
      b.inCost += heldPrice * count
      b.entryMaker &&= !f.is_taker
    } else {
      b.outQty += closing
      b.outValue += heldPrice * closing
      if (f.is_taker) b.exitTaker += closing
      else b.exitMaker += closing
    }
    b.size = Math.max(b.size, Math.abs(after), Math.abs(before))
    if (after === 0 || (opening > 0 && !adding)) {
      b.cash -= b.fees
      out.push(finish(b, f.ts))
      open.delete(f.ticker)
      if (opening > 0 && !adding) {
        // flipped: the rest of this fill opens a position on the other side
        const sign: 1 | -1 = delta > 0 ? 1 : -1
        open.set(f.ticker, {
          ticker: f.ticker,
          sign,
          opened: f.ts,
          size: opening,
          inQty: opening,
          inCost: (sign > 0 ? yes : 1 - yes) * opening,
          outQty: 0,
          outValue: 0,
          fees: 0,
          cash: (f.side === 'bid' ? -yes : yes) * opening,
          entryMaker: !f.is_taker,
          exitMaker: 0,
          exitTaker: 0,
          fills: 1,
        })
      }
    }
  }
  for (const b of open.values()) {
    b.cash -= b.fees
    out.push(finish(b, null))
  }
  return out.sort((a, b) => b.opened - a.opened)
}
