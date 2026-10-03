# Running the bot on Railway

The repo builds with the `Dockerfile` (`railway.json` tells Railway to use it). The image holds
the code, the configs and the built dashboard. Never secrets or state: those come from
variables and a volume.

## Once

1. **Stop the bot on your laptop first.** Two bots on one account fight: each cancels the
   other's orders (same `client_order_prefix`).
2. **Add a volume** to the service, mounted at `/data`. The journal (reward ledger, payout
   tracking, fill history) and logs live there; without it every redeploy starts from zero.
3. **Set the variables** (service → Variables):

   | Variable | Value |
   |---|---|
   | `KALSHI_PROD_KEY_ID` | your API key id |
   | `KALSHI_PROD_PRIVATE_KEY` | the private key file's whole text, `-----BEGIN…` to `…END-----` |
   | `KLP_DASHBOARD_PASSWORD` | a long password for the dashboard |
   | `KLP_LIVE` | `1` to trade real money (anything else: dry run) |
   | `KLP_CONFIG` | optional, default `config/prod.yaml` |

4. **Give it a public domain** (Settings → Networking → Generate domain) to reach the
   dashboard. It asks for the password (any user name).

Then deploy (`railway up`, or push to the connected GitHub branch).

## Bringing your history over (once)

The journal on your laptop (`runs/prod-live`: reward ledger, payout tracking, fills, balance
readings) can come along, so the server carries on where the laptop left off:

1. Stop the bot on the laptop (Ctrl+C), so nothing more is written.
2. `deploy/make_seed.sh`: packs it into `deploy/seed/runs.tar.gz` (~8 MB; the old log lines
   are left out). Git ignores it; `railway up` still uploads it.
3. `railway up`. On its first start the server unpacks it onto the volume. Only into an empty
   one: a volume that already has a journal is never overwritten.
4. Delete `deploy/seed/` afterwards (it isn't needed again).

## If it won't start

- `KALSHI_PROD_PRIVATE_KEY_PATH=secrets/prod.key: no such file on this server`: that variable
  was copied from your laptop's `.env`. Delete it in Railway and set `KALSHI_PROD_PRIVATE_KEY`
  to the key's text instead.

## Good to know

- Railway restarts the bot if it exits; open positions carry over a restart.
- The dashboard's "save & restart" edits `config/prod.yaml` inside the running container: it
  lasts until the next deploy. To keep a change, make it in the repo's `config/prod.yaml`.
- `events.jsonl` grows ~100 MB a day on the volume: size the volume for it, or trim it.
- Health check: `GET /api/health` (no password: it only says whether the bot runs).
