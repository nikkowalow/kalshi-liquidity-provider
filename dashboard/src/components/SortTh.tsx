import type { ReactNode } from 'react'
import type { Sorter } from '../lib/sort'

/** A header cell that sorts its table when clicked. */
export function SortTh({
  k,
  sorter,
  children,
  align,
  help,
  text = false,
  colSpan,
}: {
  k: string
  sorter: Sorter
  children: ReactNode
  align?: 'l'
  help?: string
  text?: boolean // text column: first click sorts A→Z instead of largest first
  colSpan?: number
}) {
  const active = sorter.state.key === k
  const dir = active ? sorter.state.dir : null
  const onClick = () => sorter.toggle(k, text)
  return (
    <th
      className={['sortable', align ?? '', active ? 'sorted' : ''].join(' ').trim()}
      data-help={help}
      aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'}
      tabIndex={0}
      colSpan={colSpan}
      onClick={onClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault()
          onClick()
        }
      }}
    >
      {children}
      <span className="sort-arrow">{dir === 'asc' ? '▲' : dir === 'desc' ? '▼' : ''}</span>
    </th>
  )
}
