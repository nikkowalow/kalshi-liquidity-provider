import { NOT_SELECTED, useOpenDeselect } from '../lib/openDeselect'

/**
 * An order's reason. "market no longer selected" is a link: click it for why the bot left
 * the market, with the numbers behind the decision.
 */
export function Reason({ text, ticker, ts }: { text: string; ticker: string; ts: number }) {
  const open = useOpenDeselect()
  if (text !== NOT_SELECTED || !open) return <>{text}</>
  return (
    <button
      type="button"
      className="reason-link"
      title="why the bot left this market"
      onClick={(e) => {
        e.stopPropagation()
        open(ticker, ts)
      }}
    >
      {text}
    </button>
  )
}
