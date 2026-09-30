import type { ReactNode } from 'react'

interface PanelProps {
  title: string
  help?: string
  note?: ReactNode
  tools?: ReactNode
  span?: 4 | 5 | 6 | 7 | 8 | 12
  height?: 'sm' | 'md' | 'lg'
  children: ReactNode
}

/** A titled terminal panel with a scrollable body. */
export function Panel({ title, help, note, tools, span = 12, height, children }: PanelProps) {
  return (
    <section className={`panel span-${span}`}>
      <PanelHeader title={title} help={help} note={note} tools={tools} />
      <div className={`body${height ? ` h-${height}` : ''}`}>{children}</div>
    </section>
  )
}

export function PanelHeader({
  title,
  help,
  note,
  tools,
}: Pick<PanelProps, 'title' | 'help' | 'note' | 'tools'>) {
  return (
    <h2>
      <span data-help={help}>{title}</span>
      {note !== undefined && <span className="n">{note}</span>}
      {tools && <span className="tools">{tools}</span>}
    </h2>
  )
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>
}

/** Toggle buttons for filters (blotter types, log levels). */
export function Chips<T extends string>({
  options,
  selected,
  onToggle,
  helpPrefix,
  colorClass,
}: {
  options: readonly T[]
  selected: ReadonlySet<T>
  onToggle: (option: T) => void
  helpPrefix: string
  colorClass?: (option: T) => string // shows the option's color as a swatch
}) {
  return (
    <>
      {options.map((o) => (
        <button
          key={o}
          type="button"
          className={`chip${selected.has(o) ? ' on' : ''}`}
          data-help={`${helpPrefix}:${o}`}
          onClick={() => onToggle(o)}
        >
          {colorClass && <i className={`swatch ${colorClass(o)}`}>■</i>}
          {o}
        </button>
      ))}
    </>
  )
}
