import { useEffect, useMemo, useRef, useState } from 'react'
import { span } from '../lib/clock'
import { hms, num, signedUsd, usd } from '../lib/format'
import type { Journal } from '../lib/useJournal'
import type { RunState, Sample, Totals } from '../types'
import { Flash } from './Flash'
import { PanelHeader } from './Panel'

interface Point {
  t: number
  v: number
}

interface ChartSpec {
  title: string
  help: string
  key: keyof Totals
  format: (v: number) => string
  zeroLine?: boolean
  budget?: number | null
}

/** Time windows. Short ones scroll: the x-axis is always "the last N minutes, up to now". */
const WINDOWS = [
  { id: '5M', seconds: 300, grid: 60 },
  { id: '15M', seconds: 900, grid: 180 },
  { id: '1H', seconds: 3600, grid: 600 },
  { id: '1D', seconds: 86_400, grid: 3 * 3600 },
  { id: 'ALL', seconds: null, grid: null },
] as const
type WindowId = (typeof WINDOWS)[number]['id']
const WINDOW_KEY = 'klp-chart-window'

function useChartWindow(): [WindowId, (w: WindowId) => void] {
  const [win, setWin] = useState<WindowId>(() => {
    try {
      const saved = localStorage.getItem(WINDOW_KEY)
      if (WINDOWS.some((w) => w.id === saved)) return saved as WindowId
    } catch {
      // storage unavailable: default window
    }
    return '5M'
  })
  const choose = (w: WindowId) => {
    setWin(w)
    try {
      localStorage.setItem(WINDOW_KEY, w)
    } catch {
      // not remembered
    }
  }
  return [win, choose]
}

const HEIGHT = 130
const MAX_DRAWN = 1500 // points per line; long histories are thinned evenly
const PAD = { left: 56, right: 8, top: 8, bottom: 16 }

function thin(points: Point[]): Point[] {
  if (points.length <= MAX_DRAWN) return points
  const step = points.length / (MAX_DRAWN - 1)
  const out = Array.from({ length: MAX_DRAWN - 1 }, (_, i) => points[Math.floor(i * step)])
  out.push(points[points.length - 1])
  return out
}

