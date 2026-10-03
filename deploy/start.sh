#!/bin/sh
# Start the bot on a server. Everything comes from environment variables:
#
#   KALSHI_PROD_KEY_ID        your API key id
#   KALSHI_PROD_PRIVATE_KEY   the private key itself (the PEM text, BEGIN...END lines included)
#   KLP_DASHBOARD_PASSWORD    the dashboard's password (required: it's served publicly)
#   KLP_CONFIG                config file (default config/prod.yaml)
#   KLP_DATA_DIR              persistent storage for runs/ and logs/ (default /data: mount a
#                             volume there, or the journal is lost on every redeploy)
#   KLP_LIVE                  "1" to trade real money (default: dry run, nothing sent)
#   PORT                      set by Railway: the dashboard listens on it
set -eu
cd /app

DATA="${KLP_DATA_DIR:-/data}"
mkdir -p "$DATA/runs" "$DATA/logs" "$DATA/secrets"
ln -sfn "$DATA/runs" runs
ln -sfn "$DATA/logs" logs
[ -w "$DATA/runs" ] || { echo "KLP_DATA_DIR $DATA is not writable: mount a volume there" >&2; exit 1; }

# The private keys come in as text; the bot reads them from a file.
for env in PROD DEMO; do
  eval "pem=\${KALSHI_${env}_PRIVATE_KEY:-}"
  if [ -n "$pem" ]; then
    file="$DATA/secrets/$(echo "$env" | tr 'A-Z' 'a-z').key"
    (umask 077 && printf '%s\n' "$pem" > "$file")
    export "KALSHI_${env}_PRIVATE_KEY_PATH=$file"
  fi
done

export KLP_API_HOST="${KLP_API_HOST:-0.0.0.0}"
export KLP_API_PORT="${PORT:-${KLP_API_PORT:-8050}}"
CONFIG="${KLP_CONFIG:-config/prod.yaml}"

if [ "${KLP_LIVE:-0}" = "1" ]; then
  exec klp run -c "$CONFIG" --live --confirm-prod
else
  echo "KLP_LIVE is not 1: dry run (quotes are computed and shown, nothing is sent)"
  exec klp run -c "$CONFIG"
fi
