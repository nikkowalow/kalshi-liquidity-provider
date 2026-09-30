import { useEffect, useMemo, useRef, useState } from 'react'
import type { Journal } from '../lib/useJournal'
import { hms, num, signedUsd, usd } from '../lib/format'
import type { RunState, Totals } from '../types'
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

const HEIGHT = 130
const MAX_DRAWN = 1500 // points per line; long histories are thinned evenly

function thin(points: Point[]): Point[] {
  if (points.length <= MAX_DRAWN) return points
  const step = points.length / (MAX_DRAWN - 1)
  const out = Array.from({ length: MAX_DRAWN - 1 }, (_, i) => points[Math.floor(i * step)])
  out.push(points[points.length - 1])
  return out
}

/** Time of day, plus the date once the chart spans more than a day. */
function stamp(t: number, span: number): string {
  if (span < 86_400) return hms(t)
  const d = new Date(t * 1000)
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${hms(t).slice(0, 5)}`
}
const PAD = { left: 56, right: 8, top: 8, bottom: 16 }

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

/** Single-series line chart: 2px line, recessive grid, hover crosshair + tooltip. */
function LineChart({ spec, points }: { spec: ChartSpec; points: Point[] }) {
  const [ref, width] = useWidth<HTMLDivElement>()
  const [hover, setHover] = useState<Point | null>(null)
  const latest = points.at(-1)

  const geo = useMemo(() => {
    if (points.length < 2 || width < 50) return null
    const t0 = points[0].t
    const t1 = Math.max(points.at(-1)!.t, t0 + 1)
    let lo = Math.min(...points.map((p) => p.v))
    let hi = Math.max(...points.map((p) => p.v))
    if (spec.budget) hi = Math.max(hi, spec.budget)
    if (spec.zeroLine) {
      lo = Math.min(lo, 0)
      hi = Math.max(hi, 0)
    } else {
      lo = Math.min(lo, 0)
    }
    if (hi === lo) hi = lo + 1
    const pad = (hi - lo) * 0.08
    hi += pad
    if (lo < 0) lo -= pad
    const x = (t: number) => PAD.left + ((width - PAD.left - PAD.right) * (t - t0)) / (t1 - t0)
    const y = (v: number) => PAD.top + (HEIGHT - PAD.top - PAD.bottom) * (1 - (v - lo) / (hi - lo))
    const path = points.map((p, i) => `${i ? 'L' : 'M'}${x(p.t).toFixed(1)},${y(p.v).toFixed(1)}`).join('')
    const ticks = [0, 1, 2, 3].map((i) => lo + ((hi - lo) * i) / 3)
    return { t0, t1, x, y, path, ticks }
  }, [points, width, spec.budget, spec.zeroLine])

  const onMove = (ev: React.MouseEvent<SVGSVGElement>) => {
    if (!geo) return
    const mx = ev.clientX - ev.currentTarget.getBoundingClientRect().left
    let best = points[0]
    for (const p of points) if (Math.abs(geo.x(p.t) - mx) < Math.abs(geo.x(best.t) - mx)) best = p
    setHover(best)
  }

  return (
    <div className="chart" ref={ref}>
      <div className="t" data-help={spec.help}>
        {spec.title}
        <b>
          <Flash value={latest?.v ?? null}>{latest ? spec.format(latest.v) : '—'}</Flash>
        </b>
      </div>
      {!geo ? (
        <div className="chart-empty">waiting for data…</div>
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
          <text x={PAD.left} y={HEIGHT - 3} className="axis">
            {stamp(geo.t0, geo.t1 - geo.t0)}
          </text>
          <text x={width - PAD.right} y={HEIGHT - 3} className="axis" textAnchor="end">
            {stamp(geo.t1, geo.t1 - geo.t0)}
          </text>
          {spec.zeroLine && (
            <line x1={PAD.left} x2={width - PAD.right} y1={geo.y(0)} y2={geo.y(0)} className="zero" />
          )}
          {spec.budget ? (
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
          <path d={geo.path} className="series" />
          {latest && (
            <circle cx={geo.x(latest.t)} cy={geo.y(latest.v)} r={3} className="live-dot" />
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
          {geo ? stamp(hover.t, geo.t1 - geo.t0) : hms(hover.t)} &nbsp;<b>{spec.format(hover.v)}</b>
        </div>
      )}
    </div>
  )
}

export function Charts({ journal, state }: { journal: Journal; state: RunState | null }) {
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
  const series = (key: keyof Totals): Point[] => {
    const pts: Point[] = []
    for (const m of journal.metrics) {
      const v = num(m[key])
      if (v !== null) pts.push({ t: m.ts, v })
    }
    const live = num(state?.totals[key])
    if (state && live !== null) pts.push({ t: state.updated_at, v: live })
    return thin(pts)
  }
  return (
    <section className="panel span-12">
      <PanelHeader
        title="Performance"
        help="panel:performance"
        note="sampled every 10s · spans every session of this journal · hover for values"
      />
      <div className="charts">
        {specs.map((s) => (
          <LineChart key={s.key} spec={s} points={series(s.key)} />
        ))}
      </div>
    </section>
  )
}
