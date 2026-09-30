import type { CompetitionInfo } from '../types'

const CLASS = { low: 'pos', medium: 'yl', high: 'neg' } as const
const LABEL = { low: 'LOW', medium: 'MED', high: 'HIGH' } as const

/** Competition level plus its room, e.g. "LOW 5¢". */
export function CompetitionTag({ c }: { c?: CompetitionInfo | null }) {
  if (!c) return <span className="dim">—</span>
  const room = c.room === null ? '∞' : `${Math.round(c.room * 100)}¢`
  return (
    <span className={`tag ${CLASS[c.level]}`} data-help={`comp:${c.level}`}>
      {LABEL[c.level]} {room}
    </span>
  )
}
