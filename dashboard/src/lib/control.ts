/** The bot's control actions (POST /api/control/<action>; see src/kalshi_lp/engine/controls.py). */
export type Action = 'pause' | 'resume' | 'flatten' | 'rescan' | 'budget' | 'stop' | 'config' | 'restart'

/** Press a button on the bot. Resolves to the bot's one-line answer; rejects with its refusal. */
export async function sendControl(
  token: string,
  action: Action,
  body: Record<string, unknown> = {},
): Promise<string> {
  let res: Response
  try {
    res = await fetch(`/api/control/${action}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
  } catch {
    throw new Error('bot unreachable')
  }
  const data = (await res.json().catch(() => ({}))) as { ok?: boolean; message?: string; error?: string }
  if (!res.ok || !data.ok) throw new Error(data.error ?? `${res.status} ${res.statusText}`)
  return data.message ?? 'done'
}
