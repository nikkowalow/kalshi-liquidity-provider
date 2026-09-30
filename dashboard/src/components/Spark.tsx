/** A tiny line of recent values (a KPI's last few minutes). No axes: shape only. */
export function Spark({ values }: { values: number[] }) {
  if (values.length < 2) return <svg className="spark" aria-hidden="true" />
  const lo = Math.min(...values)
  const hi = Math.max(...values)
  const range = hi - lo || 1
  const pts = values.map((v, i) => {
    const x = (i / (values.length - 1)) * 100
    const y = 15 - ((v - lo) / range) * 13
    return `${x.toFixed(2)},${y.toFixed(2)}`
  })
  const up = values.at(-1)! >= values[0]
  return (
    <svg className={`spark ${up ? 'up' : 'down'}`} viewBox="0 0 100 16" preserveAspectRatio="none" aria-hidden="true">
      <polyline points={pts.join(' ')} vectorEffect="non-scaling-stroke" />
    </svg>
  )
}
