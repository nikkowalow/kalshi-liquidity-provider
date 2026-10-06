import { memo } from 'react'
import { NO_FILTERS, type ScanFilters } from '../lib/scanFilters'

type NumKey = {
  [K in keyof ScanFilters]: ScanFilters[K] extends string ? K : never
}[keyof ScanFilters]

// [field, label, unit, help]
const NUMBER_FIELDS: [NumKey, string, string, string][] = [
  ['minVolume', 'Min vol 24h', 'contracts', 'Contracts traded in the last 24h (selection.min_volume_24h).'],
  ['minTradesReplayed', 'Min trades replayed', 'trades', 'Trades the fill-risk replay had (selection.min_fill_risk_trades). Fewer: fill risk unknown.'],
  ['maxFillsPerDay', 'Max fills/day', 'contracts', 'Expected contracts filled per day at 10/side (selection.max_fills_per_day).'],
  ['maxFillCostPct', 'Max fill cost', '% of reward', 'Expected fill cost as a share of the estimated reward (selection.max_fill_cost_share).'],
  ['minDaily', 'Min $/day', '$', 'Estimated reward per day at 10 contracts/side, before fill costs.'],
  ['minNet', 'Min net $/day', '$', 'Estimated reward minus expected fill cost.'],
  ['minPeriodPayout', 'Min period $', '$', 'Projected payout this program period (selection.min_period_payout).'],
  ['minReturnPct', 'Min return/day', '%', '$/day per dollar the quotes lock.'],
  ['minHoursToResolve', 'Min to resolve', 'hours', 'Until close, expected expiration or the date in the ticker, whichever is sooner (selection.min_seconds_to_close).'],
  ['minProgramHoursLeft', 'Min program left', 'hours', 'Until the liquidity program period ends (selection.min_program_seconds_left).'],
  ['minProgramDays', 'Min program period', 'days', 'Length of the program period (selection.min_program_period_days).'],
  ['minMid', 'Min mid', '0-1', 'YES mid price (selection.min_mid_price).'],
  ['maxMid', 'Max mid', '0-1', 'YES mid price (selection.max_mid_price).'],
  ['maxSpreadCents', 'Max spread', '¢', 'YES ask minus bid (selection.max_spread).'],
]

const TOGGLES: [keyof ScanFilters, string][] = [
  ['hideDataRelease', 'hide data-release markets'],
  ['hideExcludedSeries', 'hide excluded series'],
  ['hideSkipped', "hide bot's skips"],
  ['onlyTrading', 'only trading now'],
]

/** The scanner's filter controls. Blank number = that filter is off. */
export const ScanFilterBar = memo(function ScanFilterBar({
  filters,
  onChange,
  onBotPreset,
  shown,
  total,
}: {
  filters: ScanFilters
  onChange: (f: ScanFilters) => void
  onBotPreset: (() => void) | null // null: the bot's config isn't available
  shown: number
  total: number
}) {
  const set = <K extends keyof ScanFilters>(k: K, v: ScanFilters[K]) => onChange({ ...filters, [k]: v })
  return (
    <div className="scan-filters">
      <div className="sf-head">
        <span>
          <b className="am">{shown}</b> of {total} markets left
        </span>
        <input
          className="filter"
          type="text"
          placeholder="ticker, series or title"
          value={filters.search}
          onChange={(e) => set('search', e.target.value)}
        />
        <span className="sf-buttons">
          {onBotPreset && (
            <button type="button" className="chip" onClick={onBotPreset} title="Fill in the bot's current selection settings">
              bot's filters
            </button>
          )}
          <button type="button" className="chip" onClick={() => onChange(NO_FILTERS)}>
            clear
          </button>
        </span>
      </div>
      <div className="sf-grid">
        {NUMBER_FIELDS.map(([key, label, unit, help]) => (
          <label key={key} className={filters[key].trim() ? 'on' : undefined} title={help}>
            <span>{label}</span>
            <input
              type="text"
              inputMode="decimal"
              placeholder="off"
              value={filters[key]}
              onChange={(e) => set(key, e.target.value)}
            />
            <i>{unit}</i>
          </label>
        ))}
      </div>
      <div className="sf-toggles">
        <span className="dim">competition</span>
        {(['low', 'medium', 'high'] as const).map((level) => (
          <button
            key={level}
            type="button"
            className={`chip${filters.competition[level] ? ' on' : ''}`}
            onClick={() => set('competition', { ...filters.competition, [level]: !filters.competition[level] })}
          >
            {level}
          </button>
        ))}
        <span className="sf-sep" />
        {TOGGLES.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`chip${filters[key] ? ' on' : ''}`}
            onClick={() => set(key, !filters[key] as never)}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  )
})
