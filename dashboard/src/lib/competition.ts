import type { CompetitionInfo } from '../types'

/** Sort key: more room = less competition; no room figure = uncrowded. */
export const competitionRoom = (c?: CompetitionInfo | null): number | null =>
  c ? (c.room ?? 1) : null