/** Time of day, plus the date once the chart spans more than a day. */
function stamp(t: number, spanSeconds: number): string {
  if (spanSeconds < 86_400) return hms(t)
  const d = new Date(t * 1000)
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${hms(t).slice(0, 5)}`
}

function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width))
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return [ref, width] as const
}

/** Single-series line chart: flat 2px line, hover crosshair + tooltip.
 *  With a ``domain`` the x-axis is fixed to it, so the line scrolls as time passes. */
function LineChart({
  spec,
  points,
  domain,
  gridStep,
}: {
  spec: ChartSpec
  points: Point[]
  domain: [number, number] | null
  gridStep: number | null
}) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<Point | null>(null)
  const latest = points.at(-1)
  const first = points[0]
  const change = latest && first ? latest.v - first.v : null

  const geo = useMemo(() => {
    if (points.length < 2 || width < 50) return null
    const t0 = domain ? domain[0] : points[0].t
    const t1 = Math.max(domain ? domain[1] : points.at(-1)!.t, t0 + 1)
    let lo = Math.min(...points.map((p) => p.v))
    let hi = Math.max(...points.map((p) => p.v))
    if (domain) {
      // Short window: zoom to the data so small moves are visible.
      if (spec.zeroLine) {
        lo = Math.min(lo, 0)
        hi = Math.max(hi, 0)
      }
      const minSpan = Math.max(Math.abs(hi) * 0.002, 1e-4)
      if (hi - lo < minSpan) {
        const mid = (hi + lo) / 2
        lo = mid - minSpan / 2
        hi = mid + minSpan / 2
      }
    } else {
      if (spec.budget) hi = Math.max(hi, spec.budget)
      lo = Math.min(lo, 0)
      if (spec.zeroLine) hi = Math.max(hi, 0)
    }
    if (hi === lo) hi = lo + 1
    const pad = (hi - lo) * 0.1
    hi += pad
    if (domain || lo < 0) lo -= pad
    const plotW = width - PAD.left - PAD.right
    const x = (t: number) => PAD.left + (plotW * (t - t0)) / (t1 - t0)
    const y = (v: number) => PAD.top + (HEIGHT - PAD.top - PAD.bottom) * (1 - (v - lo) / (hi - lo))
    const inside = points.filter((p) => p.t >= t0)
    const line = inside.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('')
    const ticks = [0, 1, 2, 3].map((i) => lo + ((hi - lo) * i) / 3)
    const verticals: number[] = []
    if (gridStep) {
      for (let t = Math.ceil(t0 / gridStep) * gridStep; t <= t1; t += gridStep) verticals.push(t)
    }
    return { t0, t1, lo, hi, x, y, line, ticks, verticals }
  }, [points, width, domain, gridStep, spec.budget, spec.zeroLine])

  const onMove = (ev: React.MouseEvent<SVGSVGElement>) => {
    if (!geo) return
    const mx = ev.clientX - ev.currentTarget.getBoundingClientRect().left
    let best = points[0]
    for (const p of points) if (Math.abs(geo.x(p.t) - mx) < Math.abs(geo.x(best.t) - mx)) best = p
    setHover(best)
  }

  const budgetVisible = geo !== null && spec.budget != null && spec.budget >= geo.lo && spec.budget <= geo.hi
  return (
    <div className="chart" ref={ref}>
      <div className="t" data-help={spec.help}>
        {spec.title}
        {change !== null && change !== 0 && (
          <span className={`delta ${change > 0 ? 'pos' : 'neg'}`}>
            {change > 0 ? '▲' : '▼'} {spec.format(Math.abs(change)).replace(/^[+−-]/, '')}
          </span>
        )}
        <b>
          <Flash value={latest?.v ?? null}>{latest ? spec.format(latest.v) : '—'}</Flash>
        </b>
      </div>
      {!geo ? (
        <div className="chart-empty">
          waiting for data<span className="dots" />
        </div>
      ) : (
        <svg
          width={width}
          height={HEIGHT}
          onMouseMove={onMove}
          onMouseLeave={() => setHover(null)}
          role="img"
          aria-label={`${spec.title}, latest ${latest ? spec.format(latest.v) : 'n/a'}`}
        >
          {geo.ticks.map((v) => (
            <g key={v}>
              <line x1={PAD.left} x2={width - PAD.right} y1={geo.y(v)} y2={geo.y(v)} className="grid" />
              <text x={PAD.left - 4} y={geo.y(v) + 3} className="axis" textAnchor="end">
                {spec.format(v)}
              </text>
            </g>
          ))}
          {geo.verticals.map((t) => (
            <g key={t}>
              <line x1={geo.x(t)} x2={geo.x(t)} y1={PAD.top} y2={HEIGHT - PAD.bottom} className="grid" />
              <text x={geo.x(t)} y={HEIGHT - 3} className="axis" textAnchor="middle">
                {hms(t).slice(0, 5)}
              </text>
            </g>
          ))}
          {domain ? (
            <>
              <text x={PAD.left} y={HEIGHT - 3} className="axis">
                -{span(geo.t1 - geo.t0).replace(' 00s', '').replace(' 00m', '')}
              </text>
              <text x={width - PAD.right} y={HEIGHT - 3} className="axis now" textAnchor="end">
                now
              </text>
            </>
          ) : (
            <>
              <text x={PAD.left} y={HEIGHT - 3} className="axis">
                {stamp(geo.t0, geo.t1 - geo.t0)}
              </text>
              <text x={width - PAD.right} y={HEIGHT - 3} className="axis" textAnchor="end">
                {stamp(geo.t1, geo.t1 - geo.t0)}
              </text>
            </>
          )}
          {spec.zeroLine && geo.lo <= 0 && geo.hi >= 0 && (
            <line x1={PAD.left} x2={width - PAD.right} y1={geo.y(0)} y2={geo.y(0)} className="zero" />
          )}
          {budgetVisible && spec.budget != null ? (
            <g>
              <line
                x1={PAD.left}
                x2={width - PAD.right}
                y1={geo.y(spec.budget)}
                y2={geo.y(spec.budget)}
                className="budget"
              />
              <text x={PAD.left + 3} y={geo.y(spec.budget) - 3} className="budget-label">
                budget
              </text>
            </g>
          ) : null}
          <path d={geo.line} className="series" />
          {latest && (
            <g transform={`translate(${geo.x(latest.t)},${geo.y(latest.v)})`}>
              <circle r={3} className="live-dot" />
            </g>
          )}
          {hover && (
            <g>
              <line x1={geo.x(hover.t)} x2={geo.x(hover.t)} y1={PAD.top} y2={HEIGHT - PAD.bottom} className="cross" />
              <circle cx={geo.x(hover.t)} cy={geo.y(hover.v)} r={4} className="dot" />
            </g>
          )}
        </svg>
      )}
      {hover && geo && (
        <div
          className="tip"
          style={{ left: Math.min(geo.x(hover.t) + 12, width - 150), top: Math.max(geo.y(hover.v) - 8, 14) }}
        >
          {stamp(hover.t, geo.t1 - geo.t0)} &nbsp;<b>{spec.format(hover.v)}</b>
        </div>
      )}
    </div>
  )
}

export function Charts({
  journal,
  state,
  live,
}: {
  journal: Journal
  state: RunState | null
  live: Sample[]
}) {
  const [win, setWin] = useChartWindow()
  const w = WINDOWS.find((x) => x.id === win) ?? WINDOWS[0]
  const specs: ChartSpec[] = [
    { title: 'Est. rewards earned, all sessions ($)', help: 'chart:rewards', key: 'rewards_earned', format: (v) => usd(v, 4) },
    { title: 'Session P&L ($)', help: 'chart:pnl', key: 'session_pnl', format: (v) => signedUsd(v), zeroLine: true },
    {
      title: 'Capital in use ($)',
      help: 'chart:capital',
      key: 'capital_in_use',
      format: (v) => usd(v),
      budget: num(state?.totals.max_capital),
    },
  ]
  const now = state?.updated_at ?? live.at(-1)?.t ?? 0
  const from = w.seconds ? now - w.seconds : -Infinity
  const domain: [number, number] | null = w.seconds ? [now - w.seconds, now] : null
  // Journal history (every 10s) up to where this page's per-second samples begin.
  const liveStart = live[0]?.t ?? Infinity
  const series = (key: keyof Totals): Point[] => {
    const pts: Point[] = []
    for (const m of journal.metrics) {
      const v = num(m[key])
      if (v !== null && m.ts >= from && m.ts < liveStart) pts.push({ t: m.ts, v })
    }
    for (const s of live) {
      const v = num(s.totals[key])
      if (v !== null && s.t >= from) pts.push({ t: s.t, v })
    }
    const current = num(state?.totals[key])
    if (state && current !== null && (pts.at(-1)?.t ?? -Infinity) < state.updated_at) {
      pts.push({ t: state.updated_at, v: current })
    }
    return thin(pts)
  }
  const perSecond = w.seconds !== null && w.seconds <= 3600
  return (
    <section className="panel span-12">
      <PanelHeader
        title="Performance"
        help="panel:performance"
        note={
          perSecond ? (
            <>
              <span className="rec">● REC</span> last {w.id.toLowerCase()} · 1 sample/s while this page
              is open · hover for values
            </>
          ) : (
            'journal history, sampled every 10s · spans every session · hover for values'
          )
        }
        tools={WINDOWS.map((x) => (
          <button
            key={x.id}
            type="button"
            className={`chip${x.id === win ? ' on' : ''}`}
            data-help="chip:window"
            onClick={() => setWin(x.id)}
          >
            {x.id}
          </button>
        ))}
      />
      <div className="charts">
        {specs.map((s) => (
          <LineChart key={s.key} spec={s} points={series(s.key)} domain={domain} gridStep={w.grid} />
        ))}
      </div>
    </section>
  )
}
