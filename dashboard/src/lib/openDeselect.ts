import { createContext, useContext } from 'react'

/** The cancel reason that has a "why" popup (bot.py, LiquidityBot._decide). */
export const NOT_SELECTED = 'market no longer selected'

/**
 * Opens the "why did the bot leave this market" popup for a cancel at ``ts`` (unix seconds).
 * App provides it; every <Reason> uses it.
 */
export const OpenDeselectContext = createContext<((ticker: string, ts: number) => void) | null>(null)

export const useOpenDeselect = () => useContext(OpenDeselectContext)
