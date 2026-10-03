import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiUrl } from '../lib/api'
import { sendControl } from '../lib/control'

/** GET /api/config (see src/kalshi_lp/config_edit.py). */
interface ConfigResponse {
  path: string
  schema: { order: string[]; sections: Record<string, { properties: Record<string, FieldSchema> }> }
  values: Record<string, Record<string, unknown>> // as the file sets them
  running: Record<string, Record<string, unknown>> // as the bot runs now
  derived?: Record<string, unknown> // set from the budget (risk.scale_with_budget): key -> value
}

interface FieldSchema {
  type?: string
  anyOf?: FieldSchema[]
  enum?: string[]
  items?: FieldSchema
  default?: unknown
  description?: string
  minimum?: number
  maximum?: number
  exclusiveMinimum?: number
}

type Kind = 'bool' | 'int' | 'number' | 'enum' | 'list' | 'text'

interface Field {
  key: string // "section.setting"
  name: string
  kind: Kind
  nullable: boolean
  options?: string[]
  schema: FieldSchema
}

function describe(section: string, name: string, s: FieldSchema): Field {
  const variants = s.anyOf ?? [s]
  const types = new Set(variants.map((v) => v.type).filter(Boolean))
  const enumOf = s.enum ?? variants.find((v) => v.enum)?.enum
  const nullable = types.has('null')
  const kind: Kind = enumOf
    ? 'enum'
    : types.has('boolean')
      ? 'bool'
      : types.has('array')
        ? 'list'
        : types.has('number')
          ? 'number'
          : types.has('integer')
            ? 'int'
            : 'text'
  return { key: `${section}.${name}`, name, kind, nullable, options: enumOf, schema: s }
}

const label = (name: string) => name.replaceAll('_', ' ')
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/** What a form input holds -> the JSON value to save; throws a message if it's not valid. */
function parse(field: Field, raw: unknown): unknown {
  if (field.kind === 'bool' || field.kind === 'enum') return raw
  if (field.kind === 'list') {
    return String(raw ?? '')
      .split(/[\n,]/)
      .map((s) => s.trim())
      .filter(Boolean)
  }
  const text = String(raw ?? '').trim()
  if (text === '') {
    if (field.nullable) return null
    throw new Error(`${field.key}: needs a value`)
  }
  if (field.kind === 'text') return text
  const n = Number(text)
  if (!Number.isFinite(n)) throw new Error(`${field.key}: not a number`)
  if (field.kind === 'int' && !Number.isInteger(n)) throw new Error(`${field.key}: whole numbers only`)
  return n
}

/** A stored value -> what its input shows. */
function shown(field: Field, value: unknown): unknown {
  if (field.kind === 'list') return Array.isArray(value) ? value.join('\n') : ''
  if (field.kind === 'bool' || field.kind === 'enum') return value
  return value === null || value === undefined ? '' : String(value)
}

function Input({ field, value, onChange }: { field: Field; value: unknown; onChange: (v: unknown) => void }) {
  switch (field.kind) {
    case 'bool':
      return <input type="checkbox" checked={Boolean(value)} onChange={(e) => onChange(e.target.checked)} />
    case 'enum':
      return (
        <select value={String(value ?? '')} onChange={(e) => onChange(e.target.value)}>
          {field.options?.map((o) => (
            <option key={o} value={o}>
              {o}
            </option>
          ))}
        </select>
      )
    case 'list':
      return (
        <textarea
          rows={Math.min(8, Math.max(2, String(value ?? '').split('\n').length))}
          value={String(value ?? '')}
          placeholder="one per line"
          onChange={(e) => onChange(e.target.value)}
        />
      )
    default:
      return (
        <input
          type="text"
          inputMode={field.kind === 'text' ? 'text' : 'decimal'}
          value={String(value ?? '')}
          placeholder={field.nullable ? 'off (empty)' : ''}
          onChange={(e) => onChange(e.target.value)}
        />
      )
  }
}

