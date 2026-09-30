/** Spacer row standing in for the rows a virtualized table doesn't draw. */
export function PadRow({ height, cols }: { height: number; cols: number }) {
  if (height <= 0) return null
  return (
    <tr className="pad" aria-hidden="true">
      <td colSpan={cols} style={{ height, padding: 0, border: 0 }} />
    </tr>
  )
}
