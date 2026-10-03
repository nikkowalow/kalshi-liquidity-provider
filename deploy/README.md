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

## Good to know

- Railway restarts the bot if it exits; open positions carry over a restart.
- The dashboard's "save & restart" edits `config/prod.yaml` inside the running container: it
  lasts until the next deploy. To keep a change, make it in the repo's `config/prod.yaml`.
- `events.jsonl` grows ~100 MB a day on the volume: size the volume for it, or trim it.
- Health check: `GET /api/health` (no password: it only says whether the bot runs).