async function fetchConfig(): Promise<ConfigResponse | { error: string }> {
  try {
    const res = await fetch(apiUrl('/api/config'))
    const data = await res.json()
    return res.ok ? (data as ConfigResponse) : { error: data.error ?? res.statusText }
  } catch (e) {
    return { error: e instanceof Error ? e.message : String(e) }
  }
}

/**
 * Every editable setting in the bot's YAML config. Saving writes the file (comments kept)
 * and the bot applies it on restart: "save & restart" does both. Positions carry over.
 */
export function SettingsModal({ token, onClose }: { token: string; onClose: () => void }) {
  const [config, setConfig] = useState<ConfigResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<{ ok: boolean; text: string } | null>(null)
  const [section, setSection] = useState('selection')
  const [filter, setFilter] = useState('')
  const [draft, setDraft] = useState<Record<string, unknown>>({}) // key -> input value
  const [busy, setBusy] = useState(false)
  const [armed, setArmed] = useState(false)

  const load = useCallback(
    () =>
      fetchConfig().then((r) => {
        if ('error' in r) setError(r.error)
        else {
          setConfig(r)
          setError(null)
        }
      }),
    [],
  )
  useEffect(() => {
    let alive = true
    fetchConfig().then((r) => {
      if (!alive) return
      if ('error' in r) setError(r.error)
      else setConfig(r)
    })
    return () => {
      alive = false
    }
  }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const fields = useMemo(() => {
    const out: Record<string, Field[]> = {}
    if (!config) return out
    for (const sec of config.schema.order) {
      const props = config.schema.sections[sec]?.properties ?? {}
      out[sec] = Object.entries(props).map(([name, s]) => describe(sec, name, s))
    }
    return out
  }, [config])
  const byKey = useMemo(() => new Map(Object.values(fields).flat().map((f) => [f.key, f])), [fields])

  if (!config && !error) return null
  const fileValue = (f: Field) => config?.values[f.key.split('.')[0]]?.[f.name]
  const runValue = (f: Field) => config?.running[f.key.split('.')[0]]?.[f.name]

  // Valid edited values that differ from the file.
  const changes: Record<string, unknown> = {}
  const problems: string[] = []
  for (const [key, raw] of Object.entries(draft)) {
    const f = byKey.get(key)
    if (!f) continue
    try {
      const v = parse(f, raw)
      if (!same(v, fileValue(f))) changes[key] = v
    } catch (e) {
      problems.push(e instanceof Error ? e.message : String(e))
    }
  }
  const pending = Object.keys(changes).length
  const awaitingRestart = config
    ? Object.values(fields)
        .flat()
        .filter((f) => !(f.key in (config.derived ?? {})) && !same(fileValue(f), runValue(f))).length
    : 0

  const save = async (restart: boolean) => {
    if (problems.length) return
    setBusy(true)
    setNote(null)
    try {
      let message = pending ? await sendControl(token, 'config', { changes }) : 'no changes to save'
      setDraft({})
      if (restart) message = await sendControl(token, 'restart')
      setNote({ ok: true, text: message })
      await load()
    } catch (e) {
      setNote({ ok: false, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setBusy(false)
      setArmed(false)
    }
  }

  const q = filter.trim().toLowerCase()
  const visible = q
    ? Object.values(fields)
        .flat()
        .filter((f) => f.key.toLowerCase().includes(q) || (f.schema.description ?? '').toLowerCase().includes(q))
    : (fields[section] ?? [])

  return (
    <div className="md-backdrop" onClick={onClose}>
      <div className="md st" role="dialog" aria-modal="true" aria-label="settings" onClick={(e) => e.stopPropagation()}>
        <header className="md-head">
          <div>
            <span className="md-title">SETTINGS</span>
            <span className="dim">{config?.path}</span>
            {awaitingRestart > 0 && (
              <span className="tag yl">
                {awaitingRestart} saved, not running yet: restart to apply
              </span>
            )}
          </div>
          <div className="md-actions">
            <input
              className="st-search"
              placeholder="search settings…"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
            />
            <button type="button" className="chip" onClick={onClose} aria-label="close">
              ✕ esc
            </button>
          </div>
        </header>
        {error ? (
          <div className="empty">couldn&apos;t load the settings: {error}</div>
        ) : (
          <div className="st-body">
            <nav className="st-nav">
              {config?.schema.order.map((sec) => {
                const edited = Object.keys(changes).filter((k) => k.startsWith(`${sec}.`)).length
                return (
                  <button
                    key={sec}
                    type="button"
                    className={`chip${!q && sec === section ? ' on' : ''}`}
                    onClick={() => {
                      setFilter('')
                      setSection(sec)
                    }}
                  >
                    {label(sec)}
                    {edited > 0 && <b className="yl"> ·{edited}</b>}
                  </button>
                )
              })}
            </nav>
            <div className="st-fields">
              {visible.map((f) => {
                const fromBudget = config?.derived && f.key in config.derived
                const file = fromBudget ? config.derived?.[f.key] : fileValue(f)
                const value = f.key in draft ? draft[f.key] : shown(f, file)
                const edited = f.key in changes
                const notRunning = !fromBudget && !same(file, runValue(f))
                return (
                  <div key={f.key} className={`st-row${edited ? ' edited' : ''}`}>
                    <div className="st-name">
                      <b>{q ? f.key : label(f.name)}</b>
                      {edited && <span className="tag yl">changed</span>}
                      {fromBudget && (
                        <span className="tag pos" title="risk.scale_with_budget is on: this follows max_capital">
                          from budget
                        </span>
                      )}
                      {notRunning && !edited && (
                        <span className="tag mg" title={`running: ${JSON.stringify(runValue(f))}`}>
                          restart to apply
                        </span>
                      )}
                      <div className="st-desc">{f.schema.description}</div>
                      <div className="st-default">
                        default{' '}
                        {f.schema.default === null || f.schema.default === undefined
                          ? 'off'
                          : Array.isArray(f.schema.default)
                            ? `[${f.schema.default.join(', ')}]`
                            : String(f.schema.default)}
                        {f.schema.minimum !== undefined && ` · min ${f.schema.minimum}`}
                        {f.schema.exclusiveMinimum !== undefined && ` · above ${f.schema.exclusiveMinimum}`}
                        {f.schema.maximum !== undefined && ` · max ${f.schema.maximum}`}
                      </div>
                    </div>
                    <div className={`st-input${fromBudget ? ' locked' : ''}`}>
                      <Input field={f} value={value} onChange={(v) => setDraft((d) => ({ ...d, [f.key]: v }))} />
                      {f.key in draft && (
                        <button
                          type="button"
                          className="chip"
                          title="undo"
                          onClick={() =>
                            setDraft((d) => {
                              const { [f.key]: _, ...rest } = d
                              return rest
                            })
                          }
                        >
                          ↺
                        </button>
                      )}
                    </div>
                  </div>
                )
              })}
              {visible.length === 0 && <div className="empty">no setting matches</div>}
            </div>
          </div>
        )}
        <footer className="st-foot">
          {problems.length > 0 ? (
            <span className="neg">✗ {problems[0]}</span>
          ) : note ? (
            <span className={note.ok ? 'pos' : 'neg'}>
              {note.ok ? '✓' : '✗'} {note.text}
            </span>
          ) : (
            <span className="dim">
              {pending ? `${pending} unsaved change${pending === 1 ? '' : 's'}` : 'no unsaved changes'} · saved
              to the YAML (comments kept), applied on restart · positions carry over a restart
            </span>
          )}
          <span className="st-buttons">
            <button type="button" disabled={busy || !Object.keys(draft).length} onClick={() => setDraft({})}>
              discard
            </button>
            <button type="button" className="go" disabled={busy || !pending || problems.length > 0} onClick={() => void save(false)}>
              save
            </button>
            <button
              type="button"
              className={`warn${armed ? ' armed' : ''}`}
              disabled={busy || problems.length > 0 || (!pending && !awaitingRestart)}
              onClick={() => (armed ? void save(true) : setArmed(true))}
            >
              {busy ? 'working…' : armed ? 'confirm restart?' : 'save & restart'}
            </button>
          </span>
        </footer>
      </div>
    </div>
  )
}
