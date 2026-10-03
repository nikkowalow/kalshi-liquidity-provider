/**
 * Which bot this page shows. Every bot serves the dashboard and its own API; the page can
 * show another local bot's data (the top bar's switcher) by sending its requests there.
 * '' = the bot that served this page.
 */
const KEY = 'klp.bot'

function saved(): string {
  try {
    const url = localStorage.getItem(KEY) ?? ''
    return url === location.origin ? '' : url
  } catch {
    return ''
  }
}

let base = saved()

export const apiBase = (): string => base

/** URL of an API path (``/api/...``) on the bot this page shows. */
export const apiUrl = (path: string): string => `${base}${path}`

/** WebSocket URL of an API path on the bot this page shows. */
export function wsUrl(path: string): string {
  if (base) return base.replace(/^http/, 'ws') + path
  return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}${path}`
}

/** Show another bot (``url`` = its API origin) from now on; reloads the page. */
export function switchBot(url: string): void {
  base = url === location.origin ? '' : url
  try {
    if (base) localStorage.setItem(KEY, base)
    else localStorage.removeItem(KEY)
  } catch {
    // private mode: the switch still works until the page is reloaded
  }
  location.reload()
}
