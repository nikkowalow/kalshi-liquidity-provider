import { createContext, useContext } from 'react'

/**
 * Opens the market popup for a ticker. App provides it; every <Ticker> uses it, so any
 * ticker anywhere on the page opens the same details on click.
 */
export const OpenMarketContext = createContext<((ticker: string) => void) | null>(null)

export const useOpenMarket = () => useContext(OpenMarketContext)
